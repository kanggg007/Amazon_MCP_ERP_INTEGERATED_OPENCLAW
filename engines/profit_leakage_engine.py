"""
Profit Leakage Detection Engine (PLDS)
========================================
Calculates TRUE SKU PROFIT by combining:
  - Orders revenue
  - COGS
  - Ad spend (from settlements & Ads API)
  - FBA fees (storage, fulfillment, disposal)
  - Returns & refund losses
  - Inbound discrepancies
  - Aged inventory costs

Classifies leakage into 5 categories:
  🟥 Ads Leakage   🟧 Return Leakage   🟨 FBA Leakage
  🟦 Pricing Leakage   🟪 Operational Leakage
"""

import logging
from datetime import datetime, timedelta, date
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, text, and_

from db.connection import get_session
from db.models import (
    ProfitDailySnapshot, Order, OrderItem,
    CustomerLossEvent, FinancialTransaction,
    AdsPerformance, InventorySnapshot,
    ShipmentDiscrepancy,
)
from auth.store_registry import StoreRegistry

logger = logging.getLogger(__name__)


class ProfitLeakageEngine:
    """
    Core computation: expected profit → true profit → leakage.
    """

    def __init__(self, store_id: str):
        self.store_id = store_id
        self.store = StoreRegistry.get(store_id)

    # ──────────────────────────────────────────────
    # 1. TRUE PROFIT CALCULATION
    # ──────────────────────────────────────────────

    def calculate_sku_profit(self, sku: str, days: int = 30) -> dict:
        """
        Full TRUE profit calculation for a single SKU.
        Returns a breakdown of all cost components.
        """
        session = get_session()
        try:
            since = date.today() - timedelta(days=days)

            # Revenue & volume
            revenue_data = self._get_revenue(session, sku, since)
            units_sold = revenue_data.get("units_sold", 0)

            # Costs
            cogs = self._get_cogs(session, sku, since, units_sold)
            ad_spend = self._get_ad_spend(session, sku, since)
            fba_fees = self._get_fba_fees(session, sku, since)
            storage_fees = self._get_storage_fees(session, sku, since)
            refund_loss = self._get_refund_loss(session, sku, since)
            return_loss = self._get_return_loss(session, sku, since)
            fba_leakage = self._get_fba_leakage(session, sku, since)
            disposal_fees = self._get_disposal_fees(session, sku, since)

            # Calculations
            total_revenue = revenue_data.get("revenue", 0)
            expected_profit = total_revenue - cogs

            total_costs = (
                cogs + ad_spend + fba_fees + storage_fees
                + refund_loss + return_loss + fba_leakage + disposal_fees
            )
            true_profit = total_revenue - total_costs
            leakage = expected_profit - true_profit
            leakage_pct = (leakage / expected_profit * 100) if expected_profit > 0 else 0

            # Breakdown
            cost_total = cogs + ad_spend + fba_fees + storage_fees + refund_loss + return_loss + fba_leakage + disposal_fees
            def share(val):
                return round((val / cost_total * 100), 1) if cost_total > 0 else 0

            return {
                "sku": sku,
                "period_days": days,
                "units_sold": units_sold,
                "revenue": round(total_revenue, 2),
                # Profit layers
                "expected_profit": round(expected_profit, 2),
                "true_profit": round(true_profit, 2),
                "leakage": round(leakage, 2),
                "leakage_pct": round(leakage_pct, 2),
                # Cost breakdown
                "cogs": {"amount": round(cogs, 2), "share_pct": share(cogs)},
                "ad_spend": {"amount": round(ad_spend, 2), "share_pct": share(ad_spend)},
                "fba_fees": {"amount": round(fba_fees, 2), "share_pct": share(fba_fees)},
                "storage_fees": {"amount": round(storage_fees, 2), "share_pct": share(storage_fees)},
                "refund_loss": {"amount": round(refund_loss, 2), "share_pct": share(refund_loss)},
                "return_loss": {"amount": round(return_loss, 2), "share_pct": share(return_loss)},
                "fba_leakage": {"amount": round(fba_leakage, 2), "share_pct": share(fba_leakage)},
                "disposal_fees": {"amount": round(disposal_fees, 2), "share_pct": share(disposal_fees)},
                # Leakage categories
                "leakage_breakdown": self._classify_leakage(
                    ad_spend=ad_spend,
                    refund_loss=refund_loss + return_loss,
                    fba_loss=fba_leakage + disposal_fees,
                    storage_fees=storage_fees,
                    total_leakage=leakage,
                ),
            }
        finally:
            session.close()

    def scan_all_skus(self, days: int = 30, min_loss: float = 100) -> list[dict]:
        """
        Scan all SKUs and find those with significant profit leakage.
        Returns SKUs ranked by leakage amount.
        """
        session = get_session()
        try:
            since = date.today() - timedelta(days=days)

            skus = session.query(
                CustomerLossEvent.sku,
                func.sum(CustomerLossEvent.amount_loss).label("total_loss"),
            ).filter(
                CustomerLossEvent.store_id == self.store_id,
                CustomerLossEvent.event_date >= since,
            ).group_by(CustomerLossEvent.sku).having(
                func.sum(CustomerLossEvent.amount_loss) > min_loss
            ).order_by(
                func.sum(CustomerLossEvent.amount_loss).desc()
            ).limit(50).all()

            results = []
            for sku_row in skus:
                sku_data = self.calculate_sku_profit(sku_row.sku, days)
                if sku_data and sku_data.get("leakage", 0) > 0:
                    results.append(sku_data)

            return results
        finally:
            session.close()

    # ──────────────────────────────────────────────
    # 2. LEAKAGE CLASSIFICATION
    # ──────────────────────────────────────────────

    def _classify_leakage(self, ad_spend, refund_loss, fba_loss, storage_fees, total_leakage):
        """Classify leakage into 5 categories with percentages."""
        if total_leakage == 0:
            return {}

        categories = [
            ("ads_waste", ad_spend, "🟥 High ad spend without conversion"),
            ("return_leakage", refund_loss, "🟧 Return & refund losses"),
            ("fba_leakage", fba_loss, "🟨 FBA lost inventory / unreimbursed"),
            ("operational_leakage", storage_fees, "🟪 Storage & aged inventory"),
        ]

        # Pricing leakage = residual (what's left unclassified)
        classified_sum = sum(amt for _, amt, _ in categories if amt > 0)
        pricing_leakage = max(0, total_leakage - classified_sum)
        if pricing_leakage > 0:
            categories.append(("pricing_leakage", pricing_leakage, "🟦 Low margin / discount erosion"))

        result = {}
        for name, amount, desc in categories:
            if amount > 0:
                result[name] = {
                    "amount": round(amount, 2),
                    "share_pct": round((amount / total_leakage) * 100, 1),
                    "description": desc,
                }
        return result

    # ──────────────────────────────────────────────
    # 3. DATA FETCHERS
    # ──────────────────────────────────────────────

    def _get_revenue(self, session, sku: str, since: date) -> dict:
        row = session.query(
            func.coalesce(func.sum(ProfitDailySnapshot.revenue), 0).label("revenue"),
            func.coalesce(func.sum(ProfitDailySnapshot.units_sold), 0).label("units"),
        ).filter(
            ProfitDailySnapshot.store_id == self.store_id,
            ProfitDailySnapshot.sku == sku,
            ProfitDailySnapshot.date >= since,
        ).first()
        return {"revenue": float(row.revenue), "units_sold": int(row.units)}

    def _get_cogs(self, session, sku: str, since: date, units_sold: int) -> float:
        # Try to get unit cost from products table
        from db.models import Product
        prod = session.query(Product.unit_cost).filter(
            Product.store_id == self.store_id,
            Product.sku == sku,
        ).first()
        cost_per_unit = float(prod.unit_cost) if prod and prod.unit_cost else 0
        return round(cost_per_unit * units_sold, 2)

    def _get_ad_spend(self, session, sku: str, since: date) -> float:
        row = session.query(
            func.coalesce(func.sum(AdsPerformance.spend), 0)
        ).filter(
            AdsPerformance.store_id == self.store_id,
            AdsPerformance.sku == sku,
            AdsPerformance.date >= since,
        ).scalar()
        return float(row) if row else 0

    def _get_fba_fees(self, session, sku: str, since: date) -> float:
        row = session.query(
            func.coalesce(func.sum(ProfitDailySnapshot.fba_fees), 0)
        ).filter(
            ProfitDailySnapshot.store_id == self.store_id,
            ProfitDailySnapshot.sku == sku,
            ProfitDailySnapshot.date >= since,
        ).scalar()
        return float(row) if row else 0

    def _get_storage_fees(self, session, sku: str, since: date) -> float:
        row = session.query(
            func.coalesce(func.sum(ProfitDailySnapshot.storage_fees), 0)
        ).filter(
            ProfitDailySnapshot.store_id == self.store_id,
            ProfitDailySnapshot.sku == sku,
            ProfitDailySnapshot.date >= since,
        ).scalar()
        return float(row) if row else 0

    def _get_refund_loss(self, session, sku: str, since: date) -> float:
        row = session.query(
            func.coalesce(func.sum(ProfitDailySnapshot.refund_amount), 0)
        ).filter(
            ProfitDailySnapshot.store_id == self.store_id,
            ProfitDailySnapshot.sku == sku,
            ProfitDailySnapshot.date >= since,
        ).scalar()
        return float(row) if row else 0

    def _get_return_loss(self, session, sku: str, since: date) -> float:
        row = session.query(
            func.coalesce(func.sum(CustomerLossEvent.amount_loss), 0)
        ).filter(
            CustomerLossEvent.store_id == self.store_id,
            CustomerLossEvent.sku == sku,
            CustomerLossEvent.event_date >= since,
            CustomerLossEvent.loss_type.in_(["return_damage", "customer_return"]),
        ).scalar()
        return float(row) if row else 0

    def _get_fba_leakage(self, session, sku: str, since: date) -> float:
        row = session.query(
            func.coalesce(func.sum(CustomerLossEvent.amount_loss), 0)
        ).filter(
            CustomerLossEvent.store_id == self.store_id,
            CustomerLossEvent.sku == sku,
            CustomerLossEvent.event_date >= since,
            CustomerLossEvent.loss_type.in_(["lost_in_fba", "reimbursement"]),
        ).scalar()
        return float(row) if row else 0

    def _get_disposal_fees(self, session, sku: str, since: date) -> float:
        row = session.query(
            func.coalesce(func.sum(CustomerLossEvent.amount_loss), 0)
        ).filter(
            CustomerLossEvent.store_id == self.store_id,
            CustomerLossEvent.sku == sku,
            CustomerLossEvent.event_date >= since,
            CustomerLossEvent.loss_type == "disposal",
        ).scalar()
        return float(row) if row else 0

    # ──────────────────────────────────────────────
    # 4. PATTERN DETECTION (AI Layer)
    # ──────────────────────────────────────────────

    def detect_patterns(self, sku: str, days: int = 90) -> list[dict]:
        """
        Detect patterns like:
        - High returns correlate with "damaged on arrival"
        - Specific keyword driving wasted ad spend
        - Seasonal storage fee spikes
        """
        session = get_session()
        patterns = []

        try:
            since = date.today() - timedelta(days=days)

            # Pattern 1: Return reason correlation
            reasons = session.query(
                CustomerLossEvent.reason,
                func.count().label("count"),
                func.sum(CustomerLossEvent.amount_loss).label("total"),
            ).filter(
                CustomerLossEvent.store_id == self.store_id,
                CustomerLossEvent.sku == sku,
                CustomerLossEvent.event_date >= since,
            ).group_by(
                CustomerLossEvent.reason
            ).order_by(
                func.sum(CustomerLossEvent.amount_loss).desc()
            ).limit(5).all()

            if reasons:
                top = reasons[0]
                if top.count >= 3:
                    pct = (float(top.total) / float(sum(r.total for r in reasons))) * 100 if reasons else 0
                    patterns.append({
                        "pattern": "return_reason_cluster",
                        "confidence": min(round(pct / 100, 2), 0.95),
                        "insight": f"Top return reason: '{top.reason}' ({top.count}x, ${float(top.total):.0f})",
                        "root_cause_hint": "Packaging weakness" if "damage" in (top.reason or "").lower() else "Check product quality",
                        "suggested_action": "Improve packaging / add protective inserts",
                    })

            # Pattern 2: Ads waste (high ACOS keywords)
            ad_rows = session.query(
                AdsPerformance.keyword,
                func.sum(AdsPerformance.spend).label("total_spend"),
                func.sum(AdsPerformance.sales).label("total_sales"),
            ).filter(
                AdsPerformance.store_id == self.store_id,
                AdsPerformance.sku == sku,
                AdsPerformance.date >= since,
            ).group_by(
                AdsPerformance.keyword
            ).having(
                func.sum(AdsPerformance.spend) > 20
            ).order_by(
                func.sum(AdsPerformance.spend).desc()
            ).limit(10).all()

            for ar in ad_rows:
                if ar.keyword and float(ar.total_sales or 0) < float(ar.total_spend) * 0.5:
                    waste = float(ar.total_spend) - float(ar.total_sales or 0)
                    patterns.append({
                        "pattern": "wasted_ad_keyword",
                        "keyword": ar.keyword,
                        "confidence": 0.85,
                        "insight": f"Keyword '{ar.keyword}' spent ${float(ar.total_spend):.0f} → only ${float(ar.total_sales or 0):.0f} sales (${waste:.0f} waste)",
                        "suggested_action": "Pause keyword or reduce bid",
                    })

            # Pattern 3: FBA reimbursement gap
            discrepancy = session.query(
                func.coalesce(func.sum(ShipmentDiscrepancy.estimated_loss), 0)
            ).filter(
                ShipmentDiscrepancy.store_id == self.store_id,
                ShipmentDiscrepancy.sku == sku,
                ShipmentDiscrepancy.claim_filed == False,
            ).scalar()

            if discrepancy and float(discrepancy) > 100:
                patterns.append({
                    "pattern": "unclaimed_fba_reimbursement",
                    "confidence": 0.72,
                    "insight": f"${float(discrepancy):.0f} in unclaimed FBA reimbursements detected",
                    "suggested_action": "File reimbursement claims for {len(unclaimed)} discrepancies",
                })

            return patterns
        finally:
            session.close()

    # ──────────────────────────────────────────────
    # 5. STORE DAILY FINANCIAL SNAPSHOT
    # ──────────────────────────────────────────────

    def refresh_daily_financials(self, target_date: date = None) -> int:
        """
        Refresh the profit_daily_snapshot table for a specific date.
        Combines orders + fees + ads + refunds into one row per SKU per day.
        """
        session = get_session()
        target_date = target_date or date.today() - timedelta(days=1)
        count = 0

        try:
            # Get all SKUs sold on that day
            sku_rows = session.query(
                OrderItem.seller_sku,
            ).join(
                Order, Order.id == OrderItem.order_id,
            ).filter(
                Order.store_id == self.store_id,
                func.date(Order.purchase_date) == target_date,
                OrderItem.seller_sku.isnot(None),
            ).distinct().all()

            skus = [r.seller_sku for r in sku_rows]

            for sku in skus:
                # Get day's orders for this SKU
                day_data = session.query(
                    func.coalesce(func.sum(OrderItem.quantity_ordered), 0).label("qty"),
                    func.coalesce(func.sum(OrderItem.item_price_amount), 0).label("rev"),
                ).join(
                    Order, Order.id == OrderItem.order_id,
                ).filter(
                    Order.store_id == self.store_id,
                    OrderItem.seller_sku == sku,
                    func.date(Order.purchase_date) == target_date,
                ).first()

                units = int(day_data.qty)
                revenue = float(day_data.rev)

                # Get costs
                # (fetchers above use date range — adapt for single day)
                fba_fees = self._get_fba_fees_single(session, sku, target_date)
                ad_spend = self._get_ad_spend_single(session, sku, target_date)
                refunds = self._get_refund_single(session, sku, target_date)

                snapshot = ProfitDailySnapshot(
                    store_id=self.store_id,
                    sku=sku,
                    date=target_date,
                    units_sold=units,
                    revenue=revenue,
                    refund_amount=refunds,
                    fba_fees=fba_fees,
                    advertising_spend=ad_spend,
                )
                session.add(snapshot)
                count += 1

            session.commit()
            logger.info(f"Refreshed {count} daily financials for {target_date}")
            return count
        except Exception as e:
            session.rollback()
            logger.error(f"Daily refresh failed: {e}")
            return 0
        finally:
            session.close()

    def _get_fba_fees_single(self, session, sku: str, day: date) -> float:
        row = session.query(
            func.coalesce(func.sum(FinancialTransaction.amount), 0)
        ).filter(
            FinancialTransaction.store_id == self.store_id,
            FinancialTransaction.sku == sku,
            FinancialTransaction.transaction_type == "fee",
            func.date(FinancialTransaction.posted_date) == day,
        ).scalar()
        return abs(float(row)) if row else 0

    def _get_ad_spend_single(self, session, sku: str, day: date) -> float:
        row = session.query(
            func.coalesce(func.sum(FinancialTransaction.amount), 0)
        ).filter(
            FinancialTransaction.store_id == self.store_id,
            FinancialTransaction.sku == sku,
            FinancialTransaction.transaction_type == "charge",
            func.date(FinancialTransaction.posted_date) == day,
        ).scalar()
        return abs(float(row)) if row else 0

    def _get_refund_single(self, session, sku: str, day: date) -> float:
        row = session.query(
            func.coalesce(func.sum(Refund.refund_amount), 0)
        ).filter(
            Refund.store_id == self.store_id,
            Refund.sku == sku,
            func.date(Refund.refund_date) == day,
        ).scalar()
        return float(row) if row else 0
