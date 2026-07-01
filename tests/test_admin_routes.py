"""角色权限可配置 admin API 单测（plan/15 Phase C，boss-only）。

仿 test_chat_routes：override require_web_user_api 造登录态、monkeypatch
web.routes.admin.SessionLocal 指向内存 sqlite、stub expand_scope 校验。验证：
- boss 能 list / upsert / deactivate；
- operator 与未登记用户访问任一端点都 403；
- operator upsert 缺 scope_key / 非法 scope_key → 400；
- boss upsert 时 scope_key 被忽略存 None。
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.db import Base
from models import base_models  # noqa: F401  注册 ORM 表
from models.base_models import UserRole
from services.scope_resolution import ScopeError
from services.user_authz import UserPermission
from web.app import app
from web.routes import admin as admin_mod
from web.web_security import require_web_user_api

BOSS = UserPermission(open_id="ou_boss", role="boss", allowed_scope_key=None,
                      channel="feishu", account_id="ecom-app")
OPER = UserPermission(open_id="ou_op", role="operator", allowed_scope_key="scope-a",
                      channel="feishu", account_id="ecom-app")


@pytest.fixture()
def _db(monkeypatch):
    # TestClient 在 Starlette 线程池里跑端点，故用 StaticPool + check_same_thread=False
    # 共享单个内存连接跨线程（默认 SingletonThreadPool 会给新线程空白库 → no such table）。
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(admin_mod, "SessionLocal", Session)
    yield Session
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def _clear():
    yield
    app.dependency_overrides.clear()


@pytest.fixture()
def _scope_ok(monkeypatch):
    """默认放行已知 scope；scope-bad 视为未知 → ScopeError（仿 expand_scope 行为）。"""
    def fake_expand(key, account_id="ecom-app"):
        if key == "scope-bad":
            raise ScopeError(f"未知或停用的 scope：{key}")
        return object()
    monkeypatch.setattr(admin_mod, "expand_scope", fake_expand)


def _login(perm):
    app.dependency_overrides[require_web_user_api] = lambda: perm


def _seed(Session, **kw):
    s = Session()
    try:
        s.add(UserRole(channel=kw.get("channel", "feishu"),
                       account_id=kw.get("account_id", "ecom-app"),
                       open_id=kw["open_id"], role=kw["role"],
                       allowed_scope_key=kw.get("allowed_scope_key"),
                       note=kw.get("note"), is_active=kw.get("is_active", True)))
        s.commit()
    finally:
        s.close()


# ── boss-only 守卫 ─────────────────────────────────────────────────────────────


def test_list_requires_auth():
    # 无 cookie、无 override → require_web_user_api 返回 401
    assert TestClient(app).get("/api/admin/roles").status_code == 401


@pytest.mark.parametrize("path,method,body", [
    ("/api/admin/roles", "get", None),
    ("/api/admin/roles", "post", {"open_id": "ou_x", "role": "boss"}),
    ("/api/admin/roles/deactivate", "post", {"open_id": "ou_x"}),
])
def test_operator_forbidden(_db, path, method, body):
    _login(OPER)
    client = TestClient(app)
    r = client.get(path) if method == "get" else client.post(path, json=body)
    assert r.status_code == 403


@pytest.mark.parametrize("path,method,body", [
    ("/api/admin/roles", "get", None),
    ("/api/admin/roles", "post", {"open_id": "ou_x", "role": "boss"}),
    ("/api/admin/roles/deactivate", "post", {"open_id": "ou_x"}),
])
def test_unregistered_forbidden(_db, path, method, body):
    # 未登记用户：require_web_user_api 本会 403；这里用一个非 boss 假身份模拟“拿到登录态但非 boss”
    _login(UserPermission(open_id="ou_ghost", role="operator", allowed_scope_key=None,
                          channel="feishu", account_id="ecom-app"))
    client = TestClient(app)
    r = client.get(path) if method == "get" else client.post(path, json=body)
    assert r.status_code == 403


# ── boss 正常流程 ──────────────────────────────────────────────────────────────


def test_boss_list(_db):
    _seed(_db, open_id="ou_a", role="boss")
    _seed(_db, open_id="ou_b", role="operator", allowed_scope_key="scope-a")
    _login(BOSS)
    r = TestClient(app).get("/api/admin/roles")
    assert r.status_code == 200
    items = r.json()["items"]
    assert {i["open_id"] for i in items} == {"ou_a", "ou_b"}
    by_id = {i["open_id"]: i for i in items}
    assert by_id["ou_b"]["allowed_scope_key"] == "scope-a"
    assert by_id["ou_a"]["channel"] == "feishu"
    assert by_id["ou_a"]["account_id"] == "ecom-app"


def test_boss_upsert_creates_operator(_db, _scope_ok):
    _login(BOSS)
    r = TestClient(app).post("/api/admin/roles", json={
        "open_id": "ou_new", "role": "operator", "scope_key": "scope-a", "note": "运营A"})
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "operator" and body["allowed_scope_key"] == "scope-a"
    # 落库
    s = _db()
    try:
        row = s.query(UserRole).filter(UserRole.open_id == "ou_new").first()
        assert row.role == "operator" and row.allowed_scope_key == "scope-a"
        assert row.note == "运营A" and row.is_active is True
    finally:
        s.close()


def test_boss_upsert_updates_existing(_db, _scope_ok):
    _seed(_db, open_id="ou_up", role="operator", allowed_scope_key="scope-a", note="旧")
    _login(BOSS)
    r = TestClient(app).post("/api/admin/roles", json={
        "open_id": "ou_up", "role": "boss"})
    assert r.status_code == 200
    s = _db()
    try:
        rows = s.query(UserRole).filter(UserRole.open_id == "ou_up").all()
        assert len(rows) == 1  # upsert，不新增
        assert rows[0].role == "boss" and rows[0].allowed_scope_key is None
    finally:
        s.close()


def test_boss_upsert_ignores_scope_key(_db, _scope_ok):
    """boss upsert 时 scope_key 被忽略，存 None。"""
    _login(BOSS)
    r = TestClient(app).post("/api/admin/roles", json={
        "open_id": "ou_boss2", "role": "boss", "scope_key": "scope-a"})
    assert r.status_code == 200
    assert r.json()["allowed_scope_key"] is None
    s = _db()
    try:
        row = s.query(UserRole).filter(UserRole.open_id == "ou_boss2").first()
        assert row.allowed_scope_key is None
    finally:
        s.close()


def test_operator_upsert_missing_scope_400(_db, _scope_ok):
    _login(BOSS)
    r = TestClient(app).post("/api/admin/roles", json={
        "open_id": "ou_o", "role": "operator"})
    assert r.status_code == 400


def test_operator_upsert_invalid_scope_400(_db, _scope_ok):
    _login(BOSS)
    r = TestClient(app).post("/api/admin/roles", json={
        "open_id": "ou_o", "role": "operator", "scope_key": "scope-bad"})
    assert r.status_code == 400


def test_boss_deactivate(_db):
    _seed(_db, open_id="ou_d", role="operator", allowed_scope_key="scope-a")
    _login(BOSS)
    r = TestClient(app).post("/api/admin/roles/deactivate", json={"open_id": "ou_d"})
    assert r.status_code == 200
    assert r.json()["is_active"] is False
    s = _db()
    try:
        row = s.query(UserRole).filter(UserRole.open_id == "ou_d").first()
        assert row.is_active is False
    finally:
        s.close()


def test_boss_deactivate_not_found_404(_db):
    _login(BOSS)
    r = TestClient(app).post("/api/admin/roles/deactivate", json={"open_id": "ou_missing"})
    assert r.status_code == 404


# ── scopes 列表（Phase C 前端用）─────────────────────────────────────────────


def test_boss_list_scopes(_db, monkeypatch):
    monkeypatch.setattr(admin_mod, "list_scopes", lambda account_id="ecom-app": [
        {"scope_key": "tts-id-all", "scope_name": "印尼TikTok全部店"},
        {"scope_key": "tts-id-shop-1", "scope_name": "印尼店一"},
    ])
    _login(BOSS)
    r = TestClient(app).get("/api/admin/scopes")
    assert r.status_code == 200
    items = r.json()["items"]
    assert {i["scope_key"] for i in items} == {"tts-id-all", "tts-id-shop-1"}
    assert items[0]["scope_name"]  # 不为空


def test_operator_list_scopes_forbidden(_db):
    _login(OPER)
    assert TestClient(app).get("/api/admin/scopes").status_code == 403


def test_unauth_list_scopes_401():
    assert TestClient(app).get("/api/admin/scopes").status_code == 401


# ── 业务阈值配置（biz-configs，boss-only）─────────────────────────────────────


def test_biz_configs_list_returns_metadata_and_defaults(_db):
    _login(BOSS)
    r = TestClient(app).get("/api/admin/biz-configs")
    assert r.status_code == 200
    items = r.json()["items"]
    by_key = {i["config_key"]: i for i in items}
    hot = by_key["hotsell_daily_units_threshold"]
    assert hot["label"] and hot["unit"] == "件/天" and hot["type"] == "int"
    assert hot["default_value"] == 50 and hot["current_value"] == 50
    assert hot["is_overridden"] is False


def test_biz_config_upsert_and_reflect(_db):
    _login(BOSS)
    client = TestClient(app)
    r = client.post("/api/admin/biz-configs",
                    json={"config_key": "hotsell_daily_units_threshold", "value": 30})
    assert r.status_code == 200
    body = r.json()
    assert body["current_value"] == 30 and body["is_overridden"] is True
    # 再 list 应反映
    items = {i["config_key"]: i for i in client.get("/api/admin/biz-configs").json()["items"]}
    assert items["hotsell_daily_units_threshold"]["current_value"] == 30


def test_biz_config_reset_falls_back(_db):
    _login(BOSS)
    client = TestClient(app)
    client.post("/api/admin/biz-configs",
                json={"config_key": "hotsell_daily_units_threshold", "value": 30})
    r = client.post("/api/admin/biz-configs/reset",
                    json={"config_key": "hotsell_daily_units_threshold"})
    assert r.status_code == 200
    assert r.json()["current_value"] == 50 and r.json()["is_overridden"] is False


def test_biz_config_unknown_key_400(_db):
    _login(BOSS)
    r = TestClient(app).post("/api/admin/biz-configs",
                             json={"config_key": "no_such_key", "value": 1})
    assert r.status_code == 400


def test_biz_config_out_of_range_400(_db):
    _login(BOSS)
    # hotsell min=1 max=100000 → 0 越界
    r = TestClient(app).post("/api/admin/biz-configs",
                             json={"config_key": "hotsell_daily_units_threshold", "value": 0})
    assert r.status_code == 400


def test_biz_config_non_integer_400(_db):
    _login(BOSS)
    # int 类给小数 → 400
    r = TestClient(app).post("/api/admin/biz-configs",
                             json={"config_key": "hotsell_daily_units_threshold", "value": 30.5})
    assert r.status_code == 400


def test_biz_config_return_rate_dispatch(_db):
    """退货率走 return_rate_configs 专表分派。"""
    _login(BOSS)
    client = TestClient(app)
    r = client.post("/api/admin/biz-configs",
                    json={"config_key": "estimated_return_rate_default", "value": 0.08})
    assert r.status_code == 200
    assert abs(r.json()["current_value"] - 0.08) < 1e-9 and r.json()["is_overridden"] is True


def test_biz_config_replenishment_shared_row(_db):
    """补货三系数共享一行：改一个不影响其它。"""
    _login(BOSS)
    client = TestClient(app)
    client.post("/api/admin/biz-configs",
                json={"config_key": "replenish_normal_multiplier", "value": 1.8})
    items = {i["config_key"]: i for i in client.get("/api/admin/biz-configs").json()["items"]}
    assert abs(items["replenish_normal_multiplier"]["current_value"] - 1.8) < 1e-9
    assert items["replenish_normal_multiplier"]["is_overridden"] is True
    # 未改的两个仍是默认
    assert items["replenish_velocity_days"]["is_overridden"] is False
    assert items["replenish_superhot_multiplier"]["is_overridden"] is False


def test_biz_config_operator_forbidden(_db):
    _login(OPER)
    client = TestClient(app)
    assert client.get("/api/admin/biz-configs").status_code == 403
    assert client.post("/api/admin/biz-configs",
                       json={"config_key": "hotsell_daily_units_threshold", "value": 30}).status_code == 403


def test_biz_config_tenant_isolation(_db):
    """gtl boss 改的值不影响 ecom-app boss 看到的。"""
    GTL_BOSS = UserPermission(open_id="ou_gtl", role="boss", allowed_scope_key=None,
                              channel="feishu", account_id="ecom-app-gtl")
    # gtl boss 改爆单=20
    _login(GTL_BOSS)
    TestClient(app).post("/api/admin/biz-configs",
                         json={"config_key": "hotsell_daily_units_threshold", "value": 20})
    # ecom-app boss 看仍是默认 50
    _login(BOSS)
    items = {i["config_key"]: i for i in TestClient(app).get("/api/admin/biz-configs").json()["items"]}
    assert items["hotsell_daily_units_threshold"]["current_value"] == 50
