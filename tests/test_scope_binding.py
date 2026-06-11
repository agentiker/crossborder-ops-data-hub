from __future__ import annotations

import pytest

from models.base_models import BusinessScope, PlatformToken
from services import scope_binding, scope_resolution
from services.scope_binding import get_binding, set_binding
from services.scope_resolution import ScopeError


def _use(session, monkeypatch):
    # binding 服务自身 + 其复用的 scope_resolution 都要指向同一内存 session。
    monkeypatch.setattr(scope_binding, "SessionLocal", lambda: session)
    monkeypatch.setattr(scope_resolution, "SessionLocal", lambda: session)


def _scope(session, key, shop_ids, *, platform="tiktok_shop", country="ID", active=True):
    session.add(
        BusinessScope(
            scope_key=key,
            scope_name=f"name-{key}",
            scope_type="shop_group",
            platform=platform,
            country=country,
            shop_ids=shop_ids,
            is_active=active,
        )
    )


def _token(session, shop_id, *, platform="tiktok_shop", country="ID"):
    session.add(
        PlatformToken(
            platform=platform,
            country=country,
            shop_id=shop_id,
            scope_key=f"platform={platform}|shop={shop_id}",
        )
    )


def test_get_binding_unset_returns_not_set(session, monkeypatch):
    _use(session, monkeypatch)
    out = get_binding("ou_none")
    assert out == {
        "scope_key": None,
        "scope": "未设置默认范围（全部）",
        "is_set": False,
    }


def test_set_then_get_named_scope(session, monkeypatch):
    _use(session, monkeypatch)
    _scope(session, "tts-id-all", ["s1"])
    session.commit()

    set_out = set_binding("ou_a", "tts-id-all")
    assert set_out["scope_key"] == "tts-id-all"
    assert set_out["scope"] == "TikTok Shop / 印尼 / 1 个店铺"
    assert set_out["is_set"] is True

    get_out = get_binding("ou_a")
    assert get_out["scope_key"] == "tts-id-all"
    assert get_out["scope"] == "TikTok Shop / 印尼 / 1 个店铺"
    assert get_out["is_set"] is True


def test_set_empty_scope_means_explicit_all(session, monkeypatch):
    _use(session, monkeypatch)

    # 空字符串与 None 都归一化为"显式全量"。
    set_out = set_binding("ou_all", "")
    assert set_out["scope_key"] is None
    assert set_out["scope"] == "全部范围"
    assert set_out["is_set"] is True

    get_out = get_binding("ou_all")
    assert get_out["scope_key"] is None
    assert get_out["scope"] == "全部范围"
    assert get_out["is_set"] is True  # 已设置（全量），区别于未设置


def test_set_unknown_scope_raises(session, monkeypatch):
    _use(session, monkeypatch)
    with pytest.raises(ScopeError):
        set_binding("ou_x", "does-not-exist")
    # 校验失败不应落任何行。
    assert get_binding("ou_x")["is_set"] is False


def test_set_inactive_scope_raises(session, monkeypatch):
    _use(session, monkeypatch)
    _scope(session, "dead", ["s1"], active=False)
    session.commit()
    with pytest.raises(ScopeError):
        set_binding("ou_x", "dead")


def test_set_is_upsert_single_row(session, monkeypatch):
    _use(session, monkeypatch)
    _scope(session, "tts-id-all", ["s1"])
    _scope(session, "tts-id-vip", ["s1"])
    session.commit()

    set_binding("ou_up", "tts-id-all")
    set_binding("ou_up", "tts-id-vip")

    from models.base_models import ConversationScopeBinding

    rows = (
        session.query(ConversationScopeBinding)
        .filter(ConversationScopeBinding.open_id == "ou_up")
        .all()
    )
    assert len(rows) == 1
    assert rows[0].scope_key == "tts-id-vip"


def test_bindings_isolated_by_open_id_and_account(session, monkeypatch):
    _use(session, monkeypatch)
    _scope(session, "tts-id-all", ["s1"])
    session.commit()

    set_binding("ou_a", "tts-id-all", account_id="ecom-app")
    # 不同 open_id 互不影响
    assert get_binding("ou_b")["is_set"] is False
    # 同 open_id 不同 account 互不影响
    assert get_binding("ou_a", account_id="ecom-app-gtl")["is_set"] is False
    assert get_binding("ou_a", account_id="ecom-app")["scope_key"] == "tts-id-all"
