"""
SQLAlchemy ORM Models
=======================
Matches the PostgreSQL schema exactly.
Every model includes store_id and marketplace_id for multi-store isolation.
"""

from datetime import datetime, date
from sqlalchemy import (
    Column, BigInteger, Integer, String, Numeric, Boolean, DateTime, Date,
    Text, ForeignKey, Enum as SAEnum, UniqueConstraint, Index,
    CheckConstraint, func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import declarative_base, relationship

from db.connection import get_engine

Base = declarative_base()


class Store(Base):
    __tablename__ = "stores"
    store_id = Column(String(64), primary_key=True)
    name = Column(String(255), nullable=False)
    marketplace_ids = Column(ARRAY(String), nullable=False)
    is_active = Column(Boolean, default=True)
    metadata_ = Column("metadata", JSONB, default={})
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("store_id", "marketplace_id", "sku"),
    )
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    store_id = Column(String(64), ForeignKey("stores.store_id"), nullable=False)
    marketplace_id = Column(String(32), default="ATVPDKIKX0DER")
    sku = Column(String(255), nullable=False)
    asin = Column(String(32))
    fnsku = Column(String(64))
    upc = Column(String(32))
    ean = Column(String(32))
    title = Column(Text)
    brand = Column(String(255))
    unit_cost = Column(Numeric(12, 4), default=0)
    is_active = Column(Boolean, default=True)
    raw_data = Column(JSONB)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class InventorySnapshot(Base):
    __tablename__ = "inventory_snapshot"
    __table_args__ = (
        UniqueConstraint("store_id", "marketplace_id", "sku", "snapshot_date"),
    )
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    store_id = Column(String(64), ForeignKey("stores.store_id"), nullable=False)
    marketplace_id = Column(String(32), default="ATVPDKIKX0DER")
    sku = Column(String(255), nullable=False)
    fnsku = Column(String(64))
    asin = Column(String(32))
    total_units = Column(Integer, default=0)
    inbound_units = Column(Integer, default=0)
    sellable_units = Column(Integer, default=0)
    unsellable_units = Column(Integer, default=0)
    reserved_units = Column(Integer, default=0)
    days_of_supply = Column(Numeric(10, 2))
    forecast_depletion = Column(Date)
    snapshot_date = Column(Date, default=date.today)
    raw_data = Column(JSONB)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("store_id", "marketplace_id", "amazon_order_id"),
    )
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    store_id = Column(String(64), ForeignKey("stores.store_id"), nullable=False)
    marketplace_id = Column(String(32), default="ATVPDKIKX0DER")
    source = Column(String(32), default="amazon")
    amazon_order_id = Column(String(64), nullable=False)
    purchase_date = Column(DateTime(timezone=True))
    order_status = Column(String(32))
    fulfillment_channel = Column(String(32))
    order_total_amount = Column(Numeric(12, 2))
    order_total_currency = Column(String(8), default="USD")
    number_of_items_shipped = Column(Integer, default=0)
    number_of_items_unshipped = Column(Integer, default=0)
    is_business_order = Column(Boolean, default=False)
    is_prime = Column(Boolean, default=False)
    raw_data = Column(JSONB)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")


class OrderItem(Base):
    __tablename__ = "order_items"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    order_id = Column(BigInteger, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)
    store_id = Column(String(64), ForeignKey("stores.store_id"), nullable=False)
    marketplace_id = Column(String(32), default="ATVPDKIKX0DER")
    amazon_order_id = Column(String(64), nullable=False)
    asin = Column(String(32))
    seller_sku = Column(String(255))
    title = Column(Text)
    quantity_ordered = Column(Integer, default=0)
    quantity_shipped = Column(Integer, default=0)
    item_price_amount = Column(Numeric(12, 2))
    shipping_price = Column(Numeric(12, 2))
    raw_data = Column(JSONB)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    order = relationship("Order", back_populates="items")


class InboundShipment(Base):
    __tablename__ = "inbound_shipments"
    __table_args__ = (
        UniqueConstraint("store_id", "marketplace_id", "shipment_id"),
    )
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    store_id = Column(String(64), ForeignKey("stores.store_id"), nullable=False)
    marketplace_id = Column(String(32), default="ATVPDKIKX0DER")
    shipment_id = Column(String(64), nullable=False)
    shipment_name = Column(String(255))
    shipment_status = Column(String(64))
    total_units_shipped = Column(Integer, default=0)
    total_units_received = Column(Integer, default=0)
    discrepancy_units = Column(Integer, default=0)
    raw_data = Column(JSONB)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class ShipmentDiscrepancy(Base):
    __tablename__ = "shipment_discrepancies"
    __table_args__ = (
        UniqueConstraint("store_id", "shipment_id", "sku"),
    )
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    store_id = Column(String(64), ForeignKey("stores.store_id"), nullable=False)
    marketplace_id = Column(String(32), default="ATVPDKIKX0DER")
    shipment_id = Column(String(64), nullable=False)
    sku = Column(String(255), nullable=False)
    fnsku = Column(String(64))
    quantity_shipped = Column(Integer, default=0)
    quantity_received = Column(Integer, default=0)
    quantity_damaged = Column(Integer, default=0)
    quantity_lost = Column(Integer, default=0)
    discrepancy_type = Column(String(32), nullable=False)
    estimated_loss = Column(Numeric(12, 2), default=0)
    claim_filed = Column(Boolean, default=False)
    claim_id = Column(String(64))
    claim_status = Column(String(64), default="not_filed")
    evidence_ready = Column(Boolean, default=False)
    evidence_path = Column(Text)
    raw_data = Column(JSONB)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class AdPerformance(Base):
    __tablename__ = "ads_performance"
    __table_args__ = (
        UniqueConstraint("store_id", "campaign_id", "ad_group_id", "sku", "keyword", "date"),
    )
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    store_id = Column(String(64), ForeignKey("stores.store_id"), nullable=False)
    marketplace_id = Column(String(32), default="ATVPDKIKX0DER")
    campaign_id = Column(String(64), nullable=False)
    ad_group_id = Column(String(64))
    sku = Column(String(255))
    asin = Column(String(32))
    keyword = Column(String(255))
    match_type = Column(String(32))
    date = Column(Date, nullable=False)
    impressions = Column(Integer, default=0)
    clicks = Column(Integer, default=0)
    spend = Column(Numeric(12, 2), default=0)
    sales = Column(Numeric(12, 2), default=0)
    orders = Column(Integer, default=0)
    raw_data = Column(JSONB)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class ProfitDailySnapshot(Base):
    __tablename__ = "profit_daily_snapshot"
    __table_args__ = (
        UniqueConstraint("store_id", "marketplace_id", "sku", "date"),
    )
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    store_id = Column(String(64), ForeignKey("stores.store_id"), nullable=False)
    marketplace_id = Column(String(32), default="ATVPDKIKX0DER")
    sku = Column(String(255), nullable=False)
    asin = Column(String(32))
    date = Column(Date, nullable=False)
    units_sold = Column(Integer, default=0)
    units_refunded = Column(Integer, default=0)
    revenue = Column(Numeric(12, 2), default=0)
    refund_amount = Column(Numeric(12, 2), default=0)
    product_cost = Column(Numeric(12, 2), default=0)
    fba_fees = Column(Numeric(12, 2), default=0)
    referral_fees = Column(Numeric(12, 2), default=0)
    advertising_spend = Column(Numeric(12, 2), default=0)
    storage_fees = Column(Numeric(12, 2), default=0)
    inbound_shipping_cost = Column(Numeric(12, 2), default=0)
    returns_cost = Column(Numeric(12, 2), default=0)
    other_costs = Column(Numeric(12, 2), default=0)
    raw_data = Column(JSONB)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class CustomerFeedback(Base):
    __tablename__ = "customer_feedback"
    __table_args__ = (
        UniqueConstraint("store_id", "marketplace_id", "feedback_id"),
    )
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    store_id = Column(String(64), ForeignKey("stores.store_id"), nullable=False)
    marketplace_id = Column(String(32), default="ATVPDKIKX0DER")
    asin = Column(String(32))
    sku = Column(String(255))
    source = Column(String(32), nullable=False)
    feedback_id = Column(String(128), nullable=False)
    feedback_type = Column(String(32), nullable=False)
    star_rating = Column(Integer)
    title = Column(Text)
    body = Column(Text)
    sentiment = Column(String(16))
    is_verified_purchase = Column(Boolean, default=False)
    feedback_date = Column(DateTime(timezone=True))
    raw_data = Column(JSONB)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    topics = relationship("FeedbackTopic", back_populates="feedback", cascade="all, delete-orphan")


class FeedbackTopic(Base):
    __tablename__ = "feedback_topics"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    feedback_id = Column(BigInteger, ForeignKey("customer_feedback.id", ondelete="CASCADE"), nullable=False)
    topic = Column(String(128), nullable=False)
    sub_topic = Column(String(128))
    sentiment = Column(String(16))
    confidence_score = Column(Numeric(5, 4))
    keywords = Column(ARRAY(String))
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    feedback = relationship("CustomerFeedback", back_populates="topics")


class FeedbackLoop(Base):
    __tablename__ = "feedback_loop"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    recommendation_id = Column(String(128), ForeignKey("recommendation_log.recommendation_id"), nullable=False)
    store_id = Column(String(64), ForeignKey("stores.store_id"), nullable=False)
    outcome = Column(String(32), nullable=False)
    impact_metric = Column(String(64))
    impact_value = Column(Numeric(14, 4))
    threshold_adjusted = Column(Boolean, default=False)
    previous_threshold = Column(Numeric(5, 4))
    new_threshold = Column(Numeric(5, 4))
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class RecommendationLog(Base):
    __tablename__ = "recommendation_log"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    store_id = Column(String(64), ForeignKey("stores.store_id"), nullable=False)
    recommendation_id = Column(String(128), unique=True, nullable=False)
    recommendation_type = Column(String(64), nullable=False)
    recommendation_text = Column(Text, nullable=False)
    confidence_score = Column(Numeric(5, 4))
    risk_level = Column(String(16))
    human_action = Column(String(32), default="pending")
    actioned_by = Column(String(255))
    actioned_at = Column(DateTime(timezone=True))
    before_state = Column(JSONB)
    after_state = Column(JSONB)
    raw_data = Column(JSONB)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    store_id = Column(String(64), ForeignKey("stores.store_id"), nullable=False)
    action = Column(String(64), nullable=False)
    actioned_by = Column(String(255), nullable=False)
    resource_type = Column(String(64))
    resource_id = Column(String(128))
    before_state = Column(JSONB)
    after_state = Column(JSONB)
    status = Column(String(32), default="success")
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class Return(Base):
    """Returns — operational return events from Returns API."""
    __tablename__ = "returns"
    __table_args__ = (
        UniqueConstraint("store_id", "marketplace_id", "return_id"),
    )
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    store_id = Column(String(64), ForeignKey("stores.store_id"), nullable=False)
    marketplace_id = Column(String(32), default="ATVPDKIKX0DER")
    return_id = Column(String(128), nullable=False)
    amazon_order_id = Column(String(64))
    sku = Column(String(255))
    asin = Column(String(32))
    return_request_date = Column(DateTime(timezone=True))
    return_reason = Column(Text)
    return_status = Column(String(64))
    return_quantity = Column(Integer, default=0)
    return_type = Column(String(64))
    resolution = Column(String(64))
    rma_id = Column(String(128))
    a_to_z_claim = Column(Boolean)
    in_policy = Column(Boolean)
    is_prime = Column(Boolean)
    refunded_amount = Column(Numeric(12, 2))
    raw_data = Column(JSONB)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class Refund(Base):
    """Refunds — financial truth from Finances API."""
    __tablename__ = "refunds"
    __table_args__ = (
        UniqueConstraint("store_id", "marketplace_id", "refund_id"),
    )
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    store_id = Column(String(64), ForeignKey("stores.store_id"), nullable=False)
    marketplace_id = Column(String(32), default="ATVPDKIKX0DER")
    refund_id = Column(String(128), nullable=False)
    amazon_order_id = Column(String(64))
    sku = Column(String(255))
    refund_amount = Column(Numeric(12, 2), default=0)
    currency = Column(String(8), default="USD")
    refund_date = Column(DateTime(timezone=True))
    refund_reason_code = Column(String(128))
    refund_type = Column(String(64))
    posted_date = Column(DateTime(timezone=True))
    quantity_refunded = Column(Integer, default=0)
    fee_adjustment = Column(Numeric(12, 2), default=0)
    return_shipping = Column(Numeric(12, 2), default=0)
    tax_amount = Column(Numeric(12, 2), default=0)
    raw_data = Column(JSONB)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class ReturnItem(Base):
    """Return items — granular breakdown per return."""
    __tablename__ = "return_items"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    return_id = Column(BigInteger, ForeignKey("returns.id", ondelete="CASCADE"), nullable=False)
    store_id = Column(String(64), ForeignKey("stores.store_id"), nullable=False)
    marketplace_id = Column(String(32), default="ATVPDKIKX0DER")
    sku = Column(String(255))
    asin = Column(String(32))
    quantity = Column(Integer, default=0)
    item_condition = Column(String(64))
    reason_code = Column(String(128))
    disposition = Column(String(64))
    raw_data = Column(JSONB)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class FinancialTransaction(Base):
    """Financial transactions — source-of-truth from Finances API."""
    __tablename__ = "financial_transactions"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    store_id = Column(String(64), ForeignKey("stores.store_id"), nullable=False)
    marketplace_id = Column(String(32), default="ATVPDKIKX0DER")
    transaction_id = Column(String(128))
    amazon_order_id = Column(String(64))
    sku = Column(String(255))
    transaction_type = Column(String(64), nullable=False)
    transaction_subtype = Column(String(128))
    amount = Column(Numeric(12, 2), nullable=False)
    currency = Column(String(8), default="USD")
    posted_date = Column(DateTime(timezone=True))
    settlement_id = Column(String(64))
    fee_type = Column(String(64))
    fee_amount = Column(Numeric(12, 2))
    raw_data = Column(JSONB)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class CustomerMessage(Base):
    """Buyer messages for Customer Communication Intelligence System."""
    __tablename__ = "customer_messages"
    __table_args__ = (
        UniqueConstraint("store_id", "marketplace_id", "message_id"),
    )
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    store_id = Column(String(64), ForeignKey("stores.store_id"), nullable=False)
    marketplace_id = Column(String(32), default="ATVPDKIKX0DER")
    message_id = Column(String(128), nullable=False)
    amazon_order_id = Column(String(64))
    customer_id = Column(String(128))
    sku = Column(String(255))
    asin = Column(String(32))
    subject = Column(Text)
    body = Column(Text)
    message_type = Column(String(64))
    priority = Column(String(16))
    sentiment = Column(String(16))
    status = Column(String(32), default="pending")
    customer_value = Column(Numeric(10, 2), default=0)
    refund_probability = Column(Numeric(5, 4), default=0)
    expected_loss = Column(Numeric(10, 2), default=0)
    received_date = Column(DateTime(timezone=True))
    raw_data = Column(JSONB)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class SupportPlaybook(Base):
    """Approved support templates/resolutions."""
    __tablename__ = "support_playbooks"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    store_id = Column(String(64), ForeignKey("stores.store_id"), nullable=False)
    issue_type = Column(String(64), nullable=False)
    risk_level = Column(String(16), nullable=False)
    approved_resolution = Column(String(255), nullable=False)
    template_subject = Column(Text)
    template_body = Column(Text, nullable=False)
    requires_approval = Column(Boolean, default=True)
    is_active = Column(Boolean, default=True)
    created_by = Column(String(128))
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class MessageDraft(Base):
    """Auto-generated replies awaiting approval."""
    __tablename__ = "message_drafts"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    message_id = Column(BigInteger, ForeignKey("customer_messages.id", ondelete="CASCADE"), nullable=False)
    store_id = Column(String(64), ForeignKey("stores.store_id"), nullable=False)
    draft_reply = Column(Text, nullable=False)
    confidence = Column(Numeric(5, 4))
    approval_required = Column(Boolean, default=True)
    approval_status = Column(String(32), default="pending")
    approved_by = Column(String(128))
    approved_at = Column(DateTime(timezone=True))
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class SupportCase(Base):
    """End-to-end case tracking."""
    __tablename__ = "support_cases"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    store_id = Column(String(64), ForeignKey("stores.store_id"), nullable=False)
    marketplace_id = Column(String(32), default="ATVPDKIKX0DER")
    message_id = Column(BigInteger, ForeignKey("customer_messages.id"))
    amazon_order_id = Column(String(64))
    sku = Column(String(255))
    issue_type = Column(String(64), nullable=False)
    resolution = Column(String(255))
    resolution_cost = Column(Numeric(12, 2), default=0)
    status = Column(String(32), default="open")
    assigned_to = Column(String(128))
    resolved_at = Column(DateTime(timezone=True))
    notes = Column(Text)
    raw_data = Column(JSONB)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class CustomerLossEvent(Base):
    """Customer loss events — unified analysis table for AI."""
    __tablename__ = "customer_loss_events"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    store_id = Column(String(64), ForeignKey("stores.store_id"), nullable=False)
    marketplace_id = Column(String(32), default="ATVPDKIKX0DER")
    return_id = Column(String(128))
    refund_id = Column(String(128))
    amazon_order_id = Column(String(64))
    sku = Column(String(255))
    asin = Column(String(32))
    loss_type = Column(String(32), nullable=False)
    amount_loss = Column(Numeric(12, 2), default=0)
    currency = Column(String(8), default="USD")
    reason = Column(Text)
    event_date = Column(DateTime(timezone=True))
    linked_to_return = Column(Boolean, default=False)
    linked_to_refund = Column(Boolean, default=False)
    is_anomaly = Column(Boolean, default=False)
    anomaly_reason = Column(Text)
    raw_data = Column(JSONB)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class SeasonalityIndex(Base):
    """Monthly demand multipliers per SKU."""
    __tablename__ = "seasonality_index"
    __table_args__ = (
        UniqueConstraint("store_id", "sku", "month"),
    )
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    store_id = Column(String(64), ForeignKey("stores.store_id"), nullable=False)
    sku = Column(String(255), nullable=False)
    month = Column(Integer, nullable=False)
    seasonality_multiplier = Column(Numeric(6, 4), nullable=False, default=1.0)
    historical_sales_index = Column(Numeric(6, 4))
    confidence_score = Column(Numeric(5, 4))
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class InventoryForecastDaily(Base):
    """Daily demand + coverage tracking."""
    __tablename__ = "inventory_forecast_daily"
    __table_args__ = (
        UniqueConstraint("store_id", "sku", "forecast_date"),
    )
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    store_id = Column(String(64), ForeignKey("stores.store_id"), nullable=False)
    marketplace_id = Column(String(32), default="ATVPDKIKX0DER")
    sku = Column(String(255), nullable=False)
    forecast_date = Column(Date, nullable=False)
    on_hand = Column(Integer, default=0)
    inbound_units = Column(Integer, default=0)
    daily_demand_base = Column(Numeric(10, 4), default=0)
    daily_demand_ads = Column(Numeric(10, 4), default=0)
    daily_demand_promo = Column(Numeric(10, 4), default=0)
    total_demand = Column(Numeric(10, 4), default=0)
    days_of_cover = Column(Numeric(10, 2), default=0)
    lead_time_days = Column(Integer, default=60)
    reorder_status = Column(String(16))
    risk_level = Column(String(16))
    estimated_stockout_date = Column(Date)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class DemandForecastAdjusted(Base):
    """Seasonality + events + ads combined demand."""
    __tablename__ = "demand_forecast_adjusted"
    __table_args__ = (
        UniqueConstraint("store_id", "sku", "forecast_date"),
    )
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    store_id = Column(String(64), ForeignKey("stores.store_id"), nullable=False)
    sku = Column(String(255), nullable=False)
    forecast_date = Column(Date, nullable=False)
    base_demand = Column(Numeric(10, 4), default=0)
    seasonality_multiplier = Column(Numeric(6, 4), default=1.0)
    event_multiplier = Column(Numeric(6, 4), default=1.0)
    ads_multiplier = Column(Numeric(6, 4), default=1.0)
    promo_multiplier = Column(Numeric(6, 4), default=1.0)
    final_demand = Column(Numeric(10, 4), default=0)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class InventoryRiskForecast(Base):
    """Per-SKU risk summary."""
    __tablename__ = "inventory_risk_forecast"
    __table_args__ = (
        UniqueConstraint("store_id", "sku"),
    )
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    store_id = Column(String(64), ForeignKey("stores.store_id"), nullable=False)
    sku = Column(String(255), nullable=False)
    current_on_hand = Column(Integer, default=0)
    days_of_cover_normal = Column(Numeric(10, 2), default=0)
    days_of_cover_peak = Column(Numeric(10, 2), default=0)
    stockout_risk_normal = Column(String(16))
    stockout_risk_peak = Column(String(16))
    recommended_po_qty = Column(Integer, default=0)
    recommended_order_date = Column(Date)
    estimated_stockout_normal = Column(Date)
    estimated_stockout_peak = Column(Date)
    lead_time_days = Column(Integer, default=60)
    confidence_score = Column(Numeric(5, 4))
    status = Column(String(32), default="active")
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow)


def create_all_tables():
    """Create all tables (for development/testing)."""
    Base.metadata.create_all(bind=get_engine())
