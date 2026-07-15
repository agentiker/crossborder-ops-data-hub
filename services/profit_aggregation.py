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
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import func

from analytics.profit_alerts import ProfitRecordInput
from core.config import settings
from core.db import SessionLocal
from core.timezone import business_today, paid_window_utc
from models.base_models import FactFinanceTransaction, FactUnsettledFee, OrderHeader
from services import order_metrics, product_cost_store, refund_metrics, return_rate
from services.fx_rate import convert_idr_to_rmb

logger = logging.getLogger(__name__)

_AD_COLS = ("gmv_max_fee", "tap_commission", "affiliate_commission")
_TODAY_OFFICIAL_FEE_MIN_ORDER_COVERAGE = Decimal("0.50")


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


def _settled_fee_order_ids(
    session, metric_date, exclude_order_ids, *, platform, country, shop_id, seller_id, account_id
) -> set[str]:
    q = session.query(FactFinanceTransaction.order_id).filter(
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
    return {oid for (oid,) in q.all() if oid}


def _historical_settled_commission_rate(
    session,
    *,
    as_of: date,
    platform: str,
    country: str,
    shop_id: Optional[str],
    seller_id: Optional[str],
    account_id: Optional[str],
) -> Optional[dict]:
    """用已结算历史订单估算非广告扣点率（扣点，不含广告费）。

    当天 TikTok 未结算费用通常要次日凌晨才基本同步完整。若当天只有少量官方费用行，
    利润卡用它会把扣点压到接近 0；此时用结算完整窗口的历史扣点率作为当天占位估算。
    """
    baseline_end = as_of - timedelta(days=settings.fee_rate_settle_lag_days)
    baseline_start = baseline_end - timedelta(days=settings.fee_rate_baseline_days - 1)
    start_dt, end_dt = paid_window_utc(baseline_start, baseline_end)

    order_q = session.query(
        OrderHeader.order_id,
        OrderHeader.currency,
        OrderHeader.total_amount,
    ).filter(
        OrderHeader.paid_time.isnot(None),
        OrderHeader.paid_time >= start_dt,
        OrderHeader.paid_time <= end_dt,
    )
    order_q = _scope(order_q, OrderHeader, platform=platform, country=country,
                     shop_id=shop_id, seller_id=seller_id, account_id=account_id)
    orders = order_q.all()
    if not orders:
        return None
    gmv_by_order = {oid: (ccy, _D(amt)) for oid, ccy, amt in orders if oid}
    if not gmv_by_order:
        return None

    fee_q = session.query(
        FactFinanceTransaction.order_id,
        func.sum(
            func.coalesce(FactFinanceTransaction.fee_tax_amount, 0)
            - func.coalesce(FactFinanceTransaction.gmv_max_fee, 0)
            - func.coalesce(FactFinanceTransaction.tap_commission, 0)
            - func.coalesce(FactFinanceTransaction.affiliate_commission, 0)
        ).label("commission_fee"),
    ).filter(FactFinanceTransaction.order_id.in_(list(gmv_by_order)))
    fee_q = _scope(fee_q, FactFinanceTransaction, platform=platform, country=country,
                   shop_id=shop_id, seller_id=seller_id, account_id=account_id)
    fee_q = fee_q.group_by(FactFinanceTransaction.order_id)

    buckets: dict[str, dict] = {}
    for oid, fee in fee_q.all():
        ccy, gmv = gmv_by_order.get(oid, (None, Decimal("0")))
        if not ccy or gmv <= 0:
            continue
        agg = buckets.setdefault(
            ccy, {"gmv": Decimal("0"), "commission_fee": Decimal("0"), "order_count": 0}
        )
        agg["gmv"] += gmv
        agg["commission_fee"] += _D(fee)
        agg["order_count"] += 1
    if not buckets:
        return None

    currency, agg = max(buckets.items(), key=lambda kv: kv[1]["gmv"])
    if agg["gmv"] < Decimal(str(settings.fee_rate_min_gmv)):
        return None
    if agg["commission_fee"] <= 0:
        return None
    return {
        "currency": currency,
        "rate": agg["commission_fee"] / agg["gmv"],
        "gmv": agg["gmv"],
        "order_count": agg["order_count"],
        "window": f"{baseline_start.isoformat()}~{baseline_end.isoformat()}",
    }


def unsettled_open_count(session, metric_date, *, platform, country, shop_id, seller_id, account_id) -> int:
    """该店该业务日仍未结算的行数。0 = 订单已全部结算完毕（结算完整），可回填 settled 真实利润。"""
    q = session.query(func.count()).select_from(FactUnsettledFee).filter(
        FactUnsettledFee.platform == platform,
        FactUnsettledFee.metric_date == metric_date,
    )
    q = _scope(q, FactUnsettledFee, platform=platform, country=country,
               shop_id=shop_id, seller_id=seller_id, account_id=account_id)
    return int(q.scalar() or 0)


def compute_daily_profit(
    *,
    metric_date: date,
    platform: str,
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
    kind: str = "estimated",
    session=None,
) -> ProfitRecordInput:
    """聚合某业务日 × 店的利润，返回 ProfitRecordInput（各金额已折 CNY）。

    kind:
    - "estimated"（默认）：扣点/广告 = 未结算预估 + 已结算真实（双源混用，因近期订单未结算完），
      退货 = 退货率 × GMV（预估占位）。由 aggregate_profit flow 每日聚合前一日。
    - "settled"（结算真实）：只对结算完整的历史天（metric_date ≤ today − settle_lag、
      未结算已清零）调用。扣点/广告 = 纯 FactFinanceTransaction 真实结算值（无预估成分），
      退货 = 真实退货金额（付款后取消 sub_total，refund_metrics）。GMV / 产品成本两分支同源
      （本就真实、不随结算变）。由 backfill_settled_profit flow 回填。
    """
    if kind not in ("estimated", "settled"):
        raise ValueError(f"unknown profit kind: {kind!r}")
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

        if kind == "settled":
            # 结算完整的历史天：未结算已清零，扣点/广告全取纯真实结算值（无预估成分）。
            st_fee, st_ads = _settled_fees(
                session, metric_date, set(), platform=platform, country=country,
                shop_id=shop_id, seller_id=seller_id, account_id=account_id,
            )
            commission_idr = st_fee
            ad_idr = st_ads
        else:
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
            official_fee_order_ids = un_orders | _settled_fee_order_ids(
                session, metric_date, un_orders, platform=platform, country=country,
                shop_id=shop_id, seller_id=seller_id, account_id=account_id,
            )
            commission_fee_source = "official"
            commission_fee_source_label = "TikTok官方费用"
            commission_fee_rate = None
            commission_fee_baseline_window = None
            coverage_order_count = len(official_fee_order_ids)
            coverage_ratio = (
                Decimal(coverage_order_count) / Decimal(order_count)
                if order_count > 0 else None
            )
            if (
                metric_date == business_today()
                and order_count > 0
                and coverage_ratio is not None
                and coverage_ratio < _TODAY_OFFICIAL_FEE_MIN_ORDER_COVERAGE
            ):
                historical = _historical_settled_commission_rate(
                    session,
                    as_of=metric_date,
                    platform=platform,
                    country=country,
                    shop_id=shop_id,
                    seller_id=seller_id,
                    account_id=account_id,
                )
                if historical:
                    commission_idr = gmv_idr * historical["rate"]
                    commission_fee_source = "historical_settled_rate_estimate"
                    commission_fee_source_label = "历史已结算扣点率估算"
                    commission_fee_rate = historical["rate"]
                    commission_fee_baseline_window = historical["window"]

        # 产品成本（RMB，不折）：按 seller_sku 销量 × 单位成本
        units_by_sku = order_metrics.get_units_by_seller_sku(
            start_date=metric_date, end_date=metric_date,
            platform=platform, country=country, shop_id=shop_id, session=session,
            by_create=True,
        )
        cost_map = product_cost_store.get_cost_map_asof(
            account_id=account_id, platform=platform, metric_date=metric_date, session=session,
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

        if kind == "settled":
            # 真实退货金额（付款后取消 sub_total，IDR，同 display 口径）。结算完整的天退货
            # 窗口也已过，真实退货已发生完，比"率×GMV"预估更准。见 refund_metrics。
            refund_summary = refund_metrics.get_refund_summary(
                start_date=metric_date, end_date=metric_date,
                platform=platform, country=country, shop_id=shop_id,
            )
            refund_idr = _D(refund_summary.get("refund_amount"))
        else:
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
            profit_kind=kind,
            commission_fee_source=(
                "official" if kind == "settled" else commission_fee_source
            ),
            commission_fee_source_label=(
                "TikTok官方费用" if kind == "settled" else commission_fee_source_label
            ),
            commission_fee_rate=(None if kind == "settled" else commission_fee_rate),
            commission_fee_coverage_order_count=(
                order_count if kind == "settled" else coverage_order_count
            ),
            commission_fee_coverage_order_ratio=(
                Decimal("1") if kind == "settled" and order_count > 0 else
                (None if kind == "settled" else coverage_ratio)
            ),
            commission_fee_baseline_window=(
                None if kind == "settled" else commission_fee_baseline_window
            ),
        )
    finally:
        if own:
            session.close()
