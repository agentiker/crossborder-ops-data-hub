"""看板聚合端点 ops_dashboard_summary（/api/data/dashboard/summary）单测。

覆盖：200 且各卡片字段齐全；scope/period 等公共参数被原样透传到底层 ops_* 端点函数；
单卡取数失败时该卡片置 None 且 errors 记一条、整体仍 200（不 500）。

底层 get_* 端点函数 monkeypatch 成 fake，隔离数据层——范围解析本身已在 test_resolve_scope 覆盖。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from web.app import app
from web.routes import data as data_routes
from web.security import require_internal_token


@pytest.fixture()
def client():
    # /api/data 挂了 require_internal_token 守卫，单测里直接放行（鉴权另有专测覆盖）
    app.dependency_overrides[require_internal_token] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _fake_overview(**kw):
    # get_overview 原实现返回 dict（非 Pydantic），fake 保持一致
    return {
        "period": "x", "scope": kw.get("_scope", "全部范围"),
        "inventory": {"total_sku": 1, "total_stock": 10, "low_stock_count": 0},
        "orders": {"gmv": 100.0, "order_count": 2, "units_sold": 3, "avg_order_value": 50.0},
    }


def _patch_all(monkeypatch, *, calls=None):
    """把 5 个底层端点函数 monkeypatch 成 async fake；calls 收集各自收到的 kwargs。"""
    calls = {} if calls is None else calls

    def make(name, payload):
        async def fake(**kw):
            calls[name] = kw
            return payload
        return fake

    monkeypatch.setattr(data_routes, "get_overview", make("overview", _fake_overview(_scope="全部范围")))
    monkeypatch.setattr(data_routes, "get_orders_trend", make("orders_trend", data_routes.TrendResponse(
        start_date="2026-06-01", end_date="2026-06-07",
        window_label="近7天", points=[], scope="全部范围",
    )))
    monkeypatch.setattr(data_routes, "get_orders_top_skus", make("top_skus", data_routes.TopSkuResponse(
        items=[], total=0, scope="全部范围",
    )))
    monkeypatch.setattr(data_routes, "get_low_stock", make("low_stock", data_routes.LowStockResponse(
        items=[], buckets=data_routes.LowStockBuckets(),
        critical_days=3, warning_days=7, velocity_window_days=7, scope="全部范围",
    )))
    monkeypatch.setattr(data_routes, "get_fulfillments_pending", make("fulfillments_pending",
        data_routes.PendingFulfillmentsResponse(
            items=[], buckets=data_routes.FulfillmentBuckets(),
            by_shop=[], warning_hours=24, scope="全部范围",
        )))
    return calls


def test_summary_200_all_cards_present(client, monkeypatch):
    _patch_all(monkeypatch)
    r = client.get("/api/data/dashboard/summary")
    assert r.status_code == 200
    body = r.json()
    for key in ("overview", "orders_trend", "top_skus", "low_stock", "fulfillments_pending"):
        assert body[key] is not None, key
    assert body["errors"] == {}
    assert body["scope"] == "全部范围"
    # 各卡片内部字段齐全（response_model 校验通过即说明结构对）
    assert body["overview"]["orders"]["gmv"] == 100.0
    assert "buckets" in body["low_stock"]
    assert "buckets" in body["fulfillments_pending"]


def test_summary_passes_scope_and_period(client, monkeypatch):
    calls = _patch_all(monkeypatch)
    r = client.get(
        "/api/data/dashboard/summary",
        params={"scope_id": "tts-id-all", "shop_ids": "s1,s2",
                "platform": "tiktok_shop", "country": "ID", "period": "last_30d"},
    )
    assert r.status_code == 200
    # 公共范围参数原样透传到每个底层函数
    for name in ("overview", "orders_trend", "top_skus", "low_stock", "fulfillments_pending"):
        kw = calls[name]
        assert kw["scope_id"] == "tts-id-all"
        assert kw["shop_ids"] == "s1,s2"
        assert kw["platform"] == "tiktok_shop"
        assert kw["country"] == "ID"
    # period 只透传给趋势/榜单卡片
    assert calls["orders_trend"]["period"] == "last_30d"
    assert calls["top_skus"]["period"] == "last_30d"
    assert "period" not in calls["overview"]
    assert "period" not in calls["low_stock"]
    assert r.json()["period"] == "last_30d"


def test_single_card_failure_does_not_500(client, monkeypatch):
    _patch_all(monkeypatch)

    async def boom(**kw):
        raise RuntimeError("库存数据层炸了")

    monkeypatch.setattr(data_routes, "get_low_stock", boom)
    r = client.get("/api/data/dashboard/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["low_stock"] is None
    assert "low_stock" in body["errors"]
    assert "库存数据层炸了" in body["errors"]["low_stock"]
    # 其它卡片不受影响
    assert body["overview"] is not None
    assert body["orders_trend"] is not None
