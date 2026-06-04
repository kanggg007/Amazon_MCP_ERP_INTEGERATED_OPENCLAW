"""
Ads Engine
============
Recommendation-only. NEVER auto-adjusts bids.

Provides:
- TACoS analysis (Total Ad Cost of Sale)
- ACoS analysis (per campaign)
- Keyword scoring
- Bid recommendations (require human approval)
"""

import logging
from datetime import date, timedelta
from typing import Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy import func

from db.models import AdPerformance, ProfitDailySnapshot

logger = logging.getLogger("amazon.engine.ads")


class Recommendation:
    """A single ad recommendation with confidence and risk level."""

    def __init__(self, store_id: str, campaign_id: str, message: str,
                 rec_type: str, confidence: float, risk_level: str):
        self.store_id = store_id
        self.campaign_id = campaign_id
        self.message = message
        self.type = rec_type
        self.confidence = confidence
        self.risk_level = risk_level

    def to_dict(self) -> Dict:
        return {
            "store_id": self.store_id,
            "campaign_id": self.campaign_id,
            "message": self.message,
            "type": self.type,
            "confidence": round(self.confidence, 3),
            "risk_level": self.risk_level,
        }


class AdsEngine:
    """
    Ads optimization engine. Recommendation-only.
    All bid adjustments require Level 2 (human approval).
    """

    HIGH_ACOS_THRESHOLD = 0.25
    LOW_ACOS_THRESHOLD = 0.10
    HIGH_SPEND_THRESHOLD = 200.0

    def __init__(self, db: Session, store_id: str):
        self.db = db
        self.store_id = store_id

    def analyze_campaigns(self, days: int = 7) -> List[Dict]:
        """
        Analyze all campaigns over N days.

        Returns:
            List of campaign analyses with recommendations
        """
        start = date.today() - timedelta(days=days)

        campaigns = self.db.query(
            AdPerformance.campaign_id,
            AdPerformance.campaign_name,
            func.sum(AdPerformance.impressions).label("impressions"),
            func.sum(AdPerformance.clicks).label("clicks"),
            func.sum(AdPerformance.spend).label("spend"),
            func.sum(AdPerformance.sales).label("sales"),
            func.sum(AdPerformance.orders).label("orders"),
        ).filter(
            AdPerformance.store_id == self.store_id,
            AdPerformance.date >= start,
        ).group_by(
            AdPerformance.campaign_id, AdPerformance.campaign_name,
        ).all()

        recommendations = []
        for c in campaigns:
            impressions = int(c.impressions or 0)
            clicks = int(c.clicks or 0)
            spend = float(c.spend or 0)
            sales = float(c.sales or 0)
            orders = int(c.orders or 0)

            acos = spend / sales if sales > 0 else float("inf")
            ctr = clicks / impressions if impressions > 0 else 0
            cvr = orders / clicks if clicks > 0 else 0

            analysis = {
                "campaign_id": c.campaign_id,
                "campaign_name": c.campaign_name,
                "impressions": impressions,
                "clicks": clicks,
                "spend": round(spend, 2),
                "sales": round(sales, 2),
                "orders": orders,
                "ACoS": round(acos, 4),
                "CTR": round(ctr, 4),
                "CVR": round(cvr, 4),
                "RoAS": round(sales / spend, 2) if spend > 0 else 0,
            }

            # Generate recommendations
            if acos > self.HIGH_ACOS_THRESHOLD and spend > self.HIGH_SPEND_THRESHOLD:
                risk = "high" if acos > 0.40 else "medium"
                analysis["recommendation"] = Recommendation(
                    self.store_id, c.campaign_id,
                    f"Reduce bids or pause: ACoS {acos:.1%} with ${spend:.0f} spend ({days}d)",
                    "acos_alert", 0.80 if acos > 0.40 else 0.65, risk,
                ).to_dict()

            elif acos < self.LOW_ACOS_THRESHOLD and impressions > self.HIGH_SPEND_THRESHOLD * 5:
                analysis["recommendation"] = Recommendation(
                    self.store_id, c.campaign_id,
                    f"Consider increasing bids: ACoS only {acos:.1%} with {impressions} impressions",
                    "scale_opportunity", 0.60, "low",
                ).to_dict()

            else:
                analysis["recommendation"] = {
                    "type": "no_action",
                    "message": f"Campaign healthy. ACoS {acos:.1%}",
                }

            recommendations.append(analysis)

        return recommendations

    def calculate_tacos(self, days: int = 30) -> Dict:
        """
        Calculate Total Advertising Cost of Sale.

        TACoS = Total Ad Spend / Total Revenue (including organic)
        """
        start = date.today() - timedelta(days=days)

        ad_spend = self.db.query(func.sum(AdPerformance.spend)).filter(
            AdPerformance.store_id == self.store_id,
            AdPerformance.date >= start,
        ).scalar() or 0

        total_rev = self.db.query(func.sum(ProfitDailySnapshot.revenue)).filter(
            ProfitDailySnapshot.store_id == self.store_id,
            ProfitDailySnapshot.date >= start,
        ).scalar() or 0

        return {
            "store_id": self.store_id,
            "days": days,
            "total_ad_spend": round(float(ad_spend), 2),
            "total_revenue": round(float(total_rev), 2),
            "TACoS": round(float(ad_spend) / float(total_rev), 4) if total_rev > 0 else 0,
            "tacos_alert": (float(ad_spend) / float(total_rev)) > 0.25 if total_rev > 0 else False,
        }

    def keyword_scores(self, campaign_id: str, days: int = 30) -> List[Dict]:
        """
        Score keywords by performance.

        Returns:
            List of keywords with metrics and recommendations
        """
        start = date.today() - timedelta(days=days)
        keywords = self.db.query(
            AdPerformance.keyword,
            func.sum(AdPerformance.impressions),
            func.sum(AdPerformance.clicks),
            func.sum(AdPerformance.spend),
            func.sum(AdPerformance.sales),
            func.sum(AdPerformance.orders),
        ).filter(
            AdPerformance.store_id == self.store_id,
            AdPerformance.campaign_id == campaign_id,
            AdPerformance.date >= start,
            AdPerformance.keyword.isnot(None),
        ).group_by(AdPerformance.keyword).all()

        scored = []
        for kw, imp, clk, sp, sl, ords in keywords:
            acos = float(sp) / float(sl) if sl > 0 else float("inf")
            scored.append({
                "keyword": kw,
                "impressions": int(imp),
                "clicks": int(clk),
                "spend": round(float(sp), 2),
                "sales": round(float(sl), 2),
                "orders": int(ords),
                "ACoS": round(acos, 4),
                "score": round(1 / (acos + 0.01), 2),
            })

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored
