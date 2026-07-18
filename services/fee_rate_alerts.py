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

# 文案里列出的扣费组件：fee_breakdown 子键(API 原始名) → 中文名。未列出的键回落原始名。
# 结算单(202501)与未结算单(202507)命名体系不同(如 platform_commission vs dynamic_commission)，两套都列。
_COMPONENT_LABELS = {
    # 未结算单(202507)主费项
    "dynamic_commission_amount": "动态佣金",
    "bonus_cashback_service_fee_amount": "返现服务费",
    "vn_fix_infrastructure_fee": "基建费",
    "affiliate_ads_commission_amount": "联盟广告佣金",
    "affiliate_commission_amount": "联盟佣金",
    "affiliate_commission_before_pit_amount": "联盟佣金(税前)",
    "affiliate_partner_commission_amount": "联盟伙伴佣金",
    # 结算单(202501)主费项
    "platform_commission_amount": "平台佣金",
    "referral_fee_amount": "引荐费",
    "transaction_fee_amount": "交易手续费",
    "gmv_max_ad_fee_amount": "GMV Max 广告",
    "tap_shop_ads_commission": "达人佣金",
    # 其它常见费项
    "credit_card_handling_fee_amount": "信用卡手续费",
    "sfp_service_fee_amount": "SFP 服务费",
    "mall_service_fee_amount": "商城服务费",
    "seller_growth_fee_amount": "卖家成长费",
    "smart_promotion_fee_amount": "智能推广费",
    "refund_administration_fee_amount": "退款管理费",
}

# 分项归因：某费项占 GMV 比例较基准升幅 ≥ 此阈值(百分点)才点名（过滤微小波动）
_COMPONENT_ATTRIBUTION_MIN_PCT = 0.005


def component_label(key: str) -> str:
    """fee_breakdown 子键(API 原始名) → 中文名；未收录则回落原始名。供看板/文案共用。"""
    return _COMPONENT_LABELS.get(key, key)


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
    evidence: Optional[dict] = None


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
        return FeeRateDecision(should_alert=False, skip_reason="评估窗口无数据")

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
        baseline_components=base.get("components", {}),
        baseline_gmv=baseline_gmv,
        eval_window_label=eval_window_label,
        baseline_window_label=baseline_window_label,
        realtime=realtime,
    )
    evidence = build_fee_rate_evidence(
        currency=currency,
        eval_components=ev.get("components", {}),
        eval_gmv=eval_gmv,
        baseline_components=base.get("components", {}),
        baseline_gmv=baseline_gmv,
        eval_window_label=eval_window_label,
        baseline_window_label=baseline_window_label,
        realtime=realtime,
    )
    return FeeRateDecision(should_alert=True, message=message, evidence=evidence, **common)


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


def _attributions(
    eval_components: dict, eval_gmv: float, baseline_components: dict, baseline_gmv: float, limit: int = 3
) -> list[str]:
    """B2 分项归因：对**两侧都有**的同名费项，算各自占 GMV 比例的升幅，点名升幅最大的几项。

    仅取交集键——避免跨口径(结算 platform_commission vs 未结算 dynamic_commission，命名不同)
    时把"对方没有的键"误判成从 0 暴涨。交集为空(如及时口径多数费项名不同)则返回空、降级为纯构成展示。
    """
    if not eval_components or not baseline_components or eval_gmv <= 0 or baseline_gmv <= 0:
        return []
    rows = []
    for key in set(eval_components) & set(baseline_components):
        ev_share = eval_components[key] / eval_gmv
        base_share = baseline_components[key] / baseline_gmv
        diff = ev_share - base_share
        if diff >= _COMPONENT_ATTRIBUTION_MIN_PCT:
            rows.append((diff, key, base_share, ev_share))
    rows.sort(reverse=True)
    out = []
    for diff, key, base_share, ev_share in rows[:limit]:
        label = _COMPONENT_LABELS.get(key, key)
        out.append(f"  • {label}：+{_pct(diff)}（{_pct(base_share)}→{_pct(ev_share)}）")
    return out


def _attribution_rows(
    eval_components: dict, eval_gmv: float, baseline_components: dict, baseline_gmv: float, limit: int = 3
) -> list[dict]:
    """结构化分项归因，供飞书卡片/证据模块使用。"""
    if not eval_components or not baseline_components or eval_gmv <= 0 or baseline_gmv <= 0:
        return []
    rows = []
    for key in set(eval_components) & set(baseline_components):
        ev_share = eval_components[key] / eval_gmv
        base_share = baseline_components[key] / baseline_gmv
        diff = ev_share - base_share
        if diff >= _COMPONENT_ATTRIBUTION_MIN_PCT:
            rows.append((diff, key, base_share, ev_share))
    rows.sort(reverse=True)
    return [
        {
            "key": key,
            "name": component_label(key),
            "from": base_share,
            "to": ev_share,
            "delta": diff,
            "source_field": f"fee_tax_breakdown.fee.{key}",
            "basis": "attribution",
        }
        for diff, key, base_share, ev_share in rows[:limit]
    ]


def build_fee_rate_evidence(
    *,
    currency: str,
    eval_components: dict,
    eval_gmv: float,
    baseline_components: dict,
    baseline_gmv: float,
    eval_window_label: str,
    baseline_window_label: str,
    realtime: bool,
) -> dict:
    """构造“内部官方费用证据”：只描述已授权 Finance API 费用事实，不解释政策原因。"""
    items = _attribution_rows(eval_components, eval_gmv, baseline_components, baseline_gmv)
    mode = "attribution"
    if not items:
        mode = "current_components"
        top = sorted(eval_components.items(), key=lambda kv: kv[1], reverse=True)[:3]
        items = [
            {
                "key": key,
                "name": component_label(key),
                "from": None,
                "to": (amt / eval_gmv) if eval_gmv > 0 else 0.0,
                "delta": None,
                "source_field": f"fee_tax_breakdown.fee.{key}",
                "basis": "current_component",
            }
            for key, amt in top
            if amt > 0
        ]
    return {
        "source": "tiktok_finance_api",
        "confidence": "high",
        "mode": mode,
        "currency": currency,
        "eval_window": eval_window_label,
        "baseline_window": baseline_window_label,
        "realtime": realtime,
        "fee_items": items,
    }


def evidence_fee_keys(evidence: Optional[dict]) -> list[str]:
    """从内部证据提取费项 key，供官方公开资料检索匹配。"""
    if not evidence:
        return []
    out = []
    for item in evidence.get("fee_items") or []:
        key = item.get("key")
        if key:
            out.append(str(key))
    return out


def format_evidence_lines(evidence: Optional[dict], *, limit: int = 2) -> list[str]:
    """飞书文本 fallback 的内部证据行。"""
    if not evidence:
        return []
    items = list(evidence.get("fee_items") or [])[:limit]
    if not items:
        return []
    lines = ["🔎 检测依据（TikTok 官方费用字段）："]
    for item in items:
        name = item.get("name") or item.get("key") or "费用项"
        if item.get("delta") is not None and item.get("from") is not None:
            lines.append(
                f"  • {name}：+{_pct(float(item['delta']))}"
                f"（{_pct(float(item['from']))}→{_pct(float(item['to']))}）"
            )
        else:
            lines.append(f"  • {name}：当前占 GMV {_pct(float(item.get('to') or 0.0))}")
    return lines


def format_policy_reference_lines(policy_references: Optional[list[dict]], *, limit: int = 2) -> list[str]:
    """飞书文本 fallback 的官方公开参考资料行。"""
    refs = list(policy_references or [])[:limit]
    if not refs:
        return []
    lines = ["📚 官方参考资料："]
    for ref in refs:
        title = ref.get("title") or ref.get("url") or "TikTok 官方资料"
        url = ref.get("url") or ""
        source = ref.get("source") or "TikTok"
        lines.append(f"  • {source}：{title} {url}".rstrip())
    return lines


def enrich_message_with_evidence(
    message: str, *, evidence: Optional[dict] = None, policy_references: Optional[list[dict]] = None
) -> str:
    """给纯文本告警追加证据；卡片失败 fallback 时仍保留关键信息。"""
    lines = [message]
    extra = format_evidence_lines(evidence) + format_policy_reference_lines(policy_references)
    if extra:
        lines.extend(extra)
    return "\n".join(lines)


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
    baseline_components: dict,
    baseline_gmv: float,
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
    # B2 分项归因：能对齐到基准的费项→点名升幅；对齐不上(跨口径)→降级为当前构成展示
    attribs = _attributions(eval_components, eval_gmv, baseline_components, baseline_gmv)
    if attribs:
        lines.append("📍 主要涨幅来自：")
        lines.extend(attribs)
    comps = _top_components(eval_components, eval_gmv)
    if comps:
        lines.append("当前主要扣费构成：")
        lines.extend(comps)
    evidence_lines = format_evidence_lines(
        build_fee_rate_evidence(
            currency=currency,
            eval_components=eval_components,
            eval_gmv=eval_gmv,
            baseline_components=baseline_components,
            baseline_gmv=baseline_gmv,
            eval_window_label=eval_window_label,
            baseline_window_label=baseline_window_label,
            realtime=realtime,
        ),
        limit=2,
    )
    if evidence_lines:
        lines.extend(evidence_lines)
    lines.append("👉 请核对是否平台调佣 / 新增费项 / 活动费用，必要时复盘定价。")
    if realtime:
        lines.append("（注：基于未结算订单 TikTok 官方预估费率，反映最新费率政策；结算前即可发现调佣）")
    else:
        lines.append("（注：结算有滞后，已剔除近期未结算完的订单）")
    return "\n".join(lines)
