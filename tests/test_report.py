"""经营报告路由（web/routes/report）+ ops_report_link + agent tool 单测。

覆盖：
- report 路由 token 无效 -> 401
- report 路由 token 有效 -> 200 + HTML 包含 echarts + __DATA__ 已替换
- ops_report_link 端点返回结构 + URL 格式
- agent_tool ops_report 返回 markdown
"""
from __future__ import annotations

from datetime import timedelta

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
    # path 带 /board 前缀（替换主页 path 后落在桌面端主页 /board 范围内，PC 端才不弹外链），
    # PC 端优先 path_pc；二者一致
    assert q["path"] == ["/board/report/daily_brief"]
    assert q["path_pc"] == ["/board/report/daily_brief"]
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
    assert result.url.startswith("https://board.example.com/board/report/daily_brief?t=")
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


def test_report_link_summary_matches_collect(monkeypatch):
    """ops_report_link 返回的 summary 数字与 _collect 同源同口径（防漂移）。

    summary 是 agent 写文字报告引用的权威数字源——必须与链接里可视化报告的 _collect 完全一致，
    否则文字摘要和图表数字打架。本测试 mock 同一份数据源、分别取 summary 与 _collect，断言关键字段相等。
    """
    _patch_collect_data_sources(monkeypatch)
    from web.routes.data import _extract_report_summary, get_report_link
    from web.routes.report import _collect

    full = _run(_collect("ou_x", None, None, "last_7d"))
    link = _run(get_report_link(
        open_id="ou_x", template_name="daily_brief",
        period="last_7d", wrap_applink=False,
    ))
    assert link.summary is not None
    # 关键 KPI 数字一致
    assert link.summary["kpi"]["gmv"]["value"] == full["kpi"]["gmv"]["value"]
    assert link.summary["kpi"]["orders"]["value"] == full["kpi"]["orders"]["value"]
    assert link.summary["kpi"]["ad_spend"]["value"] == full["kpi"]["ad_spend"]["value"]
    # Top爆款 / 库存风险一致
    assert link.summary["top_skus"] == full["top_skus"]
    assert link.summary["low_stock"] == full["low_stock"]
    # 护栏标记一致
    assert link.summary["low_volume"] == full["low_volume"]
    # summary 刻意不带 trend 逐日序列（省 token）
    assert "trend" not in link.summary


def test_report_link_summary_weekly_has_health(monkeypatch):
    """周报 summary 含 health（集中度/动销率/新品），kind=weekly。"""
    _patch_collect_data_sources(monkeypatch)
    from web.routes.data import get_report_link

    link = _run(get_report_link(
        open_id="ou_x", template_name="weekly_review",
        period="last_week", wrap_applink=False,
    ))
    assert link.summary["kind"] == "weekly"
    assert "health" in link.summary
    assert "concentration" in link.summary["health"]
    assert "sell_through" in link.summary["health"]


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
    # ops_report 返回 {markdown, summary}：markdown 是链接文案，summary 是权威摘要供 agent 写文字报告
    assert isinstance(result, dict)
    assert "查看经营报告" in result["markdown"]  # last_7d → 区间报版型文案
    assert result["summary"] is not None
    assert result["summary"]["kind"] in ("daily", "period")
    assert "gmv" in result["summary"]["kpi"]


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


# ════════════════════════════════════════════════════════════════════
# 经营周报 weekly_review
# ════════════════════════════════════════════════════════════════════


# ── _weekly_windows / _window_bounds：窗口与基准 ─────────────────────


def test_weekly_windows_scheduled_last_week():
    """定时（非 intraday）：当期=上周整周（7天），基准=上上周（7天），cutoff=None，紧邻。"""
    from web.routes.report import _weekly_windows

    cur_sd, cur_ed, prev_sd, prev_ed, cutoff = _weekly_windows(intraday=False)
    assert cutoff is None
    assert (cur_ed - cur_sd).days == 6          # 上周一~上周日 = 7 天
    assert (prev_ed - prev_sd).days == 6         # 上上周也 7 天
    assert prev_ed == cur_sd - timedelta(days=1)  # 基准紧邻当期之前
    assert cur_sd.weekday() == 0                  # 周一起算


def test_weekly_windows_intraday_this_week():
    """实时（intraday）：当期=本周一~今天，基准=上周同期（天数严格相等），cutoff 非空。"""
    from web.routes.report import _weekly_windows
    from core.timezone import business_today

    cur_sd, cur_ed, prev_sd, prev_ed, cutoff = _weekly_windows(intraday=True)
    assert cutoff is not None
    assert cur_ed == business_today()
    assert cur_sd.weekday() == 0
    # 本周已过天数 == 上周同期天数（杜绝"本周3天 vs 上周整周"）
    assert (cur_ed - cur_sd).days == (prev_ed - prev_sd).days
    # 周对齐：上周一 ~ 上周同一星期几（不是紧邻前窗），与触发在周几无关
    assert prev_sd == cur_sd - timedelta(days=7)  # 上周一
    assert prev_ed == cur_ed - timedelta(days=7)  # 上周同一星期几


def test_window_bounds_intraday_vs_fullday():
    """_window_bounds：cutoff 非空→连续区间[起当地00:00, 末当地cutoff]；为空→整天闭区间。"""
    from datetime import date, time
    from core.timezone import intraday_window_utc, paid_window_utc
    from services.order_metrics import _window_bounds

    sd, ed = date(2026, 6, 8), date(2026, 6, 10)
    cut = time(14, 30)
    # intraday：起点 = 起始日当地 00:00 的 UTC；终点 = 末日当地 cutoff 的 UTC
    s, e = _window_bounds(sd, ed, cut)
    assert s == paid_window_utc(sd, sd)[0]
    assert e == intraday_window_utc(ed, cut)[1]
    # 非 intraday：整天闭区间
    s2, e2 = _window_bounds(sd, ed, None)
    assert (s2, e2) == paid_window_utc(sd, ed)


# ── _collect_weekly：商品健康度 + KPI（patch 数据源，免连库）──────────


def _patch_weekly_data_sources(monkeypatch):
    """monkeypatch _collect_weekly 依赖的端点 + 服务函数，避免连库。"""
    from datetime import date, datetime as _dt, timedelta as _td
    from services.scope_resolution import ScopeFilters

    async def fake_overview(**k):
        return {"scope": "全店", "inventory": {"total_sku": 120, "low_stock_count": 4},
                "orders": {"gmv": 1000, "order_count": 50}}

    async def fake_orders_trend(*, start_date, end_date, **k):
        sd, ed = date.fromisoformat(start_date), date.fromisoformat(end_date)
        pts, d = [], sd
        while d <= ed:
            pts.append({"date": d.isoformat(), "gmv": 100, "order_count": 5})
            d += _td(days=1)
        return {"points": pts}

    async def fake_ad_spend(**k):
        return {"total_ad_spend": 200, "roas": 5.0}

    async def fake_ad_trend(*, start_date, end_date, **k):
        return {"points": []}

    async def fake_top(**k):
        return {"items": [
            {"product_name": "爆款A", "units_sold": 60, "gmv": 600},
            {"product_name": "爆款B", "units_sold": 20, "gmv": 200},
            {"product_name": "爆款C", "units_sold": 10, "gmv": 100},
        ]}

    async def fake_low(**k):
        return {"items": [
                    {"product_name": "断货品", "available_stock": 0, "daily_velocity": 5.0,
                     "days_of_cover": 0.0, "bucket": "stockout"}],
                "buckets": {"stockout": 1, "critical": 0, "warning": 0, "total": 1}}

    monkeypatch.setattr("web.routes.report.get_overview", fake_overview)
    monkeypatch.setattr("web.routes.report.get_orders_trend", fake_orders_trend)
    monkeypatch.setattr("web.routes.report.get_ad_spend", fake_ad_spend)
    monkeypatch.setattr("web.routes.report.get_ad_spend_trend", fake_ad_trend)
    monkeypatch.setattr("web.routes.report.get_orders_top_skus", fake_top)
    monkeypatch.setattr("web.routes.report.get_low_stock", fake_low)

    monkeypatch.setattr("web.routes.report._resolve_scope",
                        lambda **k: ScopeFilters(platform=None, country=None, shop_ids=None,
                                                 scope_key=None, display_text="全店"))
    monkeypatch.setattr("web.routes.report.business_now", lambda: _dt(2026, 6, 20, 14, 30))
    # 服务层聚合（同步函数，已 import 进 report 命名空间）
    monkeypatch.setattr("web.routes.report.get_gmv_summary",
                        lambda **k: {"gmv": 1000, "order_count": 50, "units_sold": 80,
                                     "avg_order_value": 20})
    monkeypatch.setattr("web.routes.report.get_gmv_summary_intraday_range",
                        lambda **k: {"gmv": 500, "order_count": 25, "units_sold": 40,
                                     "avg_order_value": 20})
    monkeypatch.setattr("web.routes.report.get_sell_through",
                        lambda **k: {"active_sku": 5, "total_sku": 14, "rate": 35.7})
    monkeypatch.setattr("web.routes.report.get_new_product_performance",
                        lambda **k: [{"product_id": "p1", "title": "新品A", "units_sold": 10, "gmv": 300},
                                     {"product_id": "p2", "title": "新品B", "units_sold": 0, "gmv": 0}])


def test_collect_weekly_scheduled_last_week(monkeypatch):
    """定时周报（period=last_week）：kind=weekly、整周口径、较上周、5 KPI 齐全。"""
    _patch_weekly_data_sources(monkeypatch)
    from web.routes.report import _collect_weekly

    data = _run(_collect_weekly("ou_x", "last_week"))
    assert data["kind"] == "weekly"
    assert data["title"] == "经营周报"
    assert data["change_label"] == "较上周"
    assert data["intraday"] is False
    assert data["cutoff_label"] is None
    # 5 张结果卡口径
    assert data["kpi"]["gmv"]["value"] == 1000
    assert data["kpi"]["aov"]["value"] == 20            # 客单价
    assert data["kpi"]["ad_spend"]["value"] == 200
    assert data["kpi"]["roas"]["value"] == 5.0          # 1000/200
    assert data["empty_window"] is False                # 有单 → 不触发零数据护栏


def test_collect_weekly_empty_window_flag(monkeypatch):
    """整周完全无已付款订单（0 单）→ empty_window=True（护栏：全 0 不被误判成系统故障）。"""
    _patch_weekly_data_sources(monkeypatch)
    monkeypatch.setattr(
        "web.routes.report.get_gmv_summary",
        lambda **k: {"gmv": 0, "order_count": 0, "units_sold": 0, "avg_order_value": 0},
    )
    from web.routes.report import _collect_weekly

    data = _run(_collect_weekly("ou_x", "last_week"))
    assert data["empty_window"] is True
    assert data["kpi"]["gmv"]["value"] == 0
    assert data["kpi"]["aov"]["value"] == 0


def test_collect_weekly_intraday_this_week(monkeypatch):
    """实时周报（period=this_week）：intraday、较上周同期、cutoff_label 存在、GMV 走 intraday。"""
    _patch_weekly_data_sources(monkeypatch)
    from web.routes.report import _collect_weekly

    data = _run(_collect_weekly("ou_x", "this_week"))
    assert data["intraday"] is True
    assert data["change_label"] == "较上周同期"
    assert data["cutoff_label"] and "本周累计" in data["cutoff_label"]
    assert data["kpi"]["gmv"]["value"] == 500           # intraday 区间取数


def test_collect_weekly_concentration_and_health(monkeypatch):
    """爆款集中度 Top1/Top3 占比 + 动销率 + 新品表现正确装配。"""
    _patch_weekly_data_sources(monkeypatch)
    from web.routes.report import _collect_weekly

    data = _run(_collect_weekly("ou_x", "last_week"))
    health = data["health"]
    conc = health["concentration"]
    assert conc["top1_name"] == "爆款A"
    assert conc["top1_share"] == 60.0                    # 600 / 1000
    assert conc["top3_share"] == 90.0                    # (600+200+100)/1000
    assert health["sell_through"]["rate"] == 35.7
    assert len(health["new_products"]) == 2
    assert health["new_products"][1]["units_sold"] == 0  # 新品B 测款零销量


def test_collect_weekly_aov_in_kpi_tip(monkeypatch):
    """客单价卡含口径 tip；广告 tip 注明整周累计口径（实时无 intraday 边界）。"""
    _patch_weekly_data_sources(monkeypatch)
    from web.routes.report import _collect_weekly

    data = _run(_collect_weekly("ou_x", "last_week"))
    assert "客单价" in data["kpi"]["aov"]["tip"]
    assert "整周累计" in data["kpi"]["ad_spend"]["tip"]


# ── weekly 渲染 + insight ────────────────────────────────────────────


_FAKE_WEEKLY_DATA = {
    "kind": "weekly",
    "title": "经营周报",
    "change_label": "较上周",
    "low_volume": False,
    "empty_window": False,
    "baseline_label": "上周",
    "trend_title": "GMV / 广告 / 订单趋势（本周日维度）",
    "trend_mini": False,
    "intraday": False,
    "cutoff_label": None,
    "scope": "全店",
    "period_label": "印尼时间 6/8（周一） ~ 6/14（周日），共 7 天",
    "generated_at": "2026-06-15 09:05",
    "kpi": {
        "gmv": {"value": 100000, "change": 12.0, "baseline": 89000, "currency": "IDR", "tip": "GMV..."},
        "orders": {"value": 1500, "change": 8.0, "baseline": 1380},
        "aov": {"value": 66, "change": 3.0, "baseline": 64, "currency": "IDR", "tip": "客单价=GMV/订单数"},
        "ad_spend": {"value": 15000, "change": 5.0, "currency": "IDR", "tip": "广告..."},
        "roas": {"value": 6.67, "change": 2.0},
        "sku_count": 120,
        "low_stock_count": 3,
    },
    "health": {
        "concentration": {"top1_name": "爆款A", "top1_share": 55.0, "top3_share": 92.0},
        "sell_through": {"active_sku": 5, "total_sku": 14, "rate": 35.7},
        "new_products": [{"title": "新品A", "units_sold": 10, "gmv": 300},
                         {"title": "新品B", "units_sold": 0, "gmv": 0}],
    },
    "trend": {"dates": ["06-08", "06-09"], "gmv": [12000, 14000],
              "orders": [200, 230], "ad_spend": [2000, 2200]},
    "top_skus": [{"name": "爆款A", "units": 800, "gmv": 55000, "share": 55.0}],
    "low_stock": [],
}


async def _fake_collect_weekly(open_id, period):
    return _FAKE_WEEKLY_DATA


def test_weekly_report_renders_200(monkeypatch):
    """/report/weekly_review 200 + kind=weekly + 周报特有内容块。"""
    monkeypatch.setattr("web.routes.report._collect_weekly", _fake_collect_weekly)
    token = make_token("ou_test_user", ttl=1800)
    client = TestClient(app, cookies=_login_cookie("ou_test_user"))
    r = client.get(f"/report/weekly_review?t={token}&period=last_week")
    assert r.status_code == 200
    assert "__DATA__" not in r.text
    assert "经营周报" in r.text
    assert "商品结构健康度" in r.text     # 健康度卡
    assert "本周新品表现" in r.text       # 新品卡
    assert "客单价" in r.text             # 周报 KPI
    assert "动销率" in r.text
    assert "echarts" in r.text


def test_weekly_report_invalid_token_401():
    """weekly_review token 无效 → 401。"""
    client = TestClient(app)
    r = client.get("/report/weekly_review?t=bad&period=last_week")
    assert r.status_code == 401


def test_weekly_insight_success(monkeypatch):
    """周报 insight：返回 review/learnings/next_actions 三段（不同于日报 problems/actions）。"""
    monkeypatch.setattr("web.routes.report._collect_weekly", _fake_collect_weekly)
    fake = _FakeProvider(
        '{"headline":"本周稳健增长","review":["单款依赖偏高"],'
        '"learnings":["上衣类有效"],"next_actions":["拓展新品","补货爆款A"]}'
    )
    monkeypatch.setattr("services.llm.get_provider", lambda *a, **k: fake)
    token = make_token("ou_test_user", ttl=1800)
    client = TestClient(app, cookies=_login_cookie("ou_test_user"))
    r = client.get(f"/report/weekly_review/insight?t={token}&period=last_week")
    assert r.status_code == 200
    d = r.json()
    assert d["available"] is True
    assert d["headline"] == "本周稳健增长"
    assert d["review"] == ["单款依赖偏高"]
    assert d["learnings"] == ["上衣类有效"]
    assert d["next_actions"] == ["拓展新品", "补货爆款A"]


def test_parse_weekly_insight_tolerant():
    """_parse_weekly_insight：剥围栏 + 缺字段降级（缺 headline 返 None）。"""
    from web.routes.report import _parse_weekly_insight

    ok = _parse_weekly_insight(
        '```json\n{"headline":"x","review":["a"],"learnings":[],"next_actions":["b","c"]}\n```'
    )
    assert ok["headline"] == "x"
    assert ok["review"] == ["a"]
    assert ok["next_actions"] == ["b", "c"]
    # 缺 headline → None（降级，前端隐藏 AI 块）
    assert _parse_weekly_insight('{"review":["a"]}') is None
    assert _parse_weekly_insight("") is None


def test_insight_cache_key_includes_template(monkeypatch):
    """缓存键含 template_name：同 open_id+period 下日报与周报互不串味。"""
    monkeypatch.setattr("web.routes.report._collect", _fake_collect)
    monkeypatch.setattr("web.routes.report._collect_weekly", _fake_collect_weekly)
    daily = _FakeProvider('{"headline":"日报结论","problems":[],"actions":["x"]}')
    weekly = _FakeProvider(
        '{"headline":"周报结论","review":["r"],"learnings":[],"next_actions":["n"]}')

    token = make_token("ou_test_user", ttl=1800)
    client = TestClient(app, cookies=_login_cookie("ou_test_user"))

    # 先打日报 insight（落缓存）
    monkeypatch.setattr("services.llm.get_provider", lambda *a, **k: daily)
    rd = client.get(f"/report/daily_brief/insight?t={token}&period=last_week").json()
    assert rd["headline"] == "日报结论"
    # 再打周报 insight（同 open_id+period）：必须拿到周报结论，而非日报缓存
    monkeypatch.setattr("services.llm.get_provider", lambda *a, **k: weekly)
    rw = client.get(f"/report/weekly_review/insight?t={token}&period=last_week").json()
    assert rw["headline"] == "周报结论"
    assert "review" in rw and rw["review"] == ["r"]


def test_ops_report_tool_weekly(monkeypatch):
    """agent_tool ops_report 传 weekly_review → markdown 文案为「查看经营周报」。"""
    from services.user_authz import UserPermission
    from services.scope_resolution import ScopeFilters
    from web.agent_tools import run_tool

    perm = UserPermission(
        open_id="ou_test_user", role="boss", allowed_scope_key=None,
        channel="feishu", account_id="ecom-app",
    )
    monkeypatch.setattr(
        "web.agent_tools.resolve_authorized_scope",
        lambda p: ScopeFilters(platform=None, country=None, shop_ids=None,
                               scope_key=None, display_text="全店"),
    )
    result = run_tool("ops_report",
                      {"template_name": "weekly_review", "period": "last_week"}, perm)
    assert isinstance(result, dict)
    assert "查看经营周报" in result["markdown"]
    # 周报摘要含商品健康度（集中度/动销率/新品）
    assert result["summary"]["kind"] == "weekly"
    assert "health" in result["summary"]
    assert "concentration" in result["summary"]["health"]
