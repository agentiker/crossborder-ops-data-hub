"""看板「爆款商品」卡回归锁：商品级聚合(款号/规格数/小图 join) + 单品渠道 4 分解析。

Part A：get_top_products 按 product_id 聚合，带 seller_sku(款号)/sku_count(规格数)/image_url(小图)。
Part C：product_channel_metrics 把 202605 交叉拆分归并成 4 分(达人/自营素材/商品卡/店铺页)，
        各项之和=total；缺字段=0；无 analytics 数据 → available=False。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from models.base_models import OrderHeader, OrderLineItem, Product
from services.order_metrics import get_product_sku_breakdown, get_top_products
from services.product_channel_metrics import (
    _segments_from_product,
    get_product_channel_breakdown,
)


# ── Part A：商品级聚合 ──────────────────────────────────────────────────────

def _order(session, oid, paid=datetime(2026, 6, 1, 12, 0)):
    session.add(OrderHeader(
        platform="tiktok_shop", country="ID", shop_id="shop-1",
        order_id=oid, idempotency_key=f"ik-{oid}", total_amount=Decimal("100"),
        currency="IDR", paid_time=paid,
    ))


def _line(session, lid, oid, pid, sku, seller_sku=None, name="商品A", price="10"):
    session.add(OrderLineItem(
        platform="tiktok_shop", country="ID", shop_id="shop-1",
        idempotency_key=f"li-{lid}", line_item_id=lid, order_id=oid,
        product_id=pid, sku_id=sku, seller_sku=seller_sku,
        product_name=name, sku_name=name, sale_price=Decimal(price),
    ))


def test_top_products_aggregates_by_product_with_code_and_image(session):
    _order(session, "o1")
    _order(session, "o2")
    # 商品 p1：两个不同 SKU 共 3 件(跨两单)；p2：单 SKU 1 件
    _line(session, "l1", "o1", "p1", "sku-a", "CODE-A", "连衣裙", "10")
    _line(session, "l2", "o1", "p1", "sku-b", "CODE-B", "连衣裙", "12")
    _line(session, "l3", "o2", "p1", "sku-a", "CODE-A", "连衣裙", "10")
    _line(session, "l4", "o2", "p2", "sku-c", "CODE-C", "短袖", "20")
    # 商品主数据带主图(join 来源)
    session.add(Product(
        platform="tiktok_shop", country="ID", shop_id="shop-1",
        idempotency_key="p1key", product_id="p1", title="连衣裙",
        status="ACTIVATE", main_image_url="https://img/p1.jpg",
    ))
    session.commit()

    rows = get_top_products(
        start_date=date(2026, 6, 1), end_date=date(2026, 6, 1),
        platform="tiktok_shop", country="ID", shop_ids=["shop-1"], session=session,
    )
    by_id = {r["product_id"]: r for r in rows}
    # p1 排第一(3 件 > 1 件)
    assert rows[0]["product_id"] == "p1"
    assert by_id["p1"]["units_sold"] == 3
    assert by_id["p1"]["sku_count"] == 2          # 两个不同 SKU → 前端显「2 个规格」
    assert by_id["p1"]["gmv"] == 32.0             # 10+12+10
    assert by_id["p1"]["image_url"] == "https://img/p1.jpg"
    # p2 单 SKU、无 Product 行 → image_url 为 None
    assert by_id["p2"]["sku_count"] == 1
    assert by_id["p2"]["image_url"] is None
    assert by_id["p2"]["seller_sku"] == "CODE-C"


def test_product_sku_breakdown_groups_by_sku(session):
    _order(session, "o1")
    _order(session, "o2")
    # 商品 p1：sku-a 两件、sku-b 一件；商品 p2 的 SKU 不应混入
    _line(session, "l1", "o1", "p1", "sku-a", "CODE-A", "连衣裙-红", "10")
    _line(session, "l2", "o2", "p1", "sku-a", "CODE-A", "连衣裙-红", "10")
    _line(session, "l3", "o2", "p1", "sku-b", "CODE-B", "连衣裙-蓝", "12")
    _line(session, "l4", "o1", "p2", "sku-c", "CODE-C", "短袖", "20")
    session.commit()

    rows = get_product_sku_breakdown(
        product_id="p1", start_date=date(2026, 6, 1), end_date=date(2026, 6, 1),
        platform="tiktok_shop", country="ID", shop_ids=["shop-1"], session=session,
    )
    assert [r["sku_id"] for r in rows] == ["sku-a", "sku-b"]  # 按销量降序
    assert rows[0]["units_sold"] == 2 and rows[0]["sku_name"] == "连衣裙-红"
    assert rows[1]["units_sold"] == 1
    # 不含其它商品的 SKU
    assert all(r["sku_id"] != "sku-c" for r in rows)


# ── Part C：单品渠道 4 分解析 ────────────────────────────────────────────────

def _perf(amount):
    return {"attributed_gmv": {"amount": str(amount), "currency": "IDR"}}


def test_segments_four_way_sum_equals_total():
    p = {
        "id": "X",
        "affiliate_total_performance": _perf(30),
        "seller_live_performance": _perf(10),
        "seller_video_performance": _perf(20),
        "seller_product_card_performance": _perf(25),
        "shop_tab_performance": _perf(15),
    }
    seg, cur = _segments_from_product(p)
    assert cur == "IDR"
    assert seg == {"affiliate": 30.0, "seller_content": 30.0, "product_card": 25.0, "shop_tab": 15.0}
    assert sum(seg.values()) == 100.0


def test_segments_affiliate_falls_back_to_live_plus_video():
    # 无 affiliate_total → 用 affiliate_live + affiliate_video 兜
    p = {
        "id": "Y",
        "affiliate_live_performance": _perf(5),
        "affiliate_video_performance": _perf(7),
    }
    seg, _ = _segments_from_product(p)
    assert seg["affiliate"] == 12.0


def test_product_channel_breakdown_available_and_pct(monkeypatch):
    import services.product_channel_metrics as mod

    monkeypatch.setattr(mod, "_discover_shops", lambda c, s: [{"country": "ID", "shop_id": "shop-1"}])

    class _FakeClient:
        def __init__(self, **kw):
            pass

        def get_shop_products_performance(self, start, end, **kw):
            return [{
                "id": "p1",
                "affiliate_total_performance": _perf(40),
                "seller_live_performance": _perf(20),
                "seller_video_performance": _perf(20),
                "seller_product_card_performance": _perf(15),
                "shop_tab_performance": _perf(5),
            }]

    monkeypatch.setattr(mod, "TikTokShopClient", _FakeClient)

    res = get_product_channel_breakdown(
        product_id="p1", start_date=date(2026, 6, 1), end_date=date(2026, 6, 7),
        country="ID", shop_ids=["shop-1"],
    )
    assert res["available"] is True
    assert res["total_gmv"] == 100.0
    pct = {c["key"]: c["pct"] for c in res["channels"]}
    assert pct == {"affiliate": 40.0, "seller_content": 40.0, "product_card": 15.0, "shop_tab": 5.0}


def test_product_channel_breakdown_degrades_when_no_data(monkeypatch):
    import services.product_channel_metrics as mod

    monkeypatch.setattr(mod, "_discover_shops", lambda c, s: [{"country": "ID", "shop_id": "shop-1"}])

    class _NoneClient:
        def __init__(self, **kw):
            pass

        def get_shop_products_performance(self, start, end, **kw):
            return None  # 沙箱/无权限/报错

    monkeypatch.setattr(mod, "TikTokShopClient", _NoneClient)

    res = get_product_channel_breakdown(
        product_id="p1", start_date=date(2026, 6, 2), end_date=date(2026, 6, 8),
        country="ID", shop_ids=["shop-1"],
    )
    assert res["available"] is False
    assert res["total_gmv"] == 0.0
