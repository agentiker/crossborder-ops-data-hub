"""web/routes/data.py `_resolve_scope` 服务端自动注入默认范围的行为锁定。

覆盖 e408bdf（服务端自动应用默认范围）+ P0（读写对齐）的核心契约：
agent 只需传 open_id，不传 scope_id 时服务端凭 binding 表自动注入默认范围；
显式 scope_id / 范围词永远优先、不读 binding。这条逻辑若回归，弱模型切了范围却静默失效。
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from models.base_models import BusinessScope, PlatformToken
from services import scope_binding, scope_resolution
from services.scope_binding import set_binding
from web.routes import data as data_routes


def _use(session, monkeypatch):
    # _resolve_scope 内部走 get_binding（scope_binding.SessionLocal）
    # 与 resolve_filters（scope_resolution.SessionLocal），两个都要指向同一内存 session。
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


def test_open_id_with_named_binding_auto_injects_scope(session, monkeypatch):
    """没传 scope_id 但 open_id 有命名 binding → 自动注入该 scope 的 shop_ids。"""
    _use(session, monkeypatch)
    _scope(session, "tts-id-all", ["s1", "s2"])
    session.commit()
    set_binding("ou_a", "tts-id-all")

    out = data_routes._resolve_scope(open_id="ou_a")
    assert out.scope_key == "tts-id-all"
    assert sorted(out.shop_ids) == ["s1", "s2"]
    assert out.display_text == "TikTok Shop / 印尼 / 2 个店铺"


def test_explicit_scope_id_wins_binding_not_read(session, monkeypatch):
    """显式 scope_id 优先：即便 binding 设了别的范围，也用显式 scope_id、不读 binding。"""
    _use(session, monkeypatch)
    _scope(session, "tts-id-all", ["s1", "s2"])
    _scope(session, "tts-id-vip", ["s1"])
    session.commit()
    set_binding("ou_a", "tts-id-all")  # 默认是全部店

    # 消息里带范围词 → 显式 scope_id=tts-id-vip，临时覆盖、不动默认。
    out = data_routes._resolve_scope(scope_id="tts-id-vip", open_id="ou_a")
    assert out.scope_key == "tts-id-vip"
    assert out.shop_ids == ["s1"]


def test_no_binding_falls_back_to_all(session, monkeypatch):
    """open_id 从未切过范围（无 binding 行）→ 全量，无 scope。"""
    _use(session, monkeypatch)
    session.commit()

    out = data_routes._resolve_scope(open_id="ou_never")
    assert out.scope_key is None
    assert out.shop_ids == []
    assert out.display_text == "全部范围"


def test_explicit_all_binding_falls_back_to_all(session, monkeypatch):
    """切过"全部"（binding is_set 但 scope_key=None）→ 仍走全量，不注入。"""
    _use(session, monkeypatch)
    session.commit()
    set_binding("ou_all", "")  # 显式全量

    out = data_routes._resolve_scope(open_id="ou_all")
    assert out.scope_key is None
    assert out.shop_ids == []


def test_no_open_id_no_scope_is_all(session, monkeypatch):
    """既无 open_id 又无 scope_id → 全量（不查 binding）。"""
    _use(session, monkeypatch)
    session.commit()

    out = data_routes._resolve_scope()
    assert out.scope_key is None
    assert out.shop_ids == []


def test_auto_injected_scope_narrows_with_in_scope_shop(session, monkeypatch):
    """自动注入 scope 后，显式 in-scope shop_id 收窄取交集到该店。"""
    _use(session, monkeypatch)
    _scope(session, "tts-id-all", ["s1", "s2"])
    session.commit()
    set_binding("ou_a", "tts-id-all")

    out = data_routes._resolve_scope(open_id="ou_a", shop_id="s1")
    assert out.shop_ids == ["s1"]


def test_auto_injected_scope_rejects_out_of_scope_shop(session, monkeypatch):
    """自动注入 scope 后，显式越界 shop_id → 400（ScopeError 转 HTTPException）。"""
    _use(session, monkeypatch)
    _scope(session, "tts-id-all", ["s1", "s2"])
    session.commit()
    set_binding("ou_a", "tts-id-all")

    with pytest.raises(HTTPException) as exc:
        data_routes._resolve_scope(open_id="ou_a", shop_id="s_outside")
    assert exc.value.status_code == 400
