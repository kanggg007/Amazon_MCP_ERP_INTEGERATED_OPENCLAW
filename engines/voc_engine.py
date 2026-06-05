"""
Voice of Customer Engine
==========================
Classifies customer feedback into topics.
Identifies top complaints, feature requests, and listing opportunities.

Supports: Amazon reviews, return reasons, Q&A, Walmart reviews, TikTok.
"""

import logging
import re
from collections import Counter
from datetime import date, timedelta
from typing import Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy import func

from db.models import CustomerFeedback, FeedbackTopic

logger = logging.getLogger("amazon.engine.voc")


# Topic classification keywords
TOPIC_PATTERNS = {
    "packaging": ["packaging", "package", "box", "bubble wrap", "styrofoam", "damaged in shipping"],
    "quality": ["quality", "cheap", "poor", "defective", "broken", "cracked", "fell apart"],
    "installation": ["install", "installation", "setup", "mount", "hard to", "instructions", "confusing"],
    "shipping_damage": ["damaged", "broken in shipping", "cracked", "shattered", "arrived broken"],
    "dimensions": ["size", "too big", "too small", "dimension", "measurement", "fit"],
    "performance": ["doesn't work", "not working", "underperforms", "weak", "poor performance", "stopped"],
    "features": ["missing", "missing part", "accessories", "including", "includes"],
    "bluetooth": ["bluetooth", "pair", "connection", "connectivity", "disconnect"],
    "lighting": ["light", "brightness", "dim", "led", "glow"],
    "customer_service": ["customer service", "support", "refund", "return", "no response"],
    "price": ["expensive", "overpriced", "not worth", "value for money", "price"],
    "design": ["design", "look", "appearance", "ugly", "color different", "not as pictured"],
    "sound": ["noise", "loud", "quiet", "sound", "volume"],
    "durability": ["lasted", "broke after", "stopped after", "broke within", "months later"],
}


class VoCEngine:
    """
    Voice of Customer intelligence.
    Classifies feedback, surfaces complaints and opportunities.
    """

    def __init__(self, db: Session, store_id: str):
        self.db = db
        self.store_id = store_id

    def classify_pending(self, days: int = 30) -> int:
        """Classify all unclassified feedback from the last N days."""
        start = date.today() - timedelta(days=days)

        # Find feedback that doesn't have topics yet
        subq = self.db.query(FeedbackTopic.feedback_id)
        unclassified = self.db.query(CustomerFeedback).filter(
            CustomerFeedback.store_id == self.store_id,
            CustomerFeedback.feedback_date >= start,
            ~CustomerFeedback.id.in_(subq),
        ).all()

        count = 0
        for fb in unclassified:
            topics = self._extract_topics(fb.body or "" + " " + (fb.title or ""))
            for topic in topics:
                self.db.add(FeedbackTopic(
                    feedback_id=fb.id,
                    topic=topic,
                    sentiment=fb.sentiment,
                    confidence_score=0.70,
                ))
                count += 1

        self.db.commit()
        logger.info("Classified %d topics across %d feedback items", count, len(unclassified))
        return count

    def top_complaints(self, days: int = 30, limit: int = 5) -> List[Dict]:
        """Get top customer complaints."""
        start = date.today() - timedelta(days=days)
        topics = self.db.query(
            FeedbackTopic.topic,
            func.count(FeedbackTopic.id).label("count"),
        ).join(
            CustomerFeedback,
            FeedbackTopic.feedback_id == CustomerFeedback.id,
        ).filter(
            CustomerFeedback.store_id == self.store_id,
            CustomerFeedback.sentiment.in_(["negative", "neutral"]),
            CustomerFeedback.feedback_date >= start,
        ).group_by(FeedbackTopic.topic).order_by(
            func.count(FeedbackTopic.id).desc()
        ).limit(limit).all()

        total = sum(t.count for t in topics) if topics else 1
        return [
            {"topic": t.topic, "count": t.count, "pct": round(t.count / total * 100, 1)}
            for t in topics
        ]

    def asin_complaints(self, asin: str, days: int = 90) -> List[Dict]:
        """Get complaints for a specific ASIN."""
        start = date.today() - timedelta(days=days)
        topics = self.db.query(
            FeedbackTopic.topic,
            func.count(FeedbackTopic.id).label("count"),
        ).join(
            CustomerFeedback,
            FeedbackTopic.feedback_id == CustomerFeedback.id,
        ).filter(
            CustomerFeedback.store_id == self.store_id,
            CustomerFeedback.asin == asin,
            CustomerFeedback.sentiment.in_(["negative", "neutral"]),
            CustomerFeedback.feedback_date >= start,
        ).group_by(FeedbackTopic.topic).order_by(
            func.count(FeedbackTopic.id).desc()
        ).all()

        return [{"topic": t.topic, "count": t.count} for t in topics]

    def listing_opportunities(self, asin: str) -> List[Dict]:
        """Generate listing optimization suggestions based on feedback."""
        feedback = self.db.query(CustomerFeedback).filter(
            CustomerFeedback.store_id == self.store_id,
            CustomerFeedback.asin == asin,
            CustomerFeedback.sentiment == "negative",
        ).all()

        all_text = " ".join((f.body or "") + " " + (f.title or "") for f in feedback).lower()
        suggestions = []

        checks = [
            ("size", "dimensions", "Add exact dimensions to the first bullet point", "high"),
            ("installation", "instructions", "Add installation video or step-by-step guide", "medium"),
            ("quality", "material", "Emphasize premium materials in description", "high"),
            ("shipping_damage", "packaging", "Improve packaging quality", "high"),
            ("design", "image", "Update images to match actual product appearance", "medium"),
            ("features", "specs", "Clarify included accessories in bullet points", "medium"),
        ]

        for keyword, topic, suggestion, priority in checks:
            if keyword in all_text:
                suggestions.append({
                    "topic": topic,
                    "suggestion": suggestion,
                    "priority": priority,
                    "confidence": 0.7,
                })

        return suggestions

    def sentiment_trend(self, asin: str, days: int = 90) -> Dict:
        """Track sentiment over time for an ASIN."""
        start = date.today() - timedelta(days=days)
        total = self.db.query(func.count(CustomerFeedback.id)).filter(
            CustomerFeedback.store_id == self.store_id,
            CustomerFeedback.asin == asin,
            CustomerFeedback.feedback_date >= start,
        ).scalar() or 0

        positive = self.db.query(func.count(CustomerFeedback.id)).filter(
            CustomerFeedback.store_id == self.store_id,
            CustomerFeedback.asin == asin,
            CustomerFeedback.sentiment == "positive",
            CustomerFeedback.feedback_date >= start,
        ).scalar() or 0

        negative = self.db.query(func.count(CustomerFeedback.id)).filter(
            CustomerFeedback.store_id == self.store_id,
            CustomerFeedback.asin == asin,
            CustomerFeedback.sentiment == "negative",
            CustomerFeedback.feedback_date >= start,
        ).scalar() or 0

        return {
            "asin": asin,
            "days": days,
            "total_feedback": total,
            "positive": round(positive / total * 100, 1) if total > 0 else 0,
            "negative": round(negative / total * 100, 1) if total > 0 else 0,
            "health": "good" if positive > negative * 3 else "needs_attention" if positive > negative else "critical",
        }

    # ─── Enhanced: Source Tier Ranking ───

    TIER_MAP = {
        "amazon_review": 1,
        "amazon_return": 2,
        "amazon_qna": 3,
        "amazon_support_ticket": 4,
        "walmart_review": 5,
        "tiktok_comment": 5,
        "facebook_comment": 5,
    }

    TIER_LABELS = {1: "🏆 Reviews", 2: "🔄 Returns", 3: "❓ Q&A", 4: "🎫 Tickets", 5: "🌐 Social"}

    def feedback_by_source(self, days: int = 30) -> list[dict]:
        """Feedback volume grouped by source tier."""
        start = date.today() - timedelta(days=days)
        rows = self.db.query(
            CustomerFeedback.source,
            func.count(CustomerFeedback.id).label("count"),
        ).filter(
            CustomerFeedback.store_id == self.store_id,
            CustomerFeedback.feedback_date >= start,
        ).group_by(CustomerFeedback.source).all()

        by_tier = {}
        for r in rows:
            tier = self.TIER_MAP.get(r.source, 5)
            label = self.TIER_LABELS.get(tier, "Other")
            if tier not in by_tier:
                by_tier[tier] = {"tier": tier, "label": label, "sources": [], "count": 0}
            by_tier[tier]["sources"].append({"source": r.source, "count": r.count})
            by_tier[tier]["count"] += r.count

        return [by_tier[t] for t in sorted(by_tier.keys())]

    # ─── Enhanced: Trend Detection ───

    def complaint_trend(self, topic: str = None, weeks: int = 8) -> list[dict]:
        """Track complaint frequency week-over-week."""
        from datetime import timedelta as td
        end = date.today()
        start = end - td(weeks=weeks * 7)

        results = []
        for w in range(weeks):
            ws = end - td(weeks=(weeks - w) * 7)
            we = ws + td(days=7)

            q = self.db.query(func.count(FeedbackTopic.id)).join(
                CustomerFeedback,
                FeedbackTopic.feedback_id == CustomerFeedback.id,
            ).filter(
                CustomerFeedback.store_id == self.store_id,
                CustomerFeedback.feedback_date >= ws,
                CustomerFeedback.feedback_date < we,
                CustomerFeedback.sentiment.in_(["negative", "neutral"]),
            )
            if topic:
                q = q.filter(FeedbackTopic.topic == topic)

            results.append({
                "week": ws.isoformat(),
                "count": q.scalar() or 0,
            })

        return results

    # ─── Enhanced: Profit Impact ───

    def complaint_profit_impact(self, days: int = 90) -> list[dict]:
        """
        Correlate complaints with profit impact.
        Uses return/refund data to estimate how much each complaint topic costs.
        """
        start = date.today() - timedelta(days=days)

        # Get top complaint topics with counts
        topics = self.db.query(
            FeedbackTopic.topic,
            func.count(FeedbackTopic.id).label("count"),
        ).join(
            CustomerFeedback,
            FeedbackTopic.feedback_id == CustomerFeedback.id,
        ).filter(
            CustomerFeedback.store_id == self.store_id,
            CustomerFeedback.feedback_date >= start,
            CustomerFeedback.sentiment == "negative",
        ).group_by(FeedbackTopic.topic).order_by(
            func.count(FeedbackTopic.id).desc()
        ).limit(10).all()

        # Get total refunds for context
        from db.models import Refund
        total_refunds = self.db.query(
            func.coalesce(func.sum(Refund.refund_amount), 0)
        ).filter(
            Refund.store_id == self.store_id,
            Refund.refund_date >= start,
        ).scalar() or 0

        # Get total returns count
        from db.models import Return as ReturnModel
        total_returns = self.db.query(
            func.count(ReturnModel.id)
        ).filter(
            ReturnModel.store_id == self.store_id,
            ReturnModel.return_request_date >= start,
        ).scalar() or 0

        # Damage-related refunds (approximation)
        from db.models import CustomerLossEvent
        damage_loss = self.db.query(
            func.coalesce(func.sum(CustomerLossEvent.amount_loss), 0)
        ).filter(
            CustomerLossEvent.store_id == self.store_id,
            CustomerLossEvent.event_date >= start,
            CustomerLossEvent.loss_type.in_(["return_damage", "customer_return"]),
        ).scalar() or 0

        total_topics = sum(t.count for t in topics) if topics else 1
        return [
            {
                "topic": t.topic,
                "frequency": t.count,
                "share_pct": round(t.count / total_topics * 100, 1),
                "estimated_refund_cost": round(
                    float(total_refunds) * (t.count / total_topics), 2
                ),
                "estimated_damage_cost": round(
                    float(damage_loss) * (t.count / total_topics), 2
                ),
            }
            for t in topics
        ]

    # ─── Enhanced: Monthly comparison ───

    def monthly_comparison(self) -> list[dict]:
        """Compare complaint counts between last month and current month."""
        today = date.today()
        this_month_start = today.replace(day=1)
        last_month_end = this_month_start - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)

        def get_counts(start_dt, end_dt):
            rows = self.db.query(
                FeedbackTopic.topic,
                func.count(FeedbackTopic.id).label("count"),
            ).join(
                CustomerFeedback,
                FeedbackTopic.feedback_id == CustomerFeedback.id,
            ).filter(
                CustomerFeedback.store_id == self.store_id,
                CustomerFeedback.feedback_date >= start_dt,
                CustomerFeedback.feedback_date < end_dt,
                CustomerFeedback.sentiment.in_(["negative", "neutral"]),
            ).group_by(FeedbackTopic.topic).all()
            return {r.topic: r.count for r in rows}

        last = get_counts(last_month_start, this_month_start)
        current = get_counts(this_month_start, today + timedelta(days=1))

        all_topics = set(last.keys()) | set(current.keys())
        results = []
        for topic in sorted(all_topics):
            lc = last.get(topic, 0)
            cc = current.get(topic, 0)
            results.append({
                "topic": topic,
                "last_month": lc,
                "this_month": cc,
                "change": cc - lc,
                "pct_change": round((cc - lc) / lc * 100, 1) if lc > 0 else None,
            })

        return sorted(results, key=lambda x: abs(x.get("change", 0)), reverse=True)

    # ─── Enhanced: Full VoC dashboard ───

    def intelligence_dashboard(self, days: int = 90) -> dict:
        """Complete VoC dashboard combining all analyses."""
        return {
            "top_complaints": self.top_complaints(days=days, limit=10),
            "by_source": self.feedback_by_source(days=days),
            "trends": self.complaint_trend(weeks=8),
            "profit_impact": self.complaint_profit_impact(days=days),
            "monthly_change": self.monthly_comparison(),
        }

    def _extract_topics(self, text: str) -> List[str]:
        """Extract topics from text using keyword matching."""
        text_lower = text.lower()
        found = []
        for topic, keywords in TOPIC_PATTERNS.items():
            for kw in keywords:
                if kw in text_lower:
                    found.append(topic)
                    break
        return found[:3]  # Max 3 topics per feedback
