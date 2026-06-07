"""
Cost Management Engine
=======================
Manages COGS + sea freight costs per SKU in CNY.
Calculates total cost in USD for Profit Leakage Detection System.
"""

import logging
from decimal import Decimal

from sqlalchemy import func

from db.connection import get_session
from db.models import AsinCost

logger = logging.getLogger(__name__)


class CostEngine:
    """
    Cost management for profit calculations.
    All costs in CNY, converted to USD for PLDS.
    """

    # Default exchange rate: 1 CNY = 0.14 USD (~7.15 CNY/USD)
    DEFAULT_EXCHANGE_RATE = 0.14

    # Route to column mapping
    ROUTE_FIELDS = {
        "us_slow": "freight_us_slow_cny",
        "us_fast": "freight_us_fast_cny",
        "canada": "freight_canada_cny",
        "europe": "freight_europe_cny",
        "japan": "freight_japan_cny",
        "australia": "freight_australia_cny",
        "mexico": "freight_mexico_cny",
    }

    def __init__(self, store_id: str):
        self.store_id = store_id
        self._exchange_rate = None

    @property
    def exchange_rate(self) -> float:
        """Get current exchange rate (CNY to USD)."""
        # Could fetch from live API in future
        return self.DEFAULT_EXCHANGE_RATE

    # ─── CRUD Operations ───

    def set_costs(self, asin: str, sku: str = None, manufacturing_cny: float = None,
                  packaging_cny: float = None, inspection_cny: float = None,
                  freight_route: str = None, freight_cny: float = None,
                  exchange_rate: float = None) -> dict:
        """Set or update costs for a SKU."""
        session = get_session()
        try:
            cost = session.query(AsinCost).filter(
                AsinCost.store_id == self.store_id,
                AsinCost.asin == sku,
            ).first()

            if not cost:
                cost = SkuCost(store_id=self.store_id, sku=sku)
                session.add(cost)

            if manufacturing_cny is not None:
                cost.manufacturing_cost_cny = manufacturing_cny
            if packaging_cny is not None:
                cost.packaging_cost_cny = packaging_cny
            if inspection_cny is not None:
                cost.inspection_cost_cny = inspection_cny
            if exchange_rate is not None:
                cost.exchange_rate = exchange_rate
            if freight_route and freight_cny is not None:
                field = self.ROUTE_FIELDS.get(freight_route)
                if field:
                    setattr(cost, field, freight_cny)

            # Compute total cost in USD
            cost.total_cost_usd = self.compute_total_usd(cost)
            session.commit()

            return {
                "asin": asin,
                "cost_cny": {
                    "manufacturing": float(cost.manufacturing_cost_cny),
                    "packaging": float(cost.packaging_cost_cny),
                    "inspection": float(cost.inspection_cost_cny),
                },
                "exchange_rate": float(cost.exchange_rate),
                "total_cost_usd": float(cost.total_cost_usd),
            }
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to set costs: {e}")
            return {"error": str(e)}
        finally:
            session.close()

    def get_costs(self, asin: str) -> dict:
        """Get all costs for a SKU."""
        session = get_session()
        try:
            cost = session.query(AsinCost).filter(
                AsinCost.store_id == self.store_id,
                AsinCost.asin == sku,
            ).first()

            if not cost:
                return {"asin": asin, "cost_cny": {}, "total_cost_usd": 0, "configured": False}

            freight = {}
            for route, field in self.ROUTE_FIELDS.items():
                val = getattr(cost, field, 0)
                if val and float(val) > 0:
                    freight[route] = float(val)

            return {
                "asin": asin,
                "configured": True,
                "cost_cny": {
                    "manufacturing": float(cost.manufacturing_cost_cny),
                    "packaging": float(cost.packaging_cost_cny),
                    "inspection": float(cost.inspection_cost_cny),
                },
                "freight_cny": freight,
                "exchange_rate": float(cost.exchange_rate),
                "total_cost_usd": float(cost.total_cost_usd),
            }
        finally:
            session.close()

    def compute_total_usd(self, cost: SkuCost, route: str = "us_slow") -> float:
        """Compute total cost per unit in USD."""
        total_cny = (float(cost.manufacturing_cost_cny) +
                     float(cost.packaging_cost_cny) +
                     float(cost.inspection_cost_cny))

        # Add freight for the specified route
        freight_field = self.ROUTE_FIELDS.get(route, "freight_us_slow_cny")
        total_cny += float(getattr(cost, freight_field, 0))

        rate = float(cost.exchange_rate) or self.DEFAULT_EXCHANGE_RATE
        return round(total_cny * rate, 2)

    def get_true_cost(self, asin: str, route: str = "us_slow") -> float:
        """Get the true per-unit cost in USD including freight for a route."""
        session = get_session()
        try:
            cost = session.query(AsinCost).filter(
                AsinCost.store_id == self.store_id,
                AsinCost.asin == sku,
            ).first()

            if not cost:
                return 0
            return self.compute_total_usd(cost, route)
        finally:
            session.close()

    def get_profit_margin(self, asin: str, selling_price: float,
                           route: str = "us_slow") -> dict:
        """Calculate true profit margin for a SKU at a given selling price."""
        total_cost = self.get_true_cost(asin, route)
        if total_cost == 0 or selling_price == 0:
            return {"error": "Missing cost or price data"}

        gross_margin = selling_price - total_cost
        margin_pct = (gross_margin / selling_price) * 100

        return {
            "asin": asin,
            "selling_price_usd": selling_price,
            "total_cost_usd": total_cost,
            "gross_margin_usd": round(gross_margin, 2),
            "margin_pct": round(margin_pct, 2),
            "route": route,
        }
