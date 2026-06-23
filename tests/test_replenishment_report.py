"""补货采购单文案 formatter 单测：空单返回 None、款号/颜色/尺码/合计、超级爆品标、在途提示、截断。"""
from __future__ import annotations

from services.replenishment_report import build_replenishment_message


def _row(sku_id, qty, *, name="连衣裙", color="Red", size="XL", units=30, avail=10,
         intransit=0, is_super_hot=False, seller_sku=None):
    return {
        "sku_id": sku_id, "replenish_qty": qty, "product_name": name, "color": color,
        "size": size, "units": units, "available": avail, "intransit": intransit,
        "is_super_hot": is_super_hot, "seller_sku": seller_sku,
    }


_KW = dict(scope_display="印尼测试店", date_label="6/23", velocity_days=30)


def test_empty_returns_none():
    assert build_replenishment_message([], **_KW) is None


def test_message_has_header_total_and_item():
    msg = build_replenishment_message([_row("s1", 35)], **_KW)
    assert "📦 补货建议" in msg
    assert "印尼测试店" in msg
    assert "近 30 天" in msg
    assert "共 1 个 SKU 待补货，合计 35 件" in msg
    assert "连衣裙 / Red / XL：补 35 件（销30·存10）" in msg


def test_intransit_shown_when_present():
    msg = build_replenishment_message([_row("s1", 10, intransit=25)], **_KW)
    assert "途25" in msg


def test_intransit_not_connected_warning():
    msg = build_replenishment_message([_row("s1", 35)], intransit_connected=False, **_KW)
    assert "在途按 0 估" in msg


def test_intransit_connected_no_warning():
    msg = build_replenishment_message([_row("s1", 35)], intransit_connected=True, **_KW)
    assert "在途按 0 估" not in msg


def test_super_hot_flagged():
    msg = build_replenishment_message([_row("s1", 40, is_super_hot=True)], **_KW)
    assert "🔥" in msg


def test_truncates_to_top_items():
    rows = [_row(f"s{i}", 100 - i) for i in range(20)]
    msg = build_replenishment_message(rows, **_KW)
    assert "…等共 20 个" in msg
    # 合计仍是全部 20 条之和
    total = sum(100 - i for i in range(20))
    assert f"合计 {total} 件" in msg


def test_fallback_label_when_no_name():
    msg = build_replenishment_message(
        [_row("s1", 5, name=None, color=None, size=None, seller_sku="SS-1")], **_KW
    )
    assert "SS-1：补 5 件" in msg
