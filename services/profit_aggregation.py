"""预估利润聚合（阶段3a 核心）：把多源数据组装成一条 ProfitRecordInput（折 CNY）。

利润 = GMV − 扣点 − 广告费 − 产品成本(含运费) − 预估退货，统一折 CNY。

各分项数据源（对某业务日 D × 店）：
- GMV(IDR) / order_count / units_sold ← order_metrics.get_gmv_summary（已付款口径）
- 扣点(IDR) = 未结算订单预估扣点(FactUnsettledFee) + 已结算订单真实扣点(FactFinanceTransaction)
  · 同一订单不会同时在两表（结算后从 unsettled 消失），取数时按 order_id 去重避免短暂并存重复
  · 扣点口径 = 全部费税 − 三项广告费（广告费单列，避免双算）
- 广告费(IDR) = 未结算三项广告费 + 已结算三项广告费（双源同期，与扣点对称；不用结算口径
  fact_ad_spend_daily 以免「昨日广告费」滞后虚低）
- 产品成本(RMB) = Σ(seller_sku 销量 × 单位成本RMB)；缺成本 SKU 计 0（不阻断，记日志）
- 预估退货(IDR) = 退货率 × GMV（率优先真实历史率、回落配置率，见 return_rate.get_effective_return_rate）

符号假设（生产店复验）：fee/广告费在 fact 表中为正 = 对卖家扣款（费用），直接作为成本项；
estimated_fee_amount 同此口径。沙箱无数据，符号/字段命名以 hp 生产店真打校核为准。
"""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import func

from analytics.profit_alerts import ProfitRecordInput
from core.db import SessionLocal
from models.base_models import FactFinanceTransaction, FactUnsettledFee
from services import order_metrics, product_cost_store, return_rate
from services.fx_rate import convert_idr_to_rmb

logger = logging.getLogger(__name__)

_AD_COLS = ("gmv_max_fee", "tap_commission", "affiliate_commission")


def _D(value) -> Decimal:
    if value is None:
        return Decimal("0")
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _scope(query, model, *, platform, country, shop_id, seller_id, account_id):
    # platform/metric_date 已在调用处 filter；此处只夹紧 country/shop/seller/account 维度。
    if country:
        query = query.filter(model.country == country)
    if shop_id is not None:
        query = query.filter(model.shop_id == shop_id)
    if seller_id is not None:
        query = query.filter(model.seller_id == seller_id)
    if account_id is not None:
        query = query.filter(model.account_id == account_id)
    return query


def _unsettled_fees(session, metric_date, *, platform, country, shop_id, seller_id, account_id):
    """返回 (未结算扣点不含广告 IDR, 未结算广告 IDR, 未结算 order_id 集)。"""
    q = session.query(
        func.coalesce(func.sum(FactUnsettledFee.estimated_fee_amount), 0),
        *[func.coalesce(func.sum(getattr(FactUnsettledFee, c)), 0) for c in _AD_COLS],
    ).filter(
        FactUnsettledFee.platform == platform,
        FactUnsettledFee.metric_date == metric_date,
    )
    q = _scope(q, FactUnsettledFee, platform=platform, country=country,
               shop_id=shop_id, seller_id=seller_id, account_id=account_id)
    row = q.one()
    fee_total = _D(row[0])
    ads_total = sum((_D(v) for v in row[1:]), Decimal("0"))

    id_q = session.query(FactUnsettledFee.order_id).filter(
        FactUnsettledFee.platform == platform,
        FactUnsettledFee.metric_date == metric_date,
    )
    id_q = _scope(id_q, FactUnsettledFee, platform=platform, country=country,
                  shop_id=shop_id, seller_id=seller_id, account_id=account_id)
    order_ids = {oid for (oid,) in id_q.all() if oid}
    return fee_total - ads_total, ads_total, order_ids


def _settled_fees(session, metric_date, exclude_order_ids, *, platform, country, shop_id, seller_id, account_id):
    """返回 (已结算扣点不含广告 IDR, 已结算广告 IDR)；排除已在 unsettled 的 order_id。"""
    q = session.query(
        func.coalesce(func.sum(FactFinanceTransaction.fee_tax_amount), 0),
        *[func.coalesce(func.sum(getattr(FactFinanceTransaction, c)), 0) for c in _AD_COLS],
    ).filter(
        FactFinanceTransaction.platform == platform,
        FactFinanceTransaction.metric_date == metric_date,
    )
    q = _scope(q, FactFinanceTransaction, platform=platform, country=country,
               shop_id=shop_id, seller_id=seller_id, account_id=account_id)
    if exclude_order_ids:
        q = q.filter(
            (FactFinanceTransaction.order_id.is_(None))
            | (FactFinanceTransaction.order_id.notin_(exclude_order_ids))
        )
    row = q.one()
    fee_total = _D(row[0])
    ads_total = sum((_D(v) for v in row[1:]), Decimal("0"))
    return fee_total - ads_total, ads_total


def compute_daily_profit(
    *,
    metric_date: date,
    platform: str,
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
    session=None,
) -> ProfitRecordInput:
    """聚合某业务日 × 店的预估利润，返回 ProfitRecordInput（各金额已折 CNY，profit_kind=estimated）。"""
    own = session is None
    session = session or SessionLocal()
    try:
        # GMV 按下单口径（create_time 归日），与扣点 metric_date(创建日) 同队列：
        # 本店 ~75% 是 COD，付款口径会漏算在途 COD 单 → fee(创建日)÷GMV(付款日) 佣金率虚高。
        gmv = order_metrics.get_gmv_summary(
            start_date=metric_date, end_date=metric_date,
            platform=platform, country=country, shop_id=shop_id,
            by_create=True,
        )
        gmv_idr = _D(gmv.get("gmv"))
        order_count = int(gmv.get("order_count") or 0)
        units_sold = int(gmv.get("units_sold") or 0)

        un_fee, un_ads, un_orders = _unsettled_fees(
            session, metric_date, platform=platform, country=country,
            shop_id=shop_id, seller_id=seller_id, account_id=account_id,
        )
        st_fee, st_ads = _settled_fees(
            session, metric_date, un_orders, platform=platform, country=country,
            shop_id=shop_id, seller_id=seller_id, account_id=account_id,
        )
        commission_idr = un_fee + st_fee
        ad_idr = un_ads + st_ads

        # 产品成本（RMB，不折）：按 seller_sku 销量 × 单位成本
        units_by_sku = order_metrics.get_units_by_seller_sku(
            start_date=metric_date, end_date=metric_date,
            platform=platform, country=country, shop_id=shop_id, session=session,
            by_create=True,
        )
        cost_map = product_cost_store.get_cost_map(
            account_id=account_id, platform=platform, session=session,
        )
        product_cost_rmb = Decimal("0")
        missing: list[str] = []
        for sku, units in units_by_sku.items():
            unit_cost = cost_map.get(sku)
            if unit_cost is None:
                missing.append(sku)
                continue
            product_cost_rmb += unit_cost * Decimal(units)
        if missing:
            logger.warning(
                "profit %s shop=%s 缺成本 SKU %d 个（计 0）: %s",
                metric_date, shop_id, len(missing), missing[:10],
            )

        # 预估退货（IDR）= 退货率 × GMV。率优先用真实历史率（近30天该店真实退货率），
        # 算不出（样本不足）回落配置率。见 return_rate.get_effective_return_rate。
        rate = return_rate.get_effective_return_rate(
            account_id=account_id, platform=platform,
            country=country, shop_id=shop_id, as_of=metric_date, session=session,
        )
        refund_idr = gmv_idr * rate

        # 折 CNY（成本本就是 RMB）
        return ProfitRecordInput(
            metric_date=metric_date,
            platform=platform,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
            internal_sku=None,
            order_count=order_count,
            units_sold=units_sold,
            gmv=convert_idr_to_rmb(gmv_idr, metric_date),
            commission_fee=convert_idr_to_rmb(commission_idr, metric_date),
            ad_cost=convert_idr_to_rmb(ad_idr, metric_date),
            product_cost=product_cost_rmb,
            refund_amount=convert_idr_to_rmb(refund_idr, metric_date),
            currency="CNY",
            profit_kind="estimated",
        )
    finally:
        if own:
            session.close()
