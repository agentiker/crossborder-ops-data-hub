"""结算扣费率计算（按 currency 分组）。

费率 = Σ总扣费(FactFinanceTransaction.fee_tax_amount) ÷ Σ已结算订单 GMV(OrderHeader.total_amount)。
口径要点（避免结算滞后导致的虚低）：
- **只纳入已结算订单**：窗口内已付款(paid_time)且在 fact_finance_transaction 有交易行的订单。
  未结算订单贡献 GMV 但无扣费，若纳入会把费率拉低，故 inner-join 剔除。
- **GMV 不重复计数**：一个订单可有多笔结算交易，GMV 按 distinct 订单的 total_amount 求和，
  扣费按该订单所有交易行求和。
- **按 currency 分组**：扣费与 GMV 同币种相除才有意义，跨币种不混算（多店多币种各自比较）。

返回供两处复用：#4 扣点率异常告警（services/fee_rate_alerts）、#3 利润的扣点项。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import func

from core.db import SessionLocal
from core.timezone import paid_window_utc
from models.base_models import FactFinanceTransaction, FactUnsettledFee, OrderHeader

# 在文案/利润里要拆开看的扣费组件（FT 提升列），便于定位费率异动来源
_COMPONENT_COLUMNS = (
    "platform_commission_amount",
    "referral_fee_amount",
    "transaction_fee_amount",
    "gmv_max_fee",
    "tap_commission",
    "affiliate_commission",
)


def _to_decimal(value) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def _scope_filters(query, model, platform, country, shop_id, shop_ids):
    if platform:
        query = query.filter(model.platform == platform)
    if country:
        query = query.filter(model.country == country)
    if shop_ids:
        query = query.filter(model.shop_id.in_(shop_ids))
    elif shop_id:
        query = query.filter(model.shop_id == shop_id)
    return query


def get_settled_fee_rate(
    *,
    start_date: date,
    end_date: date,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    shop_id: Optional[str] = None,
    shop_ids: Optional[list[str]] = None,
    session=None,
) -> dict[str, dict]:
    """按 currency 分组返回 [start,end] 业务日窗口内已结算订单的扣费率。

    返回 {currency: {gmv, total_fee, rate, order_count, components{col: amount}}}。
    rate = total_fee / gmv（float）；gmv=0 的币种 rate 记 0.0。无已结算订单 → 返回 {}。
    """
    start_dt, end_dt = paid_window_utc(start_date, end_date)
    own_session = session is None
    session = session or SessionLocal()
    try:
        # 1) 窗口内已付款订单：order_id → (currency, gmv)。按 distinct 订单，GMV 不重复计数。
        order_q = session.query(
            OrderHeader.order_id, OrderHeader.currency, OrderHeader.total_amount
        ).filter(
            OrderHeader.paid_time.isnot(None),
            OrderHeader.paid_time >= start_dt,
            OrderHeader.paid_time <= end_dt,
        )
        order_q = _scope_filters(order_q, OrderHeader, platform, country, shop_id, shop_ids)
        orders = order_q.all()
        if not orders:
            return {}
        gmv_by_order = {oid: (ccy, _to_decimal(amt)) for oid, ccy, amt in orders}
        order_ids = list(gmv_by_order.keys())

        # 2) 这些订单的结算交易扣费，按 order_id 聚合（一单多笔交易求和）。
        comp_sums = [func.sum(getattr(FactFinanceTransaction, c)).label(c) for c in _COMPONENT_COLUMNS]
        fee_q = session.query(
            FactFinanceTransaction.order_id,
            func.sum(FactFinanceTransaction.fee_tax_amount).label("fee_tax"),
            *comp_sums,
        ).filter(FactFinanceTransaction.order_id.in_(order_ids))
        fee_q = _scope_filters(fee_q, FactFinanceTransaction, platform, country, shop_id, shop_ids)
        fee_q = fee_q.group_by(FactFinanceTransaction.order_id)
        fee_by_order = {row.order_id: row for row in fee_q.all()}

        # 3) 仅已结算订单（有 FT 行）→ 按 currency 汇总 GMV / 扣费 / 组件。
        buckets: dict[str, dict] = {}
        for oid, row in fee_by_order.items():
            ccy, gmv = gmv_by_order.get(oid, (None, Decimal("0")))
            agg = buckets.setdefault(
                ccy,
                {
                    "gmv": Decimal("0"),
                    "total_fee": Decimal("0"),
                    "order_count": 0,
                    "components": {c: Decimal("0") for c in _COMPONENT_COLUMNS},
                },
            )
            agg["gmv"] += gmv
            agg["total_fee"] += _to_decimal(row.fee_tax)
            agg["order_count"] += 1
            for c in _COMPONENT_COLUMNS:
                agg["components"][c] += _to_decimal(getattr(row, c))

        out: dict[str, dict] = {}
        for ccy, agg in buckets.items():
            gmv = agg["gmv"]
            fee = agg["total_fee"]
            rate = float(fee / gmv) if gmv > 0 else 0.0
            out[ccy] = {
                "currency": ccy,
                "gmv": float(gmv),
                "total_fee": float(fee),
                "rate": rate,
                "order_count": agg["order_count"],
                "components": {c: float(v) for c, v in agg["components"].items()},
            }
        return out
    finally:
        if own_session:
            session.close()


# 未结算预估费的提升列组件（FactUnsettledFee 仅广告费提升为独立列，主佣金等在 fee_breakdown JSON）
_UNSETTLED_COMPONENT_COLUMNS = ("gmv_max_fee", "tap_commission", "affiliate_commission")


def get_unsettled_fee_rate(
    *,
    start_date: date,
    end_date: date,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    shop_id: Optional[str] = None,
    shop_ids: Optional[list[str]] = None,
    session=None,
) -> dict[str, dict]:
    """按 currency 分组返回 [start,end] 业务日窗口内**未结算订单的预估扣费率**（**无结算滞后**）。

    与 get_settled_fee_rate 对称、可直接比对：取数源换成 FactUnsettledFee（TikTok 官方预估额，
    按 order_create_time 归 metric_date），扣费取 estimated_fee_amount 求和，GMV 取这些订单的
    distinct OrderHeader.total_amount（与 settled 同 GMV 基准），故两者费率口径一致可比。
    反映平台**最新费率政策**，结算前即可发现调佣（'政策刚变、尚未结算'）。

    返回 {currency: {gmv, total_fee, rate, order_count, components}}。无未结算预估行 → {}。
    """
    own_session = session is None
    session = session or SessionLocal()
    try:
        # 1) 窗口内未结算预估费，按 order_id 聚合扣费 + 广告组件（metric_date 已是业务日，无需窗口换算）
        comp_sums = [
            func.sum(getattr(FactUnsettledFee, c)).label(c) for c in _UNSETTLED_COMPONENT_COLUMNS
        ]
        fee_q = session.query(
            FactUnsettledFee.order_id,
            func.sum(FactUnsettledFee.estimated_fee_amount).label("total_fee"),
            *comp_sums,
        ).filter(
            FactUnsettledFee.metric_date >= start_date,
            FactUnsettledFee.metric_date <= end_date,
        )
        fee_q = _scope_filters(fee_q, FactUnsettledFee, platform, country, shop_id, shop_ids)
        fee_q = fee_q.group_by(FactUnsettledFee.order_id)
        fee_by_order = {row.order_id: row for row in fee_q.all()}
        if not fee_by_order:
            return {}
        order_ids = list(fee_by_order.keys())

        # 2) 这些订单的 GMV（distinct total_amount）+ currency
        order_q = session.query(
            OrderHeader.order_id, OrderHeader.currency, OrderHeader.total_amount
        ).filter(OrderHeader.order_id.in_(order_ids))
        order_q = _scope_filters(order_q, OrderHeader, platform, country, shop_id, shop_ids)
        gmv_by_order = {oid: (ccy, _to_decimal(amt)) for oid, ccy, amt in order_q.all()}

        # 3) 按 currency 汇总 GMV / 扣费 / 组件
        buckets: dict[str, dict] = {}
        for oid, row in fee_by_order.items():
            ccy, gmv = gmv_by_order.get(oid, (None, Decimal("0")))
            agg = buckets.setdefault(
                ccy,
                {
                    "gmv": Decimal("0"),
                    "total_fee": Decimal("0"),
                    "order_count": 0,
                    "components": {c: Decimal("0") for c in _UNSETTLED_COMPONENT_COLUMNS},
                },
            )
            agg["gmv"] += gmv
            agg["total_fee"] += _to_decimal(row.total_fee)
            agg["order_count"] += 1
            for c in _UNSETTLED_COMPONENT_COLUMNS:
                agg["components"][c] += _to_decimal(getattr(row, c))

        out: dict[str, dict] = {}
        for ccy, agg in buckets.items():
            gmv = agg["gmv"]
            fee = agg["total_fee"]
            rate = float(fee / gmv) if gmv > 0 else 0.0
            out[ccy] = {
                "currency": ccy,
                "gmv": float(gmv),
                "total_fee": float(fee),
                "rate": rate,
                "order_count": agg["order_count"],
                "components": {c: float(v) for c, v in agg["components"].items()},
            }
        return out
    finally:
        if own_session:
            session.close()
