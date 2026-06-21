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


def test_binding_isolated_by_account(session, monkeypatch):
    """多租户：account 经 contextvar 注入；同 open_id 不同租户的 binding 互不可见。

    铁律：读写 binding 用同一个头才命中同一行（gtl 曾因读写 account 不对齐切范围静默失效）。
    """
    from core.tenancy import set_current_account
    _use(session, monkeypatch)
    _scope(session, "tts-id-all", ["s1"])  # ecom-app（默认）名下
    session.add(
        BusinessScope(
            account_id="ecom-app-gtl", scope_key="gtl-all", scope_name="gtl",
            scope_type="shop_group", platform="tiktok_shop", country="ID",
            shop_ids=["s1"], is_active=True,
        )
    )
    session.commit()

    # ecom-app 用户在 ecom 租户下设了 binding
    set_current_account("ecom-app")
    set_binding("ou_shared", "tts-id-all")
    assert get_binding("ou_shared")["scope_key"] == "tts-id-all"

    # 同一 open_id 切到 gtl 租户 → 看不到 ecom 的 binding（隔离）
    set_current_account("ecom-app-gtl")
    assert get_binding("ou_shared")["is_set"] is False
    # gtl 租户下设自己的 binding，与 ecom 互不干扰
    set_binding("ou_shared", "gtl-all")
    assert get_binding("ou_shared")["scope_key"] == "gtl-all"

    # 切回 ecom：仍是 ecom 自己的 binding，没被 gtl 覆盖
    set_current_account("ecom-app")
    assert get_binding("ou_shared")["scope_key"] == "tts-id-all"


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
