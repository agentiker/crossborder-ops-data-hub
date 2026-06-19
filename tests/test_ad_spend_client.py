"""TikTok client 的 finance 取数方法回归锁：版本透传 + next_page_token 翻页。

iter_statement_transactions 必须用 202501（只有该版本含三项广告费），
iter_statements 用 202309。版本是公共参数 version、参与签名，错版本会直接取不到广告费字段。
翻页须靠 data.next_page_token 续拉，末页无 token 即停。

mock 落点：client.session.request（最底层 HTTP），断言每次调用传入的 params["version"]，
并按预置的两页响应验证翻页。
"""
from __future__ import annotations

import time

from platforms.tiktok_shop.client import TikTokShopClient


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _make_client():
    """auto_load_token=False + 远期 token，绕开 DB 与刷新分支。"""
    client = TikTokShopClient(auto_load_token=False)
    client.access_token = "acc"
    client.refresh_token = "ref"
    client.token_expire_at = time.time() + 10_000  # 远未过期，_ensure_token 不触发刷新
    return client


def _ok(data):
    return {"code": 0, "message": "success", "data": data}


def test_iter_statement_transactions_version_and_paging(monkeypatch):
    """statement_transactions：version=202501，且两页靠 next_page_token 翻页、末页停。"""
    pages = [
        _ok({"transactions": [{"order_id": "o1"}], "next_page_token": "PAGE2"}),
        _ok({"transactions": [{"order_id": "o2"}]}),  # 无 next_page_token → 停
    ]
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append({"method": method, "url": url, "params": kwargs.get("params", {})})
        return _FakeResp(pages[len(calls) - 1])

    client = _make_client()
    monkeypatch.setattr(client.session, "request", fake_request)

    out = list(client.iter_statement_transactions("STMT-1", page_size=50))

    # 两页都取到、翻页正确
    assert len(out) == 2
    assert out[0]["transactions"][0]["order_id"] == "o1"
    assert out[1]["transactions"][0]["order_id"] == "o2"
    # 公共参数版本号必须是 202501
    assert all(c["params"]["version"] == "202501" for c in calls)
    # 第二页带上了上一页的 next_page_token
    assert "page_token" not in calls[0]["params"]
    assert calls[1]["params"]["page_token"] == "PAGE2"
    # URL 命中 202501 statement_transactions 路径
    assert "/finance/202501/statements/STMT-1/statement_transactions" in calls[0]["url"]


def test_iter_statements_version_202309(monkeypatch):
    """statements 列表：version=202309，并按 next_page_token 翻两页后停。"""
    pages = [
        _ok({"statements": [{"id": "s1"}], "next_page_token": "NEXT"}),
        _ok({"statements": [{"id": "s2"}]}),
    ]
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append({"method": method, "url": url, "params": kwargs.get("params", {})})
        return _FakeResp(pages[len(calls) - 1])

    client = _make_client()
    monkeypatch.setattr(client.session, "request", fake_request)

    out = list(
        client.iter_statements(statement_time_ge=1000, statement_time_lt=2000, page_size=50)
    )

    assert [p["statements"][0]["id"] for p in out] == ["s1", "s2"]
    assert all(c["params"]["version"] == "202309" for c in calls)
    assert calls[1]["params"]["page_token"] == "NEXT"
    # 时间窗放在 query 参与签名
    assert calls[0]["params"]["statement_time_ge"] == 1000
    assert calls[0]["params"]["statement_time_lt"] == 2000
    assert "/finance/202309/statements" in calls[0]["url"]


def test_request_default_version_202309(monkeypatch):
    """request 不传 version 时默认 202309（普通接口口径，回归锁）。"""
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append(kwargs.get("params", {}))
        return _FakeResp(_ok({"ok": True}))

    client = _make_client()
    monkeypatch.setattr(client.session, "request", fake_request)

    client.request("GET", "/authorization/202309/shops")
    assert calls[0]["version"] == "202309"
