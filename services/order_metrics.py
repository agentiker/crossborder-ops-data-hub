"""Deterministic order metrics (GMV, units sold, top SKUs).

口径（已与业务确认）：
- 已付款订单：`paid_time` 非空且落在统计窗口内（按 paid_time 归日，**印尼当地时间 UTC+7**）。
- GMV：订单 `total_amount`（买家实付）求和。
- 销量：line_item 条数（每条 = 售出一件）。

时区：paid_time 存 naive UTC，归日按印尼 UTC+7（见 core.timezone）。窗口边界经 paid_window_utc
转成 UTC 查询；趋势按天归日在 Python 端用 to_business_day 完成（规避 SQL date() 的方言差异）。
公式全部在此用确定性 SQL/Python 实现，AI 仅解释结果。
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import func

from core.db import SessionLocal
from core.timezone import intraday_window_utc, paid_window_utc, to_business_day
from models.base_models import OrderHeader, OrderLineItem


def _to_float(value) -> float:
    if isinstance(value, Decimal):
        return float(value)
    return float(value or 0)


def _paid_window(start_date: date, end_date: date):
    """业务日闭区间 → naive UTC 查询边界（按印尼 UTC+7 归日，见 core.timezone）。"""
    return paid_window_utc(start_date, end_date)


def _scope_filters(query, model, platform, country, shop_id, shop_ids=None):
    if platform:
        query = query.filter(model.platform == platform)
    if country:
        query = query.filter(model.country == country)
    # shop_ids（集合）优先于单值 shop_id；两者皆空则不按店过滤
    if shop_ids:
        query = query.filter(model.shop_id.in_(shop_ids))
    elif shop_id:
        query = query.filter(model.shop_id == shop_id)
    return query


def _gmv_aggregates(
    start_dt: datetime,
    end_dt: datetime,
    platform: Optional[str],
    country: Optional[str],
    shop_id: Optional[str],
    shop_ids: Optional[list[str]],
) -> dict:
    """已付款订单 GMV/订单/销量/客单价聚合（给定 naive UTC 窗口边界）。"""
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
        header_q = _scope_filters(header_q, OrderHeader, platform, country, shop_id, shop_ids)
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
        line_q = _scope_filters(line_q, OrderHeader, platform, country, shop_id, shop_ids)
        units_sold = line_q.scalar() or 0

        order_count = int(order_count or 0)
        gmv_f = _to_float(gmv)
        avg_order_value = round(gmv_f / order_count, 2) if order_count else 0.0
        return {
            "gmv": gmv_f,
            "order_count": order_count,
            "units_sold": int(units_sold),
            "avg_order_value": avg_order_value,
        }
    finally:
        session.close()


def get_gmv_summary(
    *,
    start_date: date,
    end_date: date,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    shop_id: Optional[str] = None,
    shop_ids: Optional[list[str]] = None,
) -> dict:
    """Return paid-order GMV aggregates for AI explanation（业务日闭区间，整天口径）。"""
    start_dt, end_dt = _paid_window(start_date, end_date)
    agg = _gmv_aggregates(start_dt, end_dt, platform, country, shop_id, shop_ids)
    return {"start_date": start_date.isoformat(), "end_date": end_date.isoformat(), **agg}


def get_gmv_summary_intraday(
    *,
    day: date,
    cutoff: time,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    shop_id: Optional[str] = None,
    shop_ids: Optional[list[str]] = None,
) -> dict:
    """业务日 day 从 00:00 到 cutoff 时刻的已付款 GMV 聚合（当日累计 / 同期对比用）。"""
    start_dt, end_dt = intraday_window_utc(day, cutoff)
    agg = _gmv_aggregates(start_dt, end_dt, platform, country, shop_id, shop_ids)
    return {"start_date": day.isoformat(), "end_date": day.isoformat(),
            "cutoff": cutoff.strftime("%H:%M"), **agg}


def get_top_skus(
    *,
    start_date: date,
    end_date: date,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    shop_id: Optional[str] = None,
    shop_ids: Optional[list[str]] = None,
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
        query = _scope_filters(query, OrderHeader, platform, country, shop_id, shop_ids)
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


def get_units_by_sku(
    *,
    start_date: date,
    end_date: date,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    shop_id: Optional[str] = None,
    shop_ids: Optional[list[str]] = None,
) -> dict[str, int]:
    """窗口内各 SKU 的已付款销量（line_item 条数），返回 {sku_id: units}。

    口径同 get_top_skus（已付款订单、销量=line_item 条数），但不排序/不截断，
    供低库存预警折算日均销速用。无销量的 SKU 不在返回中。
    """
    start_dt, end_dt = _paid_window(start_date, end_date)
    session = SessionLocal()
    try:
        query = (
            session.query(
                OrderLineItem.sku_id,
                func.count(OrderLineItem.line_item_id),
            )
            .join(OrderHeader, OrderLineItem.order_id == OrderHeader.order_id)
            .filter(
                OrderHeader.paid_time.isnot(None),
                OrderHeader.paid_time >= start_dt,
                OrderHeader.paid_time <= end_dt,
            )
        )
        query = _scope_filters(query, OrderHeader, platform, country, shop_id, shop_ids)
        query = query.group_by(OrderLineItem.sku_id)
        return {sku_id: int(units or 0) for sku_id, units in query.all() if sku_id}
    finally:
        session.close()


def get_gmv_trend(
    *,
    start_date: date,
    end_date: date,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    shop_id: Optional[str] = None,
    shop_ids: Optional[list[str]] = None,
) -> list[dict]:
    """Return a per-day paid-order trend over [start_date, end_date].

    口径与 get_gmv_summary 一致（已付款 = paid_time 非空且落在窗口内，按 paid_time 归日；
    GMV = total_amount 求和；销量 = 已付款订单的 line_item 条数）。

    归日按**印尼当地时间 UTC+7**，在 Python 端用 to_business_day 完成（不在 SQL 里做
    date(paid_time + interval)，规避 SQLite/MySQL 的方言差异）。对窗口内没有订单的日期补 0，
    返回连续日序列，便于直接画趋势。
    """
    start_dt, end_dt = _paid_window(start_date, end_date)
    session = SessionLocal()
    try:
        # 每笔已付款订单的 paid_time + 金额（Python 端按印尼归日聚合）
        header_q = _scope_filters(
            session.query(OrderHeader.paid_time, OrderHeader.total_amount).filter(
                OrderHeader.paid_time.isnot(None),
                OrderHeader.paid_time >= start_dt,
                OrderHeader.paid_time <= end_dt,
            ),
            OrderHeader,
            platform,
            country,
            shop_id,
            shop_ids,
        )
        gmv_by_day: dict[date, list] = {}
        for paid_time, amount in header_q.all():
            d = to_business_day(paid_time)
            agg = gmv_by_day.setdefault(d, [0.0, 0])
            agg[0] += _to_float(amount)
            agg[1] += 1

        # 每日销量 = 已付款订单下的 line_item 条数，按订单 paid_time 归日
        line_q = _scope_filters(
            session.query(OrderHeader.paid_time)
            .join(OrderLineItem, OrderLineItem.order_id == OrderHeader.order_id)
            .filter(
                OrderHeader.paid_time.isnot(None),
                OrderHeader.paid_time >= start_dt,
                OrderHeader.paid_time <= end_dt,
            ),
            OrderHeader,
            platform,
            country,
            shop_id,
            shop_ids,
        )
        units_by_day: dict[date, int] = {}
        for (paid_time,) in line_q.all():
            d = to_business_day(paid_time)
            units_by_day[d] = units_by_day.get(d, 0) + 1

        points: list[dict] = []
        cursor = start_date
        while cursor <= end_date:
            gmv, order_count = gmv_by_day.get(cursor, (0.0, 0))
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
