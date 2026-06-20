"""经营报告路由（web/routes/report）+ ops_report_link + agent tool 单测。

覆盖：
- report 路由 token 无效 -> 401
- report 路由 token 有效 -> 200 + HTML 包含 echarts + __DATA__ 已替换
- ops_report_link 端点返回结构 + URL 格式
- agent_tool ops_report 返回 markdown
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from core.config import settings
from web.app import app
from web.signed_link import make_token
from web.web_session import make_session_cookie


@pytest.fixture(autouse=True)
def _set_secret(monkeypatch):
    """确保签名密钥可用（report 签名 token + 飞书登录 session cookie 两套）。"""
    monkeypatch.setattr(settings.dashboard, "link_secret", "test-secret-for-report-12345")
    monkeypatch.setattr(settings.dashboard, "token_ttl_seconds", 1800)
    monkeypatch.setattr(settings.dashboard, "public_base_url", "https://board.example.com")
    monkeypatch.setattr(settings.feishu_oauth, "session_secret", "test-session-secret-67890")
    monkeypatch.setattr(settings.feishu_oauth, "app_id", "cli_test123")


def _login_cookie(open_id: str) -> dict:
    """造一个匹配 open_id 的飞书登录 session cookie。"""
    return {settings.feishu_oauth.cookie_name: make_session_cookie(open_id)}


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


# ── Fake data collectors (monkeypatch _collect to avoid DB) ──────────

_FAKE_REPORT_DATA = {
    "kind": "period",
    "title": "经营报告",
    "change_label": "较上期",
    "trend_title": "GMV / 广告 / 订单趋势",
    "trend_mini": False,
    "scope": "全店",
    "period_label": "近 7 天",
    "generated_at": "2026-06-19 10:30",
    "kpi": {
        "gmv": {"value": 128450.5, "change": 12.4, "currency": "IDR"},
        "orders": {"value": 1820, "change": 8.1},
        "ad_spend": {"value": 18650.0, "change": 9.2, "currency": "IDR"},
        "roas": {"value": 6.89, "change": 3.1},
        "sku_count": 420,
        "low_stock_count": 12,
    },
    "trend": {
        "dates": ["06-13", "06-14", "06-15", "06-16", "06-17", "06-18", "06-19"],
        "gmv": [12000, 14300, 11000, 15500, 18000, 16500, 19000],
        "orders": [220, 250, 200, 280, 310, 290, 340],
    },
    "top_skus": [
        {"name": "无线蓝牙耳机 Pro", "units": 820, "gmv": 41000},
        {"name": "手机壳套装", "units": 650, "gmv": 13000},
    ],
    "low_stock": [
        {"name": "无线蓝牙耳机 Pro", "stock": 0, "velocity": 28.0, "days": 0, "level": "stockout", "level_label": "断货"},
        {"name": "手机壳套装", "stock": 5, "velocity": 10.0, "days": 0.5, "level": "critical", "level_label": "告急"},
    ],
}


async def _fake_collect(open_id, start_date, end_date, period):
    return _FAKE_REPORT_DATA


# ── Report route tests ──────────────────────────────────────────────


def test_report_invalid_token_401():
    """token 无效 -> 401 + 错误页。"""
    client = TestClient(app)
    r = client.get("/report/daily_brief?t=invalid-token&period=last_7d")
    assert r.status_code == 401
    assert "链接已失效" in r.text


def test_report_valid_token_matching_viewer_200(monkeypatch):
    """token 有效 + 打开者 == 签发对象 -> 200 + HTML 包含 echarts + 广告消耗。"""
    monkeypatch.setattr("web.routes.report._collect", _fake_collect)
    token = make_token("ou_test_user", ttl=1800)
    client = TestClient(app, cookies=_login_cookie("ou_test_user"))
    r = client.get(f"/report/daily_brief?t={token}&period=last_7d")
    assert r.status_code == 200
    assert "echarts" in r.text
    assert "__DATA__" not in r.text  # 占位符已替换
    assert "经营报告" in r.text  # period 版型标题
    assert "广告消耗" in r.text  # 趋势图第三条线
    assert "128450" in r.text  # GMV value injected


def test_report_mismatched_viewer_403(monkeypatch):
    """token 有效但打开者 != 签发对象（同事/他人转发）-> 403 拒绝。"""
    monkeypatch.setattr("web.routes.report._collect", _fake_collect)
    token = make_token("ou_owner", ttl=1800)
    client = TestClient(app, cookies=_login_cookie("ou_someone_else"))
    r = client.get(f"/report/daily_brief?t={token}&period=last_7d")
    assert r.status_code == 403
    assert "仅限本人" in r.text


def test_report_no_cookie_redirects_to_login(monkeypatch):
    """token 有效但未登录 -> 302 跳飞书登录，next 带回原始报告 URL。"""
    monkeypatch.setattr("web.routes.report._collect", _fake_collect)
    token = make_token("ou_test_user", ttl=1800)
    client = TestClient(app)
    r = client.get(
        f"/report/daily_brief?t={token}&period=last_7d", follow_redirects=False
    )
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith("/board/auth/feishu/login")
    assert "next=" in loc
    assert "report" in loc  # next 含原始报告路径


def test_report_invalid_template_404(monkeypatch):
    """未知模板名 -> 404。"""
    monkeypatch.setattr("web.routes.report._collect", _fake_collect)
    token = make_token("ou_test_user", ttl=1800)
    client = TestClient(app)
    r = client.get(f"/report/nonexistent?t={token}&period=last_7d")
    assert r.status_code == 404


# ── ops_report_link endpoint tests ──────────────────────────────────


def test_report_link_feishu_wraps_applink():
    """飞书渠道（默认）签出的链接包成 applink，让飞书端内 web-view 打开。

    用 path/path_pc（语义=本应用注册页面）而非 lk_target_url（任意外链，PC 端弹外链拦截）；
    query（t/period）按官方变通拆到 applink 顶层。详见 _wrap_feishu_applink 注释。
    """
    from urllib.parse import parse_qs, urlsplit
    from web.routes.data import get_report_link

    result = _run(get_report_link(
        open_id="ou_test_user",
        template_name="daily_brief",
        period="last_7d",
    ))
    # 走「网页应用」通道（web_app/open + appId + path/path_pc），飞书端内打开不弹外链提示
    assert result.url.startswith(
        "https://applink.feishu.cn/client/web_app/open?appId=cli_test123&mode=window"
    )
    q = parse_qs(urlsplit(result.url).query)
    # path 是相对路径（不带 /board），PC 端优先 path_pc；二者一致
    assert q["path"] == ["/report/daily_brief"]
    assert q["path_pc"] == ["/report/daily_brief"]
    # 报告参数搬到 applink 顶层（飞书重组成 主页/path?query）
    assert q["period"] == ["last_7d"]
    assert q["t"] and q["t"][0]  # 签名 token 存在
    # 彻底弃用 lk_target_url（与 path 互斥，且 PC 端会被当外链拦）
    assert "lk_target_url" not in result.url
    assert result.expires_in == 1800
    assert "查看经营报告" in result.markdown  # last_7d → 区间报版型文案
    assert result.url in result.markdown


def test_report_link_webui_raw_no_applink():
    """WebUI（wrap_applink=False）用裸链，不包 applink。"""
    from web.routes.data import get_report_link

    result = _run(get_report_link(
        open_id="ou_test_user",
        template_name="daily_brief",
        period="last_7d",
        wrap_applink=False,
    ))
    assert result.url.startswith("https://board.example.com/report/daily_brief?t=")
    assert "applink.feishu.cn" not in result.url


def test_report_link_with_dates():
    """ops_report_link 传 start_date/end_date 时 URL 包含这些参数（裸链断言更直观）。"""
    from web.routes.data import get_report_link

    result = _run(get_report_link(
        open_id="ou_test_user",
        template_name="daily_brief",
        start_date="2026-06-01",
        end_date="2026-06-15",
        period="last_7d",
        wrap_applink=False,
    ))
    assert "start_date=2026-06-01" in result.url
    assert "end_date=2026-06-15" in result.url


def test_report_link_daily_label_for_single_day():
    """单日 period（today）→ 链接文案为「查看经营日报」。"""
    from web.routes.data import get_report_link

    result = _run(get_report_link(
        open_id="ou_test_user", template_name="daily_brief",
        period="today", wrap_applink=False,
    ))
    assert "查看经营日报" in result.markdown


# ── _collect 版型自适应（按时间窗自动判定）────────────────────────────


def _patch_collect_data_sources(monkeypatch):
    """monkeypatch _collect 依赖的数据端点，避免连库；趋势按请求窗口生成点位。"""
    from datetime import date, timedelta

    async def fake_overview(**k):
        return {"scope": "全店", "inventory": {"total_sku": 100, "low_stock_count": 3},
                "orders": {"gmv": 999, "order_count": 9}}

    async def fake_orders_summary(*, start_date, end_date, **k):
        return {"gmv": 1000, "order_count": 50, "units_sold": 80, "avg_order_value": 20}

    async def fake_orders_trend(*, start_date, end_date, **k):
        sd, ed = date.fromisoformat(start_date), date.fromisoformat(end_date)
        pts, d = [], sd
        while d <= ed:
            pts.append({"date": d.isoformat(), "gmv": 100, "order_count": 5})
            d += timedelta(days=1)
        return {"points": pts}

    async def fake_ad_spend(**k):
        return {"total_ad_spend": 200, "roas": 5.0}

    async def fake_ad_trend(*, start_date, end_date, **k):
        return {"points": []}

    async def fake_top(**k):
        return {"items": [{"product_name": "热卖A", "units_sold": 30, "gmv": 250},
                          {"product_name": "热卖B", "units_sold": 20, "gmv": 100}]}

    async def fake_low(**k):
        # 销速模型：buckets.total=2（与 overview 静态 low_stock_count=3 故意不同）
        return {"items": [
                    {"product_name": "断货品", "available_stock": 0, "daily_velocity": 5.0,
                     "days_of_cover": 0.0, "bucket": "stockout"},
                    {"product_name": "告急品", "available_stock": 8, "daily_velocity": 4.0,
                     "days_of_cover": 2.0, "bucket": "critical"}],
                "buckets": {"stockout": 1, "critical": 1, "warning": 0, "total": 2}}

    async def fake_empty(**k):
        return {"items": []}

    monkeypatch.setattr("web.routes.report.get_overview", fake_overview)
    monkeypatch.setattr("web.routes.report.get_orders_summary", fake_orders_summary)
    monkeypatch.setattr("web.routes.report.get_orders_trend", fake_orders_trend)
    monkeypatch.setattr("web.routes.report.get_ad_spend", fake_ad_spend)
    monkeypatch.setattr("web.routes.report.get_ad_spend_trend", fake_ad_trend)
    monkeypatch.setattr("web.routes.report.get_orders_top_skus", fake_top)
    monkeypatch.setattr("web.routes.report.get_low_stock", fake_low)

    # 当日 intraday 分支依赖：scope 解析 / 此刻 / 同期 GMV（避免连库）
    from datetime import datetime as _dt
    from services.scope_resolution import ScopeFilters

    monkeypatch.setattr("web.routes.report._resolve_scope",
                        lambda **k: ScopeFilters(platform=None, country=None, shop_ids=None,
                                                 scope_key=None, display_text="全店"))
    monkeypatch.setattr("web.routes.report.business_now", lambda: _dt(2026, 6, 20, 14, 30))
    monkeypatch.setattr("web.routes.report.get_gmv_summary_intraday",
                        lambda **k: {"gmv": 1000, "order_count": 50, "units_sold": 80,
                                     "avg_order_value": 20})


def test_collect_single_day_is_daily(monkeypatch):
    """单日 period → 日报版型：标题/环比口径/迷你趋势 + 近 7 天趋势点。"""
    _patch_collect_data_sources(monkeypatch)
    from web.routes.report import _collect

    data = _run(_collect("ou_x", None, None, "today"))
    assert data["kind"] == "daily"
    assert data["title"] == "经营日报"
    assert data["change_label"] == "较近 7 天同期均值"   # 当日走 intraday 近7天同期均值基准
    assert data["intraday"] is True
    assert data["cutoff_label"] and "数据截至" in data["cutoff_label"]
    assert data["kpi"]["orders"]["baseline"] is not None   # 同期均值基准值
    assert data["trend_mini"] is True
    assert len(data["trend"]["dates"]) == 7  # 单日报告画近 7 天迷你趋势
    assert data["kpi"]["gmv"]["value"] == 1000  # 当日 KPI 走 intraday 取数


def test_collect_yesterday_full_day_not_intraday(monkeypatch):
    """明确『昨天』→ 整天对整天（非 intraday）：较前一日、无 cutoff。"""
    _patch_collect_data_sources(monkeypatch)
    from web.routes.report import _collect

    data = _run(_collect("ou_x", None, None, "yesterday"))
    assert data["kind"] == "daily"
    assert data["change_label"] == "较前一日"
    assert data["intraday"] is False
    assert data["cutoff_label"] is None


def test_collect_multi_day_is_period(monkeypatch):
    """多日 period → 区间报版型：标题/环比口径/完整趋势。"""
    _patch_collect_data_sources(monkeypatch)
    from web.routes.report import _collect

    data = _run(_collect("ou_x", None, None, "last_7d"))
    assert data["kind"] == "period"
    assert data["title"] == "经营报告"
    assert data["change_label"] == "较上期"
    assert data["trend_mini"] is False
    assert len(data["trend"]["dates"]) == 7  # last_7d = 7 天窗口


def test_collect_top_share_and_kpi_tips(monkeypatch):
    """Top item 含 GMV 占比；GMV/广告 KPI 含口径 tip。"""
    _patch_collect_data_sources(monkeypatch)
    from web.routes.report import _collect

    data = _run(_collect("ou_x", None, None, "today"))
    top = data["top_skus"]
    assert top and top[0]["share"] == 25.0  # 250 / 1000 = 25%
    assert "已付款" in data["kpi"]["gmv"]["tip"]
    assert "环比" in data["kpi"]["ad_spend"]["tip"]
    # 断货风险计数走销速模型 buckets.total(=2)，而非 overview 静态 low_stock_count(=3)
    assert data["kpi"]["low_stock_count"] == 2


# ── AI 洞察端点 (/report/{tpl}/insight) ──────────────────────────────


@pytest.fixture(autouse=True)
def _clear_insight_cache():
    """清当天洞察缓存，避免用例间串味（缓存按 open_id+period+日期）。"""
    from web.routes import report as _r
    _r._INSIGHT_CACHE.clear()
    yield
    _r._INSIGHT_CACHE.clear()


class _FakeProvider:
    """假 LLM provider：stream() 吐一个含 JSON 的 TurnComplete。"""
    model = "fake-model"

    def __init__(self, text):
        self._text = text

    def stream(self, messages, tools):
        from services.llm.types import TurnComplete
        yield TurnComplete(text=self._text, tool_calls=[], finish_reason="stop")


def test_insight_success(monkeypatch):
    """token+cookie 匹配 + LLM 正常 → available:true + 三段解析。"""
    monkeypatch.setattr("web.routes.report._collect", _fake_collect)
    fake = _FakeProvider('{"headline":"今日 GMV 创新高","problems":["X 断货"],"actions":["补货 X"]}')
    monkeypatch.setattr("services.llm.get_provider", lambda *a, **k: fake)
    token = make_token("ou_test_user", ttl=1800)
    client = TestClient(app, cookies=_login_cookie("ou_test_user"))
    r = client.get(f"/report/daily_brief/insight?t={token}&period=today")
    assert r.status_code == 200
    d = r.json()
    assert d["available"] is True
    assert d["headline"] == "今日 GMV 创新高"
    assert d["problems"] == ["X 断货"]
    assert d["actions"] == ["补货 X"]
    assert d["model"] == "fake-model"


def test_insight_forbidden_when_viewer_mismatch(monkeypatch):
    """打开者 != 签发对象 → available:false（不泄漏、不 500）。"""
    monkeypatch.setattr("web.routes.report._collect", _fake_collect)
    token = make_token("ou_owner", ttl=1800)
    client = TestClient(app, cookies=_login_cookie("ou_someone_else"))
    r = client.get(f"/report/daily_brief/insight?t={token}&period=today")
    assert r.status_code == 200
    assert r.json()["available"] is False


def test_insight_degrades_on_llm_error(monkeypatch):
    """LLM 配置缺失/报错 → available:false，绝不 500（不阻塞主报告）。"""
    from services.llm.types import LLMError

    monkeypatch.setattr("web.routes.report._collect", _fake_collect)

    def _boom(*a, **k):
        raise LLMError("not configured")

    monkeypatch.setattr("services.llm.get_provider", _boom)
    token = make_token("ou_test_user", ttl=1800)
    client = TestClient(app, cookies=_login_cookie("ou_test_user"))
    r = client.get(f"/report/daily_brief/insight?t={token}&period=today")
    assert r.status_code == 200
    assert r.json()["available"] is False


def test_report_html_has_ai_and_insight_hook(monkeypatch):
    """报告 HTML 含 AI 块节点 + insight 拉取路径。"""
    monkeypatch.setattr("web.routes.report._collect", _fake_collect)
    token = make_token("ou_test_user", ttl=1800)
    client = TestClient(app, cookies=_login_cookie("ou_test_user"))
    r = client.get(f"/report/daily_brief?t={token}&period=today")
    assert "ai-headline" in r.text
    assert "/insight" in r.text


# ── agent_tool ops_report tests ─────────────────────────────────────


def test_ops_report_tool_returns_markdown(monkeypatch):
    """agent_tool ops_report 返回 markdown。"""
    from services.user_authz import UserPermission
    from web.agent_tools import run_tool

    perm = UserPermission(
        open_id="ou_test_user", role="boss", allowed_scope_key=None,
        channel="feishu", account_id="ecom-app",
    )
    # Monkeypatch resolve_authorized_scope to avoid DB
    from services.scope_resolution import ScopeFilters

    monkeypatch.setattr(
        "web.agent_tools.resolve_authorized_scope",
        lambda p: ScopeFilters(platform=None, country=None, shop_ids=None, scope_key=None, display_text="全店"),
    )

    result = run_tool("ops_report", {"template_name": "daily_brief", "period": "last_7d"}, perm)
    # run_tool returns the markdown string directly for ops_report
    assert "查看经营报告" in result  # last_7d → 区间报版型文案


# ── auth_feishu next 回跳 ───────────────────────────────────────────


def test_safe_next_whitelist():
    """_safe_next 只放行站内 /report、/board、/app；挡开放重定向。"""
    from web.routes.auth_feishu import _safe_next

    assert _safe_next("/report/daily_brief?t=x") == "/report/daily_brief?t=x"
    assert _safe_next("/board") == "/board"
    assert _safe_next("/app/foo") == "/app/foo"
    # 拒绝项
    assert _safe_next("//evil.com") is None        # 协议相对 URL
    assert _safe_next("https://evil.com") is None  # 绝对 URL
    assert _safe_next("/etc/passwd") is None        # 非白名单前缀
    assert _safe_next("/reportx") is None           # 前缀边界（report 后须 / $ ?）
    assert _safe_next("") is None


def test_login_state_carries_next_roundtrip():
    """login 把 next 编进签名 state，callback 侧能原样解出（防篡改 + 回跳）。"""
    from web.routes import auth_feishu as af

    safe = af._safe_next("/report/daily_brief?t=abc&period=last_7d")
    assert safe is not None
    # login 侧：state value = nonce|b64(next)
    value = f"nonce123|{af._b64url(safe.encode('utf-8'))}"
    state = af._make_signed(value, 600)
    # callback 侧：验签 + 解码
    got = af._verify_signed(state)
    assert got is not None and "|" in got
    decoded = af._b64url_decode(got.split("|", 1)[1]).decode("utf-8")
    assert decoded == "/report/daily_brief?t=abc&period=last_7d"


# ── helper ──────────────────────────────────────────────────────────

import asyncio


def _run(coro):
    """Run async function synchronously (same pattern as agent_tools._run)."""
    return asyncio.run(coro)
