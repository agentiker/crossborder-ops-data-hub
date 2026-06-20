"""统一权限闸 services/user_authz 的权限矩阵单测（plan/14 方案 B）。

仿 test_scope_binding.py：monkeypatch SessionLocal 指向内存 sqlite。
user_authz 自身（读 user_roles）与其复用的 scope_resolution（expand/resolve_filters）
都要指向同一 session，否则越界判断读不到 scope。
"""
from __future__ import annotations

import pytest

from models.base_models import BusinessScope, PlatformToken, UserRole
from services import scope_resolution, user_authz
from services.scope_resolution import ScopeError
from services.user_authz import (
    AuthzError,
    assert_authorized,
    ensure_registration,
    get_registration_status,
    get_user_permission,
    resolve_authorized_scope,
)


def _use(session, monkeypatch):
    monkeypatch.setattr(user_authz, "SessionLocal", lambda: session)
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


def _role(session, open_id, role, *, allowed_scope_key=None, account_id="ecom-app", active=True):
    session.add(
        UserRole(
            channel="feishu",
            account_id=account_id,
            open_id=open_id,
            role=role,
            allowed_scope_key=allowed_scope_key,
            is_active=active,
        )
    )


def _seed(session):
    """A=[s1,s2,s3]，B=[s4]（与 A 不相交），sub=[s1]（A 子集）。"""
    for s in ["s1", "s2", "s3", "s4"]:
        _token(session, s)
    _scope(session, "scope-a", ["s1", "s2", "s3"])
    _scope(session, "scope-b", ["s4"])
    _scope(session, "scope-sub", ["s1"])
    session.commit()


# ---------- get_user_permission ----------

def test_get_permission_unregistered_returns_none(session, monkeypatch):
    _use(session, monkeypatch)
    assert get_user_permission("ou_none") is None


def test_get_permission_inactive_returns_none(session, monkeypatch):
    _use(session, monkeypatch)
    _role(session, "ou_dead", "operator", allowed_scope_key="scope-a", active=False)
    session.commit()
    assert get_user_permission("ou_dead") is None


def test_get_permission_empty_open_id_returns_none(session, monkeypatch):
    _use(session, monkeypatch)
    assert get_user_permission("") is None


def test_get_permission_boss(session, monkeypatch):
    _use(session, monkeypatch)
    _role(session, "ou_boss", "boss")
    session.commit()
    perm = get_user_permission("ou_boss")
    assert perm is not None
    assert perm.role == "boss"
    assert perm.is_boss is True
    assert perm.allowed_scope_key is None


def test_get_permission_operator(session, monkeypatch):
    _use(session, monkeypatch)
    _role(session, "ou_op", "operator", allowed_scope_key="scope-a")
    session.commit()
    perm = get_user_permission("ou_op")
    assert perm.role == "operator"
    assert perm.is_boss is False
    assert perm.allowed_scope_key == "scope-a"


def test_get_permission_isolated_by_account(session, monkeypatch):
    _use(session, monkeypatch)
    _role(session, "ou_x", "boss", account_id="ecom-app")
    session.commit()
    assert get_user_permission("ou_x", account_id="ecom-app") is not None
    assert get_user_permission("ou_x", account_id="ecom-app-gtl") is None


# ---------- resolve_authorized_scope: boss ----------

def test_boss_no_request_is_full_range(session, monkeypatch):
    _use(session, monkeypatch)
    _seed(session)
    _role(session, "ou_boss", "boss")
    session.commit()
    perm = get_user_permission("ou_boss")
    out = resolve_authorized_scope(perm)
    assert out.scope_key is None
    assert out.shop_ids == []
    assert out.display_text == "全部范围"


def test_boss_request_any_scope_resolves_it(session, monkeypatch):
    _use(session, monkeypatch)
    _seed(session)
    _role(session, "ou_boss", "boss")
    session.commit()
    perm = get_user_permission("ou_boss")
    out = resolve_authorized_scope(perm, requested_scope_key="scope-a")
    assert out.scope_key == "scope-a"
    assert sorted(out.shop_ids) == ["s1", "s2", "s3"]


# ---------- resolve_authorized_scope: operator ----------

def test_operator_no_request_clamped_to_allowed(session, monkeypatch):
    _use(session, monkeypatch)
    _seed(session)
    _role(session, "ou_op", "operator", allowed_scope_key="scope-a")
    session.commit()
    perm = get_user_permission("ou_op")
    out = resolve_authorized_scope(perm)
    assert out.scope_key == "scope-a"
    assert sorted(out.shop_ids) == ["s1", "s2", "s3"]


def test_operator_request_subset_is_narrowed(session, monkeypatch):
    _use(session, monkeypatch)
    _seed(session)
    _role(session, "ou_op", "operator", allowed_scope_key="scope-a")
    session.commit()
    perm = get_user_permission("ou_op")
    # 显式 shop 子集
    out = resolve_authorized_scope(perm, requested_shop_ids=["s1"])
    assert out.shop_ids == ["s1"]
    # 请求一个本身是 A 子集的命名 scope
    out2 = resolve_authorized_scope(perm, requested_scope_key="scope-sub")
    assert out2.shop_ids == ["s1"]


def test_operator_request_out_of_scope_shop_raises(session, monkeypatch):
    _use(session, monkeypatch)
    _seed(session)
    _role(session, "ou_op", "operator", allowed_scope_key="scope-a")
    session.commit()
    perm = get_user_permission("ou_op")
    with pytest.raises(ScopeError):
        resolve_authorized_scope(perm, requested_shop_ids=["s4"])


def test_operator_request_out_of_scope_named_scope_raises(session, monkeypatch):
    _use(session, monkeypatch)
    _seed(session)
    _role(session, "ou_op", "operator", allowed_scope_key="scope-a")
    session.commit()
    perm = get_user_permission("ou_op")
    # scope-b=[s4] 与 allowed A 不相交 → 越界
    with pytest.raises(ScopeError):
        resolve_authorized_scope(perm, requested_scope_key="scope-b")


def test_operator_without_allowed_scope_raises_authz(session, monkeypatch):
    _use(session, monkeypatch)
    _seed(session)
    _role(session, "ou_bad", "operator", allowed_scope_key=None)
    session.commit()
    perm = get_user_permission("ou_bad")
    with pytest.raises(AuthzError):
        resolve_authorized_scope(perm)


# ---------- assert_authorized（便捷封装） ----------

def test_assert_unregistered_raises_authz(session, monkeypatch):
    _use(session, monkeypatch)
    _seed(session)
    with pytest.raises(AuthzError):
        assert_authorized("ou_none")


def test_assert_boss_passes_through(session, monkeypatch):
    _use(session, monkeypatch)
    _seed(session)
    _role(session, "ou_boss", "boss")
    session.commit()
    out = assert_authorized("ou_boss", requested_scope_key="scope-a")
    assert sorted(out.shop_ids) == ["s1", "s2", "s3"]


def test_assert_operator_out_of_scope_raises_scope_error(session, monkeypatch):
    _use(session, monkeypatch)
    _seed(session)
    _role(session, "ou_op", "operator", allowed_scope_key="scope-a")
    session.commit()
    with pytest.raises(ScopeError):
        assert_authorized("ou_op", requested_shop_ids=["s4"])


def test_assert_operator_default_clamped(session, monkeypatch):
    _use(session, monkeypatch)
    _seed(session)
    _role(session, "ou_op", "operator", allowed_scope_key="scope-a")
    session.commit()
    out = assert_authorized("ou_op")
    assert out.scope_key == "scope-a"
    assert sorted(out.shop_ids) == ["s1", "s2", "s3"]


# ---------- ensure_registration（自助申请自动登记） ----------

def _find_role(session, open_id, account_id="ecom-app"):
    return (
        session.query(UserRole)
        .filter(UserRole.account_id == account_id, UserRole.open_id == open_id)
        .first()
    )


def test_ensure_registration_first_user_bootstraps_boss(session, monkeypatch):
    """user_roles 为空时首登者 bootstrap 为 boss + 启用（解鸡蛋问题）。"""
    _use(session, monkeypatch)
    assert ensure_registration("ou_first", name="张三") == "boss"
    row = _find_role(session, "ou_first")
    assert row.role == "boss"
    assert row.is_active is True
    assert row.allowed_scope_key is None
    # bootstrap 后立即可用
    assert get_user_permission("ou_first").is_boss is True


def test_ensure_registration_subsequent_is_pending(session, monkeypatch):
    """表非空且无此人 → 落待审批行（pending + 未启用 + 带姓名 note），仍 fail-closed。"""
    _use(session, monkeypatch)
    _role(session, "ou_boss", "boss")  # 表已有人
    session.commit()
    assert ensure_registration("ou_new", name="李四") == "pending"
    row = _find_role(session, "ou_new")
    assert row.role == "pending"
    assert row.is_active is False
    assert "李四" in (row.note or "")
    # 待审批期间不可用
    assert get_user_permission("ou_new") is None


def test_ensure_registration_existing_active_untouched(session, monkeypatch):
    """已有启用行 → 原样不动、返回 existing。"""
    _use(session, monkeypatch)
    _role(session, "ou_boss", "boss")
    _role(session, "ou_op", "operator", allowed_scope_key="scope-a")
    session.commit()
    assert ensure_registration("ou_op", name="忽略") == "existing"
    row = _find_role(session, "ou_op")
    assert row.role == "operator"  # 未被改写
    assert row.allowed_scope_key == "scope-a"


def test_ensure_registration_deactivated_not_resurrected(session, monkeypatch):
    """已停用的人重新登录 → 保持停用，不被复活成 pending。"""
    _use(session, monkeypatch)
    _role(session, "ou_boss", "boss")
    _role(session, "ou_dead", "operator", allowed_scope_key="scope-a", active=False)
    session.commit()
    assert ensure_registration("ou_dead") == "existing"
    row = _find_role(session, "ou_dead")
    assert row.is_active is False
    assert row.role == "operator"  # 仍是原角色，不是 pending


# ---------- get_registration_status（403 页文案区分） ----------

def test_registration_status_pending(session, monkeypatch):
    _use(session, monkeypatch)
    _role(session, "ou_boss", "boss")
    session.commit()
    ensure_registration("ou_new")
    assert get_registration_status("ou_new") == "pending"


def test_registration_status_deactivated(session, monkeypatch):
    _use(session, monkeypatch)
    _role(session, "ou_dead", "operator", allowed_scope_key="scope-a", active=False)
    session.commit()
    assert get_registration_status("ou_dead") == "deactivated"


def test_registration_status_none(session, monkeypatch):
    _use(session, monkeypatch)
    assert get_registration_status("ou_ghost") == "none"
