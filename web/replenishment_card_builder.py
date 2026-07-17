"""补货采购单 → 飞书 v2 CardKit 卡片 JSON（后端确定性拼装）。

与日报 report_card_builder / 告警 alert_card_builder 同一套 CardKit 组件与方案A深色系
风格——**复用 alert_card_builder 的通用组件**（_md/_hr/_table/_kpi_columns/_card），
不重造轮子。补货是「建议采购」非告急/喜报，用 indigo 头；明细用 table 对齐
销量/库存/补货量，超级爆品行首标 🔥。纯函数无副作用，便于单测。

投递：push_replenishment 优先 send_interactive_card（卡片），失败回落 send_feishu_message
（openclaw 文本）——与告警 send_alert 同链路、同回退策略，凭证缺失/卡片 JSON 错时仍能发文本不丢消息。
"""
from __future__ import annotations

from typing import Optional

# 复用告警卡片的通用 CardKit 组件（_前缀=模块私有，但同属卡片层，跨 builder 复用）。
# 未来若再添卡片 builder，宜提取共享 card_kit 模块；当前仅补货一处复用，避免动 report/alert 引入回归。
from web.alert_card_builder import (
    TEMPLATE_WARNING,
    _card,
    _hr,
    _kpi_columns,
    _md,
    _table,
)

_NAME_MAX = 24  # 同 replenishment_report：截商品名，色/码永远完整保留（采购单据此下单）


def _item_name(row: dict) -> str:
    """商品行标签：优先 Seller SKU（采购下单用的短码，比印尼语长商品名简短、移动端不挤）；
    seller_sku 缺失才回退 '商品名 / 颜色 / 尺码' 保证可辨识。超级爆品行首加 🔥。"""
    sku = row.get("seller_sku")
    if sku:
        label = str(sku)
    else:
        name = row.get("product_name") or row.get("sku_id") or "未知"
        if len(name) > _NAME_MAX:
            name = name[:_NAME_MAX] + "…"
        parts = [name]
        if row.get("color"):
            parts.append(str(row["color"]))
        if row.get("size"):
            parts.append(str(row["size"]))
        label = " / ".join(parts)
    if row.get("is_super_hot"):
        label = "🔥 " + label
    return label


def build_replenishment_card(
    rows: list[dict],
    *,
    scope_display: str,
    date_label: str,
    velocity_days: int,
    intransit_connected: bool = False,
) -> Optional[dict]:
    """补货采购单 → CardKit 卡片 JSON。无待补货 SKU 返回 None（调用方不发空单）。

    rows = compute_replenishment 的输出行（已按补货量降序），每行含 seller_sku/product_name/
    color/size/daily_velocity/available/replenish_qty/is_super_hot。
    """
    if not rows:
        return None
    total_qty = sum(int(r.get("replenish_qty") or 0) for r in rows)
    elements: list[dict] = [
        _md(f"共 <font color='red'>**{len(rows)}**</font> 个 SKU 待补货，"
            f"合计 <font color='red'>**{total_qty}**</font> 件"
            f"<font color='grey'>（按近 {velocity_days} 天销量测算）</font>"),
        _kpi_columns([("待补 SKU", f"{len(rows)}"), ("合计", f"{total_qty} 件"),
                      ("测算窗口", f"{velocity_days} 天")]),
    ]

    if rows:
        elements.append(_hr())
        table_rows = [
            {
                "name": _item_name(r),
                "daily": f"{(r.get('daily_velocity') or 0):.1f}",  # 日均销速 件/天
                "stock": str(int(r.get("available") or 0)),
                "qty": str(int(r.get("replenish_qty") or 0)),
            }
            for r in rows
        ]
        # 列宽：Seller SKU 占大头（够显 12 字符编码如 809-KKHM-2XL），日均/库存/补货右对齐数字。
        # CardKit v2 表格不支持按内容自适应，须手动配百分比（和=100%）。
        elements.append(_table(
            [
                {"name": "name", "display_name": "Seller SKU", "data_type": "text",
                 "width": "46%", "horizontal_align": "left"},
                {"name": "daily", "display_name": "日均", "data_type": "text",
                 "width": "14%", "horizontal_align": "right"},
                {"name": "stock", "display_name": "库存", "data_type": "text",
                 "width": "18%", "horizontal_align": "right"},
                {"name": "qty", "display_name": "补货", "data_type": "text",
                 "width": "22%", "horizontal_align": "right"},
            ],
            table_rows,
        ))

    elements.append(_hr())
    if not intransit_connected:
        elements.append(_md(
            "<font color='orange'>⚠️ 在途按 0 估（采购在途数据未接通），"
            "请结合实际在途量调整补货数。</font>"
        ))
    elements.append(_md(f"<font color='grey'>🕐 {date_label}</font>"))
    return _card(TEMPLATE_WARNING, "📦 补货采购单", scope_display, elements)
