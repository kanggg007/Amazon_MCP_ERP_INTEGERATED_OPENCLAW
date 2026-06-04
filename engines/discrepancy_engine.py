"""
Discrepancy Engine
===================
HIGHEST PRIORITY MODULE.

Detects missing/damaged units in FBA inbound shipments.
Prepares evidence packs for FBA reimbursement claims.
NO automatic claim filing — human approval required.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy import and_

from db.models import ShipmentDiscrepancy, InboundShipment

logger = logging.getLogger("amazon.engine.discrepancy")


class DiscrepancyEngine:
    """
    Analyzes FBA inbound shipments for discrepancies.
    Generates evidence packs for claims (Level 2 — human approval required).
    Every action logged with store_id.
    """

    def __init__(self, db: Session, store_id: str):
        self.db = db
        self.store_id = store_id

    def scan_shipments(self, min_loss_usd: float = 50.0) -> List[Dict]:
        """
        Scan all unfiled discrepancies and return actionable findings.

        Args:
            min_loss_usd: Minimum estimated loss to flag

        Returns:
            List of discrepancy findings with recovery estimates
        """
        discrepancies = self.db.query(ShipmentDiscrepancy).filter(
            ShipmentDiscrepancy.store_id == self.store_id,
            ShipmentDiscrepancy.claim_filed == False,
        ).all()

        results = []
        for d in discrepancies:
            loss = float(d.estimated_loss or 0)
            if loss >= min_loss_usd:
                results.append({
                    "id": d.id,
                    "shipment_id": d.shipment_id,
                    "sku": d.sku,
                    "type": d.discrepancy_type,
                    "shipped": d.quantity_shipped,
                    "received": d.quantity_received,
                    "damaged": d.quantity_damaged,
                    "lost": d.quantity_lost,
                    "estimated_loss": round(loss, 2),
                    "evidence_ready": d.evidence_ready,
                })

        total_loss = sum(r["estimated_loss"] for r in results)
        logger.info(
            "Discrepancy scan: %s — %d with loss (total $%.2f)",
            self.store_id, len(results), total_loss,
        )
        return results

    def prepare_claim_evidence(self, discrepancy_id: int,
                                output_dir: str = "claims") -> Optional[Dict]:
        """
        Prepare a complete evidence pack for an FBA claim.

        Args:
            discrepancy_id: Discrepancy record ID
            output_dir: Output directory

        Returns:
            Evidence dict with claim recommendation (requires human approval)
        """
        disc = self.db.query(ShipmentDiscrepancy).filter(
            ShipmentDiscrepancy.id == discrepancy_id,
            ShipmentDiscrepancy.store_id == self.store_id,
        ).first()

        if not disc:
            logger.error("Discrepancy not found: id=%d, store=%s", discrepancy_id, self.store_id)
            return None

        # Get shipment context
        shipment = self.db.query(InboundShipment).filter(
            InboundShipment.store_id == self.store_id,
            InboundShipment.shipment_id == disc.shipment_id,
        ).first()

        evidence = {
            "recommendation": {
                "store_id": self.store_id,
                "discrepancy_id": disc.id,
                "action": "file_fba_claim",
                "estimated_recovery": float(disc.estimated_loss or 0),
                "confidence": self._confidence(disc),
                "risk_level": "low",
            },
            "claim_details": {
                "shipment_id": disc.shipment_id,
                "shipment_name": shipment.shipment_name if shipment else "",
                "status": shipment.shipment_status if shipment else "",
                "sku": disc.sku,
                "discrepancy_type": disc.discrepancy_type,
                "quantity_shipped": disc.quantity_shipped,
                "quantity_received": disc.quantity_received,
                "quantity_damaged": disc.quantity_damaged,
                "quantity_lost": disc.quantity_lost,
                "estimated_loss": float(disc.estimated_loss or 0),
            },
            "evidence": {
                "items": [
                    {"type": "inbound_shipment", "data": self._shipment_data(disc.shipment_id)},
                    {"type": "inventory_reconciliation", "data": disc.raw_data or {}},
                ],
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
        }

        # Save to disk
        out_path = Path(output_dir) / self.store_id
        out_path.mkdir(parents=True, exist_ok=True)
        filepath = out_path / f"claim_{disc.shipment_id}_{disc.sku}.json"
        with open(filepath, "w") as f:
            json.dump(evidence, f, indent=2, default=str)

        # Mark evidence as ready
        disc.evidence_ready = True
        disc.evidence_path = str(filepath)
        self.db.commit()

        logger.info(
            "Evidence pack ready: %s — $%.2f recovery estimated",
            filepath, evidence["recommendation"]["estimated_recovery"],
        )
        return evidence

    def _shipment_data(self, shipment_id: str) -> Dict:
        shipment = self.db.query(InboundShipment).filter(
            InboundShipment.shipment_id == shipment_id,
            InboundShipment.store_id == self.store_id,
        ).first()
        if not shipment:
            return {}
        return {
            "shipment_id": shipment.shipment_id,
            "name": shipment.shipment_name,
            "status": shipment.shipment_status,
            "units_shipped": shipment.total_units_shipped,
            "units_received": shipment.total_units_received,
            "discrepancy_units": shipment.discrepancy_units,
        }

    def _confidence(self, disc: ShipmentDiscrepancy) -> float:
        """Confidence in the discrepancy finding."""
        if disc.evidence_ready:
            return 0.90
        if disc.raw_data:
            return 0.75
        return 0.60

    def record_claim_filed(self, discrepancy_id: int, claim_id: str):
        """Record that a claim has been filed (post human approval)."""
        disc = self.db.query(ShipmentDiscrepancy).filter(
            ShipmentDiscrepancy.id == discrepancy_id,
        ).first()
        if disc:
            disc.claim_filed = True
            disc.claim_id = claim_id
            disc.claim_status = "filed"
            disc.claim_filed_date = datetime.now(timezone.utc)
            self.db.commit()
            logger.info("Claim recorded: %s → %s", claim_id, disc.shipment_id)
