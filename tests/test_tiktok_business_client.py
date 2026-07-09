"""TikTok Business (Marketing API) client 回归锁：请求构造正确性。

坐实点（官方 SDK + portal 文档）不能回退：
- token 走 `Access-Token` 请求头（不是 query，不是 Bearer）
- gmv_max/report/get 的 store_ids/dimensions/metrics 序列化成 JSON 字符串 `["x"]`
- 端点路径 /open_api/v1.3/gmv_max/report/get/
- code!=0 抛 TikTokBusinessError（携带 code）
- 花费在 data.list[].metrics.cost

mock 落点：client.session.request（最底层 HTTP），断言 headers/params/url。
"""
from __future__ import annotations

import json

import pytest

from platforms.tiktok_business.client import (
    TikTokBusinessClient,
    TikTokBusinessError,
)


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _client():
    return TikTokBusinessClient(
        account_id="ecom-app", app_id="app123", app_secret="sec", access_token="TOK"
    )


def test_report_request_shape(monkeypatch):
    """report 请求：正确端点 + Access-Token 头 + 数组参数 JSON 字符串化。"""
    captured = {}

    def fake_request(method, url, params=None, json=None, headers=None, timeout=None):
        captured.update(method=method, url=url, params=params, headers=headers)
        return _FakeResp({
            "code": 0, "message": "OK", "request_id": "r1",
            "data": {"list": [{"dimensions": {"stat_time_day": "2025-07-01"},
                               "metrics": {"cost": "12.34", "currency": "USD"}}],
                     "page_info": {"total_number": 1}},
        })

    c = _client()
    monkeypatch.setattr(c.session, "request", fake_request)
    data = c.get_gmv_max_report("adv1", "store9", "2025-07-01", "2025-07-07")

    assert captured["url"].endswith("/open_api/v1.3/gmv_max/report/get/")
    assert captured["headers"]["Access-Token"] == "TOK"
    # 数组参数必须是 JSON 字符串形式（文档 curl 示例口径），不是逗号拼接
    assert captured["params"]["store_ids"] == json.dumps(["store9"])
    assert json.loads(captured["params"]["metrics"]) == [
        "cost", "net_cost", "gross_revenue", "orders", "roi", "cost_per_order",
    ]
    assert json.loads(captured["params"]["dimensions"]) == ["stat_time_day"]
    assert captured["params"]["advertiser_id"] == "adv1"
    # 花费解包
    assert data["list"][0]["metrics"]["cost"] == "12.34"


def test_business_error_raised(monkeypatch):
    """code!=0 抛 TikTokBusinessError 且携带 code。"""
    def fake_request(method, url, params=None, json=None, headers=None, timeout=None):
        return _FakeResp({"code": 40105, "message": "invalid token", "request_id": "r2"})

    c = _client()
    monkeypatch.setattr(c.session, "request", fake_request)
    with pytest.raises(TikTokBusinessError) as ei:
        c.get_gmv_max_report("adv1", "store9", "2025-07-01", "2025-07-07")
    assert ei.value.code == 40105


def test_authenticate_body_no_grant_type(monkeypatch):
    """换 token：body = app_id/auth_code/secret，无 grant_type；不落库（persist=False）。"""
    captured = {}

    def fake_request(method, url, params=None, json=None, headers=None, timeout=None):
        captured.update(url=url, json=json)
        return _FakeResp({
            "code": 0, "message": "OK",
            "data": {"access_token": "NEW", "advertiser_ids": ["adv1"], "scope": [4]},
        })

    c = _client()
    monkeypatch.setattr(c.session, "request", fake_request)
    data = c.authenticate("AUTHCODE", persist=False)

    assert captured["url"].endswith("/open_api/v1.3/oauth2/access_token/")
    assert set(captured["json"].keys()) == {"app_id", "auth_code", "secret"}
    assert "grant_type" not in captured["json"]
    assert c.access_token == "NEW"
    assert data["advertiser_ids"] == ["adv1"]


def test_advertiser_get_uses_query_token(monkeypatch):
    """列广告主：app_id/secret/access_token 走 query（此接口不塞 Access-Token 头）。"""
    captured = {}

    def fake_request(method, url, params=None, json=None, headers=None, timeout=None):
        captured.update(url=url, params=params, headers=headers)
        return _FakeResp({"code": 0, "data": {"list": [{"advertiser_id": "adv1"}]}})

    c = _client()
    monkeypatch.setattr(c.session, "request", fake_request)
    out = c.get_advertisers()

    assert captured["url"].endswith("/open_api/v1.3/oauth2/advertiser/get/")
    assert captured["params"]["access_token"] == "TOK"
    assert "Access-Token" not in captured["headers"]
    assert out == [{"advertiser_id": "adv1"}]
