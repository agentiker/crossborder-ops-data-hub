"""退款/取消分析指标（基于订单状态派生，确定性聚合）。

口径（数据可信是核心，见 docs/business-rules.md）：
- **退款** = `order_status = CANCELLED` 且 `paid_time IS NOT NULL`（付款后取消 = 事实退款：
  买家付了钱又取消/拒收）。金额取 `sub_total`（与展示 GMV 同口径，不含运费/税/优惠），
  按 `create_time` 归印尼日（UTC+7）。
- **发货前流失** = CANCELLED 且 `paid_time IS NULL`（下单没付 / COD 未确认），**不是退款**。
- **退款率** = 退款金额 ÷ 展示 GMV（同窗口、同 display 口径，复用 order_metrics.get_gmv_summary）。

为什么不用 TikTok return_refund 接口：实测该店近两年平台退货单数 = 0（买家不走「签收后
申请退货」流程，售后以取消完成）。故退款/退货分析统一基于订单状态派生，零新接口/表/授权。
与 profit 链里的预估 refund（率×GMV，见 services/return_rate）不同：那是利润占位预估，
本模块是真实发生的付款后取消。

时区/归日/scope 过滤全部复用 services.order_metrics 的工具，不重造。
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import func

from core.db import SessionLocal
from core.timezone import to_business_day
from models.base_models import OrderHeader, OrderLineItem, Product
from services.order_metrics import (
    _paid_window,
    _scope_filters,
    _to_float,
    get_gmv_summary,
)

# 退款判定：付款后取消。发货前流失：未付款取消（对照项，非退款）。
_CANCELLED = "CANCELLED"


def _base_cancelled_query(session, start_dt, end_dt, platform, country, shop_id, shop_ids):
    """窗口内、按 create_time 归日的 CANCELLED 订单基础查询（含付款/未付款全部取消）。"""
    q = session.query(OrderHeader).filter(
        OrderHeader.order_status == _CANCELLED,
        OrderHeader.create_time >= start_dt,
        OrderHeader.create_time <= end_dt,
    )
    return _scope_filters(q, OrderHeader, platform, country, shop_id, shop_ids)


def get_refund_summary(
    *,
    start_date: date,
    end_date: date,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    shop_id: Optional[str] = None,
    shop_ids: Optional[list[str]] = None,
) -> dict:
    """退款/取消汇总（业务日闭区间，create_time 归日）。

    返回：
    - refund_amount：付款后取消单的 sub_total 之和（事实退款金额）
    - refund_order_count：付款后取消单数
    - refund_rate：退款金额 ÷ 展示 GMV（同窗口 display 口径）；GMV 为 0 时 None
    - cancelled_total / paid_cancelled / unpaid_cancelled：取消构成拆分
    - cod_cancelled：取消单中 COD 单数
    - currency
    """
    start_dt, end_dt = _paid_window(start_date, end_date)
    session = SessionLocal()
    try:
        base = _base_cancelled_query(
            session, start_dt, end_dt, platform, country, shop_id, shop_ids
        )
        cancelled_total = base.count()
        paid_q = base.filter(OrderHeader.paid_time.isnot(None))
        paid_cancelled = paid_q.count()
        unpaid_cancelled = cancelled_total - paid_cancelled
        cod_cancelled = base.filter(OrderHeader.is_cod.is_(True)).count()

        # 退款金额 = 付款后取消单 sub_total 之和（回落 total_amount 已在入库统一，sub_total 恒有）
        amount_sum = (
            _scope_filters(
                session.query(func.coalesce(func.sum(OrderHeader.sub_total), 0)).filter(
                    OrderHeader.order_status == _CANCELLED,
                    OrderHeader.paid_time.isnot(None),
                    OrderHeader.create_time >= start_dt,
                    OrderHeader.create_time <= end_dt,
                ),
                OrderHeader,
                platform,
                country,
                shop_id,
                shop_ids,
            ).scalar()
        )
        refund_amount = _to_float(amount_sum)

        currency = (
            _scope_filters(
                session.query(OrderHeader.currency).filter(
                    OrderHeader.currency.isnot(None)
                ),
                OrderHeader,
                platform,
                country,
                shop_id,
                shop_ids,
            )
            .limit(1)
            .scalar()
        )
    finally:
        session.close()

    # 退款率分母 = 展示口径 GMV（复用 order_metrics，避免口径漂移）
    gmv = get_gmv_summary(
        start_date=start_date,
        end_date=end_date,
        platform=platform,
        country=country,
        shop_id=shop_id,
        shop_ids=shop_ids,
        display=True,
    )
    gmv_amount = gmv.get("gmv") or 0.0
    refund_rate = round(refund_amount / gmv_amount, 4) if gmv_amount else None

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "refund_amount": refund_amount,
        "refund_order_count": paid_cancelled,
        "refund_rate": refund_rate,
        "gmv": _to_float(gmv_amount),
        "cancelled_total": cancelled_total,
        "paid_cancelled": paid_cancelled,
        "unpaid_cancelled": unpaid_cancelled,
        "cod_cancelled": cod_cancelled,
        "currency": currency,
    }


def get_refund_trend(
    *,
    start_date: date,
    end_date: date,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    shop_id: Optional[str] = None,
    shop_ids: Optional[list[str]] = None,
) -> list[dict]:
    """按日退款趋势（付款后取消，create_time 归印尼日，空日补 0）。

    返回 [{date, refund_amount, refund_order_count}]。归日在 Python 端用 to_business_day，
    与 order_metrics.get_gmv_trend 同法（规避 SQL date() 方言差异）。
    """
    start_dt, end_dt = _paid_window(start_date, end_date)
    session = SessionLocal()
    try:
        rows = (
            _scope_filters(
                session.query(OrderHeader.create_time, OrderHeader.sub_total).filter(
                    OrderHeader.order_status == _CANCELLED,
                    OrderHeader.paid_time.isnot(None),
                    OrderHeader.create_time >= start_dt,
                    OrderHeader.create_time <= end_dt,
                ),
                OrderHeader,
                platform,
                country,
                shop_id,
                shop_ids,
            ).all()
        )
    finally:
        session.close()

    by_day: dict[date, list] = {}
    for ts, amount in rows:
        d = to_business_day(ts)
        agg = by_day.setdefault(d, [0.0, 0])
        agg[0] += _to_float(amount)
        agg[1] += 1

    points: list[dict] = []
    cursor = start_date
    while cursor <= end_date:
        amt, cnt = by_day.get(cursor, (0.0, 0))
        points.append(
            {
                "date": cursor.isoformat(),
                "refund_amount": _to_float(amt),
                "refund_order_count": int(cnt or 0),
            }
        )
        cursor += timedelta(days=1)
    return points


def get_refund_top_products(
    *,
    start_date: date,
    end_date: date,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    shop_id: Optional[str] = None,
    shop_ids: Optional[list[str]] = None,
    limit: int = 5,
) -> list[dict]:
    """退款归因：按商品（product_id）聚合付款后取消的退款单数 / 退款金额，降序取 Top。

    退款单数 = 该商品出现在多少张「付款后取消」单里（按 order_id 去重，一单多件同商品算一次）；
    退款金额 = 这些单里该商品行的 sale_price 之和。product_name/主图取聚合值（同 get_top_products）。
    """
    start_dt, end_dt = _paid_window(start_date, end_date)
    session = SessionLocal()
    try:
        query = (
            session.query(
                OrderLineItem.product_id,
                func.max(OrderLineItem.product_name),
                func.count(func.distinct(OrderLineItem.order_id)),
                func.coalesce(func.sum(OrderLineItem.sale_price), 0),
                func.max(Product.main_image_url),
            )
            .join(OrderHeader, OrderLineItem.order_id == OrderHeader.order_id)
            .outerjoin(
                Product,
                (Product.product_id == OrderLineItem.product_id)
                & (Product.shop_id == OrderHeader.shop_id),
            )
            .filter(
                OrderHeader.order_status == _CANCELLED,
                OrderHeader.paid_time.isnot(None),
                OrderHeader.create_time >= start_dt,
                OrderHeader.create_time <= end_dt,
            )
        )
        query = _scope_filters(query, OrderHeader, platform, country, shop_id, shop_ids)
        query = (
            query.group_by(OrderLineItem.product_id)
            .order_by(func.count(func.distinct(OrderLineItem.order_id)).desc())
            .limit(limit)
        )
        return [
            {
                "product_id": product_id,
                "product_name": product_name,
                "refund_order_count": int(cnt or 0),
                "refund_amount": _to_float(amount),
                "image_url": image_url,
            }
            for product_id, product_name, cnt, amount, image_url in query.all()
        ]
    finally:
        session.close()
