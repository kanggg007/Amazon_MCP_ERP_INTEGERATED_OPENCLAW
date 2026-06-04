"""
Approval Workflow
==================
Three-level approval system.

Level 1 — Auto: Reports, analytics, alerts
Level 2 — Approval Required: Ad changes, claims, inventory recommendations
Level 3 — Never Automatic: Pricing changes, listing removals, account settings

The system NEVER bypasses this flow:
    Data → Rules → AI → Human Approval → Execution → Feedback
"""

import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional
from uuid import uuid4

from sqlalchemy.orm import Session
from db.models import RecommendationLog, AuditLog, FeedbackLoop

logger = logging.getLogger("amazon.api.approval")


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ApprovalLevel(str, Enum):
    AUTO = "auto"
    REQUIRED = "required"
    NEVER = "never"


APPROVAL_MATRIX = {
    "generate_report": ApprovalLevel.AUTO,
    "send_alert": ApprovalLevel.AUTO,
    "run_analysis": ApprovalLevel.AUTO,
    "adjust_bid": ApprovalLevel.REQUIRED,
    "file_claim": ApprovalLevel.REQUIRED,
    "reorder": ApprovalLevel.REQUIRED,
    "update_listing": ApprovalLevel.REQUIRED,
    "change_price": ApprovalLevel.NEVER,
    "remove_listing": ApprovalLevel.NEVER,
    "change_account": ApprovalLevel.NEVER,
}


class ApprovalWorkflow:
    """
    Manages the human approval flow for AI recommendations.

    Every action is:
    1. Logged with timestamp, user, store_id, action, before/after, status
    2. Classified by risk level
    3. Routed to correct approval level
    4. Tracked in the feedback loop
    """

    def __init__(self, db: Session, store_id: str, actioned_by: str = "openclaw"):
        self.db = db
        self.store_id = store_id
        self.actioned_by = actioned_by

    def propose(self, action_type: str, message: str,
                confidence: float = 0.75, metadata: Optional[Dict] = None) -> Dict:
        """
        Propose an action. Routes to correct approval level.

        Args:
            action_type: Type of action (adjust_bid, file_claim, etc.)
            message: Human-readable recommendation
            confidence: 0-1 confidence score
            metadata: Optional context data

        Returns:
            Dict with action_id, status, and next steps
        """
        level = APPROVAL_MATRIX.get(action_type, ApprovalLevel.REQUIRED)
        risk = self._classify_risk(action_type, confidence)
        action_id = str(uuid4())[:12]

        before_state = metadata or {}

        # Log the recommendation
        rec = RecommendationLog(
            store_id=self.store_id,
            recommendation_id=action_id,
            recommendation_type=action_type,
            recommendation_text=message,
            confidence_score=min(max(confidence, 0), 1),
            risk_level=risk.value,
            human_action="auto_executed" if level == ApprovalLevel.AUTO else "pending",
            actioned_by=self.actioned_by,
            before_state=before_state,
            created_at=datetime.now(timezone.utc),
        )
        self.db.add(rec)
        self.db.commit()

        # Log audit
        self._audit(action_type, action_id, "proposed", before_state)

        if level == ApprovalLevel.AUTO:
            return {
                "action_id": action_id,
                "status": "auto_executed",
                "risk_level": risk.value,
                "approval_level": level.value,
                "message": message,
                "confidence": confidence,
            }

        elif level == ApprovalLevel.NEVER:
            return {
                "action_id": action_id,
                "status": "rejected_never_auto",
                "risk_level": risk.value,
                "approval_level": level.value,
                "message": f"Action '{action_type}' can never be automatic. Human must execute manually.",
                "confidence": confidence,
            }

        else:
            return {
                "action_id": action_id,
                "status": "pending_approval",
                "risk_level": risk.value,
                "approval_level": level.value,
                "message": message,
                "confidence": confidence,
            }

    def approve(self, action_id: str, approved: bool,
                notes: Optional[str] = None, actioned_by: Optional[str] = None) -> Dict:
        """
        Approve or reject a pending action.

        Args:
            action_id: Recommendation ID
            approved: True to approve, False to reject
            notes: Human notes on decision
            actioned_by: Who approved/rejected

        Returns:
            Result dict
        """
        rec = self.db.query(RecommendationLog).filter(
            RecommendationLog.recommendation_id == action_id,
        ).first()

        if not rec:
            return {"error": f"Action {action_id} not found", "status": "error"}

        if rec.human_action != "pending":
            return {
                "action_id": action_id,
                "status": f"already_{rec.human_action}",
                "message": f"Action was already {rec.human_action}."
            }

        action = actioned_by or self.actioned_by
        new_action = "accepted" if approved else "rejected"

        rec.human_action = new_action
        rec.actioned_by = action
        rec.human_notes = notes
        rec.actioned_at = datetime.now(timezone.utc)
        self.db.commit()

        log_entry = FeedbackLoop(
            recommendation_id=action_id,
            store_id=self.store_id,
            outcome=new_action,
            created_at=datetime.now(timezone.utc),
        )
        self.db.add(log_entry)
        self.db.commit()

        self._audit(rec.recommendation_type, action_id, new_action, {"notes": notes})

        return {
            "action_id": action_id,
            "status": new_action,
            "actioned_by": action,
            "notes": notes,
        }

    def get_pending(self) -> List[Dict]:
        """Get all actions awaiting approval."""
        recs = self.db.query(RecommendationLog).filter(
            RecommendationLog.store_id == self.store_id,
            RecommendationLog.human_action == "pending",
        ).order_by(RecommendationLog.created_at.desc()).all()

        return [
            {
                "action_id": r.recommendation_id,
                "type": r.recommendation_type,
                "message": r.recommendation_text,
                "confidence": float(r.confidence_score) if r.confidence_score else 0,
                "risk_level": r.risk_level,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in recs
        ]

    def _classify_risk(self, action_type: str, confidence: float) -> RiskLevel:
        """Classify risk level of an action."""
        if action_type in ("change_price", "remove_listing", "change_account"):
            return RiskLevel.HIGH
        if action_type in ("file_claim", "adjust_bid"):
            if confidence > 0.85:
                return RiskLevel.MEDIUM
            return RiskLevel.HIGH
        if action_type in ("reorder", "update_listing"):
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    def _audit(self, action: str, resource_id: str, status: str,
               state: Optional[Dict] = None):
        """Log every action — no exceptions."""
        audit = AuditLog(
            store_id=self.store_id,
            action=action,
            actioned_by=self.actioned_by,
            resource_type="recommendation",
            resource_id=resource_id,
            after_state=state,
            status=status,
        )
        self.db.add(audit)
        self.db.commit()
