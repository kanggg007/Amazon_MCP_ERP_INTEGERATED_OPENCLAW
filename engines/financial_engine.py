"""
Financial Engine
==================
Auto-generates monthly P&L (Profit & Loss) and Cash Flow statements
per store. All data sourced from SP-API and manual cost entries.

P&L = accrual-based (revenue when earned, costs when incurred)
Cash Flow = cash-based (revenue when received, costs when paid)
"""

import logging
from datetime import date, timedelta, datetime
from typing import Dict, List, Optional, Tuple
from decimal import Decimal

from sqlalchemy.orm import Session
from sqlalchemy import func, extract

from db.models import (
    ProfitDailySnapshot, Order, OrderItem, Product,
    AdPerformance, InboundShipment, ShipmentDiscrepancy,
)
from engines.expense_manager import ExpenseManager
from engines.po_manager import PurchaseOrderManager

logger = logging.getLogger("amazon.engine.financial")


class FinancialStatement:
    """Container for a single financial statement."""

    def __init__(self, store_id: str, statement_type: str, year: int, month: int):
        self.store_id = store_id
        self.type = statement_type  # "pnl" or "cash_flow"
        self.year = year
        self.month = month
        self.lines: Dict[str, float] = {}
        self.metadata: Dict = {}

    def add_line(self, category: str, label: str, amount: float):
        self.lines[f"{category}___{label}"] = amount

    def to_dict(self) -> Dict:
        return {
            "store_id": self.store_id,
            "type": self.type,
            "period": f"{self.year}-{self.month:02d}",
            "lines": self.lines,
            "totals": {
                "revenue": self.lines.get("revenue___total", 0),
                "costs": self.lines.get("costs___total", 0),
                "expenses": self.lines.get("expenses___total", 0),
                "net": self.lines.get("net___profit", 0),
            },
            "metadata": self.metadata,
        }


class FinancialEngine:
    """
    Generates monthly P&L and Cash Flow statements using SP-API data.
    Every statement is store-isolated.
    """

    def __init__(self, db: Session, store_id: str, marketplace_id: str = "ATVPDKIKX0DER"):
        self.db = db
        self.store_id = store_id
        self.marketplace_id = marketplace_id

    # ─── P&L Statement ──────────────────────────────────────────

    def generate_pnl(self, year: int, month: int,
                        cash_based: bool = True) -> FinancialStatement:
        """
        Generate a monthly P&L statement.

        Args:
            year: Calendar year
            month: Calendar month
            cash_based: If True, uses actual cash movements (excl AR/AP).
                        If False, uses accrual accounting.

        Returns:
            FinancialStatement with P&L lines
        """
        if cash_based:
            return self._generate_cash_pnl(year, month)
        return self.generate_accrual_pnl(year, month)

    def _generate_cash_pnl(self, year: int, month: int) -> FinancialStatement:
        """
        Cash-based P&L — excludes AR/AP.
        Uses actual PO payments for COGS and actual settlements for revenue.
        """
        stmt = FinancialStatement(self.store_id, "pnl_cash", year, month)
        period_start = date(year, month, 1)
        if month == 12:
            period_end = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            period_end = date(year, month + 1, 1) - timedelta(days=1)

        # ── Revenue (actual settlements received) ──
        revenue = self._get_revenue(period_start, period_end) * 0.85
        prior_unsettled = self._get_prior_month_revenue(year, month) * 0.15
        total_revenue = revenue + prior_unsettled
        stmt.add_line("revenue", "amazon_settlements", revenue)
        stmt.add_line("revenue", "prior_month_settlements", prior_unsettled)
        stmt.add_line("revenue", "total", total_revenue)

        # ── COGS (actual PO payments) ──
        po_mgr = PurchaseOrderManager(self.db, self.store_id)
        monthly_cogs = po_mgr.get_monthly_cogs(year, month)
        cogs_paid = monthly_cogs["cogs_paid"]
        logistics_paid = monthly_cogs["logistics_paid"]
        stmt.add_line("cogs", "po_payments_made", cogs_paid)
        stmt.add_line("cogs", "logistics_paid", logistics_paid)
        stmt.add_line("cogs", "total", cogs_paid + logistics_paid)

        gross_profit = total_revenue - cogs_paid - logistics_paid
        stmt.add_line("gross", "gross_profit", gross_profit)
        stmt.add_line("gross", "margin_pct", round(gross_profit / total_revenue * 100, 2) if total_revenue > 0 else 0)

        # ── Amazon fees (deducted from settlements) ──
        fba = self._get_fba_fees(period_start, period_end)
        ref = self._get_referral_fees(period_start, period_end)
        storage = self._get_storage_fees(period_start, period_end)
        ad = self._get_ad_spend(period_start, period_end)
        returns = self._get_returns_cost(period_start, period_end)
        stmt.add_line("amazon_fees", "fba_fees", fba)
        stmt.add_line("amazon_fees", "referral_fees", ref)
        stmt.add_line("amazon_fees", "storage_fees", storage)
        stmt.add_line("amazon_fees", "advertising", ad)
        stmt.add_line("amazon_fees", "returns", returns)
        total_fees = fba + ref + storage + ad + returns
        stmt.add_line("amazon_fees", "total", total_fees)

        # ── Manual expenses entered via invoices ──
        exp_mgr = ExpenseManager(self.db, self.store_id)
        manual = exp_mgr.get_monthly_total(year, month)
        manual_total = 0.0
        for exp_type, amount in sorted(manual.items()):
            stmt.add_line("manual_expenses", exp_type, amount)
            manual_total += amount
        stmt.add_line("manual_expenses", "total", manual_total)

        # ── Net Cash Profit ──
        net_profit = gross_profit - total_fees - manual_total
        stmt.add_line("net", "cash_profit", net_profit)
        stmt.add_line("net", "cash_margin_pct", round(net_profit / total_revenue * 100, 2) if total_revenue > 0 else 0)

        # ── Outstanding (not in P&L but tracked) ──
        stmt.add_line("outstanding", "shipped_but_unpaid", monthly_cogs["cogs_shipped_unpaid"])
        stmt.add_line("outstanding", "in_production_not_yet_payable", monthly_cogs["cogs_in_production_not_yet_payable"])
        stmt.add_line("outstanding", "unpaid_logistics", monthly_cogs["logistics_unpaid"])
        stmt.add_line("outstanding", "note", 0)

        stmt.metadata = {
            "period": f"{year}-{month:02d}",
            "method": "cash_based (excludes AR/AP)",
            "generated_at": datetime.utcnow().isoformat(),
        }

        logger.info(
            "Cash P&L: %s %d-%02d — Rev $%.0f, COGS $%.0f, Net $%.0f",
            self.store_id, year, month, total_revenue, cogs_paid, net_profit,
        )
        return stmt

    def generate_accrual_pnl(self, year: int, month: int) -> FinancialStatement:
        """
        Generate a monthly P&L statement.

        Args:
            year: Calendar year (e.g., 2026)
            month: Calendar month (1-12)

        Returns:
            FinancialStatement with P&L lines
        """
        stmt = FinancialStatement(self.store_id, "pnl", year, month)
        period_start = date(year, month, 1)
        if month == 12:
            period_end = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            period_end = date(year, month + 1, 1) - timedelta(days=1)

        # ── Revenue ──
        revenue = self._get_revenue(period_start, period_end)
        stmt.add_line("revenue", "product_sales", revenue)
        stmt.add_line("revenue", "shipping_credits", self._get_shipping_credits(period_start, period_end))
        stmt.add_line("revenue", "total", revenue)  # placeholder, updated below

        total_revenue = revenue  # + refunds_etc

        # ── COGS (Cost of Goods Sold) ──
        cogs = self._get_cogs(period_start, period_end)
        stmt.add_line("cogs", "product_cost", cogs)
        stmt.add_line("cogs", "inbound_shipping", self._get_inbound_shipping(period_start, period_end))
        total_cogs = cogs + self._get_inbound_shipping(period_start, period_end)
        stmt.add_line("cogs", "total", total_cogs)

        gross_profit = total_revenue - total_costs
        stmt.add_line("gross", "gross_profit", gross_profit)
        stmt.add_line("gross", "gross_margin_pct", round(gross_profit / total_revenue * 100, 2) if total_revenue > 0 else 0)

        # ── Fulfillment & Platform Fees ──
        fba_fees = self._get_fba_fees(period_start, period_end)
        referral_fees = self._get_referral_fees(period_start, period_end)
        storage_fees = self._get_storage_fees(period_start, period_end)
        stmt.add_line("fees", "fba_fees", fba_fees)
        stmt.add_line("fees", "referral_fees", referral_fees)
        stmt.add_line("fees", "storage_fees", storage_fees)
        total_fees = fba_fees + referral_fees + storage_fees
        stmt.add_line("fees", "total", total_fees)

        # ── Operating Expenses ──
        ad_spend = self._get_ad_spend(period_start, period_end)
        returns_cost = self._get_returns_cost(period_start, period_end)
        stmt.add_line("expenses", "advertising", ad_spend)
        stmt.add_line("expenses", "returns", returns_cost)
        total_expenses = ad_spend + returns_cost
        stmt.add_line("expenses", "total", total_expenses)

        # ── Net Profit ──
        net_profit = gross_profit - total_fees - total_expenses
        stmt.add_line("net", "profit", net_profit)
        stmt.add_line("net","profit_margin_pct", round(net_profit / total_revenue * 100, 2) if total_revenue > 0 else 0)

        # Recalculate revenue total
        stmt.lines["revenue___total"] = total_revenue

        stmt.metadata = {
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "generated_at": datetime.utcnow().isoformat(),
        }

        logger.info(
            "P&L generated: %s %d-%02d — Rev $%.0f, COGS $%.0f, Net $%.0f",
            self.store_id, year, month, total_revenue, total_cogs, net_profit,
        )
        return stmt

    # ─── Cash Flow Statement ────────────────────────────────────

    def generate_cash_flow(self, year: int, month: int) -> FinancialStatement:
        """
        Generate a monthly Cash Flow statement.

        Cash Flow differs from P&L:
        - P&L: revenue when ORDERED, costs when INCURRED
        - Cash Flow: revenue when SETTLED (T+7), costs when PAID

        Args:
            year: Calendar year
            month: Calendar month

        Returns:
            FinancialStatement with cash flow lines
        """
        stmt = FinancialStatement(self.store_id, "cash_flow", year, month)
        period_start = date(year, month, 1)
        if month == 12:
            period_end = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            period_end = date(year, month + 1, 1) - timedelta(days=1)

        # ── Operating Inflows ──
        # Amazon settles ~7-14 days after shipment. For monthly, we use
        # the orders shipped in the previous month (settled this month)
        # plus orders shipped early this month.
        # Simplified: use revenue from P&L minus estimated unsettled
        pnl_revenue = self._get_revenue(period_start, period_end)
        # Assume 85% of revenue settled within the month (Amazon standard)
        settled_revenue = pnl_revenue * 0.85
        prior_unsettled = self._get_prior_month_revenue(year, month) * 0.15

        stmt.add_line("inflow", "amazon_settlements", settled_revenue)
        stmt.add_line("inflow", "prior_month_settlement", prior_unsettled)
        total_inflow = settled_revenue + prior_unsettled
        stmt.add_line("inflow", "total", total_inflow)

        # ── Operating Outflows ──
        # --- From SP-API (Amazon deductions) ---
        ad_spend = self._get_ad_spend(period_start, period_end)
        stmt.add_line("outflow_amazon", "advertising", ad_spend)
        fba = self._get_fba_fees(period_start, period_end)
        referral = self._get_referral_fees(period_start, period_end)
        storage = self._get_storage_fees(period_start, period_end)
        stmt.add_line("outflow_amazon", "fba_fees", fba)
        stmt.add_line("outflow_amazon", "referral_fees", referral)
        stmt.add_line("outflow_amazon", "storage_fees", storage)
        returns = self._get_returns_cost(period_start, period_end)
        stmt.add_line("outflow_amazon", "returns", returns)
        total_amazon = ad_spend + fba + referral + storage + returns
        stmt.add_line("outflow_amazon", "total", total_amazon)

        # --- From Manual Expenses (invoices, overrides) ---
        exp_mgr = ExpenseManager(self.db, self.store_id)
        manual_expenses = exp_mgr.get_monthly_total(year, month)
        total_manual = 0.0
        expense_labels = {
            "cogs": "supplier_payments",
            "logistics": "upfront_logistics",
            "salary": "salaries",
            "software": "software_subscriptions",
            "marketing": "marketing_costs",
            "office": "office_expenses",
            "shipping_supplies": "shipping_supplies",
            "tax": "tax_payments",
            "insurance": "insurance",
            "professional_fees": "professional_fees",
            "storage_rent": "storage_rent",
            "travel": "travel",
            "other": "other_expenses",
        }
        for exp_type, amount in sorted(manual_expenses.items()):
            label = expense_labels.get(exp_type, exp_type)
            stmt.add_line("outflow_manual", label, amount)
            total_manual += amount
        stmt.add_line("outflow_manual", "total", total_manual)

        total_outflow = total_amazon + total_manual
        stmt.add_line("outflow", "total", total_outflow)

        # ── Net Cash Flow ──
        net_cash = total_inflow - total_outflow
        stmt.add_line("net", "cash_flow", net_cash)

        # ── Working Capital Impact ──
        # P&L profit vs actual cash difference shows working capital needs
        pnl_net = self._get_pnl_net(year, month)
        working_capital_gap = pnl_net - net_cash
        stmt.add_line("analysis", "pnl_net_profit", pnl_net)
        stmt.add_line("analysis", "net_cash_flow", net_cash)
        stmt.add_line("analysis", "working_capital_gap", working_capital_gap)

        stmt.metadata = {
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "generated_at": datetime.utcnow().isoformat(),
            "note": "Cash flow assumes 85% of revenue settled within month, "
                    "50% of COGS paid within month. Adjust based on actual payment terms.",
        }

        logger.info(
            "Cash Flow generated: %s %d-%02d — In $%.0f, Out $%.0f, Net $%.0f",
            self.store_id, year, month, total_inflow, total_outflow, net_cash,
        )
        return stmt

    # ─── Data Accessors ─────────────────────────────────────────

    def _get_revenue(self, start: date, end: date) -> float:
        result = self.db.query(func.sum(ProfitDailySnapshot.revenue)).filter(
            ProfitDailySnapshot.store_id == self.store_id,
            ProfitDailySnapshot.date.between(start, end),
        ).scalar()
        return float(result or 0)

    def _get_shipping_credits(self, start: date, end: date) -> float:
        result = self.db.query(func.sum(ProfitDailySnapshot.shipping_charge)).filter(
            ProfitDailySnapshot.store_id == self.store_id,
            ProfitDailySnapshot.date.between(start, end),
        ).scalar()
        return float(result or 0)

    def _get_cogs(self, start: date, end: date) -> float:
        """Calculate COGS from products sold in the period."""
        result = self.db.query(func.sum(ProfitDailySnapshot.product_cost)).filter(
            ProfitDailySnapshot.store_id == self.store_id,
            ProfitDailySnapshot.date.between(start, end),
        ).scalar()
        return float(result or 0)

    def _get_inbound_shipping(self, start: date, end: date) -> float:
        """Get inbound shipping costs from shipments in the period."""
        # This would be from a logistics cost tracking table or
        # estimated from inbound shipment data
        result = self.db.query(func.sum(InboundShipment.discrepancy_units)).filter(
            InboundShipment.store_id == self.store_id,
            InboundShipment.shipped_date.between(
                datetime.combine(start, datetime.min.time()),
                datetime.combine(end, datetime.max.time()),
            ) if hasattr(InboundShipment, 'shipped_date') else False,
        ).scalar()
        # Default: estimate as 3% of revenue for inbound shipping
        return self._get_revenue(start, end) * 0.03

    def _get_fba_fees(self, start: date, end: date) -> float:
        result = self.db.query(func.sum(ProfitDailySnapshot.fba_fees)).filter(
            ProfitDailySnapshot.store_id == self.store_id,
            ProfitDailySnapshot.date.between(start, end),
        ).scalar()
        return float(result or 0)

    def _get_referral_fees(self, start: date, end: date) -> float:
        result = self.db.query(func.sum(ProfitDailySnapshot.referral_fees)).filter(
            ProfitDailySnapshot.store_id == self.store_id,
            ProfitDailySnapshot.date.between(start, end),
        ).scalar()
        return float(result or 0)

    def _get_storage_fees(self, start: date, end: date) -> float:
        result = self.db.query(func.sum(ProfitDailySnapshot.storage_fees)).filter(
            ProfitDailySnapshot.store_id == self.store_id,
            ProfitDailySnapshot.date.between(start, end),
        ).scalar()
        return float(result or 0)

    def _get_ad_spend(self, start: date, end: date) -> float:
        result = self.db.query(func.sum(AdPerformance.spend)).filter(
            AdPerformance.store_id == self.store_id,
            AdPerformance.date.between(start, end),
        ).scalar()
        return float(result or 0)

    def _get_returns_cost(self, start: date, end: date) -> float:
        result = self.db.query(func.sum(ProfitDailySnapshot.returns_cost)).filter(
            ProfitDailySnapshot.store_id == self.store_id,
            ProfitDailySnapshot.date.between(start, end),
        ).scalar()
        return float(result or 0)

    def _get_prior_month_revenue(self, year: int, month: int) -> float:
        """Get revenue from the previous month."""
        if month == 1:
            return self._get_revenue(date(year - 1, 12, 1), date(year - 1, 12, 31))
        return self._get_revenue(date(year, month - 1, 1), date(year, month - 1, 28) + timedelta(days=4))

    def _get_pnl_net(self, year: int, month: int) -> float:
        """Get P&L net profit for comparison with cash flow."""
        stmt = self.generate_pnl(year, month)
        return stmt.lines.get("net___profit", 0)

    # ─── Year-to-Date & Rolling ─────────────────────────────────

    def generate_ytd_pnl(self, year: int, month: int) -> List[Dict]:
        """Generate YTD P&L (January to specified month)."""
        statements = []
        for m in range(1, month + 1):
            stmt = self.generate_pnl(year, m)
            statements.append(stmt.to_dict())
        return statements

    def generate_rolling_12m_cash_flow(self, year: int, month: int) -> List[Dict]:
        """Generate trailing 12-month cash flow."""
        statements = []
        for i in range(11, -1, -1):
            m = month - i
            y = year
            while m < 1:
                m += 12
                y -= 1
            stmt = self.generate_cash_flow(y, m)
            statements.append(stmt.to_dict())
        return statements
