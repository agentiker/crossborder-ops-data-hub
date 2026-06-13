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
from models.base_models import Alert, DailyProfit, FulfillmentAlertState


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


def build_alert_state_key(
    *, alert_type: str, account_id: Optional[str], scope_key: Optional[str]
) -> str:
    """去重状态主键：alert_type|account_id|scope_key（scope_key 空串=全量范围）。"""
    return f"{alert_type}|{account_id or ''}|{scope_key or ''}"


def get_fulfillment_alert_state(
    session, *, alert_type: str, account_id: Optional[str], scope_key: Optional[str]
) -> Optional[FulfillmentAlertState]:
    """读某收件人范围的去重状态；无则返回 None（调用方按 last_reported_overdue=0 处理）。"""
    state_key = build_alert_state_key(
        alert_type=alert_type, account_id=account_id, scope_key=scope_key
    )
    return (
        session.query(FulfillmentAlertState).filter_by(state_key=state_key).first()
    )


def upsert_fulfillment_alert_state(
    session,
    *,
    alert_type: str,
    account_id: Optional[str],
    scope_key: Optional[str],
    last_reported_overdue: int,
    last_critical: int = 0,
    mark_sent: bool = False,
) -> FulfillmentAlertState:
    """写回去重游标。mark_sent=True 时刷新 last_sent_at（仅在真正推送后置位）。"""
    from datetime import datetime, timezone

    state_key = build_alert_state_key(
        alert_type=alert_type, account_id=account_id, scope_key=scope_key
    )
    existing = (
        session.query(FulfillmentAlertState).filter_by(state_key=state_key).first()
    )
    values = {
        "state_key": state_key,
        "alert_type": alert_type,
        "account_id": account_id,
        "scope_key": scope_key,
        "last_reported_overdue": last_reported_overdue,
        "last_critical": last_critical,
    }
    if mark_sent:
        values["last_sent_at"] = datetime.now(timezone.utc).replace(tzinfo=None)
    if existing:
        for key, value in values.items():
            setattr(existing, key, value)
        result = existing
    else:
        result = FulfillmentAlertState(**values)
        session.add(result)
    session.flush()
    return result
