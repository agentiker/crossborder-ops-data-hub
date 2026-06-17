"""看板登录态 cookie（web/web_session）的单测：round-trip / 过期 / 篡改 / 错密钥 / 垃圾输入。

登录态 cookie 承担看板鉴权，验签必须 fail closed——任何异常输入返回 None，不抛、不误放行。
同构 test_signed_link.py，换密钥源为 feishu_oauth.session_secret。
"""
import pytest

from core.config import settings
from web import web_session


@pytest.fixture
def secret(monkeypatch):
    monkeypatch.setattr(settings.feishu_oauth, "session_secret", "test-session-secret")
    return "test-session-secret"


def test_round_trip(secret):
    token = web_session.make_session_cookie("ou_abc123", ttl=300)
    assert web_session.verify_session_cookie(token) == "ou_abc123"


def test_default_ttl_from_settings(secret, monkeypatch):
    monkeypatch.setattr(settings.feishu_oauth, "session_ttl_seconds", 600)
    token = web_session.make_session_cookie("ou_abc123")  # 不传 ttl，走配置
    assert web_session.verify_session_cookie(token) == "ou_abc123"


def test_expired_returns_none(secret):
    token = web_session.make_session_cookie("ou_abc123", ttl=-1)
    assert web_session.verify_session_cookie(token) is None


def test_tampered_signature_returns_none(secret):
    token = web_session.make_session_cookie("ou_abc123", ttl=300)
    last = token[-1]
    tampered = token[:-1] + ("A" if last != "A" else "B")
    assert web_session.verify_session_cookie(tampered) is None


def test_tampered_payload_returns_none(secret):
    """改 payload（换 open_id）留旧签名 → 验签必失败，防越权冒充。"""
    token = web_session.make_session_cookie("ou_abc123", ttl=300)
    _, sig = token.split(".", 1)
    forged_payload = web_session._b64url_encode(b"ou_victim:9999999999")
    assert web_session.verify_session_cookie(f"{forged_payload}.{sig}") is None


def test_wrong_secret_returns_none(secret, monkeypatch):
    token = web_session.make_session_cookie("ou_abc123", ttl=300)
    monkeypatch.setattr(settings.feishu_oauth, "session_secret", "another-secret")
    assert web_session.verify_session_cookie(token) is None


def test_no_secret_make_raises(monkeypatch):
    monkeypatch.setattr(settings.feishu_oauth, "session_secret", "")
    with pytest.raises(RuntimeError):
        web_session.make_session_cookie("ou_abc123", ttl=300)


def test_no_secret_verify_returns_none(monkeypatch):
    monkeypatch.setattr(settings.feishu_oauth, "session_secret", "")
    assert web_session.verify_session_cookie("anything.here") is None


def test_empty_value_make_raises(secret):
    with pytest.raises(ValueError):
        web_session.make_session_cookie("", ttl=300)


def test_value_with_colon_raises(secret):
    """value 含 ':' 会与 exp 分隔符冲突，必须拒签（防解析歧义）。"""
    with pytest.raises(ValueError):
        web_session._make_signed("has:colon", ttl=300)


@pytest.mark.parametrize("garbage", ["", "noseparator", "...", "a.b.c", "!!!.???", "."])
def test_garbage_input_returns_none(secret, garbage):
    assert web_session.verify_session_cookie(garbage) is None


def test_generic_signed_round_trip(secret):
    """通用 _make_signed/_verify_signed（供 OAuth state 复用）round-trip。"""
    token = web_session._make_signed("nonce-xyz", ttl=600)
    assert web_session._verify_signed(token) == "nonce-xyz"
