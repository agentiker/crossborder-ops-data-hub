"""低库存/断货告警：确定性判定 + 飞书私聊文案组装（不碰 DB / 不发消息，纯逻辑可单测）。

监控链路分三层，本模块只负责中间的「判定 + 文案」（与 fulfillment_alerts 同构）：
- 取数：flows/scan_fulfillment_alerts 调 services.stock_metrics.get_stock_risk（聚合库存+销速算
        可售天数、分桶，不在这里重算公式）。
- 判定+文案：本模块 build_decision —— 按「风险 SKU 集合」决定该不该推、推什么。
- 去重游标 + 投递：flow 读写 StockAlertState（reported_skus）、subprocess 调 openclaw message send。

去重规则（避免同一批低库存 SKU 每 30 分钟刷屏）：
- 风险 SKU 集为空：不推，并请求清空已报集合（reset_state=True）。
- 当前风险集中有「不在上次已报集合」的新 SKU：推，文案突出新进风险的；游标写回当前风险集。
- 当前风险集 ⊆ 已报集合（无新进）：不推，游标写回当前集（让已恢复的 SKU 自动移出，
  下次再跌入会重新提醒）。

文案遵循飞书私聊渲染约束（emoji + 粗体 + 短列表，无 Markdown 表格 / 多级标题）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

ALERT_TYPE = "stock_low"

# 风险明细最多列几条（按可售天数升序），其余汇总省略
_TOP_ITEMS = 8


@dataclass
class StockAlertDecision:
    """一次评估的结论。should_alert 决定是否投递；reset_state 决定已报集合是否清空。"""

    should_alert: bool
    reset_state: bool  # True=请求清空 reported_skus（风险已全部清零时）
    stockout: int
    critical: int
    warning: int
    total: int
    new_skus: list[str]  # 本次新进风险的 SKU（仅 should_alert 时有意义）
    new_reported_skus: list[str] = field(default_factory=list)  # 推送/更新后应写回的已报集合
    message: Optional[str] = None  # 待投递的飞书文案；不推时为 None


def build_decision(
    *,
    risk: dict,
    scope_display: str,
    prev_reported_skus,
) -> StockAlertDecision:
    """根据风险 SKU 集 + 上次已报集合，确定性地决定是否告警并组装文案。

    risk：services.stock_metrics.get_stock_risk 的返回（含 items/buckets/snapshot_at）。
    scope_display：范围展示名（ScopeFilters.display_text）。
    prev_reported_skus：上次已推送的风险 SKU 集合（可迭代；无历史传空）。
    """
    items = risk.get("items") or []
    buckets = risk.get("buckets") or {}
    stockout = int(buckets.get("stockout", 0))
    critical = int(buckets.get("critical", 0))
    warning = int(buckets.get("warning", 0))
    total = int(buckets.get("total", 0))

    current_skus = [i["sku_id"] for i in items]
    current_set = set(current_skus)
    prev_set = set(prev_reported_skus or [])

    if not current_set:
        # 风险已清零：不推，清空已报集合（下次哪怕只 1 个 SKU 跌入也会重新提醒）。
        return StockAlertDecision(
            should_alert=False,
            reset_state=True,
            stockout=0,
            critical=0,
            warning=0,
            total=0,
            new_skus=[],
            new_reported_skus=[],
            message=None,
        )

    new_skus = [s for s in current_skus if s not in prev_set]
    if not new_skus:
        # 无新进风险：不复读，但游标更新为当前集（已恢复的 SKU 移出，便于将来重报）。
        return StockAlertDecision(
            should_alert=False,
            reset_state=False,
            stockout=stockout,
            critical=critical,
            warning=warning,
            total=total,
            new_skus=[],
            new_reported_skus=sorted(current_set),
            message=None,
        )

    message = _format_message(
        scope_display=scope_display,
        items=items,
        stockout=stockout,
        critical=critical,
        warning=warning,
        new_count=len(new_skus),
        had_prev=bool(prev_set),
        snapshot_at=risk.get("snapshot_at"),
    )
    return StockAlertDecision(
        should_alert=True,
        reset_state=False,
        stockout=stockout,
        critical=critical,
        warning=warning,
        total=total,
        new_skus=new_skus,
        new_reported_skus=sorted(current_set),
        message=message,
    )


def _fmt_snapshot(snapshot_at: Optional[str]) -> str:
    """印尼当地 ISO 串 → "6/13 14:30（印尼时间）"。解析失败则原样兜底。"""
    if not snapshot_at:
        return "未知（暂无快照）"
    try:
        dt = datetime.fromisoformat(snapshot_at)
    except (ValueError, TypeError):
        return f"{snapshot_at}（印尼时间）"
    return f"{dt.month}/{dt.day} {dt.hour:02d}:{dt.minute:02d}（印尼时间）"


def _fmt_item(item: dict) -> str:
    """单条风险明细：'商品名 剩N件·可售~M天'（断货特别标注）。"""
    name = item.get("product_name") or item.get("sku_id") or "未知商品"
    if len(name) > 20:
        name = name[:20] + "…"
    available = int(item.get("available_stock", 0))
    if item.get("bucket") == "stockout" or available <= 0:
        return f"{name} 已断货（仍在出单）"
    cover = item.get("days_of_cover", 0)
    return f"{name} 剩{available}件·可售~{cover}天"


def _format_message(
    *,
    scope_display: str,
    items: list[dict],
    stockout: int,
    critical: int,
    warning: int,
    new_count: int,
    had_prev: bool,
    snapshot_at: Optional[str],
) -> str:
    """组装飞书私聊告警文案（确定性，无 Markdown 表格）。"""
    head_count = f"- 本次新增风险：{new_count} 个 SKU" if had_prev else None

    lines = [
        "🔻 库存预警",
        f"📦 范围：{scope_display}",
        f"🕐 快照：{_fmt_snapshot(snapshot_at)}",
        f"- ⛔ 断货（仍在出单）：{stockout} 个",
        f"- 🔴 告急（可售不足）：{critical} 个",
        f"- 🟠 预警（即将偏低）：{warning} 个",
    ]
    if head_count:
        lines.append(head_count)

    top = items[:_TOP_ITEMS]
    if top:
        lines.append("重点 SKU：")
        lines.extend(f"  • {_fmt_item(it)}" for it in top)
        if len(items) > _TOP_ITEMS:
            lines.append(f"  …等共 {len(items)} 个")

    lines.append("👉 请尽快补货 / 调整库存，避免断货丢失销量。")
    return "\n".join(lines)
