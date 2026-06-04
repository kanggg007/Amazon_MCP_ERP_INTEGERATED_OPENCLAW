"""
Purchase Order Manager
=======================
Upload and track manufacturing POs (batches) for COGS tracking.
Every PO is store-isolated. Master Admin uploads batches.
"""

import logging
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy import func

logger = logging.getLogger("amazon.engine.purchase_orders")


class PurchaseOrderManager:
    """
    Manages manufacturing purchase orders for cash-based P&L and cash flow.
    
    Key concept: PO data replaces COGS estimates with actual costs.
    """

    def __init__(self, db: Session, store_id: str):
        self.db = db
        self.store_id = store_id

    def create_po(self, data: Dict) -> int:
        """Create a purchase order with line items.

        PO starts with status 'in_production'.
        Payment is due only when status changes to 'shipped'.
        """
        po_sql = """
        INSERT INTO purchase_orders
            (store_id, po_number, supplier_name, order_date, expected_completion,
             po_status, payment_terms, total_amount, currency, exchange_rate,
             shipping_cost, shipping_vendor, shipping_invoice, shipping_due_date,
             notes, created_by)
        VALUES
            (:store_id, :po_number, :supplier_name, :order_date, :expected_completion,
             'in_production', :payment_terms, :total_amount, :currency, :exchange_rate,
             :shipping_cost, :shipping_vendor, :shipping_invoice, :shipping_due_date,
             :notes, :created_by)
        RETURNING id
        """
        params = {
            "store_id": self.store_id,
            "po_number": data["po_number"],
            "supplier_name": data["supplier_name"],
            "order_date": data["order_date"],
            "expected_completion": data.get("expected_completion") or data.get("expected_delivery"),
            "payment_terms": data.get("payment_terms", "Net 30"),
            "total_amount": data["total_amount"],
            "currency": data.get("currency", "CNY"),
            "exchange_rate": data.get("exchange_rate", 1),
            "shipping_cost": data.get("shipping_cost", 0),
            "shipping_vendor": data.get("shipping_vendor") or data.get("logistics_vendor"),
            "shipping_invoice": data.get("shipping_invoice") or data.get("logistics_invoice"),
            "shipping_due_date": data.get("shipping_due_date") or data.get("logistics_due_date"),
            "notes": data.get("notes"),
            "created_by": data.get("created_by", "master"),
        }
        po_id = self.db.execute(po_sql, params).fetchone()[0]

        for item in data.get("line_items", []):
            self.db.execute("""
                INSERT INTO po_line_items
                    (po_id, store_id, sku, product_name, quantity_ordered,
                     unit_cost, expected_delivery)
                VALUES
                    (:po_id, :store_id, :sku, :product_name, :qty, :cost, :delivery)
            """, {
                "po_id": po_id, "store_id": self.store_id,
                "sku": item["sku"], "product_name": item.get("product_name", ""),
                "qty": item["quantity"], "cost": item["unit_cost"],
                "delivery": item.get("expected_delivery"),
            })

        self.db.commit()
        logger.info("PO created: %s — $%.2f (%s) [in_production]", data["po_number"], data["total_amount"], data["supplier_name"])
        return po_id

    def update_status(self, po_id: int, new_status: str) -> bool:
        """
        Update PO production/shipping status.

        Flow: in_production → ready_to_ship → shipped → delivered

        Payment is triggered when status becomes 'shipped'.
        """
        valid = ["in_production", "ready_to_ship", "shipped", "delivered", "cancelled"]
        if new_status not in valid:
            raise ValueError(f"Invalid status: {new_status}. Must be one of {valid}")

        update_fields = {"po_status": new_status}
        if new_status == "shipped":
            update_fields["shipped_date"] = date.today()
        elif new_status == "delivered":
            update_fields["actual_delivery"] = date.today()

        sql = "UPDATE purchase_orders SET po_status = :status"
        if new_status == "shipped":
            sql += ", shipped_date = :shipped_date"
        elif new_status == "delivered":
            sql += ", actual_delivery = :actual_delivery"
        sql += " WHERE id = :id AND store_id = :store_id"

        self.db.execute(sql, {**update_fields, "id": po_id, "store_id": self.store_id})
        self.db.commit()
        logger.info("PO %d status → %s", po_id, new_status)
        return True

    def mark_paid(self, po_id: int, payment_date: Optional[date] = None) -> bool:
        """Mark a PO as paid."""
        self.db.execute("""
            UPDATE purchase_orders
            SET payment_status = 'paid', payment_date = :payment_date
            WHERE id = :id AND store_id = :store_id
        """, {"id": po_id, "payment_date": payment_date or date.today(), "store_id": self.store_id})
        self.db.commit()
        return True

    def mark_shipping_paid(self, po_id: int) -> bool:
        """Mark logistics/shipping cost as paid."""
        self.db.execute("""
            UPDATE purchase_orders
            SET shipping_paid = TRUE
            WHERE id = :id AND store_id = :store_id
        """, {"id": po_id, "store_id": self.store_id})
        self.db.commit()
        return True

    def get_monthly_cogs(self, year: int, month: int) -> Dict:
        """
        Get actual COGS from POs paid in this month (cash-based).
        Only POs with status 'shipped' or 'delivered' are payable.
        Goods still 'in_production' or 'ready_to_ship' are NOT counted as COGS yet.
        """
        start = date(year, month, 1)
        if month == 12:
            end = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            end = date(year, month + 1, 1) - timedelta(days=1)

        # Paid POs in this period (must be shipped/delivered)
        paid = self.db.execute("""
            SELECT COALESCE(SUM(amount_usd), 0) as cogs,
                   COALESCE(SUM(shipping_cost), 0) as logistics
            FROM purchase_orders
            WHERE store_id = :store_id
              AND payment_status = 'paid'
              AND payment_date BETWEEN :start AND :end
              AND po_status IN ('shipped', 'delivered')
        """, {"store_id": self.store_id, "start": start, "end": end}).fetchone()

        # Shipped but unpaid (these are technically payable)
        shipped_unpaid = self.db.execute("""
            SELECT COALESCE(SUM(amount_usd), 0)
            FROM purchase_orders
            WHERE store_id = :store_id
              AND po_status IN ('shipped', 'delivered')
              AND payment_status IN ('unpaid', 'partial')
        """).fetchone()[0]

        # In production / ready to ship (NOT payable yet)
        in_production = self.db.execute("""
            SELECT COALESCE(SUM(amount_usd), 0)
            FROM purchase_orders
            WHERE store_id = :store_id
              AND po_status IN ('in_production', 'ready_to_ship')
        """).fetchone()[0]

        # Unpaid logistics
        unpaid_logistics = self.db.execute("""
            SELECT COALESCE(SUM(shipping_cost), 0)
            FROM purchase_orders
            WHERE store_id = :store_id
              AND shipping_paid = FALSE
              AND shipping_cost > 0
              AND po_status IN ('shipped', 'delivered', 'ready_to_ship')
        """).fetchone()[0]

        return {
            "cogs_paid": float(paid[0] or 0),
            "logistics_paid": float(paid[1] or 0),
            "cogs_shipped_unpaid": float(shipped_unpaid or 0),
            "cogs_in_production_not_yet_payable": float(in_production or 0),
            "logistics_unpaid": float(unpaid_logistics or 0),
        }

    def get_production_pipeline(self) -> Dict:
        """
        Get the full production pipeline view.
        Shows what's being made vs what's ready to ship vs what's shipped.
        """
        rows = self.db.execute("""
            SELECT po_status, COUNT(*) as count, COALESCE(SUM(amount_usd), 0) as total
            FROM purchase_orders
            WHERE store_id = :store_id
            GROUP BY po_status
            ORDER BY po_status
        """, {"store_id": self.store_id}).fetchall()

        pipeline = {}
        for r in rows:
            pipeline[r[0]] = {"count": r[1], "total_usd": float(r[2] or 0)}

        return {
            "store_id": self.store_id,
            "pipeline": pipeline,
            "total_committed": sum(v["total_usd"] for v in pipeline.values()),
            "total_payable_now": pipeline.get("shipped", {}).get("total_usd", 0)
                + pipeline.get("delivered", {}).get("total_usd", 0),
        }

    def get_unpaid_summary(self) -> List[Dict]:
        """Get all unpaid POs for cash planning."""
        rows = self.db.execute("""
            SELECT id, po_number, supplier_name, order_date, total_amount,
                   currency, amount_usd, shipping_cost, shipping_paid,
                   logistics_vendor, logistics_due_date, payment_terms
            FROM purchase_orders
            WHERE store_id = :store_id
              AND payment_status IN ('unpaid', 'partial')
            ORDER BY order_date
        """, {"store_id": self.store_id}).fetchall()

        return [
            {
                "id": r[0], "po_number": r[1], "supplier": r[2],
                "date": r[3].isoformat() if r[3] else "",
                "amount": float(r[4] or 0), "currency": r[5],
                "amount_usd": float(r[6] or 0),
                "shipping_cost": float(r[7] or 0),
                "shipping_paid": r[8],
                "logistics_vendor": r[9],
                "logistics_due": r[10].isoformat() if r[10] else "",
                "payment_terms": r[11],
            }
            for r in rows
        ]

    def bulk_import(self, po_list: List[Dict]) -> Dict:
        """Import multiple POs at once."""
        success, failed = 0, 0
        for po in po_list:
            try:
                self.create_po(po)
                success += 1
            except Exception as e:
                logger.error("PO import failed: %s", e)
                failed += 1
        return {"success": success, "failed": failed}

    def get_monthly_summary(self, year: int, month: int) -> Dict:
        """Get monthly COGS + logistics summary from POs."""
        cogs_data = self.get_monthly_cogs(year, month)
        return {
            "period": f"{year}-{month:02d}",
            "cogs_paid_in_period": cogs_data["cogs_paid"],
            "logistics_paid_in_period": cogs_data["logistics_paid"],
            "total_paid": cogs_data["cogs_paid"] + cogs_data["logistics_paid"],
            "still_unpaid_cogs": cogs_data["cogs_unpaid"],
            "still_unpaid_logistics": cogs_data["logistics_unpaid"],
            "total_outstanding": cogs_data["cogs_unpaid"] + cogs_data["logistics_unpaid"],
        }
