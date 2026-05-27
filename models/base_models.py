"""ORM database models for platform sync and business metrics."""

from sqlalchemy import Column, String, Integer, DateTime, UniqueConstraint, Text
from sqlalchemy import Date, JSON, Numeric
from sqlalchemy.sql import func
from core.db import Base


class Inventory(Base):
    """Latest inventory snapshot scoped by platform account."""

    __tablename__ = "inventory"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(32), nullable=False, default="tiktok_shop", index=True)
    country = Column(String(16), nullable=False, default="GLOBAL", index=True)
    shop_id = Column(String(64), index=True)
    seller_id = Column(String(64), index=True)
    account_id = Column(String(64), index=True)
    idempotency_key = Column(String(500), nullable=False, unique=True, index=True)
    sku_id = Column(String(64), nullable=False, index=True)
    product_id = Column(String(64), nullable=False, index=True)
    product_name = Column(String(500))
    sku_name = Column(String(500))
    available_stock = Column(Integer, default=0)
    reserved_stock = Column(Integer, default=0)
    warehouse_id = Column(String(64))
    source_updated_at = Column(DateTime)
    raw_response_id = Column(Integer, nullable=True)
    synced_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint(
            "platform",
            "country",
            "shop_id",
            "seller_id",
            "account_id",
            "sku_id",
            "warehouse_id",
            name="uq_inventory_scope_sku_warehouse",
        ),
    )

    def __repr__(self):
        return f"<Inventory(sku_id={self.sku_id}, stock={self.available_stock})>"


class PlatformToken(Base):
    """Platform token persisted per platform account scope."""

    __tablename__ = "platform_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(32), nullable=False, index=True)
    country = Column(String(16), nullable=False, default="GLOBAL", index=True)
    shop_id = Column(String(64), index=True)
    seller_id = Column(String(64), index=True)
    account_id = Column(String(64), index=True)
    scope_key = Column(String(500), nullable=False, unique=True, index=True)
    access_token = Column(Text)
    refresh_token = Column(Text)
    token_expire_at = Column(DateTime)
    refresh_token_expire_at = Column(DateTime)
    token_payload = Column(JSON)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<PlatformToken(scope_key={self.scope_key})>"


class RawAPIResponse(Base):
    """Raw platform API payload for audit, replay, and backfill."""

    __tablename__ = "raw_api_responses"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(32), nullable=False, index=True)
    country = Column(String(16), nullable=False, default="GLOBAL", index=True)
    shop_id = Column(String(64), index=True)
    seller_id = Column(String(64), index=True)
    account_id = Column(String(64), index=True)
    scope_key = Column(String(500), nullable=False, index=True)
    resource = Column(String(64), nullable=False, index=True)
    method = Column(String(16), nullable=False)
    path = Column(String(500), nullable=False)
    request_params = Column(JSON)
    request_body = Column(JSON)
    response_payload = Column(JSON)
    http_status = Column(Integer)
    business_code = Column(String(64))
    error = Column(Text)
    fetched_at = Column(DateTime, server_default=func.now(), index=True)

    def __repr__(self):
        return f"<RawAPIResponse(platform={self.platform}, resource={self.resource})>"


class SyncCursor(Base):
    """Incremental sync state per platform account and resource."""

    __tablename__ = "sync_cursors"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(32), nullable=False, index=True)
    country = Column(String(16), nullable=False, default="GLOBAL", index=True)
    shop_id = Column(String(64), index=True)
    seller_id = Column(String(64), index=True)
    account_id = Column(String(64), index=True)
    resource = Column(String(64), nullable=False, index=True)
    scope_key = Column(String(500), nullable=False, unique=True, index=True)
    cursor = Column(String(500))
    window_start = Column(DateTime)
    window_end = Column(DateTime)
    last_synced_at = Column(DateTime)
    extra = Column(JSON)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<SyncCursor(scope_key={self.scope_key})>"


class DailyProfit(Base):
    """Deterministic daily profit facts consumed by BI and AI tools."""

    __tablename__ = "fact_profit_daily"

    id = Column(Integer, primary_key=True, autoincrement=True)
    metric_date = Column(Date, nullable=False, index=True)
    platform = Column(String(32), nullable=False, index=True)
    country = Column(String(16), nullable=False, default="GLOBAL", index=True)
    shop_id = Column(String(64), index=True)
    seller_id = Column(String(64), index=True)
    account_id = Column(String(64), index=True)
    internal_sku = Column(String(128), index=True)
    scope_key = Column(String(500), nullable=False, unique=True, index=True)
    order_count = Column(Integer, default=0)
    units_sold = Column(Integer, default=0)
    gmv = Column(Numeric(18, 4), nullable=False, default=0)
    product_cost = Column(Numeric(18, 4), nullable=False, default=0)
    ad_cost = Column(Numeric(18, 4), nullable=False, default=0)
    logistics_cost = Column(Numeric(18, 4), nullable=False, default=0)
    commission_fee = Column(Numeric(18, 4), nullable=False, default=0)
    tax_fee = Column(Numeric(18, 4), nullable=False, default=0)
    refund_amount = Column(Numeric(18, 4), nullable=False, default=0)
    other_cost = Column(Numeric(18, 4), nullable=False, default=0)
    gross_profit = Column(Numeric(18, 4), nullable=False, default=0)
    calculated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<DailyProfit(scope_key={self.scope_key}, gross_profit={self.gross_profit})>"


class Alert(Base):
    """Business alert generated from trusted metrics."""

    __tablename__ = "fact_alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(32), nullable=False, index=True)
    country = Column(String(16), nullable=False, default="GLOBAL", index=True)
    shop_id = Column(String(64), index=True)
    seller_id = Column(String(64), index=True)
    account_id = Column(String(64), index=True)
    scope_key = Column(String(500), nullable=False, unique=True, index=True)
    metric_date = Column(Date, index=True)
    alert_type = Column(String(64), nullable=False, index=True)
    severity = Column(String(16), nullable=False, default="warning", index=True)
    title = Column(String(200), nullable=False)
    message = Column(Text)
    impact_scope = Column(String(200))
    status = Column(String(16), nullable=False, default="open", index=True)
    payload = Column(JSON)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    resolved_at = Column(DateTime)

    def __repr__(self):
        return f"<Alert(scope_key={self.scope_key}, type={self.alert_type})>"
