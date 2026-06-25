"""看板路由（web/routes/board）鉴权与权限闸单测（TestClient）。

只测路由层：登录态守卫（无 cookie→302、未登记→403）、权限闸越界→403。
取数 _collect 的范围夹紧逻辑已在 test_user_authz 覆盖，这里 monkeypatch 掉以隔离路由行为。
"""
import asyncio

import pytest
from fastapi.testclient import TestClient

from core.config import settings
from core.tenancy import current_account, set_current_account
from services.scope_resolution import ScopeError
from services.scope_resolution import ScopeFilters
from services.user_authz import UserPermission
from web.routes import board as board_routes
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
    monkeypatch.setattr(web_security, "verify_session_cookie", lambda raw: ("ou_unknown", "ecom-app"))
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
    monkeypatch.setattr(web_security, "verify_session_cookie", lambda raw: ("ou_unknown", "ecom-app"))
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


def test_collect_sets_current_account_before_nested_data_calls(monkeypatch):
    perm = UserPermission(
        open_id="ou_gtl_boss",
        role="boss",
        allowed_scope_key=None,
        channel="feishu",
        account_id="ecom-app-gtl",
    )
    set_current_account(None)

    monkeypatch.setattr(
        board_routes,
        "resolve_authorized_scope",
        lambda perm, requested_scope_key=None: ScopeFilters(
            platform=None,
            country=None,
            shop_ids=["7494734967204644284"],
            scope_key=None,
            display_text="全部范围",
        ),
    )

    async def fake_get_overview(**kwargs):
        assert current_account() == "ecom-app-gtl"
        return {"scope": "全部范围", "orders": {}, "inventory": {}}

    async def fake_get_orders_trend(**kwargs):
        assert current_account() == "ecom-app-gtl"
        return {"points": [], "window_label": "近 30 天"}

    async def fake_get_orders_top_skus(**kwargs):
        assert current_account() == "ecom-app-gtl"
        return {"items": []}

    async def fake_get_low_stock(**kwargs):
        assert current_account() == "ecom-app-gtl"
        return {"items": [], "buckets": {}}

    async def fake_get_fulfillments_pending(**kwargs):
        assert current_account() == "ecom-app-gtl"
        return {"items": [], "buckets": {}}

    # 渠道饼图(阶段5)/利润卡(阶段3a)是 _collect 的同步嵌套数据调用——同样要 mock，否则会构造
    # 真实 client 读 platform_tokens（token 已密文化后本地无 key 解不开）。它们也属"嵌套调用"，
    # 故一并断言 current_account 已就位。
    def fake_get_channel_gmv_breakdown(**kwargs):
        assert current_account() == "ecom-app-gtl"
        return {"available": False}

    def fake_get_profit_card(**kwargs):
        assert current_account() == "ecom-app-gtl"
        return {"available": False}

    monkeypatch.setattr(board_routes, "get_overview", fake_get_overview)
    monkeypatch.setattr(board_routes, "get_orders_trend", fake_get_orders_trend)
    monkeypatch.setattr(board_routes, "get_orders_top_skus", fake_get_orders_top_skus)
    monkeypatch.setattr(board_routes, "get_low_stock", fake_get_low_stock)
    monkeypatch.setattr(board_routes, "get_fulfillments_pending", fake_get_fulfillments_pending)
    monkeypatch.setattr(board_routes, "get_channel_gmv_breakdown", fake_get_channel_gmv_breakdown)
    monkeypatch.setattr(board_routes, "get_profit_card", fake_get_profit_card)
    monkeypatch.setattr(
        board_routes,
        "get_gmv_summary",
        lambda **kwargs: {
            "gmv": 0,
            "order_count": 0,
            "units_sold": 0,
            "avg_order_value": 0,
        },
    )
    monkeypatch.setattr(
        board_routes,
        "get_ad_spend_summary",
        lambda **kwargs: {
            "total_ad_spend": 0,
            "gmv_max_fee": 0,
            "tap_commission": 0,
            "affiliate_commission": 0,
            "currency": "IDR",
        },
    )
    monkeypatch.setattr(board_routes, "get_roas", lambda **kwargs: {"roas": None})

    data = asyncio.run(board_routes._collect(perm, "last_30d", ""))
    assert data["scope"] == "全部范围"
