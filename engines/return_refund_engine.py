"""
Return & Refund Reconciliation Engine
=======================================
Reconstructs the full lifecycle: Order → Return → Refund → Loss Analysis.

Sources:
  - Finances API  (refund events, charges, adjustments)
  - Returns API   (return requests, RMA, status)
  - Orders API    (order context: SKU, price, dates)

Tables:
  - returns                 ← Returns API
  - refunds                 ← Finances API
  - return_items            ← Returns API (granular)
  - financial_transactions  ← Finances API (all events)
  - customer_loss_events    ← Computed (for AI queries)
"""

import logging
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from db.connection import get_session
from db.models import (
    Return, Refund, ReturnItem,
    FinancialTransaction, CustomerLossEvent,
)
from auth.store_registry import StoreRegistry
from auth.token_manager import TokenManager

logger = logging.getLogger(__name__)


class ReturnRefundEngine:
    """
    Orchestrates fetching, storing, and reconciling return/refund data.
    """

    def __init__(self, store_id: str, marketplace_ids: list[str] = None):
        self.store_id = store_id
        self.store = StoreRegistry.get(store_id)
        self.marketplace_ids = marketplace_ids or self.store.get("marketplace_ids", ["ATVPDKIKX0DER"])
        self.token_mgr = TokenManager(store_id)

    # ──────────────────────────────────────────────
    # 1. FETCH from Finances API
    # ──────────────────────────────────────────────

    def fetch_refunds_from_finances(
        self,
        posted_after: str = None,
        posted_before: str = None,
        max_results: int = 100,
    ) -> list[dict]:
        """
        Pull refund transactions from SP-API Finances API.
        Called directly — no report needed.
        """
        posted_after = posted_after or (datetime.utcnow() - timedelta(days=30)).isoformat() + "Z"
        posted_before = posted_before or datetime.utcnow().isoformat() + "Z"

        client = self.token_mgr.get_sp_client()
        endpoint = (
            f"/finances/v0/financialEvents"
            f"?PostedAfter={posted_after}&PostedBefore={posted_before}&MaxResults={max_results}"
        )

        response = client.get(endpoint)
        if response.status_code != 200:
            logger.error(f"Finances API error: {response.status_code} {response.text[:200]}")
            return []

        events = response.json().get("payload", {}).get("FinancialEvents", {})

        # Collect all refund-related events
        refund_events = []
        for ev in events.get("RefundEventList", []):
            refund_events.append({
                "source": "RefundEventList",
                "amazon_order_id": ev.get("AmazonOrderId"),
                "marketplace_name": ev.get("MarketplaceName"),
                "posted_date": ev.get("PostedDate"),
                "adjustments": ev.get("AdjustmentEventList", []),
                "items": ev.get("ShipmentItemAdjustmentList", []),
            })

        # Also check ShipmentEventList for refunds
        for ev in events.get("ShipmentEventList", []):
            for item in ev.get("ShipmentItemList", []):
                for promo in item.get("PromotionList", []):
                    if "refund" in (promo.get("PromotionType") or "").lower():
                        refund_events.append({
                            "source": "ShipmentEventList",
                            "amazon_order_id": ev.get("AmazonOrderId"),
                            "marketplace_name": ev.get("MarketplaceName"),
                            "posted_date": ev.get("PostedDate"),
                            "items": [{
                                "SellerSKU": item.get("SellerSKU"),
                                "QuantityShipped": item.get("QuantityShipped"),
                                "PromotionAmount": promo.get("PromotionAmount"),
                            }],
                        })

        logger.info(f"Fetched {len(refund_events)} refund events from Finances API")
        return refund_events

    # ──────────────────────────────────────────────
    # 2. STORE into DB
    # ──────────────────────────────────────────────

    def store_refund_events(self, events: list[dict]) -> int:
        """Store refund events into refunds + financial_transactions tables."""
        session = get_session()
        count = 0
        try:
            for ev in events:
                order_id = ev.get("amazon_order_id", "?")
                posted = ev.get("posted_date")

                for item in ev.get("items", []):
                    sku = item.get("SellerSKU") or item.get("MerchantSKU", "")
                    principal = self._get_charge_amount(item, "Principal")
                    tax = self._get_charge_amount(item, "Tax")
                    shipping = self._get_charge_amount(item, "ReturnShipping")
                    total = principal + tax

                    if total == 0:
                        continue

                    refund_id = f"REF-{order_id}-{sku}-{uuid.uuid4().hex[:8]}"

                    refund = Refund(
                        store_id=self.store_id,
                        marketplace_id=self._resolve_marketplace(ev.get("marketplace_name", "")),
                        refund_id=refund_id,
                        amazon_order_id=order_id,
                        sku=sku,
                        refund_amount=total,
                        currency="USD",
                        refund_date=posted,
                        posted_date=posted,
                        tax_amount=tax,
                        fee_adjustment=0,
                        return_shipping=shipping,
                        raw_data=ev,
                    )
                    session.add(refund)

                    # Also store as financial transaction
                    ft = FinancialTransaction(
                        store_id=self.store_id,
                        marketplace_id=self._resolve_marketplace(ev.get("marketplace_name", "")),
                        transaction_id=refund_id,
                        amazon_order_id=order_id,
                        sku=sku,
                        transaction_type="refund",
                        transaction_subtype="RefundEvent",
                        amount=total,
                        currency="USD",
                        posted_date=posted,
                        raw_data=ev,
                    )
                    session.add(ft)
                    count += 1

                # Also store adjustment events
                for adj in ev.get("adjustments", []):
                    adj_id = f"ADJ-{order_id}-{uuid.uuid4().hex[:8]}"
                    try:
                        adj_amt = float(adj.get("AdjustmentAmount", {}).get("CurrencyAmount", 0))
                    except (TypeError, ValueError):
                        adj_amt = 0

                    if adj_amt != 0:
                        ft = FinancialTransaction(
                            store_id=self.store_id,
                            marketplace_id=self._resolve_marketplace(ev.get("marketplace_name", "")),
                            transaction_id=adj_id,
                            amazon_order_id=order_id,
                            sku="",
                            transaction_type="adjustment",
                            transaction_subtype=adj.get("AdjustmentReason", ""),
                            amount=adj_amt,
                            currency="USD",
                            posted_date=posted,
                            raw_data=ev,
                        )
                        session.add(ft)
                        count += 1

            session.commit()
            logger.info(f"Stored {count} refund/financial records")
            return count
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to store refund events: {e}")
            return 0
        finally:
            session.close()

    # ──────────────────────────────────────────────
    # 3. RECONCILE: Build customer_loss_events
    # ──────────────────────────────────────────────

    def reconcile_loss_events(self, start_date: str = None, end_date: str = None) -> int:
        """
        Match refunds → returns → orders → generate customer_loss_events.

        This is where your AI gets its unified loss data.
        """
        session = get_session()
        count = 0
        try:
            # Pull refunds not yet reconciled
            refunds = session.query(Refund).filter(
                Refund.store_id == self.store_id,
                ~Refund.refund_id.in_(
                    session.query(CustomerLossEvent.refund_id).filter(
                        CustomerLossEvent.store_id == self.store_id,
                        CustomerLossEvent.linked_to_refund == True,
                    )
                ),
            ).all()

            for refund in refunds:
                loss = CustomerLossEvent(
                    store_id=self.store_id,
                    marketplace_id=refund.marketplace_id,
                    refund_id=refund.refund_id,
                    amazon_order_id=refund.amazon_order_id,
                    sku=refund.sku,
                    loss_type="refund" if refund.refund_amount > 0 else "partial_refund",
                    amount_loss=abs(refund.refund_amount),
                    currency=refund.currency or "USD",
                    reason=f"Refund: {refund.refund_reason_code or 'not specified'}",
                    event_date=refund.refund_date,
                    linked_to_refund=True,
                    linked_to_return=False,
                    is_anomaly=False,
                )

                # Try to find matching return
                matching_return = session.query(Return).filter(
                    Return.store_id == self.store_id,
                    Return.amazon_order_id == refund.amazon_order_id,
                    Return.sku == refund.sku,
                ).first()

                if matching_return:
                    loss.return_id = matching_return.return_id
                    loss.linked_to_return = True
                    loss.reason = f"Return: {matching_return.return_reason or 'N/A'} | Refund: {refund.refund_reason_code or 'N/A'}"
                    loss.loss_type = "return_damage" if "damage" in (matching_return.return_reason or "").lower() else "customer_return"

                # Flag anomalies
                if refund.refund_amount > 1000:
                    loss.is_anomaly = True
                    loss.anomaly_reason = "High-value refund"

                session.add(loss)
                count += 1

            session.commit()
            logger.info(f"Reconciled {count} loss events")
            return count
        except Exception as e:
            session.rollback()
            logger.error(f"Reconciliation failed: {e}")
            return 0
        finally:
            session.close()

    # ──────────────────────────────────────────────
    # 4. AI-FRIENDLY QUERIES
    # ──────────────────────────────────────────────

    def get_profit_leakage(self, sku: str = None) -> list[dict]:
        """Return loss events grouped for profitability analysis."""
        session = get_session()
        try:
            query = session.query(CustomerLossEvent).filter(
                CustomerLossEvent.store_id == self.store_id
            )
            if sku:
                query = query.filter(CustomerLossEvent.sku == sku)
            events = query.order_by(CustomerLossEvent.event_date.desc()).limit(100).all()

            return [
                {
                    "loss_type": e.loss_type,
                    "sku": e.sku,
                    "amount": float(e.amount_loss),
                    "reason": e.reason,
                    "date": e.event_date.isoformat() if e.event_date else None,
                    "order_id": e.amazon_order_id,
                    "anomaly": e.is_anomaly,
                }
                for e in events
            ]
        finally:
            session.close()

    def get_summary(self) -> dict:
        """High-level summary of returns + refunds for dashboard."""
        session = get_session()
        try:
            total_refunds = session.query(Refund).filter(
                Refund.store_id == self.store_id
            ).count()

            total_refund_amount = session.query(
                func.sum(Refund.refund_amount)
            ).filter(Refund.store_id == self.store_id).scalar() or 0

            total_returns = session.query(Return).filter(
                Return.store_id == self.store_id
            ).count()

            total_loss = session.query(
                func.sum(CustomerLossEvent.amount_loss)
            ).filter(
                CustomerLossEvent.store_id == self.store_id,
                CustomerLossEvent.is_anomaly == False,
            ).scalar() or 0

            anomalies = session.query(CustomerLossEvent).filter(
                CustomerLossEvent.store_id == self.store_id,
                CustomerLossEvent.is_anomaly == True,
            ).count()

            top_loss_skus = session.query(
                CustomerLossEvent.sku,
                func.sum(CustomerLossEvent.amount_loss).label("total_loss"),
            ).filter(
                CustomerLossEvent.store_id == self.store_id
            ).group_by(CustomerLossEvent.sku).order_by(
                func.sum(CustomerLossEvent.amount_loss).desc()
            ).limit(10).all()

            return {
                "total_refunds": total_refunds,
                "total_refund_amount": float(total_refund_amount),
                "total_returns": total_returns,
                "total_loss": float(total_loss),
                "anomalies": anomalies,
                "top_loss_skus": [
                    {"sku": s.sku, "loss": float(s.total_loss)}
                    for s in top_loss_skus
                ],
            }
        finally:
            session.close()

    # ──────────────────────────────────────────────
    # HELPERS
    # ──────────────────────────────────────────────

    def _get_charge_amount(self, item: dict, charge_type: str) -> float:
        """Extract a specific charge type from ShipmentItemAdjustmentList."""
        for charge in item.get("ItemChargeAdjustmentList", []):
            if charge.get("ChargeType") == charge_type:
                try:
                    return float(charge.get("ChargeAmount", {}).get("CurrencyAmount", 0))
                except (TypeError, ValueError):
                    return 0
        return 0

    def _resolve_marketplace(self, name: str) -> str:
        """Resolve marketplace name → ID."""
        mapping = {
            "Amazon.com": "ATVPDKIKX0DER",
            "Amazon.ca": "A2EUQ1WTGCTBG2",
            "Amazon.co.uk": "A1F83G8C2ARO7P",
            "Amazon.de": "A1PA6795UKMFR9",
            "Amazon.co.jp": "A1VC38T7YXB528",
            "Amazon.com.au": "A39IBJ37TRP1C6",
            "Amazon.com.mx": "A1AM78C64UM0Y8",
        }
        return mapping.get(name, "ATVPDKIKX0DER")
