"""
Inventory Autopilot Engine
============================
Combines supply timeline + demand forecast + seasonality into 
a single decision engine that answers:

  "How many days until I lose sales — and what should I do today?"

3-Stage System: 🟢 SAFE → 🟡 WARNING → 🔴 CRITICAL

True Demand = Base Demand × Seasonality × Events × Ads × Promo
"""

import logging
from datetime import date, timedelta, datetime
from typing import Optional
from decimal import Decimal

from sqlalchemy import func, and_, desc
from sqlalchemy.orm import Session

from db.connection import get_session
from db.models import (
    InventoryForecastDaily, DemandForecastAdjusted,
    InventoryRiskForecast, SeasonalityIndex,
    InventorySnapshot, ProfitDailySnapshot,
)
from auth.store_registry import StoreRegistry

logger = logging.getLogger(__name__)


class InventoryAutopilotEngine:
    """
    Supply chain intelligence, not inventory tracking.
    """

    # Default lead time: manufacturing + logistics + customs + buffer
    DEFAULT_LEAD_TIME_DAYS = 60
    SAFETY_BUFFER_DAYS = 15

    # Common China → World sea freight routes (days)
    # Shenzhen → Destination sea freight (slow channel, port → FBA warehouse)
    SHIPPING_ROUTES = {
        # Slow channel (sea freight, consolidated)
        ("shenzhen", "us_slow"):       45,   # Shenzhen → US (slow consolidated sea)
        ("shenzhen", "us_fast"):       25,   # Shenzhen → US (fast channel / air)
        ("shenzhen", "canada"):        50,   # Shenzhen → Canada FBA
        ("shenzhen", "europe"):        60,   # Shenzhen → EU FBA (DE/UK/FR)
        ("shenzhen", "japan"):         25,   # Shenzhen → Japan FBA
        ("shenzhen", "australia"):     60,   # Shenzhen → Australia FBA
        ("shenzhen", "mexico"):        40,   # Shenzhen → Mexico FBA
        # Other China ports
        ("shanghai", "us"):            42,   # Shanghai → US
        ("shanghai", "japan"):         22,   # Shanghai → Japan
        ("ningbo", "us"):              42,   # Ningbo → US
        ("ningbo", "europe"):          55,   # Ningbo → Europe
        ("yantian", "us"):             43,   # Yantian → US
        ("guangzhou", "us"):           44,   # Guangzhou → US
    }

    def get_route_days(self, origin: str = None, dest: str = None, fast_channel: bool = False) -> int:
        """Estimate logistics days based on origin/destination and channel speed.
        
        Args:
            fast_channel: True = fast channel (25 days US only), False = slow channel
        """
        if origin and dest:
            ol = origin.lower()
            dl = dest.lower()
            # If fast channel requested for US
            if fast_channel and ("us" in dl or "united states" in dl or "america" in dl):
                for (o, d), days in self.SHIPPING_ROUTES.items():
                    if o in ol and "fast" in d:
                        return days
                return 25  # default fast
            # Slow channel or non-US
            for (o, d), days in self.SHIPPING_ROUTES.items():
                if o in ol and "slow" not in d and "fast" not in d:
                    if d.split("_")[0] in dl:
                        return days
            # Match generic country names
            for (o, d), days in self.SHIPPING_ROUTES.items():
                if o in ol:
                    keyword = d.split("_")[0]
                    if keyword in dl:
                        return days
            # Fallback: origin match
            for (o, _), days in self.SHIPPING_ROUTES.items():
                if o in ol:
                    return days
        return 45  # default: Shenzhen → US (slow)

    def get_lead_time(self, fast_channel: bool = False) -> dict:
        """Get per-SKU lead time breakdown.
        Returns dict with factory, logistics, route info.
        Falls back to defaults if not configured.
        """
        session = get_session()
        try:
            from db.models import SupplierLeadTime
            slt = session.query(SupplierLeadTime).filter(
                SupplierLeadTime.store_id == self.store_id,
                SupplierLeadTime.sku == self.sku,
                SupplierLeadTime.is_active == True,
                SupplierLeadTime.shipping_method == "sea",
            ).first()
            if slt:
                return {
                    "factory_days": slt.factory_lead_days,
                    "logistics_days": slt.logistics_days,
                    "customs_days": slt.customs_buffer_days,
                    "total": slt.factory_lead_days + slt.logistics_days + slt.customs_buffer_days,
                    "origin": slt.origin_port or "China",
                    "destination": slt.destination_port or "Unknown",
                    "carrier": slt.carrier or "Unknown",
                    "shipping_method": slt.shipping_method,
                }
            # Estimate from route
            route_days = self.get_route_days("shenzhen", "us", fast_channel=fast_channel)
            return {
                "factory_days": 30,
                "logistics_days": route_days,
                "customs_days": 5,
                "total": 30 + route_days + 5,
                "origin": "Shenzhen, China",
                "destination": "US West Coast",
                "note": "Estimated from defaults — configure supplier_lead_times for accuracy",
            }
        except Exception as e:
            return {
                "factory_days": 30,
                "logistics_days": 25,
                "customs_days": 5,
                "total": 60,
                "note": "Using defaults",
            }
        finally:
            session.close()

    # Peak season months (Oct-Dec)
    PEAK_MONTHS = {10, 11, 12}
    # Pre-peak months (Aug-Sep)
    PRE_PEAK_MONTHS = {8, 9}

    def __init__(self, store_id: str, sku: str = None):
        self.store_id = store_id
        self.sku = sku

    # ─── 1. DEMAND FORECASTING ───

    def calculate_base_demand(self, days: int = 30) -> float:
        """Calculate base daily demand from recent sales history."""
        session = get_session()
        try:
            since = date.today() - timedelta(days=days)
            total_units = session.query(
                func.coalesce(func.sum(ProfitDailySnapshot.units_sold), 0)
            ).filter(
                ProfitDailySnapshot.store_id == self.store_id,
                ProfitDailySnapshot.sku == self.sku,
                ProfitDailySnapshot.date >= since,
            ).scalar() or 0
            return round(total_units / max(days, 1), 4)
        finally:
            session.close()

    def get_seasonality_multiplier(self, month: int = None) -> float:
        """Get seasonality multiplier for current or specified month."""
        month = month or date.today().month
        session = get_session()
        try:
            si = session.query(SeasonalityIndex).filter(
                SeasonalityIndex.store_id == self.store_id,
                SeasonalityIndex.sku == self.sku,
                SeasonalityIndex.month == month,
            ).first()
            if si:
                return float(si.seasonality_multiplier)
        finally:
            session.close()

        # Default multipliers by month if no data
        multipliers = {
            1: 0.8, 2: 0.8, 3: 0.9, 4: 1.0, 5: 1.0,
            6: 1.0, 7: 1.1, 8: 1.2, 9: 1.3,
            10: 1.8, 11: 2.8, 12: 3.2,  # Peak season
        }
        return multipliers.get(month, 1.0)

    def get_event_multiplier(self) -> float:
        """Check if any event is active (Prime Day, Black Friday, etc.)."""
        today = date.today()
        # Black Friday: ~Nov 25-30
        if today.month == 11 and today.day >= 25:
            return 3.0
        # Cyber Monday: ~Dec 1-3
        if today.month == 12 and today.day <= 3:
            return 2.5
        # Prime Day (typically July): simplified check
        if today.month == 7 and 10 <= today.day <= 17:
            return 2.5
        return 1.0

    def forecast_demand(self, days_ahead: int = 90, 
                        ads_multiplier: float = 1.0,
                        promo_multiplier: float = 1.0) -> list[dict]:
        """
        Forecast demand for N days ahead using all multipliers.
        True Demand = Base × Seasonality × Events × Ads × Promo
        """
        base = self.calculate_base_demand()
        forecast = []
        today = date.today()

        for d in range(days_ahead):
            f_date = today + timedelta(days=d)
            seasonality = self.get_seasonality_multiplier(f_date.month)
            event = self.get_event_multiplier()

            final_demand = base * seasonality * event * ads_multiplier * promo_multiplier

            forecast.append({
                "date": f_date.isoformat(),
                "base_demand": round(base, 2),
                "seasonality": round(seasonality, 2),
                "event_mult": round(event, 2),
                "ads_mult": round(ads_multiplier, 2),
                "promo_mult": round(promo_multiplier, 2),
                "final_demand": round(final_demand, 2),
            })

        return forecast

    # ─── 2. INVENTORY RISK ANALYSIS ───

    def get_current_inventory(self) -> int:
        """Get current on-hand inventory for the SKU."""
        session = get_session()
        try:
            inv = session.query(InventorySnapshot).filter(
                InventorySnapshot.store_id == self.store_id,
                InventorySnapshot.sku == self.sku,
            ).order_by(desc(InventorySnapshot.snapshot_date)).first()
            return inv.sellable_units if inv else 0
        finally:
            session.close()

    def analyze_risk(self, on_hand: int = None, lead_time_days: int = None,
                     ads_mult: float = 1.0, promo_mult: float = 1.0,
                     fast_channel: bool = False) -> dict:
        """
        Core risk analysis.
        Days of Cover vs Lead Time → 3-stage risk system.
        """
        on_hand = on_hand or self.get_current_inventory()
        lt_info = self.get_lead_time(fast_channel=fast_channel)
        if isinstance(lt_info, dict):
            lead_time_days = lead_time_days or lt_info.get("total", self.DEFAULT_LEAD_TIME_DAYS)
        else:
            lead_time_days = lead_time_days or self.DEFAULT_LEAD_TIME_DAYS
        base_demand = self.calculate_base_demand()
        current_month = date.today().month
        seasonality = self.get_seasonality_multiplier(current_month)
        event = self.get_event_multiplier()

        # Current adjusted demand
        current_demand = base_demand * seasonality * event * ads_mult * promo_mult
        days_cover = on_hand / max(current_demand, 0.01)

        # Peak demand (worst case: peak season + event + ads)
        peak_seasonality = max(self.get_seasonality_multiplier(m) for m in self.PEAK_MONTHS)
        peak_demand = base_demand * peak_seasonality * 1.5  # event buffer
        days_cover_peak = on_hand / max(peak_demand, 0.01)

        # 3-stage risk system
        total_lead = lead_time_days + self.SAFETY_BUFFER_DAYS

        if days_cover > total_lead * 1.5:
            status = "safe"
            risk = "low"
        elif days_cover > lead_time_days:
            status = "warning"
            risk = "medium"
        else:
            status = "critical"
            risk = "high"

        if days_cover_peak < lead_time_days:
            risk = "critical"

        # Stockout prediction
        stockout_normal = date.today() + timedelta(days=int(days_cover)) if days_cover > 0 else date.today()
        stockout_peak = date.today() + timedelta(days=int(days_cover_peak)) if days_cover_peak > 0 else date.today()

        # PO recommendation
        recommended_po = 0
        order_date = None
        if status in ("warning", "critical"):
            # Reorder quantity = (lead_time + buffer) × demand - on_hand
            needed_days = lead_time_days + self.SAFETY_BUFFER_DAYS
            needed_units = int(needed_days * current_demand)
            recommended_po = max(0, needed_units - on_hand)
            order_date = date.today()

        return {
            "sku": self.sku,
            "on_hand": on_hand,
            "daily_demand": round(current_demand, 2),
            "base_demand": round(base_demand, 2),
            "seasonality": round(seasonality, 2),
            "event_multiplier": round(event, 2),
            "lead_time_days": lead_time_days,
            "safety_buffer": self.SAFETY_BUFFER_DAYS,
            "days_of_cover": round(days_cover, 1),
            "days_of_cover_peak": round(days_cover_peak, 1),
            "reorder_status": status,
            "risk_level": risk,
            "estimated_stockout_date": stockout_normal.isoformat(),
            "estimated_stockout_peak": stockout_peak.isoformat(),
            "recommended_po_qty": recommended_po,
            "recommended_order_date": order_date.isoformat() if order_date else None,
            "shipping_channel": "fast" if fast_channel else "slow",
            "peak_season_mode": current_month in self.PEAK_MONTHS,
            "pre_peak_mode": current_month in self.PRE_PEAK_MONTHS,
        }

    # ─── 3. MULTI-SCENARIO SIMULATION ───

    def simulate_scenarios(self, on_hand: int = None) -> list[dict]:
        """Simulate multiple scenarios: base, ads scaling, promo, stockout recovery."""
        scenarios = [
            {"name": "Base Case", "ads_mult": 1.0, "promo_mult": 1.0},
            {"name": "Ads Scaling (+30%)", "ads_mult": 1.3, "promo_mult": 1.0},
            {"name": "Promotion Active (+50%)", "ads_mult": 1.0, "promo_mult": 1.5},
            {"name": "Ads + Promo Combined", "ads_mult": 1.3, "promo_mult": 1.5},
        ]
        results = []
        for scenario in scenarios:
            risk = self.analyze_risk(on_hand=on_hand, ads_mult=scenario["ads_mult"],
                                     promo_mult=scenario["promo_mult"])
            results.append({
                "scenario": scenario["name"],
                "days_of_cover": risk["days_of_cover"],
                "status": risk["reorder_status"],
                "stockout_date": risk["estimated_stockout_date"],
                "recommended_po": risk["recommended_po_qty"],
            })
        return results

    # ─── 4. SEASONALITY MANAGEMENT ───

    def update_seasonality(self, monthly_sales: dict[int, float]) -> int:
        """Update seasonality index from historical sales data."""
        session = get_session()
        count = 0
        try:
            total = sum(monthly_sales.values())
            avg = total / max(len(monthly_sales), 1)

            for month, sales in monthly_sales.items():
                multiplier = round(sales / max(avg, 0.01), 4)
                si = SeasonalityIndex(
                    store_id=self.store_id,
                    sku=self.sku,
                    month=month,
                    seasonality_multiplier=multiplier,
                    historical_sales_index=round(sales / max(total, 1), 4),
                    confidence_score=0.7,
                )
                session.add(si)
                count += 1
            session.commit()
            return count
        except Exception as e:
            session.rollback()
            logger.error(f"Seasonality update failed: {e}")
            return 0
        finally:
            session.close()

    # ─── 5. FULL SCAN: All SKUs ───

    def scan_all_skus(self) -> list[dict]:
        """Run risk analysis on all active SKUs."""
        session = get_session()
        try:
            # Get unique SKUs from inventory or recent sales
            sku_rows = session.query(InventorySnapshot.sku).filter(
                InventorySnapshot.store_id == self.store_id,
            ).distinct().limit(50).all()

            skus = [r.sku for r in sku_rows]
            results = []
            for sku in skus:
                self.sku = sku
                risk = self.analyze_risk()
                if risk["risk_level"] in ("high", "critical"):
                    results.append(risk)

            return sorted(results, key=lambda x: x.get("days_of_cover", 0))
        finally:
            session.close()

    # ─── 6. DASHBOARD ───

    def get_dashboard(self) -> dict:
        """Inventory autopilot dashboard."""
        session = get_session()
        try:
            critical = session.query(func.count(InventoryRiskForecast.id)).filter(
                InventoryRiskForecast.store_id == self.store_id,
                InventoryRiskForecast.stockout_risk_normal.in_(["high", "critical"]),
            ).scalar() or 0

            warning = session.query(func.count(InventoryRiskForecast.id)).filter(
                InventoryRiskForecast.store_id == self.store_id,
                InventoryRiskForecast.stockout_risk_normal == "medium",
            ).scalar() or 0

            safe = session.query(func.count(InventoryRiskForecast.id)).filter(
                InventoryRiskForecast.store_id == self.store_id,
                InventoryRiskForecast.stockout_risk_normal == "low",
            ).scalar() or 0

            total_skus = critical + warning + safe

            return {
                "store_id": self.store_id,
                "total_skus": total_skus,
                "critical": critical,
                "warning": warning,
                "safe": safe,
                "action_required": critical > 0,
                "season": "PEAK" if date.today().month in self.PEAK_MONTHS else "PRE_PEAK" if date.today().month in self.PRE_PEAK_MONTHS else "NORMAL",
            }
        finally:
            session.close()
