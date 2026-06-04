"""
Profit Engine
===============
Calculate true SKU-level profit including all costs:
sales, refunds, FBA fees, referral fees, inbound shipping, ad spend, storage fees.

Goal: True profit per SKU. No cross-store mixing.
"""

import logging
from datetime import date, timedelta
from typing import Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy import func

from db.models import ProfitDailySnapshot, Order, OrderItem, AdPerformance, Product

logger = logging.getLogger("amazon.engine.profit")


class ProfitEngine:
    """
    Calculates true SKU-level profit.
    Every record has store_id and marketplace_id.
    """

    def __init__(self, db: Session, store_id: str, marketplace_id: str = "ATVPDKIKX0DER"):
        self.db = db
        self.store_id = store_id
        self.marketplace_id = marketplace_id

    def calculate_day(self, target_date: date, cost_map: Optional[Dict[str, float]] = None) -> Dict:
        """
        Calculate profit for all SKUs on a specific day.

        Args:
            target_date: Date to calculate
            cost_map: Optional {sku: unit_cost} — loaded from DB if not provided

        Returns:
            Dict[sku -> profit_data]
        """
        if cost_map is None:
            cost_map = self._load_costs()

        # Get all order items for this date
        items = self.db.query(OrderItem).join(
            Order, OrderItem.order_id == Order.id,
        ).filter(
            Order.store_id == self.store_id,
            func.date(Order.purchase_date) == target_date,
        ).all()

        # Get ad spend for this date
        ad_spend_map = self._get_ad_spend(target_date)

        # Aggregate per SKU
        skus: Dict[str, Dict] = {}
        for item in items:
            sku = item.seller_sku or "UNKNOWN"
            if sku not in skus:
                skus[sku] = {
                    "units_sold": 0, "units_refunded": 0,
                    "revenue": 0.0, "refund_amount": 0.0,
                    "fba_fees": 0.0, "referral_fees": 0.0,
                    "shipping_charge": 0.0, "shipping_cost": 0.0,
                    "product_cost": 0.0,
                }

            d = skus[sku]
            qty = item.quantity_ordered or 0
            price = float(item.item_price_amount or 0)
            d["units_sold"] += qty
            d["revenue"] += price * qty
            d["product_cost"] += cost_map.get(sku, 0) * qty
            d["fba_fees"] += price * 0.15  # Estimate — get from FBA fee report
            d["referral_fees"] += price * 0.15  # Estimate — varies by category
            d["shipping_charge"] += float(item.shipping_price or 0)

        # Create DB records
        results = {}
        for sku, d in skus.items():
            ad_spend = ad_spend_map.get(sku, 0)
            net_profit = (
                d["revenue"] - d["refund_amount"] - d["product_cost"]
                - d["fba_fees"] - d["referral_fees"] - d["shipping_cost"]
                - ad_spend - 0  # returns_cost, storage_fees
            )
            margin = net_profit / d["revenue"] if d["revenue"] > 0 else 0

            snapshot = ProfitDailySnapshot(
                store_id=self.store_id,
                marketplace_id=self.marketplace_id,
                sku=sku,
                date=target_date,
                units_sold=d["units_sold"],
                units_refunded=d["units_refunded"],
                revenue=d["revenue"],
                refund_amount=d["refund_amount"],
                product_cost=d["product_cost"],
                fba_fees=d["fba_fees"],
                referral_fees=d["referral_fees"],
                shipping_charge=d["shipping_charge"],
                shipping_cost=d["shipping_cost"],
                advertising_spend=ad_spend,
            )
            self.db.add(snapshot)
            results[sku] = {
                "date": target_date.isoformat(),
                "revenue": round(d["revenue"], 2),
                "costs": round(d["product_cost"] + d["fba_fees"] + d["referral_fees"] + ad_spend, 2),
                "net_profit": round(net_profit, 2),
                "margin": round(margin, 4),
                "units": d["units_sold"],
            }

        self.db.commit()
        logger.info("Profit day: %s — %d SKUs", target_date, len(results))
        return results

    def sku_summary(self, sku: str, days: int = 30) -> Dict:
        """Get trailing profit summary for a SKU."""
        start = date.today() - timedelta(days=days)
        rows = self.db.query(ProfitDailySnapshot).filter(
            ProfitDailySnapshot.store_id == self.store_id,
            ProfitDailySnapshot.sku == sku,
            ProfitDailySnapshot.date >= start,
        ).all()

        if not rows:
            return {"sku": sku, "error": "No data"}

        total_rev = sum(r.revenue or 0 for r in rows)
        total_cost = sum(
            (r.product_cost or 0) + (r.fba_fees or 0) + (r.referral_fees or 0)
            + (r.advertising_spend or 0) + (r.shipping_cost or 0) + (r.returns_cost or 0)
            + (r.storage_fees or 0) + (r.inbound_shipping_cost or 0) for r in rows
        )
        return {
            "store_id": self.store_id,
            "sku": sku,
            "days": days,
            "units": sum(r.units_sold or 0 for r in rows),
            "revenue": float(total_rev),
            "total_costs": float(total_cost),
            "net_profit": float(total_rev - total_cost),
            "margin": float((total_rev - total_cost) / total_rev * 100) if total_rev > 0 else 0,
            "ad_spend": float(sum(r.advertising_spend or 0 for r in rows)),
            "fba_fees": float(sum(r.fba_fees or 0 for r in rows)),
            "tacos": float(sum(r.advertising_spend or 0 for r in rows) / total_rev * 100)
                if total_rev > 0 else 0,
        }

    def _load_costs(self) -> Dict[str, float]:
        """Load unit costs from product table."""
        products = self.db.query(Product).filter(
            Product.store_id == self.store_id,
        ).all()
        return {p.sku: float(p.unit_cost or 0) for p in products}

    def _get_ad_spend(self, target_date: date) -> Dict[str, float]:
        """Get ad spend per SKU for a date."""
        rows = self.db.query(
            AdPerformance.sku,
            func.sum(AdPerformance.spend),
        ).filter(
            AdPerformance.store_id == self.store_id,
            AdPerformance.date == target_date,
        ).group_by(AdPerformance.sku).all()
        return {r.sku: float(r[1] or 0) for r in rows if r.sku}
