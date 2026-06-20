"""飞书 OAuth 客户端（web/feishu_oauth）单测：授权 URL 拼装 + mock requests 换 token/取 open_id。

不打真实飞书网络：用 monkeypatch 替换模块内 requests.post/get。覆盖成功路径与各类失败
（配置缺失、飞书非 0 code、字段缺失），确保失败一律抛 FeishuOAuthError 而非静默放行。
"""
from urllib.parse import parse_qs, urlparse

import pytest

from core.config import settings
from web import feishu_oauth
from web.feishu_oauth import (
    FeishuOAuthError,
    build_authorize_url,
    exchange_code_for_token,
    fetch_open_id,
    fetch_user_identity,
)


class _FakeResp:
    def __init__(self, payload, status=200):
        self._p = payload
        self.status_code = status

    def json(self):
        if self._p is _NO_JSON:
            raise ValueError("not json")
        return self._p


_NO_JSON = object()


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings.feishu_oauth, "app_id", "cli_test")
    monkeypatch.setattr(settings.feishu_oauth, "app_secret", "secret_test")
    monkeypatch.setattr(
        settings.feishu_oauth, "redirect_uri",
        "https://board.agenticker.cc/board/auth/feishu/callback",
    )


# ---------- build_authorize_url ----------

def test_authorize_url_has_required_query(configured):
    url = build_authorize_url("state-abc")
    parsed = urlparse(url)
    q = parse_qs(parsed.query)
    assert parsed.netloc == "accounts.feishu.cn"
    assert q["client_id"] == ["cli_test"]
    assert q["redirect_uri"] == ["https://board.agenticker.cc/board/auth/feishu/callback"]
    assert q["response_type"] == ["code"]
    assert q["state"] == ["state-abc"]
    # 默认钉最小 scope（只请求登录所需的 open_id 权限，不请求应用声明的全集）
    assert q["scope"] == ["contact:user.id:readonly"]


def test_authorize_url_empty_scope_omits_param(configured, monkeypatch):
    """oauth_scope 置空时不拼 scope（飞书按应用全集请求；保留此口子但非默认）。"""
    monkeypatch.setattr(settings.feishu_oauth, "oauth_scope", "")
    q = parse_qs(urlparse(build_authorize_url("s")).query)
    assert "scope" not in q


def test_authorize_url_explicit_scope_arg_wins(configured):
    """显式传 scope 参数覆盖配置默认。"""
    q = parse_qs(urlparse(build_authorize_url("s", scope="bitable:app")).query)
    assert q["scope"] == ["bitable:app"]


def test_authorize_url_missing_config_raises(monkeypatch):
    monkeypatch.setattr(settings.feishu_oauth, "app_id", "")
    with pytest.raises(FeishuOAuthError):
        build_authorize_url("state-abc")


def test_authorize_url_empty_state_raises(configured):
    with pytest.raises(FeishuOAuthError):
        build_authorize_url("")


# ---------- exchange_code_for_token ----------

def test_exchange_code_success(configured, monkeypatch):
    monkeypatch.setattr(
        feishu_oauth.requests, "post",
        lambda *a, **k: _FakeResp({"code": 0, "access_token": "u-tok-123"}),
    )
    assert exchange_code_for_token("the-code") == "u-tok-123"


def test_exchange_code_error_code_raises(configured, monkeypatch):
    monkeypatch.setattr(
        feishu_oauth.requests, "post",
        lambda *a, **k: _FakeResp({"code": 20050, "error_description": "bad code"}),
    )
    with pytest.raises(FeishuOAuthError):
        exchange_code_for_token("the-code")


def test_exchange_code_missing_token_raises(configured, monkeypatch):
    monkeypatch.setattr(
        feishu_oauth.requests, "post",
        lambda *a, **k: _FakeResp({"code": 0}),
    )
    with pytest.raises(FeishuOAuthError):
        exchange_code_for_token("the-code")


def test_exchange_code_empty_raises(configured):
    with pytest.raises(FeishuOAuthError):
        exchange_code_for_token("")


# ---------- fetch_open_id ----------

def test_fetch_open_id_success(monkeypatch):
    monkeypatch.setattr(
        feishu_oauth.requests, "get",
        lambda *a, **k: _FakeResp({"code": 0, "data": {"open_id": "ou_xyz", "name": "老板"}}),
    )
    assert fetch_open_id("u-tok-123") == "ou_xyz"


def test_fetch_open_id_error_code_raises(monkeypatch):
    monkeypatch.setattr(
        feishu_oauth.requests, "get",
        lambda *a, **k: _FakeResp({"code": 99991663, "msg": "token invalid"}),
    )
    with pytest.raises(FeishuOAuthError):
        fetch_open_id("u-tok-123")


def test_fetch_open_id_missing_field_raises(monkeypatch):
    monkeypatch.setattr(
        feishu_oauth.requests, "get",
        lambda *a, **k: _FakeResp({"code": 0, "data": {"name": "无 open_id"}}),
    )
    with pytest.raises(FeishuOAuthError):
        fetch_open_id("u-tok-123")


def test_non_json_response_raises(monkeypatch):
    monkeypatch.setattr(
        feishu_oauth.requests, "get",
        lambda *a, **k: _FakeResp(_NO_JSON, status=502),
    )
    with pytest.raises(FeishuOAuthError):
        fetch_open_id("u-tok-123")


# ---------- fetch_user_identity（自助登记用，带姓名） ----------

def test_fetch_user_identity_returns_open_id_and_name(monkeypatch):
    monkeypatch.setattr(
        feishu_oauth.requests, "get",
        lambda *a, **k: _FakeResp({"code": 0, "data": {"open_id": "ou_xyz", "name": "老板"}}),
    )
    assert fetch_user_identity("u-tok-123") == ("ou_xyz", "老板")


def test_fetch_user_identity_name_optional(monkeypatch):
    """缺 name 不报错（name=None），open_id 仍强校验。"""
    monkeypatch.setattr(
        feishu_oauth.requests, "get",
        lambda *a, **k: _FakeResp({"code": 0, "data": {"open_id": "ou_xyz"}}),
    )
    assert fetch_user_identity("u-tok-123") == ("ou_xyz", None)


def test_fetch_user_identity_missing_open_id_raises(monkeypatch):
    monkeypatch.setattr(
        feishu_oauth.requests, "get",
        lambda *a, **k: _FakeResp({"code": 0, "data": {"name": "无 open_id"}}),
    )
    with pytest.raises(FeishuOAuthError):
        fetch_user_identity("u-tok-123")
