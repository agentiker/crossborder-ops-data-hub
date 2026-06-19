"""端点 ops_ad_spend_summary（GET /api/data/ads/summary）单测。

200 + 9 个字段齐全；scope 过滤（shop_ids/platform/country）原样透传到底层取数函数。
底层 get_ad_spend_summary / get_roas monkeypatch 成 fake，隔离数据层。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.scope_resolution import ScopeFilters
from web.app import app
from web.routes import data as data_routes
from web.security import require_internal_token

_FIELDS = (
    "start_date", "end_date", "total_ad_spend", "gmv_max_fee",
    "tap_commission", "affiliate_commission", "gmv", "roas", "currency",
)


@pytest.fixture()
def client():
    app.dependency_overrides[require_internal_token] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _patch(monkeypatch, calls):
    def fake_spend(**kw):
        calls["spend"] = kw
        return {
            "start_date": kw["start_date"].isoformat(),
            "end_date": kw["end_date"].isoformat(),
            "total_ad_spend": 125.0, "gmv_max_fee": 100.0,
            "tap_commission": 20.0, "affiliate_commission": 5.0, "currency": "IDR",
        }

    def fake_roas(**kw):
        calls["roas"] = kw
        return {
            "start_date": kw["start_date"].isoformat(),
            "end_date": kw["end_date"].isoformat(),
            "gmv": 500.0, "ad_spend": 125.0, "roas": 4.0,
            "gmv_max_fee": 100.0, "tap_commission": 20.0,
            "affiliate_commission": 5.0, "currency": "IDR",
        }

    monkeypatch.setattr(data_routes, "get_ad_spend_summary", fake_spend)
    monkeypatch.setattr(data_routes, "get_roas", fake_roas)


def test_ads_summary_200_all_fields(client, monkeypatch):
    _patch(monkeypatch, {})
    r = client.get("/api/data/ads/summary", params={"start_date": "2026-06-08", "end_date": "2026-06-10"})
    assert r.status_code == 200
    body = r.json()
    for f in _FIELDS:
        assert f in body, f
    assert body["total_ad_spend"] == 125.0
    assert body["gmv"] == 500.0
    assert body["roas"] == 4.0
    assert body["currency"] == "IDR"
    assert body["start_date"] == "2026-06-08"
    assert body["end_date"] == "2026-06-10"


def test_ads_summary_scope_filters_passthrough(client, monkeypatch):
    # _resolve_scope 的范围解析本身另有 test_resolve_scope 专测；这里 stub 成固定
    # ScopeFilters（避免 TestClient 工作线程跨连接访问内存 sqlite），只验证解析结果
    # 被原样透传到底层取数函数。
    captured = {"scope_kwargs": None}

    def fake_resolve_scope(**kw):
        captured["scope_kwargs"] = kw
        return ScopeFilters(
            platform="tiktok_shop", country="ID", shop_ids=["s1", "s2"],
            scope_key=None, display_text="TikTok Shop / 印尼 / 2 个店铺",
        )

    monkeypatch.setattr(data_routes, "_resolve_scope", fake_resolve_scope)
    calls = {}
    _patch(monkeypatch, calls)
    r = client.get(
        "/api/data/ads/summary",
        params={"period": "last_7d", "platform": "tiktok_shop",
                "country": "ID", "shop_ids": "s1,s2"},
    )
    assert r.status_code == 200
    # 原始 query 参数进入 _resolve_scope
    assert captured["scope_kwargs"]["shop_ids"] == "s1,s2"
    assert captured["scope_kwargs"]["platform"] == "tiktok_shop"
    # scope 过滤生效：解析后的 platform/country/shop_ids 透传到两个底层取数函数
    for key in ("spend", "roas"):
        kw = calls[key]
        assert kw["platform"] == "tiktok_shop"
        assert kw["country"] == "ID"
        assert sorted(kw["shop_ids"]) == ["s1", "s2"]


def test_ads_summary_default_window(client, monkeypatch):
    """不传日期/period → 默认 default_back_days=7（today-7 ~ today，与其它端点同口径）。"""
    calls = {}
    _patch(monkeypatch, calls)
    r = client.get("/api/data/ads/summary")
    assert r.status_code == 200
    sd = calls["spend"]["start_date"]
    ed = calls["spend"]["end_date"]
    # today-7 ~ today，含端点共 8 天（delta=7），与 _resolve_window(default_back_days=7) 一致
    assert (ed - sd).days == 7
