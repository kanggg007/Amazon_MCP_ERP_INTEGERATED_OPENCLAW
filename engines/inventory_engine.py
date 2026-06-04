"""
Inventory Engine
=================
Calculate days of cover, forecast depletion, and reorder recommendations.
Alerts for: <20 days (low stock), >90 days (overstock).
Every metric is store_id-aware. No cross-store mixing.
"""

import logging
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session
from sqlalchemy import func

from db.models import InventorySnapshot, ProfitDailySnapshot, Product

logger = logging.getLogger("amazon.engine.inventory")


class InventoryAlert:
    def __init__(self, store_id: str, sku: str, alert_type: str, message: str,
                 severity: str, days_of_supply: float, confidence: float):
        self.store_id = store_id
        self.sku = sku
        self.alert_type = alert_type
        self.message = message
        self.severity = severity
        self.days_of_supply = days_of_supply
        self.confidence = confidence

    def to_dict(self) -> Dict:
        return {
            "store_id": self.store_id,
            "sku": self.sku,
            "type": self.alert_type,
            "message": self.message,
            "severity": self.severity,
            "days_of_supply": round(self.days_of_supply, 1),
            "confidence": round(self.confidence, 3),
        }


class InventoryEngine:
    """
    Inventory intelligence engine.
    Runs per store. No cross-store data access.
    """

    DAILY_SALES_LOOKBACK = 30
    LOW_STOCK_DAYS = 20
    OVERSTOCK_DAYS = 90

    def __init__(self, db: Session, store_id: str):
        self.db = db
        self.store_id = store_id

    def analyze(self) -> List[Dict]:
        """
        Full inventory analysis for the store.

        Returns:
            List of alert dicts (low stock, overstock, out of stock)
        """
        today = date.today()
        lookback_start = today - timedelta(days=self.DAILY_SALES_LOOKBACK)

        # Get latest snapshot per SKU
        latest = self._get_latest_snapshots(today)
        alerts = []

        for snap in latest:
            sku = snap.sku
            sellable = snap.sellable_units or 0
            inbound = snap.inbound_units or 0
            available = sellable + inbound

            # Daily sales rate
            daily_rate = self._daily_sales_rate(sku, lookback_start, today)

            if daily_rate <= 0:
                # No sales in lookback — check if product is active
                continue

            days_supply = available / daily_rate if available > 0 else 0

            # Determine depletion date
            depletion = today + timedelta(days=int(days_supply)) if days_supply > 0 else today

            # Update snapshot with computed values
            if snap.days_of_supply != days_supply or snap.forecast_depletion != depletion:
                snap.days_of_supply = days_supply
                snap.forecast_depletion = depletion
                self.db.commit()

            # Alert logic
            if available <= 0:
                confidence = self._confidence(sku)
                alerts.append(InventoryAlert(
                    store_id=self.store_id, sku=sku,
                    alert_type="out_of_stock",
                    message=f"SKU {sku} OUT OF STOCK. Demand: {daily_rate:.1f}/day.",
                    severity="critical",
                    days_of_supply=0,
                    confidence=confidence,
                ).to_dict())

            elif days_supply <= self.LOW_STOCK_DAYS:
                confidence = self._confidence(sku)
                alerts.append(InventoryAlert(
                    store_id=self.store_id, sku=sku,
                    alert_type="low_stock",
                    message=f"SKU {sku}: {days_supply:.0f} days ({available} units). "
                            f"Reorder soon. ({daily_rate:.1f}/day demand)",
                    severity="critical" if days_supply <= 10 else "warning",
                    days_of_supply=days_supply,
                    confidence=confidence,
                ).to_dict())

            elif days_supply >= self.OVERSTOCK_DAYS:
                confidence = self._confidence(sku)
                alerts.append(InventoryAlert(
                    store_id=self.store_id, sku=sku,
                    alert_type="overstock",
                    message=f"SKU {sku}: {days_supply:.0f} days ({available} units). "
                            f"Consider promotions or reduce orders.",
                    severity="warning",
                    days_of_supply=days_supply,
                    confidence=confidence,
                ).to_dict())

        logger.info("Inventory analysis: %s — %d alerts", self.store_id, len(alerts))
        return alerts

    def reorder_recommendation(self, sku: str, lead_time_days: int = 45,
                                target_days: int = 60) -> Dict:
        """
        Generate a reorder recommendation for a single SKU.

        Args:
            sku: Seller SKU
            lead_time_days: Estimated lead time from supplier
            target_days: Target days of cover after reorder

        Returns:
            Dict with recommendation details
        """
        today = date.today()
        daily_rate = self._daily_sales_rate(sku, today - timedelta(days=30), today)
        if daily_rate <= 0:
            return {"sku": sku, "recommendation": "No sales data available", "reorder_qty": 0}

        # Current stock
        latest = self.db.query(InventorySnapshot).filter(
            InventorySnapshot.store_id == self.store_id,
            InventorySnapshot.sku == sku,
        ).order_by(InventorySnapshot.snapshot_date.desc()).first()

        current = (latest.sellable_units or 0) if latest else 0

        # Reorder quantity: (target_days - current_days) * daily_rate
        current_days = current / daily_rate if daily_rate > 0 else 0
        needed_days = max(0, target_days - current_days + lead_time_days)
        reorder_qty = int(needed_days * daily_rate)

        return {
            "store_id": self.store_id,
            "sku": sku,
            "current_stock": current,
            "daily_sales_rate": round(daily_rate, 1),
            "current_days_of_cover": round(current_days, 1),
            "lead_time_days": lead_time_days,
            "recommended_reorder_qty": max(reorder_qty, 0),
            "estimated_cost": None,  # filled by product cost lookup
            "confidence": self._confidence(sku),
        }

    def _get_latest_snapshots(self, as_of: date) -> List[InventorySnapshot]:
        """Get the most recent inventory snapshot per SKU."""
        subq = self.db.query(
            InventorySnapshot.sku,
            func.max(InventorySnapshot.snapshot_date).label("max_date"),
        ).filter(
            InventorySnapshot.store_id == self.store_id,
            InventorySnapshot.snapshot_date <= as_of,
        ).group_by(InventorySnapshot.sku).subquery()

        return self.db.query(InventorySnapshot).join(
            subq,
            (InventorySnapshot.sku == subq.c.sku) &
            (InventorySnapshot.snapshot_date == subq.c.max_date),
        ).all()

    def _daily_sales_rate(self, sku: str, start: date, end: date) -> float:
        """Calculate average daily sales from profit snapshots."""
        rows = self.db.query(
            func.sum(ProfitDailySnapshot.units_sold),
        ).filter(
            ProfitDailySnapshot.store_id == self.store_id,
            ProfitDailySnapshot.sku == sku,
            ProfitDailySnapshot.date.between(start, end),
        ).first()

        total_units = rows[0] or 0
        days = (end - start).days or 1
        return total_units / days

    def _confidence(self, sku: str) -> float:
        """Confidence based on data availability."""
        count = self.db.query(InventorySnapshot).filter(
            InventorySnapshot.store_id == self.store_id,
            InventorySnapshot.sku == sku,
        ).count()
        return min(0.95, 0.4 + count * 0.04)
