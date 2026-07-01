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

    async def fake_collect(perm, period, scope, *args, **kwargs):
        return _FAKE_PAYLOAD

    monkeypatch.setattr("web.routes.board._collect", fake_collect)
    r = TestClient(app).get("/board")
    assert r.status_code == 200
    assert "运营看板" in r.text


def test_operator_out_of_scope_html_403(monkeypatch):
    app.dependency_overrides[require_web_user] = lambda: OPER

    async def boom(perm, period, scope, *args, **kwargs):
        raise ScopeError("指定店铺不在 scope 范围内")

    monkeypatch.setattr("web.routes.board._collect", boom)
    r = TestClient(app).get("/board?scope=other-scope")
    assert r.status_code == 403
    assert "超出你的权限范围" in r.text


def test_operator_out_of_scope_data_403_json(monkeypatch):
    app.dependency_overrides[require_web_user] = lambda: OPER

    async def boom(perm, period, scope, *args, **kwargs):
        raise ScopeError("越界")

    monkeypatch.setattr("web.routes.board._collect", boom)
    r = TestClient(app).get("/board/data?scope=other-scope")
    assert r.status_code == 403
    assert r.json()["error"] == "forbidden"


def test_board_data_boss_ok_json(monkeypatch):
    app.dependency_overrides[require_web_user] = lambda: BOSS

    async def fake_collect(perm, period, scope, *args, **kwargs):
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
        lambda perm, requested_scope_key=None, platform=None, country=None: ScopeFilters(
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

    def fake_get_top_products(**kwargs):
        assert current_account() == "ecom-app-gtl"
        return []

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
    monkeypatch.setattr(board_routes, "get_top_products", fake_get_top_products)
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
            "paid_ad_spend": 0,
            "creator_commission": 0,
            "gmv_max_fee": 0,
            "tap_commission": 0,
            "affiliate_commission": 0,
            "currency": "IDR",
            "complete": True,
            "settled_through": "2026-06-14",
            "latest_covered_date": None,
        },
    )
    monkeypatch.setattr(board_routes, "get_roas", lambda **kwargs: {"roas": None})

    data = asyncio.run(board_routes._collect(perm, "last_30d", ""))
    assert data["scope"] == "全部范围"


def test_collect_passes_granularity_to_trend(monkeypatch):
    """_collect 把 granularity 透传给 get_orders_trend（前端选单天传 hour）。"""
    set_current_account(None)
    monkeypatch.setattr(
        board_routes,
        "resolve_authorized_scope",
        lambda perm, requested_scope_key=None, platform=None, country=None: ScopeFilters(
            platform=None, country=None, shop_ids=["s1"], scope_key=None, display_text="全部范围",
        ),
    )
    seen = {}

    async def fake_get_orders_trend(**kwargs):
        seen["granularity"] = kwargs.get("granularity")
        return {"points": [], "granularity": kwargs.get("granularity")}

    async def fake_async_empty(**kwargs):
        return {"orders": {}, "inventory": {}, "items": [], "buckets": {}}

    monkeypatch.setattr(board_routes, "get_overview", fake_async_empty)
    monkeypatch.setattr(board_routes, "get_orders_trend", fake_get_orders_trend)
    monkeypatch.setattr(board_routes, "get_low_stock", fake_async_empty)
    monkeypatch.setattr(board_routes, "get_fulfillments_pending", fake_async_empty)
    monkeypatch.setattr(board_routes, "get_top_products", lambda **k: [])
    monkeypatch.setattr(board_routes, "get_channel_gmv_breakdown", lambda **k: {"available": False})
    monkeypatch.setattr(board_routes, "get_profit_card", lambda **k: {"available": False})
    monkeypatch.setattr(
        board_routes, "get_gmv_summary",
        lambda **k: {"gmv": 0, "order_count": 0, "units_sold": 0, "avg_order_value": 0},
    )
    monkeypatch.setattr(
        board_routes, "get_ad_spend_summary",
        lambda **k: {
            "total_ad_spend": 0, "paid_ad_spend": 0, "creator_commission": 0,
            "gmv_max_fee": 0, "tap_commission": 0, "affiliate_commission": 0,
            "currency": "IDR", "complete": True, "settled_through": "2026-06-14",
            "latest_covered_date": None,
        },
    )
    monkeypatch.setattr(board_routes, "get_roas", lambda **k: {"roas": None})

    # 单天 + granularity=hour → 透传 hour
    asyncio.run(board_routes._collect(
        BOSS, "today", "", "2026-06-09", "2026-06-09", None, None, "hour",
    ))
    assert seen["granularity"] == "hour"

    # 不传 → 默认 day
    asyncio.run(board_routes._collect(BOSS, "last_30d", ""))
    assert seen["granularity"] == "day"


def _patch_collect_deps_no_db(monkeypatch, trend_points_by_window):
    """Mock 掉 _collect 全部连库依赖（含 _scope_options / fee_rate），让 _collect 可离线跑完。

    trend_points_by_window: {(start, end): [point_dict, ...]} —— 按 (start,end) 窗口返回不同趋势点，
    用来区分当期 vs 上期。
    """
    set_current_account(None)
    monkeypatch.setattr(
        board_routes,
        "resolve_authorized_scope",
        lambda perm, requested_scope_key=None, platform=None, country=None: ScopeFilters(
            platform=None, country=None, shop_ids=["s1"], scope_key=None, display_text="全部范围",
        ),
    )

    async def fake_get_orders_trend(**kwargs):
        key = (kwargs.get("start_date"), kwargs.get("end_date"))
        pts = trend_points_by_window.get(key, [])
        gran = kwargs.get("granularity") or "day"
        # 返回 TrendResponse-like 对象（_asdict 兼容 model_dump）；用 dataclass 复刻最小字段。
        from dataclasses import dataclass

        @dataclass
        class _Pt:
            date: str
            gmv: float
            order_count: int
            units_sold: int
            label: str = ""

            def model_dump(self):
                return {"date": self.date, "gmv": self.gmv, "order_count": self.order_count,
                        "units_sold": self.units_sold, "label": self.label or None}

        @dataclass
        class _Trend:
            points: list
            granularity: str = "day"
            window_label: str = ""

            def model_dump(self):
                return {"points": [p.model_dump() for p in self.points],
                        "granularity": self.granularity,
                        "window_label": self.window_label or None}

        return _Trend([_Pt(**p) for p in pts], granularity=gran,
                      window_label=f"{key[0]} ~ {key[1]}")

    async def fake_async_empty(**kwargs):
        return {"orders": {}, "inventory": {}, "items": [], "buckets": {}}

    monkeypatch.setattr(board_routes, "get_overview", fake_async_empty)
    monkeypatch.setattr(board_routes, "get_orders_trend", fake_get_orders_trend)
    monkeypatch.setattr(board_routes, "get_low_stock", fake_async_empty)
    monkeypatch.setattr(board_routes, "get_fulfillments_pending", fake_async_empty)
    monkeypatch.setattr(board_routes, "get_top_products", lambda **k: [])
    monkeypatch.setattr(board_routes, "get_channel_gmv_breakdown", lambda **k: {"available": False})
    monkeypatch.setattr(board_routes, "get_profit_card", lambda **k: {"available": False})
    monkeypatch.setattr(board_routes, "get_fee_rate_monitor", lambda **k: {"status": "insufficient", "skip_reason": "test", "trend": []})
    monkeypatch.setattr(board_routes, "_scope_options", lambda perm: [])
    monkeypatch.setattr(
        board_routes, "get_gmv_summary",
        lambda **k: {"gmv": 0, "order_count": 0, "units_sold": 0, "avg_order_value": 0},
    )
    monkeypatch.setattr(
        board_routes, "get_ad_spend_summary",
        lambda **k: {
            "total_ad_spend": 0, "paid_ad_spend": 0, "creator_commission": 0,
            "gmv_max_fee": 0, "tap_commission": 0, "affiliate_commission": 0,
            "currency": "IDR", "complete": True, "settled_through": "2026-06-14",
            "latest_covered_date": None,
        },
    )
    monkeypatch.setattr(board_routes, "get_roas", lambda **k: {"roas": None})


def test_collect_includes_prev_points_for_single_day_hour(monkeypatch):
    """单天逐小时：_collect 在 trend 上挂 prev_points（前一天的逐小时点），granularity 同为 hour。"""
    cur_pts = [{"date": "2026-06-09", "gmv": 10.0, "order_count": 1, "units_sold": 1, "label": "09:00"}]
    prev_pts = [{"date": "2026-06-08", "gmv": 5.0, "order_count": 1, "units_sold": 1, "label": "09:00"}]
    by_window = {("2026-06-09", "2026-06-09"): cur_pts, ("2026-06-08", "2026-06-08"): prev_pts}
    _patch_collect_deps_no_db(monkeypatch, by_window)

    data = asyncio.run(board_routes._collect(
        BOSS, "today", "", "2026-06-09", "2026-06-09", None, None, "hour",
    ))
    # 主体趋势照常
    assert [p["gmv"] for p in data["trend"]["points"]] == [10.0]
    # 上期对比线：前一天、逐小时、gmv 对齐
    assert data["trend"]["prev_points"] == [
        {"date": "2026-06-08", "gmv": 5.0, "order_count": 1, "units_sold": 1, "label": "09:00"}
    ]
    assert data["trend"]["prev_window_label"]


def test_collect_prev_points_absent_when_prev_window_empty(monkeypatch):
    """上期窗口无数据点时 prev_points 为空列表（不缺字段、不炸），主体趋势照常展示。"""
    cur_pts = [{"date": "2026-06-09", "gmv": 10.0, "order_count": 1, "units_sold": 1, "label": "09:00"}]
    by_window = {("2026-06-09", "2026-06-09"): cur_pts}  # 上期窗口没 mock → 返回空
    _patch_collect_deps_no_db(monkeypatch, by_window)

    data = asyncio.run(board_routes._collect(
        BOSS, "today", "", "2026-06-09", "2026-06-09", None, None, "hour",
    ))
    assert data["trend"]["points"]
    assert data["trend"]["prev_points"] == []
