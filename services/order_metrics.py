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
from models.base_models import Inventory, OrderHeader, OrderLineItem, Product


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


def _time_filter(by_create: bool, start_dt: datetime, end_dt: datetime):
    """归日时间过滤：默认按 paid_time（已付款口径，报表用）；by_create 按 create_time
    （下单口径，含未付款 COD 在途单、排除 CANCELLED）——供预估利润与扣点同队列对齐。
    create_time 与 paid_time 同为 naive UTC，归日窗口边界一致复用。"""
    if by_create:
        return [
            OrderHeader.create_time >= start_dt,
            OrderHeader.create_time <= end_dt,
            OrderHeader.order_status != "CANCELLED",
        ]
    return [
        OrderHeader.paid_time.isnot(None),
        OrderHeader.paid_time >= start_dt,
        OrderHeader.paid_time <= end_dt,
    ]


def _gmv_aggregates(
    start_dt: datetime,
    end_dt: datetime,
    platform: Optional[str],
    country: Optional[str],
    shop_id: Optional[str],
    shop_ids: Optional[list[str]],
    by_create: bool = False,
) -> dict:
    """订单 GMV/订单/销量/客单价聚合（给定 naive UTC 窗口边界）。by_create 见 _time_filter。"""
    session = SessionLocal()
    try:
        tf = _time_filter(by_create, start_dt, end_dt)
        header_q = session.query(
            func.coalesce(func.sum(OrderHeader.total_amount), 0),
            func.count(OrderHeader.order_id),
        ).filter(*tf)
        header_q = _scope_filters(header_q, OrderHeader, platform, country, shop_id, shop_ids)
        gmv, order_count = header_q.one()

        # 销量 = 窗口内订单下的 line_item 条数
        line_q = (
            session.query(func.count(OrderLineItem.line_item_id))
            .join(OrderHeader, OrderLineItem.order_id == OrderHeader.order_id)
            .filter(*tf)
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
    by_create: bool = False,
) -> dict:
    """Return order GMV aggregates for AI explanation（业务日闭区间，整天口径）。

    默认 by_create=False 为已付款口径（报表）；by_create=True 为下单口径（预估利润，
    含未付款 COD 在途单、排除 CANCELLED），与扣点 metric_date(创建日) 同队列。"""
    start_dt, end_dt = _paid_window(start_date, end_date)
    agg = _gmv_aggregates(start_dt, end_dt, platform, country, shop_id, shop_ids, by_create)
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


def get_gmv_summary_intraday_range(
    *,
    start_date: date,
    end_date: date,
    cutoff: time,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    shop_id: Optional[str] = None,
    shop_ids: Optional[list[str]] = None,
) -> dict:
    """业务日区间 [start_date 00:00 → end_date 的 cutoff 时刻] 的已付款 GMV 聚合（连续区间）。

    供周维度 intraday 同期对比用：本周一 00:00~今天此刻 vs 上周一 00:00~上周同一相对日此刻。
    注意是**连续区间**——中间各天整天计入，仅末日截到 cutoff；不是"逐天截至 cutoff 求和"
    （那会漏掉中间天 cutoff 之后的单）。起点取 paid_window 的当地 00:00，终点取 intraday 的
    当地 cutoff 时点，口径与单日 intraday 完全一致。
    """
    start_dt, _ = paid_window_utc(start_date, start_date)
    _, end_dt = intraday_window_utc(end_date, cutoff)
    agg = _gmv_aggregates(start_dt, end_dt, platform, country, shop_id, shop_ids)
    return {"start_date": start_date.isoformat(), "end_date": end_date.isoformat(),
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


def get_top_products(
    *,
    start_date: date,
    end_date: date,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    shop_id: Optional[str] = None,
    shop_ids: Optional[list[str]] = None,
    limit: int = 10,
    session=None,
) -> list[dict]:
    """爆款「商品」榜（按 product_id 聚合，付款口径），供看板爆款卡用。

    与 get_top_skus（SKU 粒度）并列保留：客户的「爆款商品」语义是商品级，且单品渠道拆分
    （202605）也按 product_id —— 故按 product_id 聚合，一行=一个商品。
    - 款号：seller_sku（同商品多 SKU 时取 max，前端按 sku_count>1 显「N 个规格」而非单一款号）。
    - 小图：LEFT JOIN products 取 main_image_url（同店同 product_id；多店时取任一）。
    """
    start_dt, end_dt = _paid_window(start_date, end_date)
    own_session = session is None
    session = session or SessionLocal()
    try:
        query = (
            session.query(
                OrderLineItem.product_id,
                func.max(OrderLineItem.product_name),
                func.max(OrderLineItem.seller_sku),
                func.count(func.distinct(OrderLineItem.sku_id)),
                func.count(OrderLineItem.line_item_id),
                func.coalesce(func.sum(OrderLineItem.sale_price), 0),
                func.max(Product.main_image_url),
            )
            .join(OrderHeader, OrderLineItem.order_id == OrderHeader.order_id)
            .outerjoin(
                Product,
                (Product.product_id == OrderLineItem.product_id)
                & (Product.platform == OrderLineItem.platform),
            )
            .filter(
                OrderLineItem.product_id.isnot(None),
                OrderHeader.paid_time.isnot(None),
                OrderHeader.paid_time >= start_dt,
                OrderHeader.paid_time <= end_dt,
            )
        )
        query = _scope_filters(query, OrderHeader, platform, country, shop_id, shop_ids)
        query = (
            query.group_by(OrderLineItem.product_id)
            .order_by(func.count(OrderLineItem.line_item_id).desc())
            .limit(limit)
        )

        return [
            {
                "product_id": product_id,
                "product_name": product_name,
                "seller_sku": seller_sku,
                "sku_count": int(sku_count or 0),
                "units_sold": int(units or 0),
                "gmv": _to_float(gmv),
                "image_url": image_url,
            }
            for product_id, product_name, seller_sku, sku_count, units, gmv, image_url in query.all()
        ]
    finally:
        if own_session:
            session.close()


def get_units_by_sku(
    *,
    start_date: date,
    end_date: date,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    shop_id: Optional[str] = None,
    shop_ids: Optional[list[str]] = None,
    session=None,
) -> dict[str, int]:
    """窗口内各 SKU 的已付款销量（line_item 条数），返回 {sku_id: units}。

    口径同 get_top_skus（已付款订单、销量=line_item 条数），但不排序/不截断，
    供低库存预警折算日均销速 / 补货公式用。无销量的 SKU 不在返回中。
    """
    start_dt, end_dt = _paid_window(start_date, end_date)
    own_session = session is None
    session = session or SessionLocal()
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
        if own_session:
            session.close()


def get_units_by_seller_sku(
    *,
    start_date: date,
    end_date: date,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    shop_id: Optional[str] = None,
    shop_ids: Optional[list[str]] = None,
    session=None,
    by_create: bool = False,
) -> dict[str, int]:
    """窗口内各 seller_sku 的销量（line_item 条数），返回 {seller_sku: units}。

    口径同 get_units_by_sku，但按 OrderLineItem.seller_sku 聚合——供利润聚合 join 产品成本
    （ProductCost 按 seller_sku 关联）。seller_sku 为空的行不计入（无法 join 成本→该部分成本计 0）。
    by_create=True 为下单口径（与 GMV/扣点同队列），默认已付款口径。
    """
    start_dt, end_dt = _paid_window(start_date, end_date)
    own_session = session is None
    session = session or SessionLocal()
    try:
        query = (
            session.query(
                OrderLineItem.seller_sku,
                func.count(OrderLineItem.line_item_id),
            )
            .join(OrderHeader, OrderLineItem.order_id == OrderHeader.order_id)
            .filter(*_time_filter(by_create, start_dt, end_dt))
        )
        query = _scope_filters(query, OrderHeader, platform, country, shop_id, shop_ids)
        query = query.group_by(OrderLineItem.seller_sku)
        return {sku: int(units or 0) for sku, units in query.all() if sku}
    finally:
        if own_session:
            session.close()


def get_units_by_product(
    *,
    start_date: date,
    end_date: date,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    shop_id: Optional[str] = None,
    shop_ids: Optional[list[str]] = None,
    session=None,
) -> dict[str, dict]:
    """窗口内各商品的已付款销量（line_item 条数），返回 {product_id: {units, product_name}}。

    口径同 get_units_by_sku 但按 product_id 聚合（爆单提醒用）。product_name 取该商品任一
    line_item 的名称。无销量商品不在返回中。
    """
    start_dt, end_dt = _paid_window(start_date, end_date)
    own_session = session is None
    session = session or SessionLocal()
    try:
        query = (
            session.query(
                OrderLineItem.product_id,
                func.count(OrderLineItem.line_item_id),
                func.max(OrderLineItem.product_name),
            )
            .join(OrderHeader, OrderLineItem.order_id == OrderHeader.order_id)
            .filter(
                OrderHeader.paid_time.isnot(None),
                OrderHeader.paid_time >= start_dt,
                OrderHeader.paid_time <= end_dt,
            )
        )
        query = _scope_filters(query, OrderHeader, platform, country, shop_id, shop_ids)
        query = query.group_by(OrderLineItem.product_id)
        return {
            pid: {"units": int(units or 0), "product_name": name}
            for pid, units, name in query.all()
            if pid
        }
    finally:
        if own_session:
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


def _window_bounds(start_date: date, end_date: date, cutoff: Optional[time]):
    """统一窗口边界：cutoff 非空 → 连续 intraday 区间 [start 00:00, end cutoff]；否则整天闭区间。"""
    if cutoff is not None:
        start_dt, _ = paid_window_utc(start_date, start_date)
        _, end_dt = intraday_window_utc(end_date, cutoff)
        return start_dt, end_dt
    return paid_window_utc(start_date, end_date)


def get_sell_through(
    *,
    start_date: date,
    end_date: date,
    cutoff: Optional[time] = None,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    shop_id: Optional[str] = None,
    shop_ids: Optional[list[str]] = None,
) -> dict:
    """动销率 = 出单 SKU 数 / 在库 SKU 数（均按 distinct sku_id，分子分母口径自洽）。

    分子：窗口内有已付款销量的 distinct SKU 数（OrderLineItem，口径同 get_units_by_sku）。
    分母：当前库存快照里 distinct sku_id 数（Inventory 表按 scope 过滤）。注意分母用 distinct
    （非 overview 的库存行数——同一 SKU 多仓会多行），保证与分子同口径、动销率不被多仓虚低。
    cutoff 非空时分子用周维度 intraday 连续区间（与周报实时口径一致）。
    """
    start_dt, end_dt = _window_bounds(start_date, end_date, cutoff)
    session = SessionLocal()
    try:
        # 分子：出单 distinct SKU 数
        active_q = (
            session.query(func.count(func.distinct(OrderLineItem.sku_id)))
            .join(OrderHeader, OrderLineItem.order_id == OrderHeader.order_id)
            .filter(
                OrderHeader.paid_time.isnot(None),
                OrderHeader.paid_time >= start_dt,
                OrderHeader.paid_time <= end_dt,
            )
        )
        active_q = _scope_filters(active_q, OrderHeader, platform, country, shop_id, shop_ids)
        active_sku = int(active_q.scalar() or 0)

        # 分母：在库 distinct SKU 数（当前快照，与时间窗无关）
        total_q = session.query(func.count(func.distinct(Inventory.sku_id)))
        total_q = _scope_filters(total_q, Inventory, platform, country, shop_id, shop_ids)
        total_sku = int(total_q.scalar() or 0)

        rate = round(active_sku / total_sku * 100, 1) if total_sku else None
        return {"active_sku": active_sku, "total_sku": total_sku, "rate": rate}
    finally:
        session.close()


def get_new_product_performance(
    *,
    start_date: date,
    end_date: date,
    cutoff: Optional[time] = None,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    shop_id: Optional[str] = None,
    shop_ids: Optional[list[str]] = None,
    limit: int = 20,
) -> list[dict]:
    """本周上新商品（Product.source_create_time 落窗口内）的本周销量/GMV 表现。

    上新判定与销量统计同窗：source_create_time 落 [start, end]（按 UTC 边界过滤，±时区偏移
    误差对"本周新品"判定无碍）。新品即便零销量也列出（测款失败信号），按销量降序。
    product↔line_item 用 product_id 关联（两表都有且带索引）。
    """
    start_dt, end_dt = _window_bounds(start_date, end_date, cutoff)
    session = SessionLocal()
    try:
        # 1) 窗口内上新的商品（scope 过滤）
        prod_q = session.query(Product.product_id, Product.title).filter(
            Product.source_create_time.isnot(None),
            Product.source_create_time >= start_dt,
            Product.source_create_time <= end_dt,
        )
        prod_q = _scope_filters(prod_q, Product, platform, country, shop_id, shop_ids)
        new_products = {pid: title for pid, title in prod_q.all() if pid}
        if not new_products:
            return []

        # 2) 这些新品在窗口内的已付款销量/GMV（按 product_id 聚合）
        sales_q = (
            session.query(
                OrderLineItem.product_id,
                func.count(OrderLineItem.line_item_id),
                func.coalesce(func.sum(OrderLineItem.sale_price), 0),
            )
            .join(OrderHeader, OrderLineItem.order_id == OrderHeader.order_id)
            .filter(
                OrderHeader.paid_time.isnot(None),
                OrderHeader.paid_time >= start_dt,
                OrderHeader.paid_time <= end_dt,
                OrderLineItem.product_id.in_(list(new_products.keys())),
            )
        )
        sales_q = _scope_filters(sales_q, OrderHeader, platform, country, shop_id, shop_ids)
        sales = {
            pid: (int(units or 0), _to_float(gmv))
            for pid, units, gmv in sales_q.group_by(OrderLineItem.product_id).all()
        }

        items = []
        for pid, title in new_products.items():
            units, gmv = sales.get(pid, (0, 0.0))
            items.append({
                "product_id": pid,
                "title": title or pid,
                "units_sold": units,
                "gmv": gmv,
            })
        items.sort(key=lambda it: (it["units_sold"], it["gmv"]), reverse=True)
        return items[:limit]
    finally:
        session.close()
