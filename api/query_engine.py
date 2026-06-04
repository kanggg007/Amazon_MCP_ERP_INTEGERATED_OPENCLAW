"""
OpenClaw Query Engine
=======================
Natural language query interface.
OpenClaw is the command center — it can recommend but cannot bypass approval rules.

Supported queries:
- "Show top inventory risks"
- "Show reimbursement opportunities"
- "Show products with declining profit"
- "Show biggest customer complaints"
- "What ad campaigns exceeded 25% TACoS last week?"
"""

import logging
import re
from typing import Dict, List, Optional
from datetime import date, timedelta

from sqlalchemy.orm import Session as DBSession

from engines.inventory_engine import InventoryEngine
from engines.discrepancy_engine import DiscrepancyEngine
from engines.profit_engine import ProfitEngine
from engines.ads_engine import AdsEngine
from engines.voc_engine import VoCEngine
from db.connection import get_session

logger = logging.getLogger("amazon.api.query")

# Intent patterns
PATTERNS = {
    "inventory_risks": r"(inventory|stock|supply)",
    "reimbursement": r"(reimburs|claim|discrepancy|missing|damaged)",
    "declining_profit": r"(declining|dropping|profit margin|negative profit)",
    "complaints": r"(complaint|pain points|negative feedback|customers? (say|complaint))",
    "ad_campaigns": r"(ad|ppc|sponsored|campaign|tacos|acos)",
    "tacos": r"(tacos?|total ad)",
    "reorder": r"(reorder|restock|buy more|purchase order)",
}


class QueryEngine:
    """
    Natural language query → structured data + recommendations.

    OpenClaw can recommend actions.
    OpenClaw CANNOT bypass approval rules.
    """

    def __init__(self, db: DBSession, store_id: str):
        self.db = db
        self.store_id = store_id

    def query(self, text: str) -> Dict:
        """Process a natural language query."""
        text_lower = text.lower()

        # Match intent
        intent = self._detect_intent(text_lower)

        if intent == "inventory_risks":
            return self._inventory_risks(text_lower)
        elif intent == "reimbursement":
            return self._reimbursement_opportunities(text_lower)
        elif intent == "declining_profit":
            return self._declining_profit(text_lower)
        elif intent == "complaints":
            return self._complaints(text_lower)
        elif intent == "ad_campaigns" or intent == "tacos":
            return self._ad_analysis(text_lower)
        elif intent == "reorder":
            return self._reorder(text_lower)
        else:
            return self._help_response()

    def _detect_intent(self, text: str) -> Optional[str]:
        for intent, pattern in PATTERNS.items():
            if re.search(pattern, text):
                return intent
        return None

    def _extract_days(self, text: str, default: int = 7) -> int:
        m = re.search(r"(\d+)\s*(day|days|d)", text)
        return int(m.group(1)) if m else default

    def _extract_limit(self, text: str, default: int = 5) -> int:
        m = re.search(r"top\s*(\d+)", text)
        return int(m.group(1)) if m else default

    def _extract_asin(self, text: str) -> Optional[str]:
        m = re.search(r"B[A-Z0-9]{9}", text)
        return m.group(0) if m else None

    def _inventory_risks(self, text: str) -> Dict:
        engine = InventoryEngine(self.db, self.store_id)
        alerts = engine.analyze()
        critical = [a for a in alerts if a["severity"] == "critical"]
        warnings = [a for a in alerts if a["severity"] == "warning"]

        return {
            "query": "inventory_risks",
            "store_id": self.store_id,
            "critical_count": len(critical),
            "warning_count": len(warnings),
            "alerts": critical + warnings,
            "recommendation": (
                f"{len(critical)} critical alerts need immediate action."
                if critical else "No critical inventory issues."
            ),
            "confidence": 0.85,
            "approval_required": False,
        }

    def _reimbursement_opportunities(self, text: str) -> Dict:
        engine = DiscrepancyEngine(self.db, self.store_id)
        discrepancies = engine.scan_shipments(min_loss_usd=50.0)
        total_loss = sum(d["estimated_loss"] for d in discrepancies)

        return {
            "query": "reimbursement_opportunities",
            "store_id": self.store_id,
            "count": len(discrepancies),
            "total_estimated_loss": round(total_loss, 2),
            "discrepancies": discrepancies,
            "recommendation": (
                f"File FBA claims for {len(discrepancies)} discrepancies totaling ${total_loss:,.2f}"
                if discrepancies else "No discrepancies found."
            ),
            "confidence": 0.85,
            "approval_required": True,  # Claims require human approval
        }

    def _declining_profit(self, text: str) -> Dict:
        days = self._extract_days(text, 30)
        engine = ProfitEngine(self.db, self.store_id)
        # Get all SKUs with margin < 10% or negative
        from db.models import ProfitDailySnapshot
        from sqlalchemy import func, text as sqla_text

        start = date.today() - timedelta(days=days)
        rows = self.db.query(
            ProfitDailySnapshot.sku,
            func.sum(ProfitDailySnapshot.revenue).label("revenue"),
            func.sum(ProfitDailySnapshot.units_sold).label("units"),
        ).filter(
            ProfitDailySnapshot.store_id == self.store_id,
            ProfitDailySnapshot.date >= start,
        ).group_by(ProfitDailySnapshot.sku).having(
            func.sum(ProfitDailySnapshot.revenue) > 100,
        ).order_by(
            func.sum(ProfitDailySnapshot.units_sold).desc()
        ).all()

        declining = []
        for r in rows:
            summary = engine.sku_summary(r.sku, days)
            if summary.get("margin", 100) < 10:
                declining.append(summary)

        return {
            "query": "declining_profit",
            "store_id": self.store_id,
            "days": days,
            "products_at_risk": len(declining),
            "products": declining[:10],
            "recommendation": f"Review {len(declining)} SKUs with margins below 10%."
                if declining else "All SKUs profitable.",
            "confidence": 0.75,
            "approval_required": False,
        }

    def _complaints(self, text: str) -> Dict:
        days = self._extract_days(text, 30)
        limit = self._extract_limit(text, 5)
        asin = self._extract_asin(text)
        engine = VoCEngine(self.db, self.store_id)
        engine.classify_pending(days)

        if asin:
            complaints = engine.asin_complaints(asin, days)
            sentiment = engine.sentiment_trend(asin, days)
            opportunities = engine.listing_opportunities(asin)
        else:
            complaints = engine.top_complaints(days, limit)
            sentiment = {}
            opportunities = []

        return {
            "query": "customer_complaints",
            "store_id": self.store_id,
            "asin": asin,
            "days": days,
            "top_complaints": complaints,
            "sentiment": sentiment,
            "listing_opportunities": opportunities,
            "recommendation": f"Top issue: {complaints[0]['topic']} ({complaints[0]['pct']}%)"
                if complaints else "No significant complaints.",
            "confidence": 0.70,
            "approval_required": False,
        }

    def _ad_analysis(self, text: str) -> Dict:
        days = self._extract_days(text, 7)
        engine = AdsEngine(self.db, self.store_id)
        campaigns = engine.analyze_campaigns(days=days)
        tacos = engine.calculate_tacos(days=30)
        flagged = [c for c in campaigns if "acos_alert" in str(c.get("recommendation", {}))]

        return {
            "query": "ad_analysis",
            "store_id": self.store_id,
            "days": days,
            "TACoS_30d": tacos,
            "campaigns_flagged": len(flagged),
            "campaigns": flagged[:10],
            "recommendation": f"{len(flagged)} campaigns need attention." if flagged else "Ad spend healthy.",
            "confidence": 0.75,
            "approval_required": True,
        }

    def _reorder(self, text: str) -> Dict:
        engine = InventoryEngine(self.db, self.store_id)
        sku_match = re.search(r"sku[:\s]*([A-Za-z0-9_-]+)", text)
        if sku_match:
            sku = sku_match.group(1)
            rec = engine.reorder_recommendation(sku)
            return {
                "query": "reorder",
                "store_id": self.store_id,
                "sku": sku,
                "recommendation": rec,
                "confidence": rec.get("confidence", 0),
                "approval_required": True,
            }
        else:
            alerts = engine.analyze()
            low = [a for a in alerts if a["type"] == "low_stock"]
            return {
                "query": "reorder_all",
                "store_id": self.store_id,
                "skus_needing_reorder": len(low),
                "alerts": low[:10],
                "recommendation": f"Review {len(low)} SKUs needing reorder.",
                "confidence": 0.80,
                "approval_required": True,
            }

    def _help_response(self) -> Dict:
        return {
            "query": "help",
            "store_id": self.store_id,
            "available_queries": [
                "Show top inventory risks",
                "Show reimbursement opportunities",
                "Show products with declining profit",
                "Show biggest customer complaints",
                "What ad campaigns exceeded 25% TACoS last week?",
                "Show reorder recommendations for SKU ABC123",
            ],
            "note": "OpenClaw can recommend actions but cannot bypass approval rules.",
            "approval_required": False,
        }
