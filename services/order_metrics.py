"""Deterministic order metrics (GMV, units sold, top SKUs).

口径（已与业务确认）：
- 已付款订单：`paid_time` 非空且落在统计窗口内（按 paid_time 归日，**印尼当地时间 UTC+7**）。
- GMV：订单 `total_amount`（买家实付）求和。
- 销量：line_item 条数（每条 = 售出一件）；**展示口径下销量单独收紧**——排除取消/未付款单，
  对齐 TikTok 后台 Analytics 的 Items sold（GMV/订单数仍含取消，见 `_units_status_filter`）。

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
from core.timezone import (
    business_hour_now,
    business_today,
    intraday_window_utc,
    paid_window_utc,
    to_business_day,
    to_business_hour,
)
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


def _time_filter(by_create: bool, start_dt: datetime, end_dt: datetime, display: bool = False):
    """归日时间过滤：默认按 paid_time（已付款口径，ROAS 用）；by_create/display 按 create_time
    （下单口径）。三套语义：
    - display=True（展示口径）：create_time 归日、**不排除 CANCELLED**（含所有状态，对齐 TikTok
      后台 GMV），含 COD 在途单。
    - by_create=True 且 not display（利润口径）：create_time 归日、**排除 CANCELLED**、含 COD 在途。
    - 两者皆 False（付款/ROAS 口径）：paid_time 非空归日。
    create_time 与 paid_time 同为 naive UTC，归日窗口边界一致复用。"""
    if by_create or display:
        conds = [
            OrderHeader.create_time >= start_dt,
            OrderHeader.create_time <= end_dt,
        ]
        if not display:  # 利润口径排除取消；展示口径含取消（后台不扣）
            conds.append(OrderHeader.order_status != "CANCELLED")
        return conds
    return [
        OrderHeader.paid_time.isnot(None),
        OrderHeader.paid_time >= start_dt,
        OrderHeader.paid_time <= end_dt,
    ]


def _time_col(by_create: bool, display: bool = False):
    """归日/归时用的时间列：下单/展示口径取 create_time，付款口径取 paid_time。
    与 _time_filter 配套——趋势类在 Python 端按该列 to_business_day/hour 归桶。"""
    return OrderHeader.create_time if (by_create or display) else OrderHeader.paid_time


# 展示口径下「销量（件）」额外排除的订单状态：与 TikTok 后台 Analytics 的 Items sold 对齐。
# 后台 Items sold/Orders 是「已付款」口径（官方定义 "paid SKU orders placed"），排除未付款与
# 取消单；而我们展示口径的 GMV/订单数刻意保留取消单（对齐后台 GMV「含退款退货」及订单管理列表）。
# 故仅销量单独收紧：display=True 时排除 CANCELLED/UNPAID，其余口径（付款/下单）不受影响。
_UNITS_EXCLUDED_STATUSES = ("CANCELLED", "UNPAID")


def _units_status_filter(display: bool):
    """销量（line_item 计数）在展示口径下额外排除取消/未付款单；非展示口径返回空（不额外过滤）。

    只作用于「销量（件）」，不动 GMV/订单数——三处 units 查询（汇总/日趋势/时趋势）复用此函数，
    保证「销量对齐后台 Items sold、GMV/订单数仍含取消」这条口径分叉在一处定义、不散落魔法值。
    """
    if not display:
        return []
    return [OrderHeader.order_status.notin_(_UNITS_EXCLUDED_STATUSES)]


def _gmv_aggregates(
    start_dt: datetime,
    end_dt: datetime,
    platform: Optional[str],
    country: Optional[str],
    shop_id: Optional[str],
    shop_ids: Optional[list[str]],
    by_create: bool = False,
    display: bool = False,
) -> dict:
    """订单 GMV/订单/销量/客单价聚合（给定 naive UTC 窗口边界）。by_create/display 见 _time_filter。

    display=True（展示口径）金额用 sub_total（商品小计，对齐后台），回填期老单 sub_total 为 NULL
    时 coalesce 回退到 total_amount，使 GMV 从偏高平滑收敛而非暴跌到 0（回填后新单皆有值，长期无害）。
    order_count 与 GMV 共用同一 tf（display 下含取消，对齐后台）；**units_sold 在 display 下额外排除
    取消/未付款**（对齐后台 Items sold，见 `_units_status_filter`），故销量与订单数口径刻意不同。
    """
    session = SessionLocal()
    try:
        tf = _time_filter(by_create, start_dt, end_dt, display=display)
        amount_col = (
            func.coalesce(OrderHeader.sub_total, OrderHeader.total_amount)
            if display
            else OrderHeader.total_amount
        )
        header_q = session.query(
            func.coalesce(func.sum(amount_col), 0),
            func.count(OrderHeader.order_id),
        ).filter(*tf)
        header_q = _scope_filters(header_q, OrderHeader, platform, country, shop_id, shop_ids)
        gmv, order_count = header_q.one()

        # 销量（件）= 窗口内订单下的 line_item 条数；展示口径额外排除取消/未付款（对齐后台 Items sold），
        # 故 units 的过滤 = tf + _units_status_filter，而 GMV/订单数仍用原 tf（含取消）。
        line_q = (
            session.query(func.count(OrderLineItem.line_item_id))
            .join(OrderHeader, OrderLineItem.order_id == OrderHeader.order_id)
            .filter(*tf, *_units_status_filter(display))
        )
        line_q = _scope_filters(line_q, OrderHeader, platform, country, shop_id, shop_ids)
        units_sold = line_q.scalar() or 0

        # 已取消单数（仅展示口径有意义：GMV/订单数含取消，此处单列出其中取消的单数供前端灰字标注）。
        # 非展示口径（利润排除取消、付款口径无取消单）恒为 0，前端据此不显。
        cancelled_count = 0
        if display:
            cxl_q = session.query(func.count(OrderHeader.order_id)).filter(
                *tf, OrderHeader.order_status == "CANCELLED"
            )
            cxl_q = _scope_filters(cxl_q, OrderHeader, platform, country, shop_id, shop_ids)
            cancelled_count = int(cxl_q.scalar() or 0)

        order_count = int(order_count or 0)
        gmv_f = _to_float(gmv)
        avg_order_value = round(gmv_f / order_count, 2) if order_count else 0.0
        return {
            "gmv": gmv_f,
            "order_count": order_count,
            "units_sold": int(units_sold),
            "cancelled_count": cancelled_count,
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
    display: bool = False,
) -> dict:
    """Return order GMV aggregates for AI explanation（业务日闭区间，整天口径）。

    默认 by_create=False 为已付款口径（ROAS）；by_create=True 为利润口径（下单归日、排除
    CANCELLED、total_amount）；display=True 为展示口径（下单归日、含所有状态、sub_total 对齐后台）。"""
    start_dt, end_dt = _paid_window(start_date, end_date)
    agg = _gmv_aggregates(start_dt, end_dt, platform, country, shop_id, shop_ids, by_create, display)
    return {"start_date": start_date.isoformat(), "end_date": end_date.isoformat(), **agg}


def get_gmv_summary_intraday(
    *,
    day: date,
    cutoff: time,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    shop_id: Optional[str] = None,
    shop_ids: Optional[list[str]] = None,
    by_create: bool = False,
    display: bool = False,
) -> dict:
    """业务日 day 从 00:00 到 cutoff 时刻的 GMV 聚合（当日累计 / 同期对比用）。

    by_create=True 利润口径（排除 CANCELLED、total_amount）；display=True 展示口径（含所有状态、
    sub_total 对齐后台）；默认已付款口径。见 _time_filter。"""
    start_dt, end_dt = intraday_window_utc(day, cutoff)
    agg = _gmv_aggregates(start_dt, end_dt, platform, country, shop_id, shop_ids, by_create, display)
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
    by_create: bool = False,
    display: bool = False,
) -> dict:
    """业务日区间 [start_date 00:00 → end_date 的 cutoff 时刻] 的 GMV 聚合（连续区间）。

    供周维度 intraday 同期对比用：本周一 00:00~今天此刻 vs 上周一 00:00~上周同一相对日此刻。
    注意是**连续区间**——中间各天整天计入，仅末日截到 cutoff；不是"逐天截至 cutoff 求和"
    （那会漏掉中间天 cutoff 之后的单）。起点取 paid_window 的当地 00:00，终点取 intraday 的
    当地 cutoff 时点，口径与单日 intraday 完全一致。by_create/display 见 _time_filter。
    """
    start_dt, _ = paid_window_utc(start_date, start_date)
    _, end_dt = intraday_window_utc(end_date, cutoff)
    agg = _gmv_aggregates(start_dt, end_dt, platform, country, shop_id, shop_ids, by_create, display)
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
    by_create: bool = False,
) -> list[dict]:
    """Return top SKUs by units sold. by_create=True 为下单口径（见 _time_filter）。"""
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
            .filter(*_time_filter(by_create, start_dt, end_dt))
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
    by_create: bool = False,
) -> list[dict]:
    """爆款「商品」榜（按 product_id 聚合），供看板爆款卡用。by_create=True 为下单口径。

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
                *_time_filter(by_create, start_dt, end_dt),
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


def get_product_sku_breakdown(
    *,
    product_id: str,
    start_date: date,
    end_date: date,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    shop_id: Optional[str] = None,
    shop_ids: Optional[list[str]] = None,
    session=None,
    by_create: bool = False,
) -> list[dict]:
    """某商品内各 SKU 的销量/GMV（商品详情弹窗「各 SKU 占比」用）。by_create=True 为下单口径。

    按 sku_id 聚合该 product_id 下的 line_item（同 get_top_products 口径），返回
    [{sku_id, sku_name, seller_sku, units_sold, gmv}]，按销量降序。前端据此算占比条。
    """
    start_dt, end_dt = _paid_window(start_date, end_date)
    own_session = session is None
    session = session or SessionLocal()
    try:
        query = (
            session.query(
                OrderLineItem.sku_id,
                func.max(OrderLineItem.sku_name),
                func.max(OrderLineItem.seller_sku),
                func.count(OrderLineItem.line_item_id),
                func.coalesce(func.sum(OrderLineItem.sale_price), 0),
            )
            .join(OrderHeader, OrderLineItem.order_id == OrderHeader.order_id)
            .filter(
                OrderLineItem.product_id == product_id,
                *_time_filter(by_create, start_dt, end_dt),
            )
        )
        query = _scope_filters(query, OrderHeader, platform, country, shop_id, shop_ids)
        query = (
            query.group_by(OrderLineItem.sku_id)
            .order_by(func.count(OrderLineItem.line_item_id).desc())
        )
        return [
            {
                "sku_id": sku_id,
                "sku_name": sku_name,
                "seller_sku": seller_sku,
                "units_sold": int(units or 0),
                "gmv": _to_float(gmv),
            }
            for sku_id, sku_name, seller_sku, units, gmv in query.all()
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
    by_create: bool = False,
) -> dict[str, dict]:
    """窗口内各商品的销量（line_item 条数），返回 {product_id: {units, product_name}}。

    按 product_id 聚合（爆单提醒用）。product_name 取该商品任一 line_item 的名称。无销量商品
    不在返回中。by_create=True 为下单口径（见 _time_filter）。
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
            .filter(*_time_filter(by_create, start_dt, end_dt))
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
    granularity: str = "day",
    by_create: bool = False,
    display: bool = False,
) -> list[dict]:
    """Return an order trend over [start_date, end_date].

    口径与 get_gmv_summary 一致：默认已付款（paid_time 归桶）；by_create=True 利润口径
    （create_time 归桶、排除 CANCELLED，total_amount）；display=True 展示口径（create_time 归桶、
    含所有状态、sub_total 对齐后台）；销量 = 订单的 line_item 条数。

    granularity：
    - "day"（默认）：逐日点 `{date, label:None, gmv, order_count, units_sold}`，窗口内无单的日补 0。
    - "hour"：单天逐小时点 `{date, label:"HH:00", ...}`，要求 start_date==end_date（调用方保证）。
      今天只画到当前印尼小时（business_hour_now），过去某天补满 00:00–23:00 共 24 格。

    归桶按**印尼当地时间 UTC+7**，在 Python 端用 to_business_day / to_business_hour 完成（不在 SQL 里做
    date(time + interval)，规避 SQLite/MySQL 的方言差异）。返回连续序列，便于直接画趋势。
    """
    if granularity == "hour":
        return _get_gmv_trend_hourly(
            day=start_date,
            platform=platform,
            country=country,
            shop_id=shop_id,
            shop_ids=shop_ids,
            by_create=by_create,
            display=display,
        )
    start_dt, end_dt = _paid_window(start_date, end_date)
    tcol = _time_col(by_create, display)
    amount_col = (
        func.coalesce(OrderHeader.sub_total, OrderHeader.total_amount)
        if display
        else OrderHeader.total_amount
    )
    session = SessionLocal()
    try:
        # 每笔订单的归日时间 + 金额（Python 端按印尼归日聚合）
        header_q = _scope_filters(
            session.query(tcol, amount_col).filter(
                *_time_filter(by_create, start_dt, end_dt, display=display),
            ),
            OrderHeader,
            platform,
            country,
            shop_id,
            shop_ids,
        )
        gmv_by_day: dict[date, list] = {}
        for ts, amount in header_q.all():
            d = to_business_day(ts)
            agg = gmv_by_day.setdefault(d, [0.0, 0])
            agg[0] += _to_float(amount)
            agg[1] += 1

        # 每日销量（件）= 订单下的 line_item 条数，按订单归日时间归日；展示口径排除取消/未付款
        line_q = _scope_filters(
            session.query(tcol)
            .join(OrderLineItem, OrderLineItem.order_id == OrderHeader.order_id)
            .filter(
                *_time_filter(by_create, start_dt, end_dt, display=display),
                *_units_status_filter(display),
            ),
            OrderHeader,
            platform,
            country,
            shop_id,
            shop_ids,
        )
        units_by_day: dict[date, int] = {}
        for (ts,) in line_q.all():
            d = to_business_day(ts)
            units_by_day[d] = units_by_day.get(d, 0) + 1

        points: list[dict] = []
        cursor = start_date
        while cursor <= end_date:
            gmv, order_count = gmv_by_day.get(cursor, (0.0, 0))
            points.append(
                {
                    "date": cursor.isoformat(),
                    "label": None,
                    "gmv": _to_float(gmv),
                    "order_count": int(order_count or 0),
                    "units_sold": int(units_by_day.get(cursor, 0) or 0),
                }
            )
            cursor += timedelta(days=1)
        return points
    finally:
        session.close()


def _get_gmv_trend_hourly(
    *,
    day: date,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    shop_id: Optional[str] = None,
    shop_ids: Optional[list[str]] = None,
    by_create: bool = False,
    display: bool = False,
) -> list[dict]:
    """单天逐小时趋势（印尼当地小时归桶）。被 get_gmv_trend(granularity="hour") 调。

    SQL 窗口仍取整天 [day 00:00, day 23:59:59]（印尼），归桶在 Python 端用 to_business_hour。
    补零上界：day 是今天 → 补到当前印尼小时（不画未来空格）；过去某天 → 补满 24 格。
    by_create=True 利润口径（排除 CANCELLED）；display=True 展示口径（含所有状态、sub_total）。
    """
    start_dt, end_dt = _paid_window(day, day)
    tcol = _time_col(by_create, display)
    amount_col = (
        func.coalesce(OrderHeader.sub_total, OrderHeader.total_amount)
        if display
        else OrderHeader.total_amount
    )
    session = SessionLocal()
    try:
        header_q = _scope_filters(
            session.query(tcol, amount_col).filter(
                *_time_filter(by_create, start_dt, end_dt, display=display),
            ),
            OrderHeader,
            platform,
            country,
            shop_id,
            shop_ids,
        )
        gmv_by_hour: dict[datetime, list] = {}
        for ts, amount in header_q.all():
            h = to_business_hour(ts)
            agg = gmv_by_hour.setdefault(h, [0.0, 0])
            agg[0] += _to_float(amount)
            agg[1] += 1

        line_q = _scope_filters(
            session.query(tcol)
            .join(OrderLineItem, OrderLineItem.order_id == OrderHeader.order_id)
            .filter(
                *_time_filter(by_create, start_dt, end_dt, display=display),
                *_units_status_filter(display),
            ),
            OrderHeader,
            platform,
            country,
            shop_id,
            shop_ids,
        )
        units_by_hour: dict[datetime, int] = {}
        for (ts,) in line_q.all():
            h = to_business_hour(ts)
            units_by_hour[h] = units_by_hour.get(h, 0) + 1

        # 补零上界：今天只到当前小时，其它天补满 23 点。
        last_h = business_hour_now().hour if day == business_today() else 23
        points: list[dict] = []
        for hh in range(0, last_h + 1):
            bucket = datetime.combine(day, time(hh, 0))
            gmv, order_count = gmv_by_hour.get(bucket, (0.0, 0))
            points.append(
                {
                    "date": day.isoformat(),
                    "label": f"{hh:02d}:00",
                    "gmv": _to_float(gmv),
                    "order_count": int(order_count or 0),
                    "units_sold": int(units_by_hour.get(bucket, 0) or 0),
                }
            )
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
    by_create: bool = False,
) -> dict:
    """动销率 = 出单 SKU 数 / 在库 SKU 数（均按 distinct sku_id，分子分母口径自洽）。

    分子：窗口内有销量的 distinct SKU 数（OrderLineItem）。分母：当前库存快照里 distinct sku_id
    数（Inventory 表按 scope 过滤）。注意分母用 distinct（非 overview 的库存行数——同一 SKU 多仓会
    多行），保证与分子同口径、动销率不被多仓虚低。cutoff 非空时分子用周维度 intraday 连续区间
    （与周报实时口径一致）。by_create=True 为下单口径（见 _time_filter），与展示 GMV 同口径。
    """
    start_dt, end_dt = _window_bounds(start_date, end_date, cutoff)
    session = SessionLocal()
    try:
        # 分子：出单 distinct SKU 数
        active_q = (
            session.query(func.count(func.distinct(OrderLineItem.sku_id)))
            .join(OrderHeader, OrderLineItem.order_id == OrderHeader.order_id)
            .filter(*_time_filter(by_create, start_dt, end_dt))
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
    by_create: bool = False,
) -> list[dict]:
    """本周上新商品（Product.source_create_time 落窗口内）的本周销量/GMV 表现。

    上新判定与销量统计同窗：source_create_time 落 [start, end]（按 UTC 边界过滤，±时区偏移
    误差对"本周新品"判定无碍）。新品即便零销量也列出（测款失败信号），按销量降序。
    product↔line_item 用 product_id 关联（两表都有且带索引）。by_create=True 为下单口径。
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

        # 2) 这些新品在窗口内的销量/GMV（按 product_id 聚合）
        sales_q = (
            session.query(
                OrderLineItem.product_id,
                func.count(OrderLineItem.line_item_id),
                func.coalesce(func.sum(OrderLineItem.sale_price), 0),
            )
            .join(OrderHeader, OrderLineItem.order_id == OrderHeader.order_id)
            .filter(
                *_time_filter(by_create, start_dt, end_dt),
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


def get_new_product_ids(
    *,
    as_of: date,
    lookback_days: int = 30,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    shop_id: Optional[str] = None,
    shop_ids: Optional[list[str]] = None,
    session=None,
) -> set[str]:
    """近 lookback_days 天上线（Product.source_create_time 落窗口）的在售商品 product_id 集合。

    仅取 ACTIVATE（在售口径，见 docs/business-rules §3）。供爆单告警「标注新品」用——
    判某个破阈商品是不是新品，无需算曲线，故单独提供轻量集合查询。
    """
    start_dt, _ = _paid_window(as_of - timedelta(days=lookback_days - 1), as_of)
    own_session = session is None
    session = session or SessionLocal()
    try:
        q = session.query(Product.product_id).filter(
            Product.source_create_time.isnot(None),
            Product.source_create_time >= start_dt,
            Product.status == "ACTIVATE",
        )
        q = _scope_filters(q, Product, platform, country, shop_id, shop_ids)
        return {pid for (pid,) in q.all() if pid}
    finally:
        if own_session:
            session.close()


def get_new_product_trends(
    *,
    as_of: date,
    lookback_days: int = 30,
    threshold: int = 50,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    shop_id: Optional[str] = None,
    shop_ids: Optional[list[str]] = None,
    session=None,
    by_create: bool = False,
) -> list[dict]:
    """看板「近 N 天新品」卡取数：近 lookback_days 天上线的在售商品，每个带每日销量曲线 + 爆单判定。

    - 选品：Product.status='ACTIVATE' 且 source_create_time 落 [as_of-(N-1), as_of]（在售口径）。
    - 销量：默认付款口径，by_create=True 为下单口径（create_time 归桶、排除 CANCELLED），按印尼
      业务日（to_business_day）归日为每日 line_item 条数，与 get_gmv_trend 同口径。曲线画布从
      「上线业务日」（或窗口起，取较晚者）到 as_of，前段无单补 0，诚实反映「上线即起跑」。
    - burst：曲线峰值单日销量 ≥ threshold（默认 50）。爆单的优先排前，其余按总销量降序。
    - 只纳入窗口内有销量（total_units>0）的新品，避免一堆 0 行刷屏（测款未起量的暂不展示）。
    """
    window_start = as_of - timedelta(days=lookback_days - 1)
    start_dt, end_dt = _paid_window(window_start, as_of)
    tcol = _time_col(by_create)
    own_session = session is None
    session = session or SessionLocal()
    try:
        # 1) 窗口内上线的在售商品主数据（标题 / 主图 / 上线时间）
        prod_q = session.query(
            Product.product_id,
            Product.title,
            Product.main_image_url,
            Product.source_create_time,
        ).filter(
            Product.source_create_time.isnot(None),
            Product.source_create_time >= start_dt,
            Product.status == "ACTIVATE",
        )
        prod_q = _scope_filters(prod_q, Product, platform, country, shop_id, shop_ids)
        products = {
            pid: {"title": title, "image_url": img, "created": created}
            for pid, title, img, created in prod_q.all()
            if pid
        }
        if not products:
            return []

        # 2) 这些新品在窗口内的 line_item 明细，Python 端归印尼业务日聚合
        rows = (
            _scope_filters(
                session.query(
                    OrderLineItem.product_id,
                    tcol,
                    OrderLineItem.sale_price,
                    OrderLineItem.seller_sku,
                    OrderLineItem.sku_id,
                )
                .join(OrderHeader, OrderLineItem.order_id == OrderHeader.order_id)
                .filter(
                    *_time_filter(by_create, start_dt, end_dt),
                    OrderLineItem.product_id.in_(list(products.keys())),
                ),
                OrderHeader,
                platform,
                country,
                shop_id,
                shop_ids,
            ).all()
        )

        # per product 累加：每日销量 / 总销量 / 总 GMV / 款号 / 规格集合
        agg: dict[str, dict] = {}
        for pid, ts, sale_price, seller_sku, sku_id in rows:
            a = agg.setdefault(
                pid, {"by_day": {}, "units": 0, "gmv": 0.0, "seller_sku": None, "skus": set()}
            )
            d = to_business_day(ts)
            a["by_day"][d] = a["by_day"].get(d, 0) + 1
            a["units"] += 1
            a["gmv"] += _to_float(sale_price)
            if seller_sku and not a["seller_sku"]:
                a["seller_sku"] = seller_sku
            if sku_id:
                a["skus"].add(sku_id)

        items: list[dict] = []
        for pid, meta in products.items():
            a = agg.get(pid)
            if not a or a["units"] <= 0:
                continue  # 只展示已起量的新品
            launch_day = to_business_day(meta["created"])
            series_start = max(launch_day, window_start)
            # 连续日序列（含补 0），峰值 / 峰值日
            series: list[dict] = []
            peak_units, peak_date = 0, None
            cursor = series_start
            while cursor <= as_of:
                u = int(a["by_day"].get(cursor, 0))
                series.append({"date": cursor.isoformat(), "units": u})
                if u > peak_units:
                    peak_units, peak_date = u, cursor.isoformat()
                cursor += timedelta(days=1)
            items.append({
                "product_id": pid,
                "title": meta["title"] or pid,
                "seller_sku": a["seller_sku"],
                "sku_count": len(a["skus"]),
                "image_url": meta["image_url"],
                "source_create_time": meta["created"].isoformat() if meta["created"] else None,
                "days_online": max((as_of - launch_day).days, 0),
                "total_units": int(a["units"]),
                "total_gmv": _to_float(a["gmv"]),
                "series": series,
                "peak_units": int(peak_units),
                "peak_date": peak_date,
                "burst": peak_units >= threshold,
            })

        # 爆单优先，其次按总销量降序
        items.sort(key=lambda it: (it["burst"], it["total_units"]), reverse=True)
        return items
    finally:
        if own_session:
            session.close()
