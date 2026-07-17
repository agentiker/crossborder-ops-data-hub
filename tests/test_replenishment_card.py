"""补货采购单卡片 builder 单测：Seller SKU 列、日均销速列、seller_sku 缺失回退、超级爆品标。

按 JSON 子串断言——CardKit 结构细节（column/row schema）由 alert_card_builder 的 _table 负责，
此处只验我们关心的「列改了、值渲染了、短码优先」不回归。
"""
import json

from web.replenishment_card_builder import build_replenishment_card


def _row(sku_id, *, seller_sku, daily, qty, avail=0, is_super_hot=False,
         name="连衣裙", color="Red", size="XL", units=30):
    return {
        "sku_id": sku_id, "seller_sku": seller_sku, "product_name": name,
        "color": color, "size": size, "units": units, "daily_velocity": daily,
        "available": avail, "replenish_qty": qty, "is_super_hot": is_super_hot,
    }


_KW = dict(scope_display="印尼测试店", date_label="6/23", velocity_days=30)


def _dump(rows):
    card = build_replenishment_card(rows, **_KW)
    assert card is not None
    return json.dumps(card, ensure_ascii=False)


def test_empty_returns_none():
    assert build_replenishment_card([], **_KW) is None


def test_columns_and_daily_value():
    s = _dump([_row("s1", seller_sku="SS-连衣裙红M", daily=3.2, qty=40, avail=5)])
    assert "Seller SKU" in s      # 表头由「商品」改为 Seller SKU
    assert "日均" in s            # 「销量」列改为「日均」
    assert "库存" in s and "补货" in s
    assert "3.2" in s             # 日均销速数值（件/天，保留 1 位）
    assert "SS-连衣裙红M" in s    # 行标签用 seller_sku


def test_name_prefers_seller_sku_over_long_name():
    """有 seller_sku 时用短码，印尼语长商品名不出现（移动端不挤）。"""
    s = _dump([_row("s1", seller_sku="SS-1", daily=1.5, qty=18,
                    name="MossWood Kasur Spring Bed Orthopedic Premium")])
    assert "SS-1" in s
    assert "MossWood" not in s


def test_fallback_to_product_name_when_no_seller_sku():
    """seller_sku 缺失时回退「商品名 / 颜色 / 尺码」保证可辨识（采购单据此下单）。"""
    s = _dump([_row("s1", seller_sku=None, daily=1.5, qty=18, name="连衣裙",
                    color="Red", size="M")])
    assert "连衣裙" in s and "Red" in s and "M" in s


def test_super_hot_flagged():
    s = _dump([_row("s1", seller_sku="SS-1", daily=8.1, qty=81, is_super_hot=True)])
    assert "🔥" in s
