"""Persistence helpers for trusted business metrics and alerts."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from analytics.profit_alerts import (
    Alert as AlertInput,
    ProfitRecordInput,
    build_alert_scope_key,
    build_profit_scope_key,
    calculate_gross_profit,
)
from models.base_models import Alert, DailyProfit


def upsert_daily_profit(session, record: ProfitRecordInput) -> DailyProfit:
    """Insert or update one daily profit fact."""
    scope_key = build_profit_scope_key(record)
    existing = session.query(DailyProfit).filter_by(scope_key=scope_key).first()
    values = {
        "metric_date": record.metric_date,
        "platform": record.platform,
        "country": record.country,
        "shop_id": record.shop_id,
        "seller_id": record.seller_id,
        "account_id": record.account_id,
        "internal_sku": record.internal_sku,
        "scope_key": scope_key,
        "order_count": record.order_count,
        "units_sold": record.units_sold,
        "gmv": record.gmv,
        "product_cost": record.product_cost,
        "ad_cost": record.ad_cost,
        "logistics_cost": record.logistics_cost,
        "commission_fee": record.commission_fee,
        "tax_fee": record.tax_fee,
        "refund_amount": record.refund_amount,
        "other_cost": record.other_cost,
        "gross_profit": calculate_gross_profit(record),
    }
    if existing:
        for key, value in values.items():
            setattr(existing, key, value)
        result = existing
    else:
        result = DailyProfit(**values)
        session.add(result)
    session.flush()
    return result


def upsert_alert(
    session,
    *,
    platform: str,
    alert: AlertInput,
    metric_date: Optional[date] = None,
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
    impact_scope: Optional[str] = None,
    payload: Optional[dict] = None,
) -> Alert:
    """Insert or update an open alert at a deterministic grain."""
    scope_key = build_alert_scope_key(
        platform=platform,
        alert_type=alert.alert_type,
        metric_date=metric_date,
        country=country,
        shop_id=shop_id,
        seller_id=seller_id,
        account_id=account_id,
        impact_scope=impact_scope,
    )
    existing = session.query(Alert).filter_by(scope_key=scope_key).first()
    values = {
        "platform": platform,
        "country": country,
        "shop_id": shop_id,
        "seller_id": seller_id,
        "account_id": account_id,
        "scope_key": scope_key,
        "metric_date": metric_date,
        "alert_type": alert.alert_type,
        "severity": alert.severity,
        "title": alert.message,
        "message": alert.message,
        "impact_scope": impact_scope,
        "status": "open",
        "payload": {
            **(payload or {}),
            "metric_value": _jsonable_decimal(alert.metric_value),
        },
    }
    if existing:
        for key, value in values.items():
            setattr(existing, key, value)
        result = existing
    else:
        result = Alert(**values)
        session.add(result)
    session.flush()
    return result


def _jsonable_decimal(value):
    if isinstance(value, Decimal):
        return str(value)
    return value
