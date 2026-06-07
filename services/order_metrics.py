"""Deterministic order metrics (GMV, units sold, top SKUs).

口径（已与业务确认）：
- 已付款订单：`paid_time` 非空且落在统计窗口内（按 paid_time 归日）。
- GMV：订单 `total_amount`（买家实付）求和。
- 销量：line_item 条数（每条 = 售出一件）。

公式全部在此用确定性 SQL/Python 实现，AI 仅解释结果。
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import func

from core.db import SessionLocal
from models.base_models import OrderHeader, OrderLineItem


def _to_float(value) -> float:
    if isinstance(value, Decimal):
        return float(value)
    return float(value or 0)


def _paid_window(start_date: date, end_date: date):
    """Inclusive day window → [start 00:00:00, end 23:59:59] naive UTC bounds."""
    start_dt = datetime.combine(start_date, time.min)
    end_dt = datetime.combine(end_date, time.max)
    return start_dt, end_dt


def _scope_filters(query, model, platform, country, shop_id):
    if platform:
        query = query.filter(model.platform == platform)
    if country:
        query = query.filter(model.country == country)
    if shop_id:
        query = query.filter(model.shop_id == shop_id)
    return query


def get_gmv_summary(
    *,
    start_date: date,
    end_date: date,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    shop_id: Optional[str] = None,
) -> dict:
    """Return paid-order GMV aggregates for AI explanation."""
    start_dt, end_dt = _paid_window(start_date, end_date)
    session = SessionLocal()
    try:
        header_q = session.query(
            func.coalesce(func.sum(OrderHeader.total_amount), 0),
            func.count(OrderHeader.order_id),
        ).filter(
            OrderHeader.paid_time.isnot(None),
            OrderHeader.paid_time >= start_dt,
            OrderHeader.paid_time <= end_dt,
        )
        header_q = _scope_filters(header_q, OrderHeader, platform, country, shop_id)
        gmv, order_count = header_q.one()

        # 销量 = 已付款订单下的 line_item 条数
        line_q = (
            session.query(func.count(OrderLineItem.line_item_id))
            .join(OrderHeader, OrderLineItem.order_id == OrderHeader.order_id)
            .filter(
                OrderHeader.paid_time.isnot(None),
                OrderHeader.paid_time >= start_dt,
                OrderHeader.paid_time <= end_dt,
            )
        )
        line_q = _scope_filters(line_q, OrderHeader, platform, country, shop_id)
        units_sold = line_q.scalar() or 0

        order_count = int(order_count or 0)
        gmv_f = _to_float(gmv)
        avg_order_value = round(gmv_f / order_count, 2) if order_count else 0.0

        return {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "gmv": gmv_f,
            "order_count": order_count,
            "units_sold": int(units_sold),
            "avg_order_value": avg_order_value,
        }
    finally:
        session.close()


def get_top_skus(
    *,
    start_date: date,
    end_date: date,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    shop_id: Optional[str] = None,
    limit: int = 10,
) -> list[dict]:
    """Return top SKUs by units sold within paid orders."""
    start_dt, end_dt = _paid_window(start_date, end_date)
    session = SessionLocal()
    try:
        query = (
            session.query(
                OrderLineItem.sku_id,
                func.max(OrderLineItem.product_name),
                func.max(OrderLineItem.sku_name),
                func.count(OrderLineItem.line_item_id),
                func.coalesce(func.sum(OrderLineItem.sale_price), 0),
            )
            .join(OrderHeader, OrderLineItem.order_id == OrderHeader.order_id)
            .filter(
                OrderHeader.paid_time.isnot(None),
                OrderHeader.paid_time >= start_dt,
                OrderHeader.paid_time <= end_dt,
            )
        )
        query = _scope_filters(query, OrderHeader, platform, country, shop_id)
        query = (
            query.group_by(OrderLineItem.sku_id)
            .order_by(func.count(OrderLineItem.line_item_id).desc())
            .limit(limit)
        )

        return [
            {
                "sku_id": sku_id,
                "product_name": product_name,
                "sku_name": sku_name,
                "units_sold": int(units or 0),
                "gmv": _to_float(gmv),
            }
            for sku_id, product_name, sku_name, units, gmv in query.all()
        ]
    finally:
        session.close()


def get_gmv_trend(
    *,
    start_date: date,
    end_date: date,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    shop_id: Optional[str] = None,
) -> list[dict]:
    """Return a per-day paid-order trend over [start_date, end_date].

    口径与 get_gmv_summary 一致（已付款 = paid_time 非空且落在窗口内，按 paid_time 归日；
    GMV = total_amount 求和；销量 = 已付款订单的 line_item 条数）。按天 GROUP BY 后，
    对窗口内没有订单的日期补 0，返回连续日序列，便于直接画趋势。
    """
    start_dt, end_dt = _paid_window(start_date, end_date)
    session = SessionLocal()
    try:
        day = func.date(OrderHeader.paid_time)

        # 每日 GMV 与订单量（订单头维度）
        header_rows = (
            _scope_filters(
                session.query(
                    day.label("day"),
                    func.coalesce(func.sum(OrderHeader.total_amount), 0),
                    func.count(OrderHeader.order_id),
                ).filter(
                    OrderHeader.paid_time.isnot(None),
                    OrderHeader.paid_time >= start_dt,
                    OrderHeader.paid_time <= end_dt,
                ),
                OrderHeader,
                platform,
                country,
                shop_id,
            )
            .group_by(day)
            .all()
        )

        # 每日销量 = 已付款订单下的 line_item 条数（行维度）
        units_rows = (
            _scope_filters(
                session.query(
                    day.label("day"),
                    func.count(OrderLineItem.line_item_id),
                )
                .join(OrderHeader, OrderLineItem.order_id == OrderHeader.order_id)
                .filter(
                    OrderHeader.paid_time.isnot(None),
                    OrderHeader.paid_time >= start_dt,
                    OrderHeader.paid_time <= end_dt,
                ),
                OrderHeader,
                platform,
                country,
                shop_id,
            )
            .group_by(day)
            .all()
        )

        gmv_by_day = {_as_date(d): (g, c) for d, g, c in header_rows}
        units_by_day = {_as_date(d): u for d, u in units_rows}

        points: list[dict] = []
        cursor = start_date
        while cursor <= end_date:
            gmv, order_count = gmv_by_day.get(cursor, (0, 0))
            points.append(
                {
                    "date": cursor.isoformat(),
                    "gmv": _to_float(gmv),
                    "order_count": int(order_count or 0),
                    "units_sold": int(units_by_day.get(cursor, 0) or 0),
                }
            )
            cursor += timedelta(days=1)
        return points
    finally:
        session.close()


def _as_date(value) -> date:
    """Normalize a SQL DATE() result (date or 'YYYY-MM-DD' string) to date."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    return date.fromisoformat(str(value))
