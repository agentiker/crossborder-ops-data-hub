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

import json
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import func

from core.config import settings
from core.db import SessionLocal
from core.timezone import business_today, paid_window_utc
from models.base_models import FactFinanceTransaction, FactUnsettledFee, OrderHeader


def _to_decimal(value) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def _accumulate_fee_components(rows) -> dict[str, dict[str, float]]:
    """[(currency, fee_breakdown_json)] → {currency: {fee 子键: 正额}}（B2 分项归因用）。

    fee_breakdown JSON 存原始 API 值（负数=扣款），取绝对值累加成正成本量级，与费率分子同向。
    比提升列更全：主佣金 dynamic_commission 等只在 JSON、不在提升列，分项点名必须取 JSON 全集。
    """
    out: dict[str, dict[str, float]] = {}
    for ccy, fb in rows:
        if not fb:
            continue
        if isinstance(fb, str):
            try:
                fb = json.loads(fb)
            except (ValueError, TypeError):
                continue
        bucket = out.setdefault(ccy, {})
        for key, val in fb.items():
            try:
                amt = abs(float(val))
            except (TypeError, ValueError):
                continue
            if amt:
                bucket[key] = bucket.get(key, 0.0) + amt
    return out


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

    返回 {currency: {gmv, total_fee, rate, order_count, components{fee子键: 正额}}}。
    components 从 fee_breakdown JSON 聚合完整费项（含 dynamic_commission 等非提升列），供 B2 分项归因。
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
        fee_q = session.query(
            FactFinanceTransaction.order_id,
            func.sum(FactFinanceTransaction.fee_tax_amount).label("fee_tax"),
        ).filter(FactFinanceTransaction.order_id.in_(order_ids))
        fee_q = _scope_filters(fee_q, FactFinanceTransaction, platform, country, shop_id, shop_ids)
        fee_q = fee_q.group_by(FactFinanceTransaction.order_id)
        fee_by_order = {row.order_id: _to_decimal(row.fee_tax) for row in fee_q.all()}

        # 2b) 完整费项构成（从 fee_breakdown JSON，按 currency）——B2 分项归因
        comp_q = session.query(
            FactFinanceTransaction.currency, FactFinanceTransaction.fee_breakdown
        ).filter(FactFinanceTransaction.order_id.in_(order_ids))
        comp_q = _scope_filters(comp_q, FactFinanceTransaction, platform, country, shop_id, shop_ids)
        comp_by_ccy = _accumulate_fee_components(comp_q.all())

        # 3) 仅已结算订单（有 FT 行）→ 按 currency 汇总 GMV / 扣费。
        buckets: dict[str, dict] = {}
        for oid, fee_tax in fee_by_order.items():
            ccy, gmv = gmv_by_order.get(oid, (None, Decimal("0")))
            agg = buckets.setdefault(
                ccy, {"gmv": Decimal("0"), "total_fee": Decimal("0"), "order_count": 0}
            )
            agg["gmv"] += gmv
            agg["total_fee"] += fee_tax
            agg["order_count"] += 1

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
                "components": comp_by_ccy.get(ccy, {}),
            }
        return out
    finally:
        if own_session:
            session.close()


def _fmt_window(start: date, end: date) -> str:
    """date 区间 → 'MM/DD~MM/DD'（看板/前端展示用）。"""
    return f"{start.strftime('%m/%d')}~{end.strftime('%m/%d')}"


def get_fee_rate_monitor(
    *,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    shop_id: Optional[str] = None,
    shop_ids: Optional[list[str]] = None,
    scope_display: str = "全部范围",
    trend_days: int = 14,
    session=None,
) -> dict:
    """看板「费率监控」卡数据：实时算、不落库。严格复用 B1 及时口径与 build_decision，与告警同源。

    口径（与 flows/scan_fulfillment_alerts._scan_unsettled_fee_rate 一致）：
    - eval = 最近 fee_rate_realtime_eval_days 天 **unsettled 预估**费率（无结算滞后、反映最新政策）。
    - baseline = 避开滞后段的 **settled 已结算**历史费率（稳基准）。
    - status：build_decision(realtime=True) 判定——
        'alert'        should_alert=True（费率异常升高）
        'normal'       已评估、未达阈值（有数据、费率正常）
        'insufficient' 无主币种/GMV不过护栏/基准不足（数据积累中，不误报）
    - trend：近 trend_days 个业务日逐日 unsettled 预估费率（主币种），无数据的日点 rate=None。

    返回结构供 /dashboard/summary 注入，前端 FeeRateMonitor.tsx 渲染（前端自行格式化百分比）。
    """
    from services import fee_rate_alerts  # 避免与 alerts 模块循环导入

    own_session = session is None
    session = session or SessionLocal()
    try:
        today = business_today()
        eval_end = today
        eval_start = today - timedelta(days=settings.fee_rate_realtime_eval_days - 1)
        baseline_end = today - timedelta(days=settings.fee_rate_settle_lag_days)
        baseline_start = baseline_end - timedelta(days=settings.fee_rate_baseline_days - 1)

        common_scope = dict(platform=platform, country=country, shop_id=shop_id, shop_ids=shop_ids)
        eval_by_ccy = get_unsettled_fee_rate(
            start_date=eval_start, end_date=eval_end, session=session, **common_scope
        )
        baseline_by_ccy = get_settled_fee_rate(
            start_date=baseline_start, end_date=baseline_end, session=session, **common_scope
        )

        decision = fee_rate_alerts.build_decision(
            eval_by_ccy=eval_by_ccy,
            baseline_by_ccy=baseline_by_ccy,
            scope_display=scope_display,
            min_gmv=settings.fee_rate_min_gmv,
            rel_pct=settings.fee_rate_alert_rel_pct,
            abs_pct=settings.fee_rate_alert_abs_pct,
            eval_window_label=_fmt_window(eval_start, eval_end),
            baseline_window_label=_fmt_window(baseline_start, baseline_end),
            realtime=True,
        )

        # 三/四态（用数值判定，不靠 skip_reason 字符串）：
        #   有"当前数据"= 主币种存在且评估窗口 GMV 过护栏（current_rate/构成/趋势可信）。
        #   - alert            异常升高
        #   - normal           有当前 + 基准充足 + 未达阈值
        #   - baseline_pending 有当前但基准（已结算历史）不足→展示当前费率/构成/趋势，仅不判异常
        #   - insufficient     连当前数据都没有（评估窗口空 / GMV 低于护栏）→ 灰态
        currency = decision.currency
        has_current = currency is not None and decision.eval_gmv >= settings.fee_rate_min_gmv
        baseline_ok = decision.baseline_gmv >= settings.fee_rate_min_gmv and decision.baseline_rate > 0
        if decision.should_alert:
            status = "alert"
        elif not has_current:
            status = "insufficient"
        elif not baseline_ok:
            status = "baseline_pending"
        else:
            status = "normal"

        # 当前构成（主币种 eval 侧），结构化供前端
        ev = eval_by_ccy.get(currency, {}) if currency else {}
        eval_components = ev.get("components", {})
        eval_gmv = float(ev.get("gmv", 0.0))
        components = []
        for key, amt in sorted(eval_components.items(), key=lambda kv: kv[1], reverse=True)[:5]:
            if amt <= 0:
                continue
            components.append({
                "key": key,
                "name": fee_rate_alerts.component_label(key),
                "share": (amt / eval_gmv) if eval_gmv > 0 else 0.0,
            })

        # 分项归因（仅 alert 时有意义；交集为空→空列表，前端降级为纯构成展示）
        attributions = []
        if status == "alert":
            base = baseline_by_ccy.get(currency, {})
            base_components = base.get("components", {})
            base_gmv = float(base.get("gmv", 0.0))
            if eval_gmv > 0 and base_gmv > 0:
                rows = []
                for key in set(eval_components) & set(base_components):
                    ev_share = eval_components[key] / eval_gmv
                    base_share = base_components[key] / base_gmv
                    diff = ev_share - base_share
                    if diff >= fee_rate_alerts._COMPONENT_ATTRIBUTION_MIN_PCT:
                        rows.append((diff, key, base_share, ev_share))
                rows.sort(reverse=True)
                for diff, key, base_share, ev_share in rows[:3]:
                    attributions.append({
                        "key": key,
                        "name": fee_rate_alerts.component_label(key),
                        "from": base_share,
                        "to": ev_share,
                        "delta": diff,
                    })

        # 趋势：近 trend_days 个业务日逐日 unsettled 预估费率（主币种）
        trend = []
        for i in range(trend_days - 1, -1, -1):
            d = today - timedelta(days=i)
            day_by_ccy = get_unsettled_fee_rate(
                start_date=d, end_date=d, session=session, **common_scope
            )
            rate = None
            if currency and currency in day_by_ccy:
                rate = day_by_ccy[currency].get("rate")
            elif day_by_ccy:  # 无主币种时（如 currency=None）取该日 GMV 最大币种
                top = max(day_by_ccy.items(), key=lambda kv: kv[1].get("gmv", 0.0))
                rate = top[1].get("rate")
            trend.append({"date": d.isoformat(), "rate": rate})

        return {
            "currency": currency,
            "status": status,
            "skip_reason": decision.skip_reason,
            "current_rate": decision.eval_rate,
            "baseline_rate": decision.baseline_rate,
            "abs_delta": decision.abs_change,
            "rel_delta": decision.rel_change,
            "eval_gmv": decision.eval_gmv,
            "baseline_gmv": decision.baseline_gmv,
            "order_count": int(ev.get("order_count", 0)),
            "eval_window": _fmt_window(eval_start, eval_end),
            "baseline_window": _fmt_window(baseline_start, baseline_end),
            "components": components,
            "attributions": attributions,
            "trend": trend,
        }
    finally:
        if own_session:
            session.close()


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

    components 从 fee_breakdown JSON 聚合完整费项（含 dynamic_commission 等主项），供 B2 分项归因。
    返回 {currency: {gmv, total_fee, rate, order_count, components}}。无未结算预估行 → {}。
    """
    own_session = session is None
    session = session or SessionLocal()
    try:
        # 1) 窗口内未结算预估费，按 order_id 聚合扣费（metric_date 已是业务日，无需窗口换算）
        fee_q = session.query(
            FactUnsettledFee.order_id,
            func.sum(FactUnsettledFee.estimated_fee_amount).label("total_fee"),
        ).filter(
            FactUnsettledFee.metric_date >= start_date,
            FactUnsettledFee.metric_date <= end_date,
        )
        fee_q = _scope_filters(fee_q, FactUnsettledFee, platform, country, shop_id, shop_ids)
        fee_q = fee_q.group_by(FactUnsettledFee.order_id)
        fee_by_order = {row.order_id: _to_decimal(row.total_fee) for row in fee_q.all()}
        if not fee_by_order:
            return {}
        order_ids = list(fee_by_order.keys())

        # 1b) 完整费项构成（从 fee_breakdown JSON，按 currency）——B2 分项归因（currency 在预估行级自带）
        comp_q = session.query(
            FactUnsettledFee.currency, FactUnsettledFee.fee_breakdown
        ).filter(
            FactUnsettledFee.metric_date >= start_date,
            FactUnsettledFee.metric_date <= end_date,
        )
        comp_q = _scope_filters(comp_q, FactUnsettledFee, platform, country, shop_id, shop_ids)
        comp_by_ccy = _accumulate_fee_components(comp_q.all())

        # 2) 这些订单的 GMV（distinct total_amount）+ currency
        order_q = session.query(
            OrderHeader.order_id, OrderHeader.currency, OrderHeader.total_amount
        ).filter(OrderHeader.order_id.in_(order_ids))
        order_q = _scope_filters(order_q, OrderHeader, platform, country, shop_id, shop_ids)
        gmv_by_order = {oid: (ccy, _to_decimal(amt)) for oid, ccy, amt in order_q.all()}

        # 3) 按 currency 汇总 GMV / 扣费
        buckets: dict[str, dict] = {}
        for oid, total_fee in fee_by_order.items():
            ccy, gmv = gmv_by_order.get(oid, (None, Decimal("0")))
            agg = buckets.setdefault(
                ccy, {"gmv": Decimal("0"), "total_fee": Decimal("0"), "order_count": 0}
            )
            agg["gmv"] += gmv
            agg["total_fee"] += total_fee
            agg["order_count"] += 1

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
                "components": comp_by_ccy.get(ccy, {}),
            }
        return out
    finally:
        if own_session:
            session.close()
