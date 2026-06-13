"""待发货超时告警：确定性判定 + 飞书私聊文案组装（不碰 DB / 不发消息，纯逻辑可单测）。

监控链路分三层，本模块只负责中间的「判定 + 文案」：
- 取数：flows/scan_fulfillment_alerts 调 services.fulfillment_metrics.get_pending_fulfillments
        （复用现成的超时分桶 buckets / 分店 by_shop，不在这里重算公式）。
- 判定+文案：本模块 build_decision —— 决定「这次该不该推、推什么文案」。
- 去重游标 + 投递：flow 读写 FulfillmentAlertState、subprocess 调 openclaw message send。

去重规则（避免同一批超时单每 30 分钟刷屏）：
- overdue == 0：不推，并请求把游标重置为 0（下次哪怕只新增 1 单也会重新提醒）。
- overdue > 上次已上报值：推，文案带「较上次 +N」。
- 0 < overdue <= 上次已上报值：不推，游标不动（持平/下降不复读）。

文案遵循飞书私聊渲染约束（emoji + 粗体 + 短列表，无 Markdown 表格 / 多级标题），
与 docs/feishu-bot-onboarding.md、SKILL.md 既有约定一致。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

ALERT_TYPE = "fulfillment_overdue"

# 分店明细最多列几条（按超时单数降序），其余汇总省略
_TOP_SHOPS = 3


@dataclass
class AlertDecision:
    """一次评估的结论。should_alert 决定是否投递；reset_state 决定游标是否归零。"""

    should_alert: bool
    reset_state: bool  # True=请求把 last_reported_overdue 重置为 0（overdue 已清零时）
    overdue: int
    critical: int
    total: int
    delta: int  # 较上次已上报值的增量（仅 should_alert 时有意义）
    new_reported_overdue: int  # 推送后应写回的已上报值
    message: Optional[str]  # 待投递的飞书文案；不推时为 None


def build_decision(
    *,
    metrics: dict,
    scope_display: str,
    prev_reported: int,
) -> AlertDecision:
    """根据待发货分桶 + 上次已上报值，确定性地决定是否告警并组装文案。

    metrics：services.fulfillment_metrics.get_pending_fulfillments 的返回（含 buckets/by_shop/snapshot_at）。
    scope_display：范围展示名（ScopeFilters.display_text，如 "TikTok Shop / 印尼 / 3 个店铺"）。
    prev_reported：上次已推送的超时单数（无历史则传 0）。
    """
    buckets = metrics.get("buckets") or {}
    overdue = int(buckets.get("overdue", 0))
    critical = int(buckets.get("critical", 0))
    total = int(buckets.get("total", 0))

    if overdue == 0:
        # 这批超时单已清零：不推，但把游标归零，让下一批新增能重新触发。
        return AlertDecision(
            should_alert=False,
            reset_state=True,
            overdue=0,
            critical=critical,
            total=total,
            delta=0,
            new_reported_overdue=0,
            message=None,
        )

    if overdue <= prev_reported:
        # 持平或下降：不复读，游标保持不动。
        return AlertDecision(
            should_alert=False,
            reset_state=False,
            overdue=overdue,
            critical=critical,
            total=total,
            delta=0,
            new_reported_overdue=prev_reported,
            message=None,
        )

    delta = overdue - prev_reported
    message = _format_message(
        scope_display=scope_display,
        overdue=overdue,
        critical=critical,
        delta=delta,
        prev_reported=prev_reported,
        by_shop=metrics.get("by_shop") or [],
        snapshot_at=metrics.get("snapshot_at"),
    )
    return AlertDecision(
        should_alert=True,
        reset_state=False,
        overdue=overdue,
        critical=critical,
        total=total,
        delta=delta,
        new_reported_overdue=overdue,
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


def _format_message(
    *,
    scope_display: str,
    overdue: int,
    critical: int,
    delta: int,
    prev_reported: int,
    by_shop: list[dict],
    snapshot_at: Optional[str],
) -> str:
    """组装飞书私聊告警文案（确定性，无 Markdown 表格）。"""
    # 超时行：首次（prev=0）只报数；之后带「较上次 +N」
    overdue_line = f"- 已超时：{overdue} 单"
    if prev_reported > 0:
        overdue_line += f"（较上次 +{delta}）"

    lines = [
        "🚨 待发货超时预警",
        f"📦 范围：{scope_display}",
        f"🕐 快照：{_fmt_snapshot(snapshot_at)}",
        overdue_line,
        f"- 临界（24h 内截止）：{critical} 单",
    ]

    top = sorted(
        (s for s in by_shop if int(s.get("overdue", 0)) > 0),
        key=lambda s: int(s.get("overdue", 0)),
        reverse=True,
    )[:_TOP_SHOPS]
    if top:
        shop_str = "、".join(
            f"{s.get('shop_id') or '未知店铺'} {int(s['overdue'])}单" for s in top
        )
        lines.append(f"- 重点店铺：{shop_str}")

    lines.append("👉 请尽快在后台安排发货/揽收，避免平台超时自动取消。")
    return "\n".join(lines)
