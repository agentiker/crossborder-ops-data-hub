"""低库存/断货告警：build_decision 去重分支（纯函数）+ get_stock_risk 分桶口径（带 DB）单测。"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.db import Base
from models.base_models import Inventory, Product
from services import stock_alerts, stock_metrics


# ── build_decision（纯函数，按风险 SKU 集合去重）────────────────────────────────

def _item(sku, bucket, *, stock=5, cover=2.0, vel=2.0, name=None):
    return {
        "sku_id": sku,
        "product_name": name or f"商品{sku}",
        "shop_id": "s1",
        "available_stock": stock,
        "daily_velocity": vel,
        "days_of_cover": cover,
        "bucket": bucket,
    }


def _risk(items, snapshot="2026-06-13T14:30:00"):
    b = {"stockout": 0, "critical": 0, "warning": 0, "total": 0}
    for i in items:
        b[i["bucket"]] += 1
        b["total"] += 1
    return {"items": items, "buckets": b, "snapshot_at": snapshot}


def test_empty_risk_resets_state():
    d = stock_alerts.build_decision(risk=_risk([]), scope_display="x", prev_reported_skus=["A"])
    assert d.should_alert is False
    assert d.reset_state is True
    assert d.new_reported_skus == []
    assert d.message is None


def test_new_skus_alert_no_history():
    items = [_item("A", "stockout", stock=0, cover=0.0), _item("B", "critical")]
    d = stock_alerts.build_decision(risk=_risk(items), scope_display="印尼", prev_reported_skus=[])
    assert d.should_alert is True
    assert set(d.new_skus) == {"A", "B"}
    assert d.new_reported_skus == ["A", "B"]
    # 首次（无历史）不显示「本次新增风险」行
    assert "本次新增风险" not in d.message
    assert "🔻 库存预警" in d.message
    assert "|" not in d.message  # 无 Markdown 表格


def test_same_set_does_not_alert():
    items = [_item("A", "critical"), _item("B", "warning")]
    d = stock_alerts.build_decision(risk=_risk(items), scope_display="x", prev_reported_skus=["A", "B"])
    assert d.should_alert is False
    assert d.new_reported_skus == ["A", "B"]  # 游标收敛到当前集
    assert d.message is None


def test_churn_alerts_only_new_and_drops_recovered():
    # 上次报过 A、B；现在 A 恢复（不在风险），B 仍在，C 新进 → 只报 C，游标=B,C
    items = [_item("B", "critical"), _item("C", "warning")]
    d = stock_alerts.build_decision(risk=_risk(items), scope_display="x", prev_reported_skus=["A", "B"])
    assert d.should_alert is True
    assert d.new_skus == ["C"]
    assert d.new_reported_skus == ["B", "C"]
    assert "本次新增风险：1 个" in d.message


def test_recovered_then_redrop_realerts():
    # A 恢复后游标清掉 A；再次跌入风险应重报
    d1 = stock_alerts.build_decision(risk=_risk([]), scope_display="x", prev_reported_skus=["A"])
    assert d1.new_reported_skus == []
    d2 = stock_alerts.build_decision(
        risk=_risk([_item("A", "critical")]), scope_display="x", prev_reported_skus=d1.new_reported_skus
    )
    assert d2.should_alert is True
    assert d2.new_skus == ["A"]


# ── get_stock_risk（分桶口径，带 DB）────────────────────────────────────────────

@pytest.fixture()
def db(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(stock_metrics, "SessionLocal", Session)
    # 固定业务日，避免依赖真实日期
    monkeypatch.setattr(stock_metrics, "business_today", lambda: date(2026, 6, 13))
    return Session


def _inv(session, sku, stock, *, shop="s1", name=None, sku_name=None):
    session.add(
        Inventory(
            platform="tiktok_shop",
            country="ID",
            shop_id=shop,
            account_id="acc",
            idempotency_key=f"{shop}-{sku}-{stock}-wh",
            sku_id=sku,
            product_id=f"p-{sku}",
            product_name=name or f"商品{sku}",
            sku_name=sku_name,
            available_stock=stock,
            warehouse_id="wh",
        )
    )


def test_get_stock_risk_classifies_and_excludes_dead_stock(db, monkeypatch):
    session = db()
    _inv(session, "FAST_OUT", 0, name="爆款断货")   # stock 0 + 有销量 → stockout
    _inv(session, "CRIT", 6, name="告急")            # cover=6/3=2 <3 → critical
    _inv(session, "WARN", 25, name="预警")           # cover=25/2.5=10? tune below
    _inv(session, "OK", 100, name="充足")            # cover 高 → 不入
    _inv(session, "DEAD", 0, name="死货")            # stock 0 但无销量 → 排除
    session.commit()

    # 日均销速：window=7 → daily = units/7
    monkeypatch.setattr(
        stock_metrics,
        "get_units_by_sku",
        lambda **kw: {"FAST_OUT": 35, "CRIT": 21, "WARN": 35, "OK": 7},  # daily 5/3/5/1
    )
    # FAST_OUT: stock0+vel5 → stockout
    # CRIT: 6/3=2.0 <3 → critical
    # WARN: 25/5=5.0 in [3,7) → warning
    # OK: 100/1=100 → 正常排除；DEAD: 无销量排除
    out = stock_metrics.get_stock_risk(critical_days=3, warning_days=7, velocity_window_days=7)

    skus = {i["sku_id"]: i for i in out["items"]}
    assert set(skus) == {"FAST_OUT", "CRIT", "WARN"}
    assert skus["FAST_OUT"]["bucket"] == "stockout"
    assert skus["CRIT"]["bucket"] == "critical"
    assert skus["WARN"]["bucket"] == "warning"
    assert out["buckets"] == {"stockout": 1, "critical": 1, "warning": 1, "total": 3}
    # 断货（cover 0）排最前，整体按可售天数升序
    covers = [i["days_of_cover"] for i in out["items"]]
    assert covers == sorted(covers)
    assert out["items"][0]["sku_id"] == "FAST_OUT"


def test_get_stock_risk_include_all_full_ranking(db, monkeypatch):
    """include_all=True（报告展示口径）：列全部 SKU，按可售天数升序，无销量(idle)排末尾、
    其内按库存升序；buckets 计数仍只算真实风险桶（与告警口径一致）。"""
    session = db()
    _inv(session, "FAST_OUT", 0, name="爆款断货")    # stock0+有销量 → stockout
    _inv(session, "CRIT", 6, name="告急")             # 6/3=2 <3 → critical
    _inv(session, "OK", 100, name="充足")             # cover 高 → ok
    _inv(session, "IDLE_HI", 50, name="无销量高库存")  # 无销量 → idle，库存高排更后
    _inv(session, "IDLE_LO", 3, name="无销量低库存")   # 无销量 → idle，库存低先冒头
    session.commit()
    monkeypatch.setattr(
        stock_metrics, "get_units_by_sku",
        lambda **kw: {"FAST_OUT": 35, "CRIT": 21, "OK": 7},  # daily 5/3/1；两个 IDLE 无销量
    )
    out = stock_metrics.get_stock_risk(
        critical_days=3, warning_days=7, velocity_window_days=7, include_all=True)

    skus = [i["sku_id"] for i in out["items"]]
    # 全部 5 个 SKU 都在
    assert set(skus) == {"FAST_OUT", "CRIT", "OK", "IDLE_HI", "IDLE_LO"}
    # 断货最前；无销量 idle 排末尾，低库存的 IDLE_LO 先于高库存的 IDLE_HI
    assert skus[0] == "FAST_OUT"
    assert skus[-2:] == ["IDLE_LO", "IDLE_HI"]
    by = {i["sku_id"]: i for i in out["items"]}
    assert by["OK"]["bucket"] == "ok"
    assert by["IDLE_LO"]["bucket"] == "idle"
    assert by["IDLE_LO"]["days_of_cover"] is None       # 无销量 → 可售天数为 None
    # buckets 仍只算真实风险（stockout+critical），不含 ok/idle
    assert out["buckets"] == {"stockout": 1, "critical": 1, "warning": 0, "total": 2}


def test_get_stock_risk_aggregates_stock_across_shops(db, monkeypatch):
    session = db()
    _inv(session, "X", 4, shop="s1")
    _inv(session, "X", 5, shop="s2")  # 跨店合计 9
    session.commit()
    monkeypatch.setattr(stock_metrics, "get_units_by_sku", lambda **kw: {"X": 21})  # daily 3
    out = stock_metrics.get_stock_risk(critical_days=3, warning_days=7, velocity_window_days=7)
    x = out["items"][0]
    assert x["available_stock"] == 9
    assert x["days_of_cover"] == 3.0  # 9/3
    assert x["bucket"] == "warning"  # 3.0 in [3,7)


def test_get_stock_risk_includes_sku_name_and_image(db, monkeypatch):
    """明细展示：item 带 sku_name（Inventory）+ image_url（批量查 Product 主图，缺图 None）。"""
    session = db()
    _inv(session, "RED", 6, name="连衣裙", sku_name="红色 / M")   # 有图
    _inv(session, "BLUE", 6, name="连衣裙", sku_name="蓝色 / L")  # product 无主图 → None
    # 只给 p-RED 建 Product 主图；p-BLUE 无 Product 行 → image_url 应为 None
    session.add(
        Product(
            platform="tiktok_shop",
            country="ID",
            shop_id="s1",
            account_id="acc",
            idempotency_key="p-RED-key",
            product_id="p-RED",
            title="连衣裙",
            main_image_url="https://cdn.example/red.jpg",
        )
    )
    session.commit()
    monkeypatch.setattr(stock_metrics, "get_units_by_sku", lambda **kw: {"RED": 21, "BLUE": 21})  # daily 3
    out = stock_metrics.get_stock_risk(critical_days=3, warning_days=7, velocity_window_days=7)
    by = {i["sku_id"]: i for i in out["items"]}
    assert by["RED"]["sku_name"] == "红色 / M"
    assert by["RED"]["image_url"] == "https://cdn.example/red.jpg"
    assert by["BLUE"]["sku_name"] == "蓝色 / L"
    assert by["BLUE"]["image_url"] is None


# ── ops_low_stock 端点 smoke（不依赖真实 DB：override 鉴权 + monkeypatch 取数）──────

def test_ops_low_stock_endpoint_smoke(monkeypatch):
    from types import SimpleNamespace
    from fastapi.testclient import TestClient

    from web.app import app
    from web.routes import data as data_route
    from web.security import require_internal_token

    app.dependency_overrides[require_internal_token] = lambda: None
    monkeypatch.setattr(
        data_route,
        "_resolve_scope",
        lambda **kw: SimpleNamespace(
            platform="tiktok_shop", country="ID", shop_ids=None, display_text="印尼全部店"
        ),
    )
    monkeypatch.setattr(
        data_route,
        "get_stock_risk",
        lambda **kw: {
            "items": [
                {
                    "sku_id": "A",
                    "product_name": "爆款A",
                    "shop_id": "s1",
                    "available_stock": 0,
                    "daily_velocity": 5.0,
                    "days_of_cover": 0.0,
                    "bucket": "stockout",
                }
            ],
            "buckets": {"stockout": 1, "critical": 0, "warning": 0, "total": 1},
            "snapshot_at": "2026-06-13T14:30:00",
            "critical_days": 3,
            "warning_days": 7,
            "velocity_window_days": 7,
        },
    )
    try:
        client = TestClient(app)
        r = client.get("/api/data/inventory/low-stock")
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["buckets"]["stockout"] == 1
        assert j["items"][0]["sku_id"] == "A"
        assert j["caliber"]  # 口径声明随响应返回
    finally:
        app.dependency_overrides.clear()

