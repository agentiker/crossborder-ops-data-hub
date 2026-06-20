"""看板路由（web/routes/board）鉴权与权限闸单测（TestClient）。

只测路由层：登录态守卫（无 cookie→302、未登记→403）、权限闸越界→403。
取数 _collect 的范围夹紧逻辑已在 test_user_authz 覆盖，这里 monkeypatch 掉以隔离路由行为。
"""
import pytest
from fastapi.testclient import TestClient

from core.config import settings
from services.scope_resolution import ScopeError
from services.user_authz import UserPermission
from web import web_security
from web.app import app
from web.web_security import require_web_user

BOSS = UserPermission(
    open_id="ou_boss", role="boss", allowed_scope_key=None,
    channel="feishu", account_id="ecom-app",
)
OPER = UserPermission(
    open_id="ou_op", role="operator", allowed_scope_key="scope-a",
    channel="feishu", account_id="ecom-app",
)

_FAKE_PAYLOAD = {
    "scope": "全部范围", "scope_key": "", "can_switch": True, "scopes": [],
    "role": "boss", "period": "last_30d",
    "overview": {"orders": {}, "inventory": {}}, "trend": {"points": []},
    "top": {"items": []}, "low": {"items": [], "buckets": {}},
    "fulfillment": {"items": [], "buckets": {}},
}


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def test_no_cookie_redirects_to_login():
    client = TestClient(app, follow_redirects=False)
    r = client.get("/board")
    assert r.status_code == 302
    assert "/board/auth/feishu/login" in r.headers["location"]


def test_logged_in_pending_403_friendly(monkeypatch):
    # cookie 验签通过但权限为 None，且登记状态=pending → 友好"申请已提交"页（不回显 open_id）
    monkeypatch.setattr(web_security, "verify_session_cookie", lambda raw: "ou_unknown")
    monkeypatch.setattr(web_security, "get_user_permission", lambda oid, **k: None)
    monkeypatch.setattr(web_security, "get_registration_status", lambda oid, **k: "pending")
    client = TestClient(app, follow_redirects=False)
    r = client.get("/board", cookies={settings.feishu_oauth.cookie_name: "whatever"})
    assert r.status_code == 403
    assert "等待管理员开通" in r.text  # 待审批友好文案
    assert "ou_unknown" not in r.text  # 不再回显 open_id
    assert "user_admin" not in r.text  # 不再要人跑 CLI


def test_logged_in_unregistered_403_generic(monkeypatch):
    # 无登记记录（none）/ 已停用 → 通用"未获授权"页，仍 403 fail closed
    monkeypatch.setattr(web_security, "verify_session_cookie", lambda raw: "ou_unknown")
    monkeypatch.setattr(web_security, "get_user_permission", lambda oid, **k: None)
    monkeypatch.setattr(web_security, "get_registration_status", lambda oid, **k: "none")
    client = TestClient(app, follow_redirects=False)
    r = client.get("/board", cookies={settings.feishu_oauth.cookie_name: "whatever"})
    assert r.status_code == 403
    assert "暂无看板权限" in r.text


def test_boss_renders_200(monkeypatch):
    app.dependency_overrides[require_web_user] = lambda: BOSS

    async def fake_collect(perm, period, scope):
        return _FAKE_PAYLOAD

    monkeypatch.setattr("web.routes.board._collect", fake_collect)
    r = TestClient(app).get("/board")
    assert r.status_code == 200
    assert "运营看板" in r.text


def test_operator_out_of_scope_html_403(monkeypatch):
    app.dependency_overrides[require_web_user] = lambda: OPER

    async def boom(perm, period, scope):
        raise ScopeError("指定店铺不在 scope 范围内")

    monkeypatch.setattr("web.routes.board._collect", boom)
    r = TestClient(app).get("/board?scope=other-scope")
    assert r.status_code == 403
    assert "超出你的权限范围" in r.text


def test_operator_out_of_scope_data_403_json(monkeypatch):
    app.dependency_overrides[require_web_user] = lambda: OPER

    async def boom(perm, period, scope):
        raise ScopeError("越界")

    monkeypatch.setattr("web.routes.board._collect", boom)
    r = TestClient(app).get("/board/data?scope=other-scope")
    assert r.status_code == 403
    assert r.json()["error"] == "forbidden"


def test_board_data_boss_ok_json(monkeypatch):
    app.dependency_overrides[require_web_user] = lambda: BOSS

    async def fake_collect(perm, period, scope):
        return _FAKE_PAYLOAD

    monkeypatch.setattr("web.routes.board._collect", fake_collect)
    r = TestClient(app).get("/board/data")
    assert r.status_code == 200
    assert r.json()["scope"] == "全部范围"
