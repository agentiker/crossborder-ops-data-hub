"""扣点率异常告警：确定性判定 + 飞书私聊文案（不碰 DB / 不发消息，纯逻辑可单测）。

与 stock_alerts / fulfillment_alerts 同构。取数在 services.fee_rate_metrics（已结算订单的
Σ扣费/ΣGMV，按 currency 分组），本模块只判「评估窗口费率 vs 基准均值」是否异常并组装文案。

判定（只报「费率上升」，下降是好事不报）：
- 选评估窗口 GMV 最大的币种为主币种（多币种店取主力盘，其余忽略，避免跨币种混算）。
- 护栏：评估/基准任一窗口 GMV < min_gmv，或基准无数据（冷启动/历史不足）→ 不报（skip_reason 标注）。
- 异常条件：评估费率 > 基准费率 且 相对升幅 > rel_pct 且 绝对升幅 > abs_pct（百分点）。
- 去重：调用方按 eval_window_end 控制「同一评估窗口只报一次」，本模块不管状态。

文案遵循飞书私聊渲染约束（emoji + 粗体 + 短列表，无 Markdown 表格）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

ALERT_TYPE = "fee_rate_anomaly"
# 及时费率告警（unsettled 预估口径）独立去重状态，与结算口径互不覆盖
ALERT_TYPE_REALTIME = "fee_rate_anomaly_realtime"

# 文案里列出的扣费组件（按金额降序取前几），列名 → 中文名
_COMPONENT_LABELS = {
    "platform_commission_amount": "平台佣金",
    "referral_fee_amount": "引荐费",
    "transaction_fee_amount": "交易手续费",
    "gmv_max_fee": "GMV Max 广告",
    "tap_commission": "达人佣金",
    "affiliate_commission": "联盟佣金",
}


@dataclass
class FeeRateDecision:
    """一次评估结论。should_alert 决定是否投递；skip_reason 解释为何不报（护栏/无异常）。"""

    should_alert: bool
    currency: Optional[str] = None
    eval_rate: float = 0.0
    baseline_rate: float = 0.0
    rel_change: float = 0.0  # 相对升幅 (eval-base)/base
    abs_change: float = 0.0  # 绝对升幅 eval-base（小数，0.03=3pct）
    eval_gmv: float = 0.0
    baseline_gmv: float = 0.0
    message: Optional[str] = None
    skip_reason: Optional[str] = None


def _dominant_currency(eval_by_ccy: dict[str, dict]) -> Optional[str]:
    """评估窗口里 GMV 最大的币种。"""
    if not eval_by_ccy:
        return None
    return max(eval_by_ccy.items(), key=lambda kv: kv[1].get("gmv", 0.0))[0]


def build_decision(
    *,
    eval_by_ccy: dict[str, dict],
    baseline_by_ccy: dict[str, dict],
    scope_display: str,
    min_gmv: float,
    rel_pct: float,
    abs_pct: float,
    eval_window_label: str,
    baseline_window_label: str,
    realtime: bool = False,
) -> FeeRateDecision:
    """判定评估窗口费率是否异常升高。无主币种/护栏不过/无异常都返回 should_alert=False。

    realtime=True：评估期为 unsettled 预估口径（无结算滞后），文案标注"预估口径·实时"。
    """
    currency = _dominant_currency(eval_by_ccy)
    if currency is None:
        return FeeRateDecision(should_alert=False, skip_reason="评估窗口无已结算订单")

    ev = eval_by_ccy.get(currency, {})
    base = baseline_by_ccy.get(currency, {})
    eval_gmv = float(ev.get("gmv", 0.0))
    baseline_gmv = float(base.get("gmv", 0.0))
    eval_rate = float(ev.get("rate", 0.0))
    baseline_rate = float(base.get("rate", 0.0))

    common = dict(
        currency=currency,
        eval_rate=eval_rate,
        baseline_rate=baseline_rate,
        eval_gmv=eval_gmv,
        baseline_gmv=baseline_gmv,
    )

    # 护栏：低基数 / 冷启动
    if eval_gmv < min_gmv:
        return FeeRateDecision(should_alert=False, skip_reason="评估窗口 GMV 低于护栏", **common)
    if baseline_gmv < min_gmv:
        return FeeRateDecision(should_alert=False, skip_reason="基准窗口 GMV 不足（历史不够）", **common)
    if baseline_rate <= 0:
        return FeeRateDecision(should_alert=False, skip_reason="基准费率为 0，无法比较", **common)

    abs_change = eval_rate - baseline_rate
    rel_change = abs_change / baseline_rate
    common["abs_change"] = abs_change
    common["rel_change"] = rel_change

    # 只报上升且同时过相对/绝对双阈值
    if abs_change <= 0 or rel_change < rel_pct or abs_change < abs_pct:
        return FeeRateDecision(should_alert=False, skip_reason="未达异常阈值或费率未升", **common)

    message = _format_message(
        scope_display=scope_display,
        currency=currency,
        eval_rate=eval_rate,
        baseline_rate=baseline_rate,
        rel_change=rel_change,
        abs_change=abs_change,
        eval_components=ev.get("components", {}),
        eval_gmv=eval_gmv,
        eval_window_label=eval_window_label,
        baseline_window_label=baseline_window_label,
        realtime=realtime,
    )
    return FeeRateDecision(should_alert=True, message=message, **common)


def _pct(value: float) -> str:
    """0.1834 → '18.34%'。"""
    return f"{value * 100:.2f}%"


def _top_components(components: dict, gmv: float, limit: int = 3) -> list[str]:
    """按金额降序列出占比最大的几项扣费（各自占 GMV 的比例）。"""
    items = sorted(components.items(), key=lambda kv: kv[1], reverse=True)
    out = []
    for col, amt in items[:limit]:
        if amt <= 0:
            continue
        label = _COMPONENT_LABELS.get(col, col)
        share = (amt / gmv) if gmv > 0 else 0.0
        out.append(f"  • {label}：占 GMV {_pct(share)}")
    return out


def _format_message(
    *,
    scope_display: str,
    currency: str,
    eval_rate: float,
    baseline_rate: float,
    rel_change: float,
    abs_change: float,
    eval_components: dict,
    eval_gmv: float,
    eval_window_label: str,
    baseline_window_label: str,
    realtime: bool = False,
) -> str:
    """组装飞书私聊告警文案（确定性，无 Markdown 表格）。"""
    title = "⚠️ 扣点率异常升高（预估口径·实时）" if realtime else "⚠️ 扣点率异常升高"
    eval_prefix = "预估扣费率" if realtime else "扣费率"
    lines = [
        title,
        f"🏪 范围：{scope_display}（{currency}）",
        f"- 评估期（{eval_window_label}）{eval_prefix}：{_pct(eval_rate)}",
        f"- 基准（{baseline_window_label}）：{_pct(baseline_rate)}",
        f"- 升幅：+{_pct(abs_change)}（相对 +{_pct(rel_change)}）",
    ]
    comps = _top_components(eval_components, eval_gmv)
    if comps:
        lines.append("主要扣费构成：")
        lines.extend(comps)
    lines.append("👉 请核对是否平台调佣 / 新增费项 / 活动费用，必要时复盘定价。")
    if realtime:
        lines.append("（注：基于未结算订单 TikTok 官方预估费率，反映最新费率政策；结算前即可发现调佣）")
    else:
        lines.append("（注：结算有滞后，已剔除近期未结算完的订单）")
    return "\n".join(lines)
