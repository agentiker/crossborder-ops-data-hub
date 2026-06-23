"""SKU 变体 parse + upsert + prune 单测。

parse_sku_variants：从 get_product data.skus[].sales_attributes 解析颜色/尺码（含中文属性名、
缺属性、无 sku id 跳过、title 取 data.title 回退 titles 映射）。
upsert/prune：按 sku_id 幂等、清退不在本次集合的变体、空集不删。
"""
from __future__ import annotations

from core.domain import DomainSkuVariant
from models.base_models import SkuVariant
from services.sku_variant_store import (
    parse_sku_variants,
    prune_sku_variants_not_in,
    upsert_sku_variants,
)


def _detail(title, skus):
    return {"title": title, "skus": skus}


def _sku(sku_id, attrs, seller_sku=None):
    return {"id": sku_id, "seller_sku": seller_sku, "sales_attributes": attrs}


# ── parse ────────────────────────────────────────────────────────────────────

def test_parse_color_size_from_attributes():
    details = {"p1": _detail("连衣裙", [
        _sku("s1", [
            {"name": "Color", "value_name": "Red"},
            {"name": "Size", "value_name": "XL"},
        ], seller_sku="DRESS-RED-XL"),
    ])}
    out = parse_sku_variants(details)
    assert len(out) == 1
    v = out[0]
    assert v.sku_id == "s1"
    assert v.product_id == "p1"
    assert v.product_name == "连衣裙"
    assert v.color == "Red"
    assert v.size == "XL"
    assert v.seller_sku == "DRESS-RED-XL"
    assert {"name": "Color", "value_name": "Red"} in v.attributes


def test_parse_chinese_attribute_names():
    details = {"p1": _detail("短袖", [
        _sku("s1", [{"name": "颜色", "value_name": "黑色"}, {"name": "尺码", "value_name": "M"}]),
    ])}
    v = parse_sku_variants(details)[0]
    assert v.color == "黑色"
    assert v.size == "M"


def test_parse_indonesian_attribute_names():
    """实测 TikTok 印尼店属性名为 'Warna'(颜色)/'ukuran kasur'(尺寸)，须命中。回归 hp 真数据 bug。"""
    details = {"p1": _detail("Kasur", [
        _sku("s1", [
            {"name": "Warna", "value_name": "Hitam"},
            {"name": "ukuran kasur", "value_name": "90 x 200 x 30cm"},
        ]),
    ])}
    v = parse_sku_variants(details)[0]
    assert v.color == "Hitam"
    assert v.size == "90 x 200 x 30cm"


def test_parse_missing_attributes_default_none():
    details = {"p1": _detail("无属性品", [_sku("s1", [])])}
    v = parse_sku_variants(details)[0]
    assert v.color is None and v.size is None
    assert v.attributes is None


def test_parse_skips_sku_without_id():
    details = {"p1": _detail("x", [
        {"sales_attributes": [{"name": "Color", "value_name": "Red"}]},  # 无 id
        _sku("s2", [{"name": "Size", "value_name": "L"}]),
    ])}
    out = parse_sku_variants(details)
    assert [v.sku_id for v in out] == ["s2"]


def test_parse_title_fallback_to_titles_map():
    details = {"p1": {"skus": [_sku("s1", [])]}}  # data 无 title
    v = parse_sku_variants(details, titles={"p1": "兜底标题"})[0]
    assert v.product_name == "兜底标题"


# ── upsert / prune ───────────────────────────────────────────────────────────

def _v(sku_id, *, color="Red", size="M", product_id="p1"):
    return DomainSkuVariant(
        sku_id=sku_id, product_id=product_id, seller_sku=f"sk-{sku_id}",
        product_name="商品", color=color, size=size, attributes=[{"name": "Color", "value_name": color}],
    )


def test_upsert_idempotent_and_updates(session):
    n1 = upsert_sku_variants(session, [_v("s1", color="Red")], shop_id="shop-1", raw_response_id=1)
    session.commit()
    n2 = upsert_sku_variants(session, [_v("s1", color="Blue")], shop_id="shop-1", raw_response_id=2)
    session.commit()
    assert (n1, n2) == (1, 1)
    rec = session.query(SkuVariant).one()
    assert rec.color == "Blue"  # 刷新
    assert rec.raw_response_id == 2


def test_prune_removes_absent_variants(session):
    upsert_sku_variants(session, [_v("s1"), _v("s2")], shop_id="shop-1")
    session.commit()
    # 本次只剩 s1 → s2 被清退
    pruned = prune_sku_variants_not_in(session, [_v("s1")], shop_id="shop-1")
    assert pruned == 1
    assert {r.sku_id for r in session.query(SkuVariant).all()} == {"s1"}


def test_prune_empty_keeps_all(session):
    upsert_sku_variants(session, [_v("s1")], shop_id="shop-1")
    session.commit()
    assert prune_sku_variants_not_in(session, [], shop_id="shop-1") == 0
    assert session.query(SkuVariant).count() == 1
