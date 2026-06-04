"""
Alert Engine
=============
Evaluates business rules and dispatches alerts via Slack/email.
Supports multi-store, multi-marketplace.
All alerts logged to audit_log.
"""

import json
import logging
from datetime import date, timedelta
from typing import Dict, List, Optional

import httpx

from sqlalchemy.orm import Session
from config.settings import settings
from engines.inventory_engine import InventoryEngine
from engines.discrepancy_engine import DiscrepancyEngine

logger = logging.getLogger("amazon.alerts.engine")


class Alert:
    def __init__(self, store_id: str, alert_type: str, severity: str,
                 title: str, message: str, details: Optional[Dict] = None):
        self.store_id = store_id
        self.alert_type = alert_type
        self.severity = severity
        self.title = title
        self.message = message
        self.details = details or {}

    def to_dict(self) -> Dict:
        return {
            "store_id": self.store_id,
            "type": self.alert_type,
            "severity": self.severity,
            "title": self.title,
            "message": self.message,
            "details": self.details,
        }


class AlertEngine:
    """
    Evaluates business rules and triggers alerts.
    Supports Slack and email dispatch.
    """

    def __init__(self, db: Session):
        self.db = db
        self._http = httpx.AsyncClient(timeout=15.0)

    async def evaluate_store(self, store_id: str) -> List[Alert]:
        """Evaluate all rules for a single store and return alerts."""
        alerts = []

        # 1. Inventory alerts
        inv_engine = InventoryEngine(self.db, store_id)
        inv_alerts = inv_engine.analyze()
        for a in inv_alerts:
            alerts.append(Alert(
                store_id=store_id, alert_type=a["type"], severity=a["severity"],
                title=f"Inventory {a['type'].replace('_',' ').title()}",
                message=a["message"],
                details={"sku": a["sku"]},
            ))

        # 2. Discrepancy alerts
        disc_engine = DiscrepancyEngine(self.db, store_id)
        discrepancies = disc_engine.scan_shipments(min_loss_usd=100.0)
        for d in discrepancies:
            alerts.append(Alert(
                store_id=store_id, alert_type="reimbursement", severity="warning",
                title=f"Claim Opportunity: ${d['estimated_loss']:.0f}",
                message=f"SKU {d['sku']} — {d['type']} — est. ${d['estimated_loss']:.0f}",
                details=d,
            ))

        return alerts

    async def dispatch_alerts(self, alerts: List[Alert]):
        """Send alerts via configured channels."""
        for alert in alerts:
            if alert.severity == "critical":
                await self._send_slack(alert)
                await self._send_email(alert)
            elif alert.severity == "warning":
                await self._send_slack(alert)

    async def _send_slack(self, alert: Alert):
        """Send a Slack notification."""
        if not settings.slack_webhook_url:
            return

        color = "#FF0000" if alert.severity == "critical" else "#FFA500" if alert.severity == "warning" else "#36a64f"

        payload = {
            "attachments": [{
                "color": color,
                "title": f"[{alert.store_id}] {alert.title}",
                "text": alert.message,
                "fields": [
                    {"title": "Type", "value": alert.alert_type, "short": True},
                    {"title": "Severity", "value": alert.severity, "short": True},
                ],
                "footer": "Amazon Ops Intel",
                "ts": date.today().isoformat(),
            }]
        }

        try:
            await self._http.post(settings.slack_webhook_url, json=payload)
            logger.info("Slack alert sent: %s", alert.title)
        except Exception as e:
            logger.error("Slack dispatch failed: %s", e)

    async def _send_email(self, alert: Alert):
        """Send an email alert (stub — integrate with SendGrid/SES)."""
        if not settings.alert_email_to:
            return
        logger.info(
            "Email alert for %s: %s — %s (sendgrid integration pending)",
            alert.store_id, alert.title, alert.message,
        )

    async def close(self):
        await self._http.aclose()
