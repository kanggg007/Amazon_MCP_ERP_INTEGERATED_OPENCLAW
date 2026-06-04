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
