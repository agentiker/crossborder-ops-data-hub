"""看板签名 token（web/signed_link）的单测：round-trip / 过期 / 篡改 / 错密钥 / 垃圾输入。

token 承担看板的全部鉴权，验签必须 fail closed——任何异常输入都返回 None，不能抛异常、
更不能误放行。这些用例锁定该契约。
"""
import pytest

from core.config import settings
from web import signed_link


@pytest.fixture
def secret(monkeypatch):
    """配一个固定密钥，让 make/verify 都生效。"""
    monkeypatch.setattr(settings.dashboard, "link_secret", "test-secret-xyz")
    return "test-secret-xyz"


def test_round_trip(secret):
    token = signed_link.make_token("ou_abc123", ttl=300)
    assert signed_link.verify_token(token) == "ou_abc123"


def test_default_ttl_from_settings(secret, monkeypatch):
    monkeypatch.setattr(settings.dashboard, "token_ttl_seconds", 600)
    token = signed_link.make_token("ou_abc123")  # 不传 ttl，走配置
    assert signed_link.verify_token(token) == "ou_abc123"


def test_expired_returns_none(secret):
    token = signed_link.make_token("ou_abc123", ttl=-1)  # 已过期
    assert signed_link.verify_token(token) is None


def test_tampered_signature_returns_none(secret):
    token = signed_link.make_token("ou_abc123", ttl=300)
    # 篡改最后一个字符（避开恰好等于原字符）
    last = token[-1]
    tampered = token[:-1] + ("A" if last != "A" else "B")
    assert signed_link.verify_token(tampered) is None


def test_tampered_payload_returns_none(secret):
    """改 payload（换 open_id）但留旧签名 → 验签必失败，越权拿不到别人范围。"""
    token = signed_link.make_token("ou_abc123", ttl=300)
    payload_b64, sig = token.split(".", 1)
    forged_payload = signed_link._b64url_encode(b"ou_victim:9999999999")
    assert signed_link.verify_token(f"{forged_payload}.{sig}") is None


def test_wrong_secret_returns_none(secret, monkeypatch):
    token = signed_link.make_token("ou_abc123", ttl=300)
    monkeypatch.setattr(settings.dashboard, "link_secret", "another-secret")
    assert signed_link.verify_token(token) is None


def test_no_secret_make_raises(monkeypatch):
    monkeypatch.setattr(settings.dashboard, "link_secret", "")
    with pytest.raises(RuntimeError):
        signed_link.make_token("ou_abc123", ttl=300)


def test_no_secret_verify_returns_none(monkeypatch):
    monkeypatch.setattr(settings.dashboard, "link_secret", "")
    assert signed_link.verify_token("anything.here") is None


def test_empty_open_id_raises(secret):
    with pytest.raises(ValueError):
        signed_link.make_token("", ttl=300)


@pytest.mark.parametrize(
    "garbage",
    ["", "noseparator", "...", "a.b.c", "!!!.???", "."],
)
def test_garbage_input_returns_none(secret, garbage):
    assert signed_link.verify_token(garbage) is None


def test_open_id_with_underscore_round_trip(secret):
    """真实 open_id 形如 ou_xxx，含下划线但不含冒号；rsplit 切 exp 必须稳。"""
    token = signed_link.make_token("ou_7f3a_b9c2_d1", ttl=300)
    assert signed_link.verify_token(token) == "ou_7f3a_b9c2_d1"
