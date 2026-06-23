"""补货采购单飞书文案组装（确定性，无 Markdown 表格，遵循飞书私聊渲染约束）。

输入 = compute_replenishment 的行（已按补货量降序）。每行展示「款号 / 颜色 / 尺码：补 N 件」，
附测算依据（近窗口销量·库存）。在途 MVP=0，文案明确提示，避免运营误以为已扣在途。
"""
from __future__ import annotations

from typing import Optional

_TOP_ITEMS = 15  # 文案最多列几条，其余汇总


_NAME_MAX = 24  # 仅截商品名；色/码永远完整保留（采购单据此下单，不能丢）


def _fmt_item(row: dict) -> str:
    """单条：'款号 / 颜色 / 尺码：补 N 件（销S·存K[·途T]）'。

    印尼语商品名常很长，只截名字本身、永远保留色/码——否则运营不知补哪个变体。
    """
    name = row.get("product_name") or row.get("seller_sku") or row.get("sku_id") or "未知"
    if len(name) > _NAME_MAX:
        name = name[:_NAME_MAX] + "…"
    parts = [name]
    if row.get("color"):
        parts.append(str(row["color"]))
    if row.get("size"):
        parts.append(str(row["size"]))
    label = " / ".join(parts)
    basis = f"销{row.get('units', 0)}·存{row.get('available', 0)}"
    if row.get("intransit"):
        basis += f"·途{row['intransit']}"
    flag = "🔥" if row.get("is_super_hot") else ""
    return f"{flag}{label}：补 {row['replenish_qty']} 件（{basis}）"


def build_replenishment_message(
    rows: list[dict],
    *,
    scope_display: str,
    date_label: str,
    velocity_days: int,
    intransit_connected: bool = False,
) -> Optional[str]:
    """组装补货采购单文案。无待补货 SKU 返回 None（调用方不推空单）。"""
    if not rows:
        return None
    total_qty = sum(r["replenish_qty"] for r in rows)
    lines = [
        "📦 补货建议",
        f"🏪 范围：{scope_display}",
        f"📅 {date_label}（按近 {velocity_days} 天销量测算）",
        f"共 {len(rows)} 个 SKU 待补货，合计 {total_qty} 件：",
    ]
    for r in rows[:_TOP_ITEMS]:
        lines.append(f"  • {_fmt_item(r)}")
    if len(rows) > _TOP_ITEMS:
        lines.append(f"  …等共 {len(rows)} 个")
    if not intransit_connected:
        lines.append("⚠️ 在途按 0 估（采购在途数据未接通），请结合实际在途量调整。")
    lines.append("👉 🔥=超级爆品（已用更高系数）。如需改数量/跳过/标爆品，回复运营。")
    return "\n".join(lines)
