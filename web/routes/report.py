"""经营报告可视化 HTML（plan/16 artifact）。

与 dashboard.py 同架构：后端预定义 HTML 模板 + 真实数据注入，签名链接鉴权。
飞书/WebUI 对话中 agent 调 ops_report 工具签出链接，用户点开看到 echarts 图表。

鉴权 = 双重校验（绑定打开者身份，防转发）：
  1. 签名 token（`?t=<token>`）验签拿"签发对象"open_id + 时效 + 参数；
  2. 飞书登录 session cookie（board_session，与 /board、/app 同一套）拿"打开者"open_id；
  两者必须一致才放行——本人看自己数据，同企业同事/外部一律拒绝（见 plan：转发防护）。
强制软隔离：scope_id/shop_ids/shop_id 一律钉 None，只按 token 里的 open_id 解析范围。

取数直接 await 现有路由处理函数，所有参数显式传齐。
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from statistics import median
from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from core.config import settings
from core.timezone import (
    business_now,
    business_today,
    describe_window,
    previous_window,
    resolve_period,
)
from services.order_metrics import get_gmv_summary_intraday
from web.routes.data import (
    _resolve_scope,
    get_ad_spend,
    get_ad_spend_trend,
    get_low_stock,
    get_orders_summary,
    get_orders_top_skus,
    get_orders_trend,
    get_overview,
)
from web.signed_link import verify_token
from web.web_session import verify_session_cookie

_LOGIN_PATH = "/board/auth/feishu/login"

logger = logging.getLogger("report")

router = APIRouter()

# 印尼时区 UTC+7
_JAKARTA_TZ = timezone(timedelta(hours=7))

# AI 洞察当天缓存：key=(open_id, period/dates, business_date) → 三段 dict（刷新不重复烧 LLM）
_INSIGHT_CACHE: dict[tuple, dict] = {}


def _asdict(obj):
    """兼容 Pydantic 模型 / 普通 dict 两种返回。"""
    return obj.model_dump() if hasattr(obj, "model_dump") else obj


def _resolve_dates(start_date, end_date, period):
    """解析日期窗口，返回 (start_date, end_date, period_label)。"""
    if start_date or end_date:
        from datetime import date as date_type

        today = business_today()
        sd = date_type.fromisoformat(start_date) if start_date else today - timedelta(days=7)
        ed = date_type.fromisoformat(end_date) if end_date else today
        return sd, ed, describe_window(sd, ed)
    if period:
        try:
            sd, ed = resolve_period(period)
        except ValueError:
            sd, ed = business_today() - timedelta(days=7), business_today()
        return sd, ed, describe_window(sd, ed)
    today = business_today()
    sd, ed = today - timedelta(days=7), today
    return sd, ed, describe_window(sd, ed)


def _calc_change(current, previous):
    """计算环比百分比变化。previous=0 或 None 时返回 None。"""
    if previous is None or previous == 0:
        return None
    return round((current - previous) / previous * 100, 1)


async def _collect(open_id: str, start_date, end_date, period) -> dict:
    """按 open_id 的 binding scope 取报告数据，组装成前端用的 dict。

    强制软隔离：scope_id/shop_ids/shop_id 一律钉 None，只按 token 里的 open_id 解析范围。
    """
    sd, ed, period_label = _resolve_dates(start_date, end_date, period)
    is_single_day = sd == ed
    # 当日（数据不全）：环比走"截至此刻 vs 昨日同一时刻"；过去的某天一律整天对整天
    is_today = is_single_day and ed == business_today()

    # 版型：单日→日报（当日数 + 近 7 天迷你趋势参照）；多日→区间报（区间汇总 + 完整趋势）
    if is_single_day:
        kind, title = "daily", "经营日报"
        change_label = "较近 7 天同期均值" if is_today else "较前一日"
        trend_title, trend_mini = "近 7 天趋势（参考）", True
        trend_sd, trend_ed = ed - timedelta(days=6), ed  # 迷你背景图画近 7 天
    else:
        kind, title, change_label = "period", "经营报告", "较上期"
        trend_title, trend_mini = "GMV / 广告 / 订单趋势", False
        trend_sd, trend_ed = sd, ed

    # 环比基准：紧邻当期、等长的上一窗口（单日即昨日）
    prev_sd, prev_ed = previous_window(sd, ed)

    # 库存快照（当前快照，与时间窗无关）
    overview = _asdict(await get_overview(
        platform=None, country=None, shop_id=None,
        scope_id=None, shop_ids=None, open_id=open_id,
    ))
    # 订单 KPI：
    #   当日 → 截至此刻 vs 昨日同一时刻（intraday 同期对比，避免半天 vs 昨日全天的假暴跌）
    #   其余 → 按 [sd, ed] 整天对整天（修正：原先取 overview 固定近 7 天，不随 period 变）
    cutoff_label = None
    if is_today:
        scope = _resolve_scope(open_id=open_id)
        now = business_now()
        cutoff = now.time()
        _sc = dict(platform=scope.platform, country=scope.country, shop_ids=scope.shop_ids)
        orders_cur = get_gmv_summary_intraday(day=ed, cutoff=cutoff, **_sc)
        # 基准 = 近 7 天每天截至同一时刻的均值（摊平昨日爆单等单日异常，比单看昨天稳）
        base_days = [get_gmv_summary_intraday(day=ed - timedelta(days=i), cutoff=cutoff, **_sc)
                     for i in range(1, 8)]
        n = len(base_days) or 1
        orders_prev = {
            "gmv": sum(b["gmv"] for b in base_days) / n,
            "order_count": sum(b["order_count"] for b in base_days) / n,
        }
        cutoff_label = ("数据截至 " + now.strftime("%H:%M")
                        + "（印尼时间）· 当日累计 vs 近 7 天同期均值")
    else:
        orders_cur = _asdict(await get_orders_summary(
            start_date=sd.isoformat(), end_date=ed.isoformat(), period=None,
            platform=None, country=None, shop_id=None,
            scope_id=None, shop_ids=None, open_id=open_id,
        ))
        orders_prev = _asdict(await get_orders_summary(
            start_date=prev_sd.isoformat(), end_date=prev_ed.isoformat(), period=None,
            platform=None, country=None, shop_id=None,
            scope_id=None, shop_ids=None, open_id=open_id,
        ))
    # 趋势（订单 + 广告，按趋势窗口：单日近 7 天 / 多日选定区间）
    trend = _asdict(await get_orders_trend(
        start_date=trend_sd.isoformat(), end_date=trend_ed.isoformat(), period=None,
        platform=None, country=None, shop_id=None,
        scope_id=None, shop_ids=None, open_id=open_id,
    ))
    ad_trend = _asdict(await get_ad_spend_trend(
        start_date=trend_sd.isoformat(), end_date=trend_ed.isoformat(), period=None,
        platform=None, country=None, shop_id=None,
        scope_id=None, shop_ids=None, open_id=open_id,
    ))
    # Top5 爆款按 KPI 窗口 [sd, ed]
    top = _asdict(await get_orders_top_skus(
        start_date=sd.isoformat(), end_date=ed.isoformat(), period=None,
        platform=None, country=None, shop_id=None,
        scope_id=None, shop_ids=None, limit=5, open_id=open_id,
    ))
    low = _asdict(await get_low_stock(
        platform=None, country=None, shop_id=None,
        scope_id=None, shop_ids=None,
        critical_days=None, warning_days=None, open_id=open_id,
    ))
    # 广告 KPI（当期 + 上期，按 KPI 窗口）
    ad = _asdict(await get_ad_spend(
        start_date=sd.isoformat(), end_date=ed.isoformat(), period=None,
        platform=None, country=None, shop_id=None,
        scope_id=None, shop_ids=None, open_id=open_id,
    ))
    ad_prev = _asdict(await get_ad_spend(
        start_date=prev_sd.isoformat(), end_date=prev_ed.isoformat(), period=None,
        platform=None, country=None, shop_id=None,
        scope_id=None, shop_ids=None, open_id=open_id,
    ))

    # 上一期汇总
    prev_gmv = orders_prev.get("gmv", 0) or 0
    prev_orders = orders_prev.get("order_count", 0) or 0
    prev_ad_spend = ad_prev.get("total_ad_spend", 0) or 0

    # 当期汇总
    cur_gmv = orders_cur.get("gmv", 0) or 0
    cur_orders = orders_cur.get("order_count", 0) or 0
    cur_ad_spend = ad.get("total_ad_spend", 0) or 0
    cur_roas = ad.get("roas")

    # 上一期 ROAS
    prev_roas = None
    if prev_ad_spend and prev_ad_spend > 0:
        prev_roas = round(prev_gmv / prev_ad_spend, 2)

    # 低单量护栏：当期或基准单量为个位数时，环比百分比是除以接近 0 的小基准 / 小样本噪声，
    # 极不可靠（如 1 单 vs 均值 0.3 单 = ↑250%）。此时不在 GMV/订单卡片显示百分比，
    # 改由前端渲染「vs <基准> 绝对值」对比（更有信息量、不误导）；change=None 同时让喂给
    # AI 的环比% 自动变空，避免 AI 把噪声当成「增长 X%」复述。详见 _build_insight_prompt。
    low_volume = (cur_orders < 10) or (prev_orders < 10)
    # 基准口径短语（去掉「较」前缀）：「较近 7 天同期均值」→「近 7 天同期均值」
    baseline_label = change_label.lstrip("较").strip()

    # 趋势数据
    trend_points = trend.get("points", [])
    dates = [p.get("date", "")[5:] for p in trend_points]  # MM-DD
    gmv_series = [p.get("gmv", 0) for p in trend_points]
    orders_series = [p.get("order_count", 0) for p in trend_points]

    # 广告消耗按业务日对齐到订单趋势的日期轴（缺失日补 0）
    ad_by_date = {
        p.get("date"): p.get("total_ad_spend", 0)
        for p in ad_trend.get("points", [])
    }
    ad_series = [ad_by_date.get(p.get("date"), 0) for p in trend_points]

    # Top 5 SKU（加 GMV 占比 = 单品 GMV / 当期总 GMV，guard 除零）
    top_items = []
    for item in top.get("items", [])[:5]:
        item_gmv = item.get("gmv", 0) or 0
        share = round(item_gmv / cur_gmv * 100, 1) if cur_gmv else None
        top_items.append({
            "name": item.get("product_name") or item.get("sku_name") or item.get("sku_id") or "?",
            "units": item.get("units_sold", 0),
            "gmv": item_gmv,
            "share": share,
        })

    # 断货预警：按严重度（断货 > 告急 > 预警）排序，同级最紧急（可售天数最少）在前
    low_items = []
    level_map = {"stockout": "断货", "critical": "告急", "warning": "预警"}
    _severity = {"stockout": 0, "critical": 1, "warning": 2}
    low_sorted = sorted(
        low.get("items", []),
        key=lambda it: (_severity.get(it.get("bucket", ""), 9), it.get("days_of_cover", 0)),
    )
    # 断货风险计数统一用销速模型（与下方表格同源），而非 overview 的静态「库存<10」计数
    # ——后者含卖不动的滞销死货，会和「按可售天数」的表格自相矛盾
    risk_count = low.get("buckets", {}).get("total")
    if risk_count is None:
        risk_count = len(low.get("items", []))
    for item in low_sorted[:20]:
        bucket = item.get("bucket", "")
        low_items.append({
            "name": item.get("product_name") or item.get("sku_id") or "?",
            "stock": item.get("available_stock", 0),
            "velocity": round(item.get("daily_velocity", 0), 1),
            "days": round(item.get("days_of_cover", 0), 1),
            "level": bucket,
            "level_label": level_map.get(bucket, bucket),
        })

    generated_at = datetime.now(_JAKARTA_TZ).strftime("%Y-%m-%d %H:%M")

    # KPI 问号 tip：精简取数口径 + 环比基准
    gmv_tip = (
        "GMV=已付款订单买家实付额（含运费/税/优惠，非平台结算）。"
        f"环比={change_label}（紧邻等长窗口）。"
    )
    ad_tip = (
        "广告消耗=结算口径，含 GMV Max / TAP / 联盟三项拆分。"
        f"环比={change_label}（紧邻等长窗口）。广告数据接通中，可能偏低或为 0。"
    )

    return {
        "kind": kind,
        "title": title,
        "change_label": change_label,
        "low_volume": low_volume,
        "baseline_label": baseline_label,
        "trend_title": trend_title,
        "trend_mini": trend_mini,
        "intraday": is_today,
        "cutoff_label": cutoff_label,
        "scope": overview.get("scope") or "全店",
        "period_label": period_label,
        "generated_at": generated_at,
        "kpi": {
            "gmv": {
                "value": cur_gmv,
                "change": None if low_volume else _calc_change(cur_gmv, prev_gmv),
                "baseline": round(prev_gmv),
                "currency": "IDR",
                "tip": gmv_tip,
            },
            "orders": {
                "value": cur_orders,
                "change": None if low_volume else _calc_change(cur_orders, prev_orders),
                "baseline": round(prev_orders, 1),
            },
            "ad_spend": {
                "value": cur_ad_spend,
                "change": _calc_change(cur_ad_spend, prev_ad_spend),
                "currency": "IDR",
                "tip": ad_tip,
            },
            "roas": {
                "value": cur_roas,
                "change": _calc_change(cur_roas, prev_roas) if cur_roas and prev_roas else None,
            },
            "sku_count": overview.get("inventory", {}).get("total_sku", 0),
            "low_stock_count": risk_count,
        },
        "trend": {
            "dates": dates,
            "gmv": gmv_series,
            "orders": orders_series,
            "ad_spend": ad_series,
        },
        "top_skus": top_items,
        "low_stock": low_items,
    }


_VALID_TEMPLATES = {"daily_brief"}


@router.get("/report/{template_name}", response_class=HTMLResponse, include_in_schema=False)
async def report(
    request: Request,
    template_name: str,
    t: str = Query("", description="签名 token（含 open_id + 过期）"),
    start_date: str = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: str = Query(None, description="结束日期 YYYY-MM-DD"),
    period: str = Query("last_7d", description="时间窗口: last_7d / last_30d / today"),
):
    # 1) 签名 token → 报告的"签发对象"open_id（时效 + 参数 + 防篡改）
    open_id = verify_token(t)
    if not open_id:
        return HTMLResponse(_render_error(), status_code=401)
    if template_name not in _VALID_TEMPLATES:
        return HTMLResponse(_render_error(), status_code=404)

    # 2) 飞书登录态 → "打开者"open_id（与 /board、/app 同一 board_session cookie）
    raw = request.cookies.get(settings.feishu_oauth.cookie_name, "")
    viewer = verify_session_cookie(raw) if raw else None
    if not viewer:
        # 未登录：跳飞书登录，登录后回跳本报告 URL（飞书内免登静默，飞书外自然被挡）
        nxt = request.url.path + (("?" + request.url.query) if request.url.query else "")
        return RedirectResponse(
            f"{_LOGIN_PATH}?{urlencode({'next': nxt})}", status_code=302
        )
    if viewer != open_id:
        # 已登录但非本人（同企业同事/他人转发）：拒绝
        return HTMLResponse(_render_forbidden(), status_code=403)

    # 3) 本人：照常取数渲染（软隔离按 open_id 的 binding）
    data = await _collect(open_id, start_date, end_date, period)
    return HTMLResponse(_render(data))


# ── AI 洞察（结论 / 今日问题 / 明日动作）─────────────────────────────
# 渲染时服务端调 LLM、前端渐进加载；当天缓存、优雅降级（绝不阻塞主报告）。

_INSIGHT_SYSTEM = (
    "你是跨境电商运营分析助手，为老板写极简经营洞察。严格只依据给定数字，"
    "禁止编造任何未提供的数据或原因。输出**严格 JSON**（不要 markdown 围栏、不要多余文字）："
    '{"headline": "一句话结论", "problems": ["..."], "actions": ["..."]}。'
    "headline≤40字、点明经营好坏与最关键信号；problems 今日问题 0-3 条、每条≤30字、"
    "只挑数字暴露的真问题（如环比骤降、断货）；actions 明日动作 1-3 条、具体可执行。"
    "全部用中文。无明显问题时 problems 可为空数组。"
    "重要约束："
    "(1) 广告消耗为 0 通常是广告数据尚未接通（不是没投广告），**不要**当作问题、"
    "**不要**建议投放/启动广告。"
    "(2) 若数据标注为『当日累计』：数字是当日进行中的累计、环比已是同期口径，"
    "**不要**因当日绝对值偏小而判断经营崩盘。"
    "(3) **低单量护栏**：当订单数是个位数（如 1~2 单）时，环比百分比是小样本噪声、极不可靠，"
    "**禁止**用『骤降/暴跌/严重』等警报措辞、**不要**把它当成今日问题；如需提及就如实陈述"
    "绝对值（如『今日 1 单 vs 近 7 天同期均值约 2 单，样本小、属正常波动』），把注意力放在"
    "断货、爆款等更确定的信号上。"
)


def _detect_anomalies(dates: list, gmv: list) -> list:
    """确定性异常日：> 中位数×1.5 的最大日记『爆单』，>0 且 < 中位数×0.5 的最小日记『骤降』。"""
    out = []
    pts = [(d, v) for d, v in zip(dates, gmv) if v is not None]
    if len(pts) < 3:
        return out
    vals = [v for _, v in pts]
    m = median(vals)
    if m <= 0:
        return out
    hi = max(pts, key=lambda x: x[1])
    lo = min(pts, key=lambda x: x[1])
    if hi[1] > m * 1.5:
        out.append({"date": hi[0], "kind": "spike", "label": "爆单"})
    if 0 < lo[1] < m * 0.5 and lo[0] != hi[0]:
        out.append({"date": lo[0], "kind": "drop", "label": "骤降"})
    return out


def _llm_complete(provider, messages) -> str:
    """消费 stream() 取最后 TurnComplete 的完整文本。"""
    from services.llm.types import TurnComplete

    text = ""
    for ev in provider.stream(messages, tools=[]):
        if isinstance(ev, TurnComplete):
            text = ev.text or ""
    return text


def _parse_insight(text: str) -> dict | None:
    """容错解析 LLM 返回的 JSON（剥 ```json 围栏）。失败返回 None。"""
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1] if "```" in s[3:] else s.strip("`")
        s = s[4:].strip() if s.lower().startswith("json") else s.strip()
    try:
        obj = json.loads(s)
    except (ValueError, TypeError):
        # 退一步：截取首个 { 到末个 }
        i, j = s.find("{"), s.rfind("}")
        if i == -1 or j == -1 or j <= i:
            return None
        try:
            obj = json.loads(s[i : j + 1])
        except (ValueError, TypeError):
            return None
    if not isinstance(obj, dict) or "headline" not in obj:
        return None
    return {
        "headline": str(obj.get("headline", "")).strip(),
        "problems": [str(x).strip() for x in (obj.get("problems") or []) if str(x).strip()][:3],
        "actions": [str(x).strip() for x in (obj.get("actions") or []) if str(x).strip()][:3],
    }


def _build_insight_prompt(data: dict) -> str:
    """把报告指标压成喂给 LLM 的精简数字上下文。"""
    kpi = data.get("kpi", {})
    anomalies = _detect_anomalies(
        data.get("trend", {}).get("dates", []), data.get("trend", {}).get("gmv", [])
    )
    payload = {
        "报告类型": "日报" if data.get("kind") == "daily" else "区间报告",
        "数据口径": (data.get("cutoff_label") or "完整窗口") if data.get("intraday")
                    else "完整窗口",
        "范围": data.get("scope"),
        "周期": data.get("period_label"),
        "环比基准": data.get("change_label"),
        "GMV": kpi.get("gmv", {}).get("value"),
        "GMV基准值": kpi.get("gmv", {}).get("baseline"),
        "GMV环比%": kpi.get("gmv", {}).get("change"),
        "订单数": kpi.get("orders", {}).get("value"),
        "订单基准值": kpi.get("orders", {}).get("baseline"),
        "订单环比%": kpi.get("orders", {}).get("change"),
        "广告消耗": kpi.get("ad_spend", {}).get("value"),
        "广告环比%": kpi.get("ad_spend", {}).get("change"),
        "断货风险SKU数": kpi.get("low_stock_count"),
        "Top爆款": [
            {"名称": t.get("name"), "销量": t.get("units"), "GMV占比%": t.get("share")}
            for t in data.get("top_skus", [])[:5]
        ],
        "断货预警Top": [
            {"名称": l.get("name"), "风险": l.get("level_label"),
             "库存": l.get("stock"), "可售天数": l.get("days")}
            for l in data.get("low_stock", [])[:5]
        ],
        "趋势异常日": anomalies,
        "币种": "IDR（印尼盾）",
    }
    cur_orders = kpi.get("orders", {}).get("value") or 0
    base_orders = kpi.get("orders", {}).get("baseline") or 0
    if cur_orders < 10 or base_orders < 10:
        payload["低单量提示"] = (
            "今日及基准单量很小（个位数），环比百分比为小样本噪声，勿用骤降/暴跌措辞、"
            "勿当严重问题，按低单量护栏处理。"
        )
    return json.dumps(payload, ensure_ascii=False)


@router.get("/report/{template_name}/insight", include_in_schema=False)
async def report_insight(
    request: Request,
    template_name: str,
    t: str = Query("", description="签名 token"),
    start_date: str = Query(None),
    end_date: str = Query(None),
    period: str = Query("last_7d"),
):
    """AI 三段洞察（结论/问题/动作）。鉴权同 report()，失败一律降级 {available:false}，绝不 500。"""
    open_id = verify_token(t)
    if not open_id or template_name not in _VALID_TEMPLATES:
        return JSONResponse({"available": False, "reason": "invalid"})
    raw = request.cookies.get(settings.feishu_oauth.cookie_name, "")
    viewer = verify_session_cookie(raw) if raw else None
    if not viewer or viewer != open_id:
        return JSONResponse({"available": False, "reason": "forbidden"})

    cache_key = (open_id, start_date or "", end_date or "", period or "", str(business_today()))
    if cache_key in _INSIGHT_CACHE:
        return JSONResponse(_INSIGHT_CACHE[cache_key])

    try:
        from services.llm import get_provider
        from services.llm.types import ChatMessage

        data = await _collect(open_id, start_date, end_date, period)
        provider = get_provider()
        messages = [
            ChatMessage(role="system", content=_INSIGHT_SYSTEM),
            ChatMessage(role="user", content=_build_insight_prompt(data)),
        ]
        text = _llm_complete(provider, messages)
        parsed = _parse_insight(text)
        if not parsed:
            return JSONResponse({"available": False, "reason": "parse"})
        result = {
            "available": True,
            "generated_by": "ai",
            "model": getattr(provider, "model", ""),
            **parsed,
        }
        _INSIGHT_CACHE[cache_key] = result
        return JSONResponse(result)
    except Exception as exc:  # LLMError / 网络 / 超时 — 一律降级
        logger.warning("report insight unavailable: %s", exc)
        return JSONResponse({"available": False, "reason": "llm_error"})


def _render(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return DAILY_BRIEF_HTML.replace("__DATA__", payload)


def _render_error() -> str:
    return _ERROR_PAGE


def _render_forbidden() -> str:
    return _FORBIDDEN_PAGE


_FORBIDDEN_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>无权查看</title>
<style>
  body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
         background:hsl(58 20% 96%); color:rgba(25,36,32,.88);
         font:14px/1.6 ui-sans-serif,system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif; }
  .box { text-align:center; padding:32px 24px; max-width:420px; }
  .icon { font-size:40px; margin-bottom:12px; }
  h1 { font-size:18px; margin:0 0 8px; font-weight:600; }
  p { color:rgba(25,36,32,.5); margin:0; font-size:13px; }
</style>
</head>
<body>
  <div class="box">
    <div class="icon">🚫</div>
    <h1>此报告仅限本人查看</h1>
    <p>这条报告链接是发给特定账号的，请在你自己的飞书里向机器人获取属于你的报告。</p>
  </div>
</body>
</html>"""


_ERROR_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>链接已失效</title>
<style>
  body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
         background:hsl(58 20% 96%); color:rgba(25,36,32,.88);
         font:14px/1.6 ui-sans-serif,system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif; }
  .box { text-align:center; padding:32px 24px; }
  .icon { font-size:40px; margin-bottom:12px; }
  h1 { font-size:18px; margin:0 0 8px; font-weight:600; }
  p { color:rgba(25,36,32,.5); margin:0; font-size:13px; }
</style>
</head>
<body>
  <div class="box">
    <div class="icon">🔒</div>
    <h1>链接已失效</h1>
    <p>请回到对话里重新获取报告链接。</p>
  </div>
</body>
</html>"""


DAILY_BRIEF_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>经营报告</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
<style>
  /* 设计 token 对齐 SPA 控制台（frontend/src/index.css，StoreClaw 体系） */
  :root { --primary:hsl(158 18% 12%); --success:hsl(152 52% 36%);
          --warn:hsl(32 84% 44%); --danger:hsl(0 72% 50%);
          --bg:hsl(58 20% 96%); --card:hsl(56 38% 99%);
          --txt:rgba(25,36,32,.88); --txt2:rgba(25,36,32,.7); --sub:rgba(25,36,32,.5);
          --border:rgba(0,0,0,.1); --border-shallow:rgba(0,0,0,.05);
          --fill:rgba(0,0,0,.04); --fill-shallow:rgba(0,0,0,.02); }
  @font-face { font-family:"GoogleSansFlex"; font-display:swap;
               src:url("/app/fonts/GoogleSansFlex.woff2") format("woff2"); }
  * { box-sizing:border-box; margin:0; padding:0; }
  html { background:rgba(107,104,31,.05); }
  body { background:var(--bg); color:var(--txt);
         font:14px/1.5 "GoogleSansFlex",ui-sans-serif,system-ui,-apple-system,
              "PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
         -webkit-font-smoothing:antialiased; }
  .wrap { max-width:800px; margin:0 auto; padding:16px 12px 40px; }
  header { margin-bottom:16px; }
  header h1 { font-size:20px; font-weight:700; color:var(--primary); letter-spacing:-.01em; }
  .meta { color:var(--sub); font-size:12px; margin-top:4px; }
  .kpis { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; }
  .kpi { background:var(--card); border:1px solid var(--border-shallow); border-radius:12px;
         padding:14px; box-shadow:0 1px 2px rgba(0,0,0,.04); }
  .kpi .label { color:var(--sub); font-size:11px; }
  .kpi .val { font-size:19px; font-weight:700; margin-top:3px;
              font-variant-numeric:tabular-nums; letter-spacing:-.01em; }
  .kpi .chg { display:inline-flex; align-items:center; gap:2px; margin-top:6px;
              padding:1px 7px; border-radius:8px; font-size:11px; font-weight:600;
              font-variant-numeric:tabular-nums; }
  .chg.up { color:var(--success); background:rgba(34,139,90,.12); }
  .chg.down { color:var(--danger); background:rgba(220,38,38,.1); }
  .chg.base { color:var(--sub); background:transparent; padding:1px 0; font-weight:500; }
  .card { background:var(--card); border:1px solid var(--border-shallow); border-radius:12px;
          padding:14px; margin-top:12px; box-shadow:0 1px 2px rgba(0,0,0,.04); }
  .card h2 { font-size:14px; font-weight:600; color:var(--txt); margin-bottom:10px;
             display:flex; align-items:center; justify-content:space-between; gap:8px; }
  .card h2 small { font-weight:400; font-size:12px; color:var(--sub); }
  #trend-chart { width:100%; height:280px; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th,td { text-align:left; padding:8px 6px; border-bottom:1px solid var(--border-shallow); }
  tbody tr:last-child td { border-bottom:none; }
  tbody tr:hover td { background:var(--fill-shallow); }
  th { color:var(--sub); font-weight:500; font-size:12px; }
  td.num { text-align:right; font-variant-numeric:tabular-nums; }
  .pill { display:inline-block; padding:1px 8px; border-radius:8px; font-size:11px; font-weight:600; }
  .pill.stockout { background:rgba(220,38,38,.1); color:var(--danger); }
  .pill.critical { background:rgba(214,140,20,.14); color:var(--warn); }
  .pill.warning  { background:rgba(25,36,32,.08); color:var(--txt2); }
  .kpi-note { color:var(--sub); font-size:11px; margin-top:6px; padding:0 2px; }
  /* 问号 tip */
  .qmark { display:inline-flex; align-items:center; justify-content:center; cursor:pointer;
           width:14px; height:14px; margin-left:4px; border-radius:50%; font-size:10px;
           color:var(--sub); background:var(--fill); vertical-align:middle; }
  .kpi { position:relative; }
  .kpi .tip { display:none; position:absolute; z-index:5; left:10px; right:10px; top:100%;
              margin-top:4px; padding:8px 10px; border-radius:8px; font-size:11px; line-height:1.5;
              font-weight:400; color:var(--card); background:rgba(25,36,32,.92);
              box-shadow:0 4px 12px rgba(0,0,0,.18); }
  .kpi .tip.show { display:block; }
  /* AI 一句话结论 */
  .ai-headline { margin:0 0 12px; padding:12px 14px; border-radius:12px;
                 background:linear-gradient(135deg, rgba(44,140,95,.10), rgba(25,36,32,.04));
                 border:1px solid var(--border-shallow); color:var(--txt);
                 font-size:15px; font-weight:600; line-height:1.5; }
  .ai-headline .tag { display:inline-block; margin-right:6px; padding:1px 6px; border-radius:6px;
                      font-size:10px; font-weight:600; color:var(--success);
                      background:rgba(44,140,95,.14); vertical-align:middle; }
  .ai-headline.loading, .ai-card .ai-loading { color:var(--sub); font-weight:400; }
  /* AI 问题 / 动作 */
  .ai-block { margin-bottom:10px; }
  .ai-block:last-child { margin-bottom:0; }
  .ai-block .h { font-size:12px; font-weight:600; color:var(--sub); margin-bottom:4px; }
  .ai-block ul { margin:0; padding-left:18px; }
  .ai-block li { font-size:13px; line-height:1.7; }
  .ai-block.actions li { color:var(--success); }
  /* 表格：商品名截断 */
  td.name { max-width:170px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .foot { color:var(--sub); font-size:11px; margin-top:20px; text-align:center; }
  @media (max-width:480px){
    .kpis { grid-template-columns:repeat(2,1fr); }
    .kpi .val { font-size:18px; }
  }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1 id="title">经营报告</h1>
    <div class="meta" id="meta"></div>
  </header>

  <div class="ai-headline loading" id="ai-headline" style="display:none">
    <span class="tag">🤖 AI 总结</span><span id="ai-headline-text">正在生成洞察…</span>
  </div>

  <div class="kpis" id="kpis"></div>
  <div class="kpi-note" id="kpi-note"></div>

  <div class="card">
    <h2><span id="trend-title">GMV / 广告 / 订单趋势</span> <small id="trend-title-note"></small></h2>
    <div id="trend-chart"></div>
  </div>

  <div class="card">
    <h2>Top 5 爆款 <small>占比＝占当期 GMV（商品行口径近似）</small></h2>
    <table><thead><tr><th>商品</th><th class="num">销量</th><th class="num">GMV</th><th class="num">占比</th></tr></thead>
    <tbody id="top-body"></tbody></table>
  </div>

  <div class="card">
    <h2>断货预警 <small>按可售天数（库存÷日均销速）排序</small></h2>
    <div id="low-wrap"></div>
  </div>

  <div class="card" id="ai-card" style="display:none">
    <h2>🤖 今日问题 &amp; 明日动作 <small>AI 总结</small></h2>
    <div id="ai-body"><div class="ai-loading">正在生成…</div></div>
  </div>

  <div class="foot" id="foot"></div>
</div>

<script>
const DATA = __DATA__;

// -- header --
document.getElementById('title').textContent = DATA.title || '经营报告';
document.title = DATA.title || '经营报告';
document.getElementById('meta').textContent =
  DATA.scope + '  ·  ' + DATA.period_label + '  ·  生成于 ' + DATA.generated_at;

// -- formatters --
// 大额 IDR 缩写：Rp + K/M/B（后端返回原值，缩写只在展示层）
const abbr = n => {
  n = Number(n); const a = Math.abs(n);
  if (a >= 1e9) return (n/1e9).toFixed(2) + 'B';
  if (a >= 1e6) return (n/1e6).toFixed(1) + 'M';
  if (a >= 1e3) return (n/1e3).toFixed(0) + 'K';
  return String(Math.round(n));
};
const fmtMoney = n => (n == null ? '—' : 'Rp ' + abbr(n));         // GMV / 广告消耗 / GMV 列
const fmtInt = n => (n == null ? '—' : Number(n).toLocaleString('en-US')); // 订单 / SKU / 销量
const fmtDec = n => (n == null ? '—' : Number(n).toFixed(2));      // ROAS

// -- KPI cards --
function chgHtml(c) {
  if (c == null) return '';
  const cls = c >= 0 ? 'up' : 'down';
  const arrow = c >= 0 ? '↑' : '↓';
  return '<span class="chg ' + cls + '">' + arrow + ' ' + Math.abs(c) + '%</span>';
}

// 低单量护栏：环比百分比是噪声，改显示「vs <基准口径> 绝对值」对比（后端已把 change 置 null）
const _LV = !!DATA.low_volume;
const _BL = DATA.baseline_label || '上期';
const baseHtml = valHtml => '<span class="chg base">vs ' + _BL + ' ' + valHtml + '</span>';
const _gmv = {label:'GMV', val: fmtMoney(DATA.kpi.gmv.value),
  chg: _LV ? baseHtml(fmtMoney(DATA.kpi.gmv.baseline))
           : chgHtml(DATA.kpi.gmv.change),
  tip: DATA.kpi.gmv.tip};
const _ord = {label:'订单数', val: fmtInt(DATA.kpi.orders.value),
  chg: _LV ? baseHtml(fmtInt(DATA.kpi.orders.baseline) + ' 单')
           : chgHtml(DATA.kpi.orders.change)};
const _ad  = {label:'广告消耗', val: fmtMoney(DATA.kpi.ad_spend.value), chg: chgHtml(DATA.kpi.ad_spend.change), tip: DATA.kpi.ad_spend.tip};
const _lowc = {label:'断货风险', val: fmtInt(DATA.kpi.low_stock_count), chg: ''};
// 日报：纯核心 4 张（去 ROAS / 库存 SKU）；区间报：保留完整 6 张
const kpiDefs = (DATA.kind === 'daily')
  ? [_gmv, _ord, _ad, _lowc]
  : [_gmv, _ord, _ad,
     {label:'ROAS', val: fmtDec(DATA.kpi.roas.value), chg: chgHtml(DATA.kpi.roas.change)},
     {label:'库存 SKU', val: fmtInt(DATA.kpi.sku_count), chg: ''},
     _lowc];
const kpisEl = document.getElementById('kpis');
kpiDefs.forEach(d => {
  const div = document.createElement('div');
  div.className = 'kpi';
  const q = d.tip ? '<span class="qmark" title="点击查看口径">?</span>' : '';
  const tip = d.tip ? '<div class="tip">' + d.tip + '</div>' : '';
  div.innerHTML = '<div class="label">' + d.label + q + '</div>'
    + '<div class="val">' + d.val + '</div>' + d.chg + tip;
  kpisEl.appendChild(div);
});
// 问号点击切换口径浮层（点别处关闭）
kpisEl.querySelectorAll('.qmark').forEach(q => {
  q.addEventListener('click', e => {
    e.stopPropagation();
    const tip = q.closest('.kpi').querySelector('.tip');
    const open = tip.classList.contains('show');
    kpisEl.querySelectorAll('.tip.show').forEach(t => t.classList.remove('show'));
    if (!open) tip.classList.add('show');
  });
});
document.addEventListener('click', () =>
  kpisEl.querySelectorAll('.tip.show').forEach(t => t.classList.remove('show')));
document.getElementById('kpi-note').textContent =
  (DATA.cutoff_label ? DATA.cutoff_label
                     : '↑↓ 为环比变化（' + (DATA.change_label || '较上期') + '）')
  + (DATA.low_volume ? ' · 单量小，环比百分比噪声大已隐藏，改示绝对基准对比' : '');

// -- 趋势卡标题 + 迷你视图（单日报告画近 7 天作背景参照，弱化呈现）--
document.getElementById('trend-title').textContent = DATA.trend_title || 'GMV / 广告 / 订单趋势';
if (DATA.trend_mini) { document.getElementById('trend-chart').style.height = '200px'; }

// -- Trend chart (dual Y: GMV + 广告消耗 lines share money axis, Orders bar on right) --
// 配色对齐 SPA token：GMV=墨绿主色 / 广告=橙警示 / 订单=绿
const C_GMV = 'rgb(25,36,32)', C_AD = 'rgb(206,118,18)', C_ORD = 'rgb(44,140,95)';
const C_SUB = 'rgba(25,36,32,.5)', C_GRID = 'rgba(0,0,0,.06)';
const chart = echarts.init(document.getElementById('trend-chart'));
const _dates = DATA.trend.dates || [];
const _isSingleDay = _dates.length <= 1;

// 异常日标注（确定性，不臆造原因）：> 中位数×1.5 标「爆单」、>0 且 < 中位数×0.5 标「骤降」；末点高亮「当日」
function _median(arr){ const a=arr.filter(v=>v!=null).slice().sort((x,y)=>x-y);
  if(!a.length) return 0; const m=Math.floor(a.length/2); return a.length%2?a[m]:(a[m-1]+a[m])/2; }
const _g = DATA.trend.gmv || [];
const _mk = [];
if (_g.length >= 3) {
  const m = _median(_g);
  if (m > 0) {
    let hi=0, lo=0;
    _g.forEach((v,i)=>{ if(v>_g[hi]) hi=i; if(v<_g[lo]) lo=i; });
    if (_g[hi] > m*1.5)
      _mk.push({coord:[_dates[hi],_g[hi]], value:'爆单', symbol:'pin', symbolSize:42,
                itemStyle:{color:'rgba(206,118,18,.92)'}, label:{color:'#fff',fontSize:10,fontWeight:600}});
    if (_g[lo] > 0 && _g[lo] < m*0.5 && lo!==hi)
      _mk.push({coord:[_dates[lo],_g[lo]], value:'骤降', symbol:'pin', symbolSize:42,
                itemStyle:{color:'rgba(220,38,38,.92)'}, label:{color:'#fff',fontSize:10,fontWeight:600}});
  }
}
if (_g.length) {
  const li = _g.length-1;
  _mk.push({coord:[_dates[li],_g[li]], symbol:'circle', symbolSize:9, itemStyle:{color:C_GMV},
            label:{show:true, position:'top', color:C_SUB, fontSize:10, formatter:'当日'}});
}

// 含柱状图，x 轴始终留边距（boundaryGap:true），避免首尾柱子贴 Y 轴
chart.setOption({
  tooltip: { trigger:'axis',
    formatter: function(ps){
      let s = ps[0].axisValue + '<br/>';
      ps.forEach(p => {
        const v = p.seriesName === '订单数' ? fmtInt(p.value) : fmtMoney(p.value);
        s += p.marker + p.seriesName + '：<b>' + v + '</b><br/>';
      });
      return s;
    } },
  legend: { data:['GMV','广告消耗','订单数'], bottom:0, left:'center', type:'scroll',
            itemWidth:14, itemHeight:8, itemGap:16,
            textStyle:{color:C_SUB}, pageTextStyle:{color:C_SUB} },
  grid: { top:30, left:48, right:44, bottom:54 },
  xAxis: { type:'category', data: _dates, boundaryGap:true,
    axisLabel:{ color:C_SUB }, axisLine:{ lineStyle:{ color:C_GRID } },
    axisTick:{ show:false } },
  yAxis: [
    { type:'value', name:'GMV/广告', position:'left', nameTextStyle:{ color:C_SUB, fontSize:11 },
      axisLabel:{ color:C_SUB, formatter:v=> abbr(v) },
      splitLine:{ lineStyle:{ color:C_GRID } } },
    { type:'value', name:'订单', position:'right', nameTextStyle:{ color:C_SUB, fontSize:11 },
      axisLabel:{ color:C_SUB }, splitLine:{ show:false } },
  ],
  series: [
    { name:'GMV', type:'line', data: DATA.trend.gmv, smooth:true, showSymbol:_isSingleDay,
      symbolSize:6, lineStyle:{ width:2.5 }, itemStyle:{color:C_GMV},
      areaStyle:{color:'rgba(25,36,32,.06)'},
      markPoint:{ data:_mk, silent:true } },
    { name:'广告消耗', type:'line', data: DATA.trend.ad_spend || [], smooth:true, showSymbol:_isSingleDay,
      symbolSize:6, lineStyle:{ width:2 }, itemStyle:{color:C_AD} },
    { name:'订单数', type:'bar', yAxisIndex:1, data: DATA.trend.orders,
      barMaxWidth: _isSingleDay ? 44 : 26,
      itemStyle:{color:'rgba(44,140,95,.55)', borderRadius:[4,4,0,0]} },
  ],
});
document.getElementById('trend-title-note').textContent = _isSingleDay ? '单日视图' : '';
window.addEventListener('resize', () => chart.resize());

// -- Top 5 SKU table --
const topBody = document.getElementById('top-body');
const _esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
(DATA.top_skus || []).forEach(s => {
  const tr = document.createElement('tr');
  const nm = _esc(s.name);
  tr.innerHTML = '<td class="name" title="' + nm + '">' + nm + '</td>'
    + '<td class="num">' + fmtInt(s.units) + '</td>'
    + '<td class="num">' + fmtMoney(s.gmv) + '</td>'
    + '<td class="num">' + (s.share == null ? '—' : s.share + '%') + '</td>';
  topBody.appendChild(tr);
});
if (!DATA.top_skus || !DATA.top_skus.length) {
  topBody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--sub)">暂无数据</td></tr>';
}

// -- Low stock table --
const lowWrap = document.getElementById('low-wrap');
const lowItems = DATA.low_stock || [];
if (!lowItems.length) {
  lowWrap.innerHTML = '<div style="text-align:center;color:var(--sub);padding:16px 0">暂无断货风险 SKU</div>';
} else {
  let html = '<table><thead><tr><th>商品</th><th>风险</th><th class="num">库存</th>'
    + '<th class="num">日均销速</th><th class="num">可售天数</th></tr></thead><tbody>';
  lowItems.forEach(it => {
    const nm = _esc(it.name);
    html += '<tr><td class="name" title="' + nm + '">' + nm + '</td>'
      + '<td><span class="pill ' + it.level + '">' + it.level_label + '</span></td>'
      + '<td class="num">' + fmtInt(it.stock) + '</td>'
      + '<td class="num">' + it.velocity + '</td>'
      + '<td class="num">' + it.days + '</td></tr>';
  });
  html += '</tbody></table>';
  lowWrap.innerHTML = html;
}

// -- Footer --
document.getElementById('foot').textContent =
  '数据由 Data Hub 提供 · 结算口径 · ' + DATA.generated_at;

// -- AI 洞察：渐进加载（数字/图表已就绪，AI 失败不影响整页）--
(function loadInsight(){
  const hl = document.getElementById('ai-headline');
  const hlText = document.getElementById('ai-headline-text');
  const card = document.getElementById('ai-card');
  const body = document.getElementById('ai-body');
  hl.style.display = 'block';            // 先显示「正在生成」骨架
  card.style.display = 'block';
  const url = location.pathname + '/insight' + location.search;
  fetch(url, {credentials:'same-origin'})
    .then(r => r.json())
    .then(d => {
      if (!d || !d.available) {          // 降级：隐藏 AI 块
        hl.style.display = 'none'; card.style.display = 'none'; return;
      }
      hl.classList.remove('loading');
      hlText.textContent = d.headline || '';
      let html = '';
      if (d.problems && d.problems.length) {
        html += '<div class="ai-block problems"><div class="h">今日问题</div><ul>'
          + d.problems.map(p => '<li>' + _esc(p) + '</li>').join('') + '</ul></div>';
      }
      if (d.actions && d.actions.length) {
        html += '<div class="ai-block actions"><div class="h">明日动作</div><ul>'
          + d.actions.map(a => '<li>' + _esc(a) + '</li>').join('') + '</ul></div>';
      }
      const model = d.model ? '<div style="color:var(--sub);font-size:11px;margin-top:8px">🤖 由 '
        + _esc(d.model) + ' 总结，仅供参考</div>' : '';
      body.innerHTML = (html || '<div class="ai-loading">今日无突出问题</div>') + model;
    })
    .catch(() => { hl.style.display = 'none'; card.style.display = 'none'; });
})();
</script>
</body>
</html>"""
