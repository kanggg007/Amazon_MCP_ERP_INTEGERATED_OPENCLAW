"""
Expense Manager
=================
Track manual expenses for cash flow: invoices, salaries, logistics, etc.
Sources: invoice uploads, manual entry, recurring expenses.
Every expense is store-isolated.
"""

import logging
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy import func

from db.models import Store
from db.connection import get_session
from config.settings import settings

logger = logging.getLogger("amazon.engine.expenses")

# New table — defined here for reference
EXPENSE_TYPES = [
    "cogs", "logistics", "salary", "software",
    "marketing", "office", "shipping_supplies",
    "tax", "insurance", "professional_fees",
    "storage_rent", "travel", "other",
]


class ExpenseRecord:
    """A single expense record."""

    def __init__(self, store_id: str, expense_date: date, expense_type: str,
                 category: str, amount: float, description: str = "",
                 vendor: str = "", invoice_number: str = "",
                 payment_status: str = "unpaid"):
        self.store_id = store_id
        self.expense_date = expense_date
        self.expense_type = expense_type
        self.category = category
        self.amount = amount
        self.description = description
        self.vendor = vendor
        self.invoice_number = invoice_number
        self.payment_status = payment_status
        self.payment_date = None
        self.tax_amount = 0.0

    def to_dict(self) -> Dict:
        return {
            "store_id": self.store_id,
            "expense_date": self.expense_date.isoformat(),
            "expense_type": self.expense_type,
            "category": self.category,
            "amount": self.amount,
            "description": self.description,
            "vendor": self.vendor,
            "invoice_number": self.invoice_number,
            "payment_status": self.payment_status,
        }


class ExpenseManager:
    """
    Manages manual expense entries for cash flow tracking.

    Expenses that SP-API doesn't cover:
    - Supplier payments (COGS)
    - Upfront logistics costs
    - Staff salaries
    - Software subscriptions
    - Office rent
    - Professional fees (accountants, lawyers)
    """

    def __init__(self, db: Session, store_id: str):
        self.db = db
        self.store_id = store_id

    def add_expense(self, expense: ExpenseRecord) -> int:
        """
        Add a manual expense entry.

        Args:
            expense: ExpenseRecord with details

        Returns:
            Expense ID
        """
        sql = """
        INSERT INTO expenses
            (store_id, expense_date, expense_type, category, description,
             vendor, invoice_number, amount, tax_amount, payment_status)
        VALUES
            (:store_id, :expense_date, :expense_type, :category, :description,
             :vendor, :invoice_number, :amount, :tax_amount, :payment_status)
        RETURNING id
        """
        result = self.db.execute(
            sql,
            {
                "store_id": self.store_id,
                "expense_date": expense.expense_date,
                "expense_type": expense.expense_type,
                "category": expense.category,
                "description": expense.description,
                "vendor": expense.vendor,
                "invoice_number": expense.invoice_number,
                "amount": expense.amount,
                "tax_amount": expense.tax_amount,
                "payment_status": expense.payment_status,
            }
        )
        self.db.commit()
        expense_id = result.fetchone()[0]
        logger.info(
            "Expense added: %s — $%.2f [%s]",
            expense.category, expense.amount, expense.expense_type,
        )
        return expense_id

    def get_expenses(self, year: int, month: int,
                     expense_type: Optional[str] = None) -> List[Dict]:
        """
        Get expenses for a specific month.

        Args:
            year: Calendar year
            month: Calendar month
            expense_type: Optional filter

        Returns:
            List of expense dicts
        """
        start = date(year, month, 1)
        if month == 12:
            end = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            end = date(year, month + 1, 1) - timedelta(days=1)

        query = """
        SELECT id, expense_date, expense_type, category, description,
               vendor, invoice_number, amount, tax_amount, payment_status,
               payment_date
        FROM expenses
        WHERE store_id = :store_id
          AND expense_date BETWEEN :start AND :end
        """
        params = {"store_id": self.store_id, "start": start, "end": end}

        if expense_type:
            query += " AND expense_type = :expense_type"
            params["expense_type"] = expense_type

        query += " ORDER BY expense_date DESC"

        rows = self.db.execute(query, params).fetchall()
        return [
            {
                "id": r[0], "date": r[1].isoformat() if r[1] else "",
                "type": r[2], "category": r[3], "description": r[4],
                "vendor": r[5], "invoice": r[6], "amount": float(r[7] or 0),
                "tax": float(r[8] or 0), "status": r[9],
            }
            for r in rows
        ]

    def get_monthly_total(self, year: int, month: int) -> Dict[str, float]:
        """
        Get total expenses by type for a month.

        Returns:
            Dict of {expense_type: total_amount}
        """
        start = date(year, month, 1)
        if month == 12:
            end = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            end = date(year, month + 1, 1) - timedelta(days=1)

        rows = self.db.execute("""
            SELECT expense_type, SUM(amount) as total
            FROM expenses
            WHERE store_id = :store_id
              AND expense_date BETWEEN :start AND :end
            GROUP BY expense_type
        """, {"store_id": self.store_id, "start": start, "end": end}).fetchall()

        return {r[0]: float(r[1] or 0) for r in rows}

    def mark_paid(self, expense_id: int, payment_date: Optional[date] = None) -> bool:
        """Mark an expense as paid."""
        if not payment_date:
            payment_date = date.today()
        self.db.execute("""
            UPDATE expenses
            SET payment_status = 'paid', payment_date = :payment_date
            WHERE id = :id AND store_id = :store_id
        """, {"id": expense_id, "payment_date": payment_date, "store_id": self.store_id})
        self.db.commit()
        return True

    def bulk_import(self, expenses: List[ExpenseRecord]) -> Dict:
        """Import multiple expenses at once."""
        success, failed = 0, 0
        for exp in expenses:
            try:
                self.add_expense(exp)
                success += 1
            except Exception as e:
                logger.error("Failed to import expense: %s", e)
                failed += 1
        return {"success": success, "failed": failed}

    def get_upcoming_payments(self, days: int = 30) -> List[Dict]:
        """Get upcoming unpaid expenses."""
        start = date.today()
        end = start + timedelta(days=days)
        rows = self.db.execute("""
            SELECT id, expense_date, expense_type, category, description,
                   amount, vendor
            FROM expenses
            WHERE store_id = :store_id
              AND payment_status = 'unpaid'
              AND expense_date BETWEEN :start AND :end
            ORDER BY expense_date
        """, {"store_id": self.store_id, "start": start, "end": end}).fetchall()

        return [
            {
                "id": r[0], "due_date": r[1].isoformat() if r[1] else "",
                "type": r[2], "category": r[3], "description": r[4],
                "amount": float(r[5] or 0), "vendor": r[6],
            }
            for r in rows
        ]
