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


def create_all_tables():
    """Create all tables (for development/testing)."""
    Base.metadata.create_all(bind=get_engine())
