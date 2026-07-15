"""Pure profit metric and alert calculations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Optional

from services.scoping import build_scope_key


@dataclass(frozen=True)
class ProfitInputs:
    gmv: Decimal
    product_cost: Decimal = Decimal("0")
    ad_spend: Decimal = Decimal("0")
    logistics_cost: Decimal = Decimal("0")
    commission: Decimal = Decimal("0")
    tax: Decimal = Decimal("0")
    refund_amount: Decimal = Decimal("0")
    other_fees: Decimal = Decimal("0")


@dataclass(frozen=True)
class ProfitMetrics:
    gmv: Decimal
    cost_total: Decimal
    profit: Decimal
    profit_margin: Decimal
    roi: Decimal | None


@dataclass(frozen=True)
class Alert:
    alert_type: str
    severity: str
    message: str
    metric_value: Decimal | int | None = None


@dataclass(frozen=True)
class ProfitRecordInput:
    metric_date: date
    platform: str
    country: str = "GLOBAL"
    shop_id: Optional[str] = None
    seller_id: Optional[str] = None
    account_id: Optional[str] = None
    internal_sku: Optional[str] = None
    order_count: int = 0
    units_sold: int = 0
    gmv: Decimal = Decimal("0")
    product_cost: Decimal = Decimal("0")
    ad_cost: Decimal = Decimal("0")
    logistics_cost: Decimal = Decimal("0")
    commission_fee: Decimal = Decimal("0")
    tax_fee: Decimal = Decimal("0")
    refund_amount: Decimal = Decimal("0")
    other_cost: Decimal = Decimal("0")
    # 阶段3a：利润行的展示币种（MVP 各金额已折算为 CNY）与口径（estimated=预估 / settled=结算后真实）。
    # profit_kind 纳入 scope_key，使同日同店的预估行与真实回填行（3b）并存、互不覆盖。
    currency: str = "CNY"
    profit_kind: str = "estimated"
    # 展示/解释元信息：不参与 scope_key 或落库公式。用于当天官方费用尚未覆盖时，
    # 告知前端扣点是否来自历史已结算费率估算。
    commission_fee_source: str = "official"
    commission_fee_source_label: str = "TikTok官方费用"
    commission_fee_rate: Optional[Decimal] = None
    commission_fee_coverage_order_count: int = 0
    commission_fee_coverage_order_ratio: Optional[Decimal] = None
    commission_fee_baseline_window: Optional[str] = None


def calculate_profit_metrics(inputs: ProfitInputs) -> ProfitMetrics:
    """Calculate deterministic profit metrics for one aggregation grain."""
    cost_total = (
        inputs.product_cost
        + inputs.ad_spend
        + inputs.logistics_cost
        + inputs.commission
        + inputs.tax
        + inputs.refund_amount
        + inputs.other_fees
    )
    profit = inputs.gmv - cost_total
    profit_margin = _safe_ratio(profit, inputs.gmv)
    roi = None if inputs.ad_spend == 0 else _safe_ratio(profit, inputs.ad_spend)
    return ProfitMetrics(
        gmv=inputs.gmv,
        cost_total=cost_total,
        profit=profit,
        profit_margin=profit_margin,
        roi=roi,
    )


def calculate_gross_profit(record: ProfitRecordInput) -> Decimal:
    """Calculate the exact stored gross profit formula."""
    metrics = calculate_profit_metrics(
        ProfitInputs(
            gmv=record.gmv,
            product_cost=record.product_cost,
            ad_spend=record.ad_cost,
            logistics_cost=record.logistics_cost,
            commission=record.commission_fee,
            tax=record.tax_fee,
            refund_amount=record.refund_amount,
            other_fees=record.other_cost,
        )
    )
    return metrics.profit


def build_profit_scope_key(record: ProfitRecordInput) -> str:
    grain = record.internal_sku or "all"
    # profit_kind 入 key：预估行(estimated)与结算后真实行(settled，3b)同日同店共存、互不覆盖。
    return build_scope_key(
        platform=record.platform,
        country=record.country,
        shop_id=record.shop_id,
        seller_id=record.seller_id,
        account_id=record.account_id,
        resource=f"profit:{record.profit_kind}:{record.metric_date.isoformat()}:{grain}",
    )


def build_alert_scope_key(
    *,
    platform: str,
    alert_type: str,
    metric_date: date | None = None,
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
    impact_scope: Optional[str] = None,
) -> str:
    date_part = metric_date.isoformat() if metric_date else "open"
    impact_part = impact_scope or "account"
    return build_scope_key(
        platform=platform,
        country=country,
        shop_id=shop_id,
        seller_id=seller_id,
        account_id=account_id,
        resource=f"alert:{date_part}:{alert_type}:{impact_part}",
    )


def generate_alerts(
    *,
    metrics: ProfitMetrics,
    baseline_gmv: Decimal | None = None,
    baseline_roi: Decimal | None = None,
    available_stock: int | None = None,
    recent_7d_sales: int | None = None,
    return_rate: Decimal | None = None,
    baseline_return_rate: Decimal | None = None,
    data_age_hours: int | None = None,
) -> list[Alert]:
    """Generate business alerts from deterministic metrics and thresholds."""
    alerts: list[Alert] = []

    if baseline_gmv and baseline_gmv > 0:
        gmv_change = (metrics.gmv - baseline_gmv) / baseline_gmv
        if gmv_change <= Decimal("-0.30"):
            alerts.append(
                Alert("gmv_drop", "warning", "GMV低于近7日均值30%", gmv_change)
            )

    if baseline_roi is not None and metrics.roi is not None:
        if metrics.roi < baseline_roi:
            alerts.append(Alert("roi_drop", "warning", "ROI连续下降或低于基线", metrics.roi))

    if available_stock is not None and recent_7d_sales is not None:
        if available_stock < recent_7d_sales:
            alerts.append(
                Alert("low_inventory", "critical", "可售库存低于7天销量", available_stock)
            )

    if (
        return_rate is not None
        and baseline_return_rate is not None
        and return_rate > baseline_return_rate
    ):
        alerts.append(Alert("high_return_rate", "warning", "T+15退货率高于基准", return_rate))

    if data_age_hours is not None and data_age_hours >= 24:
        alerts.append(Alert("stale_data", "critical", "某账号24小时未更新", data_age_hours))

    return alerts


def _safe_ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    if denominator == 0:
        return Decimal("0")
    return numerator / denominator
