"""TikTok client 限流退避回归锁。

回填近 30 天历史会高频打 API（~150 页/店、串行多店），原 `_request_with_headers`
只处理 HTTP 401，HTTP 429 与业务限流码 36009002 全程不重试、直接抛错 → 回填中途断。

此处锁定：429 / 5xx / code=36009002 触发指数退避后重试，退避后重新签名（timestamp 变化），
最终成功；且退避用 time.sleep（被 patch 掉，断言确有退避、不真等）。
"""
from unittest.mock import MagicMock

import pytest

from platforms.tiktok_shop import client as client_mod
from platforms.tiktok_shop.client import TikTokShopClient


class _FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"raise_for_status 不该在重试码上触发: {self.status_code}")


@pytest.fixture
def client(monkeypatch):
    c = TikTokShopClient(country="ID", shop_id="shop-1", auto_load_token=False)
    c.access_token = "acc-1"
    c.app_key = "ak"
    c.app_secret = "sk"
    c.base_url = "https://example.test"
    # 隔离外部副作用
    monkeypatch.setattr(c, "_ensure_token", lambda: None)
    monkeypatch.setattr(c, "_generate_sign", lambda path, params, body="": "SIG")
    monkeypatch.setattr(client_mod, "log_api_call_safe", lambda **kw: None, raising=False)
    return c


def _setup(monkeypatch, client, responses):
    """让 session.request 依次返回 responses 序列；patch sleep 记录退避次数。"""
    seq = iter(responses)
    timestamps = []

    def fake_request(method, url, **kw):
        timestamps.append(kw["params"].get("timestamp"))
        return next(seq)

    client.session = MagicMock()
    client.session.request = fake_request

    sleeps = []
    monkeypatch.setattr(client_mod.time, "sleep", lambda s: sleeps.append(s))
    return sleeps, timestamps


def test_http_429_backs_off_then_succeeds(client, monkeypatch):
    ok = {"code": 0, "message": "ok", "data": {"x": 1}}
    sleeps, _ = _setup(monkeypatch, client, [
        _FakeResp(429, {"code": 0}),
        _FakeResp(429, {"code": 0}),
        _FakeResp(200, ok),
    ])
    result = client.request("GET", "/orders/search")
    assert result == ok
    assert len(sleeps) == 2          # 两次 429 → 两次退避
    assert all(s > 0 for s in sleeps)


def test_business_rate_limit_code_backs_off_then_succeeds(client, monkeypatch):
    ok = {"code": 0, "data": {}}
    sleeps, _ = _setup(monkeypatch, client, [
        _FakeResp(200, {"code": 36009002, "message": "rate limit"}),
        _FakeResp(200, ok),
    ])
    result = client.request("GET", "/finance/statements")
    assert result == ok
    assert len(sleeps) == 1


def test_backoff_resigns_with_fresh_timestamp(client, monkeypatch):
    """退避重试前刷新 timestamp，避免签名过期。"""
    ok = {"code": 0, "data": {}}
    # 让 time.time 在两次请求间前进，验证 timestamp 确实被重写
    fake_now = iter([1000.0, 1000.0, 1005.0, 1005.0, 1005.0, 1005.0])
    monkeypatch.setattr(client_mod.time, "time", lambda: next(fake_now))
    sleeps, timestamps = _setup(monkeypatch, client, [
        _FakeResp(429, {"code": 0}),
        _FakeResp(200, ok),
    ])
    client.request("GET", "/orders/search")
    assert timestamps[0] != timestamps[1]   # 重试用了新时间戳


def test_429_exhausts_retries_then_raises(client, monkeypatch):
    """始终 429 时退避用尽后照常抛错（不静默吞）。"""
    monkeypatch.setattr(client_mod.time, "sleep", lambda s: None)
    client.session = MagicMock()
    client.session.request = lambda *a, **k: _FakeResp(429, {"code": 0})
    # 让 raise_for_status 真正抛
    monkeypatch.setattr(_FakeResp, "raise_for_status",
                        lambda self: (_ for _ in ()).throw(Exception("429")))
    with pytest.raises(Exception):
        client.request("GET", "/orders/search", max_retries=2)
