"""看板「近 30 天新品」卡回归锁 + 爆单告警「标注新品」增强。

Part A：get_new_product_trends —— 选品(近30天上线·ACTIVATE·有销量)、每日销量曲线补零、
        burst 判定(峰值单日 ≥ threshold)、爆单优先排序；get_new_product_ids 集合。
Part B：hotsell_alerts.build_decision 传 new_product_ids 时新品项带 🌟 + 尾注；不传则文案逐字不变。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from models.base_models import OrderHeader, OrderLineItem, Product
from services import hotsell_alerts
from services.order_metrics import get_new_product_ids, get_new_product_trends

AS_OF = date(2026, 6, 28)


def _order(session, oid, paid: datetime):
    session.add(OrderHeader(
        platform="tiktok_shop", country="ID", shop_id="shop-1",
        order_id=oid, idempotency_key=f"ik-{oid}", total_amount=Decimal("100"),
        currency="IDR", paid_time=paid,
    ))


def _line(session, lid, oid, pid, sku, seller_sku=None, name="商品", price="10"):
    session.add(OrderLineItem(
        platform="tiktok_shop", country="ID", shop_id="shop-1",
        idempotency_key=f"li-{lid}", line_item_id=lid, order_id=oid,
        product_id=pid, sku_id=sku, seller_sku=seller_sku,
        product_name=name, sku_name=name, sale_price=Decimal(price),
    ))


def _product(session, pid, created: datetime, status="ACTIVATE", title="新品"):
    session.add(Product(
        platform="tiktok_shop", country="ID", shop_id="shop-1",
        idempotency_key=f"{pid}key", product_id=pid, title=title,
        status=status, source_create_time=created, main_image_url=f"https://img/{pid}.jpg",
    ))


# 午间 UTC → +7 印尼仍同日，归业务日确定。
def _at(d: date, hour=6) -> datetime:
    return datetime(d.year, d.month, d.day, hour, 0)


# ── Part A ──────────────────────────────────────────────────────────────────

def test_new_product_trends_selection_series_and_burst(session):
    # p1 新品爆款：6/24 售 1 件、6/25 售 2 件 → 峰值 2（threshold=2 触发 burst）
    _product(session, "p1", _at(date(2026, 6, 20)), title="爆款新品")
    # p2 新品平稳：6/22 售 1 件 → 峰值 1，不爆
    _product(session, "p2", _at(date(2026, 6, 21)), title="平稳新品")
    # p_old 旧品（>30天前上线）有销量 → 不算新品，排除
    _product(session, "pold", _at(date(2026, 4, 1)), title="旧品")
    # p_nosale 近30天上线但无销量 → 排除（只展示已起量）
    _product(session, "pnos", _at(date(2026, 6, 23)), title="未起量")
    # p_draft 近30天上线有销量但非在售 → status 过滤排除
    _product(session, "pdraft", _at(date(2026, 6, 24)), status="DRAFT", title="草稿")

    _order(session, "o1", _at(date(2026, 6, 24)))
    _order(session, "o2", _at(date(2026, 6, 25)))
    _order(session, "o3", _at(date(2026, 6, 22)))
    _order(session, "o4", _at(date(2026, 6, 26)))
    _line(session, "l1", "o1", "p1", "p1-a", "CODE-1")
    _line(session, "l2", "o2", "p1", "p1-a", "CODE-1")
    _line(session, "l3", "o2", "p1", "p1-b", "CODE-1")  # 同日第二件 → 6/25 峰值 2
    _line(session, "l4", "o3", "p2", "p2-a", "CODE-2")
    _line(session, "l5", "o1", "pold", "po-a")          # 旧品有销量
    _line(session, "l6", "o4", "pdraft", "pd-a")        # 草稿有销量
    session.commit()

    rows = get_new_product_trends(
        as_of=AS_OF, lookback_days=30, threshold=2,
        platform="tiktok_shop", country="ID", shop_ids=["shop-1"], session=session,
    )
    ids = [r["product_id"] for r in rows]
    # 仅 p1 / p2 入选（旧品 / 无销量 / 草稿全排除）
    assert ids == ["p1", "p2"]  # 爆单(p1)优先，再按总销量
    p1 = rows[0]
    assert p1["burst"] is True and p1["peak_units"] == 2 and p1["peak_date"] == "2026-06-25"
    assert p1["total_units"] == 3
    assert p1["sku_count"] == 2 and p1["seller_sku"] == "CODE-1"
    assert p1["days_online"] == 8  # 6/28 - 6/20
    assert p1["image_url"] == "https://img/p1.jpg"
    # 曲线从上线业务日(6/20)连续补零到 as_of(6/28) = 9 天
    assert [s["date"] for s in p1["series"]][0] == "2026-06-20"
    assert len(p1["series"]) == 9
    by_day = {s["date"]: s["units"] for s in p1["series"]}
    assert by_day["2026-06-24"] == 1 and by_day["2026-06-25"] == 2 and by_day["2026-06-21"] == 0
    # p2 不爆
    assert rows[1]["product_id"] == "p2" and rows[1]["burst"] is False and rows[1]["peak_units"] == 1


def test_new_product_trends_burst_threshold_boundary(session):
    # 峰值正好等于 threshold → burst True；低 1 → False
    _product(session, "pa", _at(date(2026, 6, 20)))
    _order(session, "oa1", _at(date(2026, 6, 25)))
    _order(session, "oa2", _at(date(2026, 6, 25)))
    _line(session, "la1", "oa1", "pa", "pa-a")
    _line(session, "la2", "oa2", "pa", "pa-a")  # 6/25 两件
    session.commit()

    hit = get_new_product_trends(as_of=AS_OF, threshold=2, shop_ids=["shop-1"], session=session)
    assert hit[0]["burst"] is True
    miss = get_new_product_trends(as_of=AS_OF, threshold=3, shop_ids=["shop-1"], session=session)
    assert miss[0]["burst"] is False


def test_new_product_ids_set(session):
    _product(session, "p1", _at(date(2026, 6, 20)))
    _product(session, "p2", _at(date(2026, 6, 26)))
    _product(session, "pold", _at(date(2026, 4, 1)))
    _product(session, "pdraft", _at(date(2026, 6, 24)), status="DRAFT")
    session.commit()

    ids = get_new_product_ids(as_of=AS_OF, lookback_days=30, shop_ids=["shop-1"], session=session)
    assert ids == {"p1", "p2"}  # 旧品超窗排除、草稿非在售排除；无需销量


# ── Part B：爆单告警「标注新品」增强 ─────────────────────────────────────────

def _decision(new_ids):
    return hotsell_alerts.build_decision(
        units_by_product={
            "p1": {"units": 60, "product_name": "新款连衣裙"},
            "p2": {"units": 55, "product_name": "经典T恤"},
        },
        threshold=50, prev_reported_ids=[], new_product_ids=new_ids,
        scope_display="测试店", date_label="6/28",
    )


def test_hotsell_tags_new_product_with_star():
    d = _decision({"p1"})
    assert d.should_alert is True
    # 新品 p1 带 🌟，存量 p2 不带
    assert "🌟 新款连衣裙 今日已售 60 件" in d.message
    assert "🌟 经典T恤" not in d.message
    assert "经典T恤 今日已售 55 件" in d.message
    # 有新品爆发 → 追加图例尾注
    assert "🌟 = 新上线爆款" in d.message
    by_id = {p["product_id"]: p for p in d.new_products}
    assert by_id["p1"]["is_new"] is True and by_id["p2"]["is_new"] is False


def test_hotsell_no_new_product_message_unchanged():
    """不传 new_product_ids（默认空集）→ 无 🌟、无尾注，存量爆单文案逐字不变（回归锁）。"""
    d = _decision(None)
    assert "🌟" not in d.message
    assert "新上线爆款" not in d.message
    assert "新款连衣裙 今日已售 60 件" in d.message
    assert all(p["is_new"] is False for p in d.new_products)
