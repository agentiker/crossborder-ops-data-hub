"""爆单提醒：确定性判定 + 飞书私聊文案（不碰 DB / 不发消息，纯逻辑可单测）。

与 stock_alerts 同构。取数在 services.order_metrics.get_units_by_product（当日各商品已付款销量），
本模块判「哪些商品今天破阈值且今天还没报过」并组装文案。

当日去重（避免同一爆款一天反复刷屏）：
- 破阈商品中「不在今天已报集合」的 = 本次新爆单 → 报。
- 已报集合按业务日；跨天后调用方传入空集（新的一天重新计）。
- 写回集合 = 今天所有破阈商品（让破阈后回落的商品移出，极少见；当天再破不会重报）。

文案遵循飞书私聊渲染约束（emoji + 粗体 + 短列表，无 Markdown 表格）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

ALERT_TYPE = "hotsell"

_TOP_ITEMS = 8  # 文案最多列几个爆款


@dataclass
class HotsellDecision:
    """一次评估结论。should_alert 决定是否投递；new_reported_ids 为写回的当日已报集合。"""

    should_alert: bool
    threshold: int
    new_products: list[dict] = field(default_factory=list)  # 本次新破阈的商品（含 units/name）
    new_reported_ids: list[str] = field(default_factory=list)  # 写回的当日已报集合
    message: Optional[str] = None


def build_decision(
    *,
    units_by_product: dict[str, dict],
    threshold: int,
    prev_reported_ids,
    scope_display: str,
    date_label: str,
    new_product_ids=None,
) -> HotsellDecision:
    """判定今日破阈且未报过的爆款。

    units_by_product：{product_id: {units, product_name}}（get_units_by_product 返回）。
    prev_reported_ids：今天已报过的商品集合（跨天传空）。
    date_label：业务日展示串（如 "6/23"）。
    new_product_ids：近 30 天上线的新品 product_id 集合（get_new_product_ids 返回）；命中者文案
        标注 🌟「新品爆发」。默认空集——存量商品文案不受影响（纯加法，旧文案逐字不变）。
    """
    new_set = set(new_product_ids or [])
    hot = [
        {
            "product_id": pid,
            "units": int(v.get("units", 0)),
            "product_name": v.get("product_name"),
            "is_new": pid in new_set,
        }
        for pid, v in units_by_product.items()
        if int(v.get("units", 0)) >= threshold
    ]
    hot.sort(key=lambda p: p["units"], reverse=True)
    hot_ids = {p["product_id"] for p in hot}
    prev_set = set(prev_reported_ids or [])

    new_products = [p for p in hot if p["product_id"] not in prev_set]
    if not new_products:
        # 无新爆款：不推，但写回当日破阈集合（保持当天状态）
        return HotsellDecision(
            should_alert=False,
            threshold=threshold,
            new_products=[],
            new_reported_ids=sorted(hot_ids),
            message=None,
        )

    message = _format_message(
        scope_display=scope_display,
        date_label=date_label,
        threshold=threshold,
        new_products=new_products,
    )
    return HotsellDecision(
        should_alert=True,
        threshold=threshold,
        new_products=new_products,
        new_reported_ids=sorted(hot_ids),
        message=message,
    )


def _fmt_item(item: dict) -> str:
    name = item.get("product_name") or item.get("product_id") or "未知商品"
    if len(name) > 24:
        name = name[:24] + "…"
    tag = "🌟 " if item.get("is_new") else ""  # 近 30 天新品爆发，醒目标注
    return f"{tag}{name} 今日已售 {item['units']} 件"


def _format_message(
    *, scope_display: str, date_label: str, threshold: int, new_products: list[dict]
) -> str:
    """组装飞书私聊爆单文案（确定性，无 Markdown 表格）。"""
    lines = [
        "🔥 爆单提醒",
        f"🏪 范围：{scope_display}",
        f"📅 {date_label}（截至当前）已有 {len(new_products)} 个商品单日销量破 {threshold} 件：",
    ]
    top = new_products[:_TOP_ITEMS]
    lines.extend(f"  • {_fmt_item(it)}" for it in top)
    if len(new_products) > _TOP_ITEMS:
        lines.append(f"  …等共 {len(new_products)} 个")
    lines.append("👉 关注库存与备货，别让爆款断货丢量。")
    if any(it.get("is_new") for it in new_products):  # 有新品爆发才加图例，存量爆单文案不变
        lines.append("🌟 = 近 30 天新品爆发，重点追单 / 加大备货，别错过新款窗口。")
    return "\n".join(lines)
