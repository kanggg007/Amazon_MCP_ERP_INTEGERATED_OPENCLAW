"""
FBA Reimbursement Detection System (RDS)
==========================================
Automatically detects:
  - Inbound shipment shortages
  - Lost/damaged FBA inventory
  - Customer returns not received
  - Inventory disappearance
  - Reimbursement underpayment

Generates evidence packs, confidence scores, and approval queue.
"""

import logging
from datetime import datetime, timedelta, date
from typing import Optional

from sqlalchemy import func, and_

from db.connection import get_session
from db.models import (
    InboundShipment, ShipmentDiscrepancy,
    FinancialTransaction, CustomerLossEvent,
    InventorySnapshot,
)
from auth.store_registry import StoreRegistry

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 1. DATABASE TABLES (defined in schema.sql)
# ──────────────────────────────────────────────
# inventory_loss_events  — ledger-style inventory movements
# reimbursement_tracking  — claim cases with status/deadlines
# recovery_opportunities  — universal recovery queue


class ReimbursementEngine:
    """
    Orchestrates detection, evidence generation, and recovery tracking.
    """

    DETECTION_RULES = {
        "inbound_shortage": {
            "description": "Units shipped vs received discrepancy after reconciliation window",
            "confidence_high": 0.95,
            "confidence_medium": 0.70,
            "reconciliation_days": 45,
        },
        "inventory_lost": {
            "description": "Inventory decreased with no sale recorded",
            "confidence": 0.70,
        },
        "damage_event": {
            "description": "Warehouse damage with no reimbursement issued",
            "confidence": 0.80,
        },
        "reimbursement_underpayment": {
            "description": "Expected vs actual reimbursement amount mismatch",
            "confidence": 0.85,
        },
        "return_not_received": {
            "description": "Customer returned item not received at warehouse",
            "confidence": 0.60,
        },
    }

    def __init__(self, store_id: str):
        self.store_id = store_id
        self.store = StoreRegistry.get(store_id)

    # ──────────────────────────────────────────────
    # 2. DETECTION
    # ──────────────────────────────────────────────

    def scan_all(self, days_back: int = 90) -> list[dict]:
        """Run all detection rules and return opportunities."""
        opportunities = []
        opportunities.extend(self._detect_inbound_shortages(days_back))
        opportunities.extend(self._detect_inventory_losses(days_back))
        opportunities.extend(self._detect_damage_events(days_back))
        opportunities.extend(self._detect_return_not_received(days_back))
        return sorted(opportunities, key=lambda x: x.get("estimated_value", 0), reverse=True)

    def _detect_inbound_shortages(self, days_back: int) -> list[dict]:
        """Rule 1: Inbound shipment shortage after reconciliation window."""
        session = get_session()
        opportunities = []
        try:
            since = date.today() - timedelta(days=days_back)
            cutoff = date.today() - timedelta(days=self.DETECTION_RULES["inbound_shortage"]["reconciliation_days"])

# Use existing discrepancies or scan shipments directly
            shipments = session.query(InboundShipment).filter(
                InboundShipment.store_id == self.store_id,
                InboundShipment.received_date.isnot(None),
                func.date(InboundShipment.received_date) >= since,
                func.date(InboundShipment.received_date) <= cutoff,
                InboundShipment.discrepancy_units > 0,
            ).all()

            for s in shipments:
                # Get SKU-level details from discrepancies
                disc_rows = session.query(ShipmentDiscrepancy).filter(
                    ShipmentDiscrepancy.store_id == self.store_id,
                    ShipmentDiscrepancy.shipment_id == s.shipment_id,
                    ShipmentDiscrepancy.claim_filed == False,
                ).all()

                for d in disc_rows:
                    opportunities.append({
                        "type": "inbound_shortage",
                        "store_id": self.store_id,
                        "shipment_id": s.shipment_id,
                        "sku": d.sku,
                        "units_sent": d.quantity_shipped,
                        "units_received": d.quantity_received,
                        "missing_qty": d.quantity_shipped - d.quantity_received,
                        "estimated_value": float(d.estimated_loss),
                        "confidence": self.DETECTION_RULES["inbound_shortage"]["confidence_high"],
                        "detected_date": date.today().isoformat(),
                        "rule": "Inbound shipment: shipped {0} received {1}".format(
                            d.quantity_shipped, d.quantity_received
                        ),
                    })

            return opportunities
        finally:
            session.close()

    def _detect_inventory_losses(self, days_back: int) -> list[dict]:
        """Rule 2: Inventory lost after receiving (no sale recorded)."""
        session = get_session()
        opportunities = []
        try:
            since = date.today() - timedelta(days=days_back)

            # Check inventory loss events
            loss_events = session.execute(
                text("""
                    SELECT sku, event_type, SUM(quantity) as total_qty,
                           SUM(estimated_value) as total_value
                    FROM inventory_loss_events
                    WHERE store_id = :sid
                      AND event_date >= :since
                      AND event_type IN ('lost', 'missing')
                      AND id NOT IN (
                          SELECT loss_event_id FROM reimbursement_tracking WHERE store_id = :sid
                      )
                    GROUP BY sku, event_type
                    HAVING SUM(quantity) > 0
                """),
                {"sid": self.store_id, "since": since},
            ).fetchall()

            for row in loss_events:
                opportunities.append({
                    "type": "inventory_lost",
                    "store_id": self.store_id,
                    "sku": row.sku,
                    "missing_qty": row.total_qty,
                    "estimated_value": float(row.total_value) if row.total_value else 0,
                    "confidence": self.DETECTION_RULES["inventory_lost"]["confidence"],
                    "detected_date": date.today().isoformat(),
                    "rule": f"Inventory lost/removed: {row.event_type} ({row.total_qty} units)",
                })

            return opportunities
        finally:
            session.close()

    def _detect_damage_events(self, days_back: int) -> list[dict]:
        """Rule 3: Damage events without reimbursement."""
        session = get_session()
        opportunities = []
        try:
            since = date.today() - timedelta(days=days_back)

            damage_events = session.execute(
                text("""
                    SELECT sku, event_type, SUM(quantity) as total_qty,
                           SUM(estimated_value) as total_value
                    FROM inventory_loss_events
                    WHERE store_id = :sid
                      AND event_date >= :since
                      AND event_type = 'damaged'
                      AND id NOT IN (
                          SELECT loss_event_id FROM reimbursement_tracking WHERE store_id = :sid
                      )
                    GROUP BY sku, event_type
                    HAVING SUM(quantity) > 0
                """),
                {"sid": self.store_id, "since": since},
            ).fetchall()

            for row in damage_events:
                opportunities.append({
                    "type": "damage_event",
                    "store_id": self.store_id,
                    "sku": row.sku,
                    "missing_qty": row.total_qty,
                    "estimated_value": float(row.total_value) if row.total_value else 0,
                    "confidence": self.DETECTION_RULES["damage_event"]["confidence"],
                    "detected_date": date.today().isoformat(),
                    "rule": f"Warehouse damage: {row.total_qty} units not reimbursed",
                })

            return opportunities
        finally:
            session.close()

    def _detect_return_not_received(self, days_back: int) -> list[dict]:
        """Rule 5: Customer returns not received at warehouse."""
        session = get_session()
        opportunities = []
        try:
            since = date.today() - timedelta(days=days_back)

            # Check financial transactions for unreimbursed customer returns
            missing = session.execute(
                text("""
                    SELECT ft.sku, COUNT(*) as return_count,
                           SUM(ABS(ft.amount)) as total_value
                    FROM financial_transactions ft
                    WHERE ft.store_id = :sid
                      AND ft.transaction_type = 'refund'
                      AND ft.posted_date >= :since
                      AND ft.amazon_order_id NOT IN (
                          SELECT amazon_order_id FROM customer_loss_events
                          WHERE store_id = :sid AND linked_to_return = TRUE
                      )
                    GROUP BY ft.sku
                    HAVING SUM(ABS(ft.amount)) > 0
                """),
                {"sid": self.store_id, "since": since},
            ).fetchall()

            for row in missing:
                opportunities.append({
                    "type": "return_not_received",
                    "store_id": self.store_id,
                    "sku": row.sku,
                    "missing_qty": row.return_count,
                    "estimated_value": float(row.total_value) if row.total_value else 0,
                    "confidence": self.DETECTION_RULES["return_not_received"]["confidence"],
                    "detected_date": date.today().isoformat(),
                    "rule": f"{row.return_count} customer returns not reconciled",
                })

            return opportunities
        finally:
            session.close()

    # ──────────────────────────────────────────────
    # 3. EVIDENCE PACK GENERATOR
    # ──────────────────────────────────────────────

    def generate_evidence_pack(self, opportunity: dict) -> dict:
        """
        Generate structured evidence pack for human review.
        Ready to be copied into Seller Central claim form.
        """
        pack = {
            "store_id": self.store_id,
            "case_type": opportunity.get("type", "unknown"),
            "estimated_value": opportunity.get("estimated_value", 0),
            "confidence": opportunity.get("confidence", 0),
            "generated_at": datetime.utcnow().isoformat(),
            "evidence": [],
        }

        if opportunity.get("type") == "inbound_shortage":
            pack["shipment_id"] = opportunity.get("shipment_id", "")
            pack["sku"] = opportunity.get("sku", "")
            pack["units_sent"] = opportunity.get("units_sent")
            pack["units_received"] = opportunity.get("units_received")
            pack["missing_qty"] = opportunity.get("missing_qty")

            # Get supporting shipment details
            session = get_session()
            try:
                shipment = session.query(InboundShipment).filter(
                    InboundShipment.store_id == self.store_id,
                    InboundShipment.shipment_id == opportunity.get("shipment_id"),
                ).first()

                if shipment:
                    pack["shipment_name"] = shipment.shipment_name
                    pack["received_date"] = (
                        shipment.received_date.isoformat() if shipment.received_date else None
                    )
                    pack["destination_center"] = shipment.destination_fulfillment_center
                    pack["evidence"].append({
                        "type": "shipment_record",
                        "detail": f"Shipment {shipment.shipment_id}: {shipment.total_units_shipped} sent, {shipment.total_units_received} received",
                    })
            finally:
                session.close()

            # Evidence text for Seller Central
            pack["claim_draft"] = (
                f"FBA Inbound Shipment Shortage Claim\n"
                f"Shipment ID: {opportunity.get('shipment_id', '')}\n"
                f"SKU: {opportunity.get('sku', '')}\n"
                f"Units Shipped: {opportunity.get('units_sent', 0)}\n"
                f"Units Received: {opportunity.get('units_received', 0)}\n"
                f"Missing: {opportunity.get('missing_qty', 0)}\n"
                f"Estimated Value: ${opportunity.get('estimated_value', 0):.2f}\n"
                f"Confidence: {opportunity.get('confidence', 0)*100:.0f}%"
            )

        elif opportunity.get("type") in ("inventory_lost", "damage_event"):
            pack["sku"] = opportunity.get("sku", "")
            pack["missing_qty"] = opportunity.get("missing_qty", 0)
            pack["claim_draft"] = (
                f"FBA {'Inventory Loss' if opportunity['type'] == 'inventory_lost' else 'Warehouse Damage'} Claim\n"
                f"SKU: {opportunity.get('sku', '')}\n"
                f"Units Affected: {opportunity.get('missing_qty', 0)}\n"
                f"Estimated Value: ${opportunity.get('estimated_value', 0):.2f}\n"
                f"Confidence: {opportunity.get('confidence', 0)*100:.0f}%"
            )

        return pack

    # ──────────────────────────────────────────────
    # 4. RECOVERY QUEUE
    # ──────────────────────────────────────────────

    def get_recovery_summary(self) -> dict:
        """Total recoverable value across all categories."""
        session = get_session()
        try:
            total_pending = session.execute(
                text("""
                    SELECT COALESCE(SUM(estimated_value), 0)
                    FROM recovery_opportunities
                    WHERE store_id = :sid AND status = 'pending'
                """),
                {"sid": self.store_id},
            ).scalar() or 0

            by_category = session.execute(
                text("""
                    SELECT category, COUNT(*) as count,
                           SUM(estimated_value) as total_value
                    FROM recovery_opportunities
                    WHERE store_id = :sid AND status = 'pending'
                    GROUP BY category
                    ORDER BY total_value DESC
                """),
                {"sid": self.store_id},
            ).fetchall()

            return {
                "total_pending": float(total_pending),
                "by_category": {
                    row.category: {
                        "count": row.count,
                        "value": float(row.total_value),
                    }
                    for row in by_category
                },
                "store_id": self.store_id,
            }
        finally:
            session.close()

    def get_recovery_queue(self, min_value: float = 100, status: str = "pending") -> list[dict]:
        """Get pending recovery opportunities sorted by value."""
        session = get_session()
        try:
            rows = session.execute(
                text("""
                    SELECT id, category, sku, estimated_value, confidence_score,
                           status, created_at
                    FROM recovery_opportunities
                    WHERE store_id = :sid
                      AND status = :status
                      AND estimated_value >= :min_val
                    ORDER BY estimated_value DESC
                    LIMIT 50
                """),
                {"sid": self.store_id, "status": status, "min_val": min_value},
            ).fetchall()

            return [
                {
                    "id": row.id,
                    "category": row.category,
                    "sku": row.sku,
                    "value": float(row.estimated_value),
                    "confidence": float(row.confidence_score) if row.confidence_score else 0,
                    "status": row.status,
                    "detected_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in rows
            ]
        finally:
            session.close()

    def file_opportunity(self, opp_id: int, filed_by: str = "system") -> bool:
        """Mark an opportunity as filed (after human approval)."""
        session = get_session()
        try:
            session.execute(
                text("""
                    UPDATE recovery_opportunities
                    SET status = 'filed', assigned_to = :filed_by, updated_at = NOW()
                    WHERE id = :oid AND store_id = :sid
                """),
                {"oid": opp_id, "sid": self.store_id, "filed_by": filed_by},
            )
            session.commit()
            return True
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to file opportunity {opp_id}: {e}")
            return False
        finally:
            session.close()
