"""
Customer Communication Intelligence System (CCIS)
==================================================
Classifies buyer messages, generates drafts, manages approvals.
Integrates with VoC for profit intelligence.

Architecture:
  Message → Classifier → Intent Detection → Knowledge Retrieval
  → Draft Generator → Approval Workflow → Send
"""

import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, and_

from db.connection import get_session
from db.models import (
    CustomerMessage, MessageDraft, SupportPlaybook, SupportCase,
    CustomerLossEvent, Refund, OrderItem,
)
from auth.store_registry import StoreRegistry

logger = logging.getLogger(__name__)

# Message classification patterns
CLASSIFICATION_PATTERNS = {
    "shipping": ["shipping", "delivery", "late", "hasn't arrived", "not received", "tracking", "lost", "where is my"],
    "damage": ["damaged", "broken", "cracked", "shattered", "arrived broken", "fractured", "chip"],
    "missing_part": ["missing", "missing part", "not included", "didn't come with", "screw", "bracket", "mount"],
    "installation": ["installation", "install", "how to", "setup", "mount", "hard to", "instructions", "help"],
    "warranty": ["warranty", "guarantee", "covered", "repair", "replace"],
    "refund_request": ["refund", "money back", "give me my money", "chargeback"],
    "return_request": ["return", "send back", "return label", "rma"],
    "product_question": ["does this", "will this", "can this", "compatible", "size", "dimension", "color"],
    "review_threat": ["bad review", "negative review", "1 star", "one star", "report you", "complaint"],
    "replacement_request": ["replacement", "send me a new", "exchange", "swap"],
}


class CCISEngine:
    """
    Customer Communication Intelligence System.
    """

    def __init__(self, store_id: str):
        self.store_id = store_id

    # ─── 1. CLASSIFICATION ───

    def classify_message(self, body: str, subject: str = "") -> dict:
        """Classify a buyer message by intent, priority, and sentiment."""
        text = (subject + " " + body).lower()

        # Detect type
        detected_type = "other"
        best_score = 0
        for msg_type, keywords in CLASSIFICATION_PATTERNS.items():
            score = sum(1 for kw in keywords if kw in text)
            if score > best_score:
                best_score = score
                detected_type = msg_type

        # Priority mapping
        priority_map = {
            "damage": "high", "refund_request": "high", "review_threat": "high",
            "warranty": "medium", "replacement_request": "medium", "missing_part": "medium",
            "shipping": "medium", "return_request": "medium",
            "installation": "low", "product_question": "low",
        }

        # Sentiment
        neg_words = ["broken", "damaged", "terrible", "awful", "frustrated", "angry", "worst", "bad", "useless"]
        pos_words = ["good", "great", "love", "perfect", "thanks", "help", "please"]
        neg_count = sum(1 for w in neg_words if w in text)
        pos_count = sum(1 for w in pos_words if w in text)
        sentiment = "negative" if neg_count > pos_count else "positive" if pos_count > neg_count else "neutral"

        return {
            "message_type": detected_type,
            "priority": priority_map.get(detected_type, "low"),
            "sentiment": sentiment,
            "confidence": min(best_score / 4 + 0.5, 0.95),
        }

    # ─── 2. CUSTOMER RISK SCORE ───

    def calculate_risk(self, amazon_order_id: str, sku: str = "") -> dict:
        """Calculate customer risk score based on order history."""
        session = get_session()
        try:
            # Check refund history
            refund_count = session.query(func.count(Refund.id)).filter(
                Refund.store_id == self.store_id,
                Refund.amazon_order_id == amazon_order_id,
            ).scalar() or 0

            # Check message history
            msg_count = session.query(func.count(CustomerMessage.id)).filter(
                CustomerMessage.store_id == self.store_id,
                CustomerMessage.amazon_order_id == amazon_order_id,
            ).scalar() or 0

            # Check loss events
            loss_total = session.query(
                func.coalesce(func.sum(CustomerLossEvent.amount_loss), 0)
            ).filter(
                CustomerLossEvent.store_id == self.store_id,
                CustomerLossEvent.amazon_order_id == amazon_order_id,
            ).scalar() or 0

            # Get order value
            order_value = session.query(
                func.coalesce(func.sum(OrderItem.item_price_amount), 0)
            ).filter(
                OrderItem.amazon_order_id == amazon_order_id,
            ).scalar() or 0

            refund_prob = min(refund_count * 0.15 + msg_count * 0.05, 0.95)
            expected_loss = float(order_value) * refund_prob

            return {
                "customer_value": float(order_value),
                "refund_probability": round(refund_prob, 4),
                "expected_loss": round(expected_loss, 2),
                "previous_refunds": refund_count,
                "previous_messages": msg_count,
            }
        finally:
            session.close()

    # ─── 3. KNOWLEDGE RETRIEVAL ───

    def get_playbook(self, issue_type: str) -> Optional[dict]:
        """Get the best matching playbook for an issue type."""
        session = get_session()
        try:
            pb = session.query(SupportPlaybook).filter(
                SupportPlaybook.store_id == self.store_id,
                SupportPlaybook.issue_type == issue_type,
                SupportPlaybook.is_active == True,
            ).first()

            if pb:
                return {
                    "issue_type": pb.issue_type,
                    "risk_level": pb.risk_level,
                    "resolution": pb.approved_resolution,
                    "template": pb.template_body,
                    "requires_approval": pb.requires_approval,
                }
            return None
        finally:
            session.close()

    # ─── 4. DRAFT GENERATOR ───

    def generate_draft(self, message: CustomerMessage, playbook: Optional[dict] = None) -> str:
        """Generate a draft reply based on message type and playbook."""
        if playbook and playbook.get("template"):
            return playbook["template"]

        # Basic templates
        templates = {
            "shipping": ("Thank you for contacting us about your delivery. "
                        "We have checked the tracking information and it appears {detail}. "
                        "Please allow 1-2 business days for the carrier to update the status. "
                        "If it does not arrive by then, please let us know and we will arrange a solution."),
            "damage": ("We are sorry to hear about the damage. "
                      "Please provide photos of the damaged item and packaging. "
                      "Once confirmed, we will send a replacement right away. You do not need to return the damaged item — we will arrange disposal with Amazon."),
            "missing_part": ("We apologize for the missing parts. "
                           "Please let us know which specific parts are missing from your order. "
                           "We will ship them to you immediately."),
            "installation": ("We are happy to help with the installation. "
                           "{detail} For video instructions, please visit our product page. "
                           "If you need further assistance, please reply with more details."),
            "refund_request": ("We understand you would like a refund. "
                             "Please provide your order number and reason for the refund request. "
                             "Our team will review and respond within 24 hours."),
            "return_request": ("We are sorry the product did not meet your expectations. "
                             "For items under $50, we offer a returnless refund — no need to send it back. We will arrange disposal with Amazon. Please confirm your order number."),
        }

        template = templates.get(message.message_type or "other",
            "Thank you for your message. We are reviewing your inquiry and will get back to you shortly.")
        return template.replace("{detail}", "our team is reviewing your case")

    # ─── 5. FULL PIPELINE ───

    def process_message(self, body: str, subject: str = "", amazon_order_id: str = "",
                       sku: str = "", customer_id: str = "") -> dict:
        """Full pipeline: classify → risk → knowledge → draft → store."""
        session = get_session()
        try:
            # 1. Classify
            classification = self.classify_message(body, subject)

            # 2. Calculate risk
            risk = self.calculate_risk(amazon_order_id, sku) if amazon_order_id else {}

            # 3. Store message
            msg = CustomerMessage(
                store_id=self.store_id,
                marketplace_id="ATVPDKIKX0DER",
                message_id=f"MSG-{uuid.uuid4().hex[:12]}",
                amazon_order_id=amazon_order_id,
                customer_id=customer_id,
                sku=sku,
                subject=subject,
                body=body,
                message_type=classification["message_type"],
                priority=classification["priority"],
                sentiment=classification["sentiment"],
                status="classified",
                customer_value=risk.get("customer_value", 0),
                refund_probability=risk.get("refund_probability", 0),
                expected_loss=risk.get("expected_loss", 0),
                received_date=datetime.utcnow(),
            )
            session.add(msg)
            session.flush()

            # 4. Get playbook
            playbook = self.get_playbook(classification["message_type"])

            # 5. Generate draft
            draft_text = self.generate_draft(msg, playbook)
            approval_needed = playbook["requires_approval"] if playbook else True

            draft = MessageDraft(
                message_id=msg.id,
                store_id=self.store_id,
                draft_reply=draft_text,
                confidence=classification["confidence"],
                approval_required=approval_needed,
                approval_status="pending" if approval_needed else "approved",
            )
            session.add(draft)

            # 6. Create support case
            case = SupportCase(
                store_id=self.store_id,
                message_id=msg.id,
                amazon_order_id=amazon_order_id,
                sku=sku,
                issue_type=classification["message_type"],
                status="open",
            )
            session.add(case)
            session.commit()

            return {
                "message_id": msg.message_id,
                "classification": classification,
                "risk": risk,
                "draft": draft_text,
                "approval_required": approval_needed,
                "playbook_used": playbook is not None,
                "case_created": True,
            }
        except Exception as e:
            session.rollback()
            logger.error(f"Process message failed: {e}")
            return {"error": str(e)}
        finally:
            session.close()

    # ─── 6. DASHBOARD ───

    def get_dashboard(self, days: int = 30) -> dict:
        """CCIS dashboard: top complaints, risk, trends."""
        session = get_session()
        try:
            since = datetime.utcnow() - timedelta(days=days)

            # Messages by type
            by_type = session.query(
                CustomerMessage.message_type,
                func.count(CustomerMessage.id).label("count"),
            ).filter(
                CustomerMessage.store_id == self.store_id,
                CustomerMessage.created_at >= since,
            ).group_by(CustomerMessage.message_type).all()

            # Pending approvals
            pending = session.query(func.count(MessageDraft.id)).filter(
                MessageDraft.store_id == self.store_id,
                MessageDraft.approval_status == "pending",
            ).scalar() or 0

            # Total expected loss
            total_loss = session.query(
                func.coalesce(func.sum(CustomerMessage.expected_loss), 0)
            ).filter(
                CustomerMessage.store_id == self.store_id,
                CustomerMessage.created_at >= since,
            ).scalar() or 0

            # High priority
            high_priority = session.query(func.count(CustomerMessage.id)).filter(
                CustomerMessage.store_id == self.store_id,
                CustomerMessage.priority == "high",
                CustomerMessage.created_at >= since,
            ).scalar() or 0

            return {
                "messages_by_type": {r.message_type: r.count for r in by_type},
                "pending_approvals": pending,
                "total_expected_loss": float(total_loss),
                "high_priority": high_priority,
                "total_messages": sum(r.count for r in by_type),
            }
        finally:
            session.close()
