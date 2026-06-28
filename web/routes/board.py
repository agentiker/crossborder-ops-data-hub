"""独立运营看板（plan/14 Phase 4）：飞书 OAuth 登录态 + 数据层权限闸。

与 plan/13 的 /dashboard?t=（签名链接）共存、互不影响。区别：
- 鉴权：飞书 OAuth 登录 cookie（require_web_user），非一次性签名链接。
- 范围：经 services/user_authz 的硬权限闸——boss 看全部、operator 锁定 allowed_scope
  且不可越界（改 ?scope= 越界 → 403）。

取数复用 web/routes/data.py 的路由函数，但范围由 resolve_authorized_scope 夹紧后以显式
shop_ids 传入（open_id=None / scope_id=None，绕开会话 binding）——这样看板与对话侧最终
共用同一套取数 + 同一权限上限。
"""

import json
import logging

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse, JSONResponse

from core.tenancy import set_current_account
from core.timezone import business_now, business_today, previous_window
from services.ad_metrics import get_ad_spend_summary, get_roas
from services.channel_metrics import get_channel_gmv_breakdown
from services.fee_rate_metrics import get_fee_rate_monitor
from services.order_metrics import (
    get_gmv_summary,
    get_gmv_summary_intraday_range,
    get_product_sku_breakdown,
    get_top_products,
)
from services.product_channel_metrics import get_product_channel_breakdown
from services.profit_summary import get_profit_card
from services.scope_resolution import ScopeError, list_scopes
from services.user_authz import AuthzError, UserPermission, resolve_authorized_scope
from web.routes.data import (
    _resolve_window,
    get_fulfillments_pending,
    get_low_stock,
    get_orders_trend,
    get_overview,
)
from web.web_security import require_web_user

logger = logging.getLogger(__name__)
router = APIRouter()


def _asdict(obj):
    return obj.model_dump() if hasattr(obj, "model_dump") else obj


def _pct(cur: float, prev: float):
    """环比百分比（保留 1 位小数）。上期基准为 0（无可比）→ None，前端不渲染该行，不臆造。"""
    if not prev:
        return None
    return round((cur - prev) / prev * 100, 1)


def _overview_window_and_gmv(
    cur_start, cur_end, prev_start, prev_end, *, platform, country, shop_ids,
):
    """当期/上期 GMV 摘要 + 窗口元信息。

    窗口结束在「今天」(WIB 业务日)时,当期含半天今天。若直接整窗 vs 上期整窗,环比会被半天
    拉成「假暴跌」(半天比全天)。含今日则改用 intraday 公平比较:cur/prev 都钉「截至此刻」
    (末日截到 cutoff、中间天整天),与日报 _weekly_windows 同款口径(get_gmv_summary_intraday_range);
    不含今日则照旧整天对整天。返回 (cur, prev, window_meta)。
    """
    includes_today = cur_end == business_today()
    if includes_today:
        cutoff = business_now().time()
        cur = get_gmv_summary_intraday_range(
            start_date=cur_start, end_date=cur_end, cutoff=cutoff,
            platform=platform, country=country, shop_ids=shop_ids,
        )
        prev = get_gmv_summary_intraday_range(
            start_date=prev_start, end_date=prev_end, cutoff=cutoff,
            platform=platform, country=country, shop_ids=shop_ids,
        )
        as_of_label = (
            f"数据截至 {business_now().strftime('%m-%d %H:%M')}（印尼时间）· 今日为当日累计"
        )
    else:
        cur = get_gmv_summary(
            start_date=cur_start, end_date=cur_end,
            platform=platform, country=country, shop_ids=shop_ids,
        )
        prev = get_gmv_summary(
            start_date=prev_start, end_date=prev_end,
            platform=platform, country=country, shop_ids=shop_ids,
        )
        as_of_label = None
    window_meta = {
        "start": cur_start.isoformat(),
        "end": cur_end.isoformat(),
        "includes_today": includes_today,
        "as_of_label": as_of_label,
    }
    return cur, prev, window_meta


def _scope_options(perm: UserPermission) -> list[dict]:
    """范围切换条数据。boss：全部范围 + 所有 scope；operator：仅其 allowed（锁定单项）。"""
    if perm.is_boss:
        opts = [{"key": "", "label": "全部范围"}]
        opts += [
            {"key": s["scope_key"], "label": s["scope_name"]}
            for s in list_scopes(perm.account_id)
        ]
        return opts
    # operator：只暴露被授权的那个 scope，不可切换到其它
    allowed = perm.allowed_scope_key
    label = allowed
    for s in list_scopes(perm.account_id):
        if s["scope_key"] == allowed:
            label = s["scope_name"]
            break
    return [{"key": allowed, "label": label}]


async def _collect(
    perm: UserPermission,
    period: str,
    requested_scope_key: str,
    start_date: str | None = None,
    end_date: str | None = None,
    platform_q: str | None = None,
    country_q: str | None = None,
) -> dict:
    """按权限闸夹紧范围后取看板各块数据。越界由 resolve_authorized_scope 抛 ScopeError。

    start_date/end_date：显式起止日期（YYYY-MM-DD，日历筛选）；传了即覆盖 period。
    platform_q/country_q：平台/区域筛选（正交附加维度，叠加在 scope 的 shop_ids 之上、不参与越界判断）。
    """
    # /board 渲染链路不走 X-Account-Id 注入；复用 data.py 路由前先把当前老板的租户写进 context，
    # 让下游 _resolve_scope / ORM 自动过滤按同一 account_id 生效。
    set_current_account(perm.account_id)
    filters = resolve_authorized_scope(
        perm,
        requested_scope_key=requested_scope_key or None,
        platform=platform_q or None,
        country=country_q or None,
    )
    # 夹紧后的具体店集合作为显式条件传入；open_id/scope_id 钉 None，绕开会话 binding。
    shop_ids = ",".join(filters.shop_ids) if filters.shop_ids else None
    platform, country = filters.platform, filters.country

    overview = await get_overview(
        platform=platform, country=country, shop_id=None,
        scope_id=None, shop_ids=shop_ids, open_id=None,
    )
    trend = await get_orders_trend(
        start_date=start_date, end_date=end_date, period=period,
        platform=platform, country=country, shop_id=None,
        scope_id=None, shop_ids=shop_ids, open_id=None,
    )
    low = await get_low_stock(
        platform=platform, country=country, shop_id=None,
        scope_id=None, shop_ids=shop_ids,
        critical_days=None, warning_days=None, open_id=None,
    )
    fulfillment = await get_fulfillments_pending(
        platform=platform, country=country, shop_id=None,
        scope_id=None, shop_ids=shop_ids,
        warning_hours=None, limit=50, open_id=None,
    )

    overview = _asdict(overview)
    # 环比：取当期 + 紧邻等长上期，把 overview.orders 统一成跟随当期窗口的口径，并注入 change。
    # 当期窗口复用 data._resolve_window：显式 start/end 优先（日历筛选），否则按 period 预设。
    # previous_window 对任意等长窗口成立（纯算术），无需 period 对齐。
    cur_start, cur_end = _resolve_window(
        start_date, end_date, period, default_back_days=6
    )
    prev_start, prev_end = previous_window(cur_start, cur_end)
    shop_id_list = filters.shop_ids or None
    # 爆款「商品」榜（按 product_id 聚合，带小图/款号；窗口同当期）。直调服务（不走 data 路由），
    # 与 channels/profit 一致：商品级语义 + 单品渠道拆分 join 都按 product_id。
    top_items = get_top_products(
        start_date=cur_start, end_date=cur_end,
        platform=platform, country=country, shop_ids=shop_id_list, limit=10,
    )
    # 渠道 GMV 拆分（直播/视频/商品卡，实时调 TikTok analytics + 进程缓存）。沙箱店无
    # analytics 数据时内部降级返回 available=False，不抛错、不阻断看板其它块。
    channels = get_channel_gmv_breakdown(
        start_date=cur_start, end_date=cur_end,
        platform=platform, country=country, shop_ids=shop_id_list,
    )
    # 预估利润卡（折 CNY）：从 fact_profit_daily 聚合预估/真实双套；无聚合数据 available=False。
    profit = get_profit_card(
        start_date=cur_start, end_date=cur_end,
        platform=platform, country=country, shop_ids=shop_id_list,
    )
    # 费率监控卡（实时算、复用 B1 及时口径）：当前预估费率 vs 已结算基准 + 趋势 + 三态徽章。
    # 不随 period 变（固定近 N 天 unsettled vs 历史 settled）。出错不阻断看板其它块。
    try:
        fee_rate = get_fee_rate_monitor(
            platform=platform, country=country, shop_ids=shop_id_list,
            scope_display=filters.display_text,
        )
    except Exception:  # noqa: BLE001 — 单卡兜底，费率取数失败不整页挂
        logger.warning("fee_rate monitor card failed", exc_info=True)
        fee_rate = {"status": "insufficient", "skip_reason": "取数失败", "trend": []}
    cur, prev, window_meta = _overview_window_and_gmv(
        cur_start, cur_end, prev_start, prev_end,
        platform=platform, country=country, shop_ids=shop_id_list,
    )
    # 广告消耗（结算口径）：复用同一当期/上期窗口与 filters，各取一次摘要算环比；当期再取 ROAS。
    # 无结算数据时 get_* 返回 0/None，不抛错；前端按 0/None 做降级展示。
    cur_ads = get_ad_spend_summary(
        start_date=cur_start, end_date=cur_end,
        platform=platform, country=country, shop_ids=shop_id_list,
    )
    prev_ads = get_ad_spend_summary(
        start_date=prev_start, end_date=prev_end,
        platform=platform, country=country, shop_ids=shop_id_list,
    )
    cur_roas = get_roas(
        start_date=cur_start, end_date=cur_end,
        platform=platform, country=country, shop_ids=shop_id_list,
    )
    prev_roas = get_roas(
        start_date=prev_start, end_date=prev_end,
        platform=platform, country=country, shop_ids=shop_id_list,
    )
    overview["orders"] = {
        "gmv": cur["gmv"], "order_count": cur["order_count"],
        "units_sold": cur["units_sold"], "avg_order_value": cur["avg_order_value"],
    }
    overview["ads"] = {
        "total_ad_spend": cur_ads["total_ad_spend"],
        "roas": cur_roas["roas"],
        "gmv_max_fee": cur_ads["gmv_max_fee"],
        "tap_commission": cur_ads["tap_commission"],
        "affiliate_commission": cur_ads["affiliate_commission"],
        "currency": cur_ads["currency"],
    }
    # ROAS 环比：任一期 roas 为 None（该期无广告费）则不可比 → None，不臆造。
    roas_change = (
        _pct(cur_roas["roas"], prev_roas["roas"])
        if cur_roas["roas"] is not None and prev_roas["roas"] is not None
        else None
    )
    overview["change"] = {
        "gmv": _pct(cur["gmv"], prev["gmv"]),
        "order_count": _pct(cur["order_count"], prev["order_count"]),
        "units_sold": _pct(cur["units_sold"], prev["units_sold"]),
        "avg_order_value": _pct(cur["avg_order_value"], prev["avg_order_value"]),
        "ad_cost": _pct(cur_ads["total_ad_spend"], prev_ads["total_ad_spend"]),
        "roas": roas_change,
    }
    return {
        "scope": filters.display_text,
        "scope_key": requested_scope_key or "",
        "can_switch": perm.is_boss,
        "scopes": _scope_options(perm),
        "role": perm.role,
        "period": period,
        # 当期窗口元信息:含今日时前端显「当日累计」徽章 + 利润卡提示,避免客户把半天今天当下降。
        "window": window_meta,
        "overview": overview,
        "trend": _asdict(trend),
        "top": {"items": top_items},
        "low": _asdict(low),
        "fulfillment": _asdict(fulfillment),
        "channels": channels,
        "profit": profit,
        "fee_rate": fee_rate,
    }


@router.get("/board", response_class=HTMLResponse, include_in_schema=False)
async def board(
    perm: UserPermission = Depends(require_web_user),
    period: str = Query("last_30d", description="趋势/榜单时间窗口（无显式 start/end 时回退）"),
    scope: str = Query("", description="范围切换 scope_key（boss 任意 / operator 限其授权）"),
    start_date: str | None = Query(None, description="显式起始日 YYYY-MM-DD（覆盖 period）"),
    end_date: str | None = Query(None, description="显式结束日 YYYY-MM-DD（覆盖 period）"),
    platform: str | None = Query(None, description="平台筛选（如 tiktok_shop；空=全部）"),
    country: str | None = Query(None, description="区域筛选 ISO alpha-2（如 ID；空=全部）"),
):
    try:
        data = await _collect(perm, period, scope, start_date, end_date, platform, country)
    except (ScopeError, AuthzError) as exc:
        return HTMLResponse(_render_denied(str(exc)), status_code=403)
    return HTMLResponse(_PAGE.replace("__DATA__", json.dumps(data, ensure_ascii=False)))


@router.get("/board/data", include_in_schema=False)
async def board_data(
    perm: UserPermission = Depends(require_web_user),
    period: str = Query("last_30d"),
    scope: str = Query(""),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    platform: str | None = Query(None),
    country: str | None = Query(None),
):
    """切换日期/范围/平台/区域用的 JSON 端点：前端 AJAX 局部重绘。越界 → 403 JSON。"""
    try:
        data = await _collect(perm, period, scope, start_date, end_date, platform, country)
    except (ScopeError, AuthzError) as exc:
        return JSONResponse({"error": "forbidden", "detail": str(exc)}, status_code=403)
    return JSONResponse(data)


@router.get("/board/product-detail", include_in_schema=False)
async def board_product_detail(
    perm: UserPermission = Depends(require_web_user),
    product_id: str = Query(..., description="商品 product_id"),
    period: str = Query("last_30d"),
    scope: str = Query(""),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    platform: str | None = Query(None),
    country: str | None = Query(None),
):
    """商品详情弹窗懒加载端点：点击爆款卡某商品时才请求，返回 {channels, skus}。

    - channels：单品渠道 4 分（达人/自营素材/商品卡/店铺页）；沙箱/无 analytics→available=False。
    - skus：该商品各 SKU 已付款销量/GMV（前端算占比条）。
    窗口/范围与看板同源（_resolve_window + resolve_authorized_scope 夹紧），不拖慢看板首载。
    """
    set_current_account(perm.account_id)
    try:
        filters = resolve_authorized_scope(
            perm, requested_scope_key=scope or None,
            platform=platform or None, country=country or None,
        )
    except (ScopeError, AuthzError) as exc:
        return JSONResponse({"error": "forbidden", "detail": str(exc)}, status_code=403)
    cur_start, cur_end = _resolve_window(start_date, end_date, period, default_back_days=6)
    channels = get_product_channel_breakdown(
        product_id=product_id,
        start_date=cur_start, end_date=cur_end,
        country=filters.country, shop_ids=filters.shop_ids or None,
    )
    skus = get_product_sku_breakdown(
        product_id=product_id,
        start_date=cur_start, end_date=cur_end,
        platform=filters.platform, country=filters.country,
        shop_ids=filters.shop_ids or None,
    )
    return JSONResponse({"channels": channels, "skus": skus})


def _render_denied(msg: str) -> str:
    return _DENIED_PAGE.replace("__MSG__", msg.replace("<", "&lt;").replace(">", "&gt;"))


_DENIED_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>超出权限范围</title>
<style>
  body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
       background:#0f1117;color:#e6e8ee;font:14px/1.6 -apple-system,"PingFang SC",sans-serif;}
  .box{text-align:center;padding:32px 24px;max-width:420px;}
  h1{font-size:18px;margin:0 0 8px;} p{color:#8a90a2;font-size:13px;}
  a{color:#5b8cff;text-decoration:none;}
</style></head>
<body><div class="box"><div style="font-size:40px">🚫</div>
<h1>超出你的权限范围</h1><p>__MSG__</p><p><a href="/board">返回看板</a></p>
</div></body></html>"""


_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>运营看板</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  :root { --bg:#0f1117; --card:#1a1d27; --line:#272b38; --txt:#e6e8ee; --sub:#8a90a2;
          --accent:#5b8cff; --green:#3ecf8e; --amber:#f5a623; --red:#ff5c6c; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--txt);
         font:14px/1.5 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif; }
  .wrap { max-width:1100px; margin:0 auto; padding:20px 16px 60px; }
  header { display:flex; align-items:baseline; justify-content:space-between;
           flex-wrap:wrap; gap:8px; margin-bottom:4px; }
  h1 { font-size:18px; margin:0; font-weight:600; }
  .scope { color:var(--sub); font-size:13px; }
  .switch { display:flex; gap:6px; flex-wrap:wrap; margin:10px 0 0; }
  .switch a { color:var(--sub); text-decoration:none; padding:4px 11px; border-radius:14px;
              border:1px solid var(--line); font-size:12px; }
  .switch a.on { color:#fff; background:#2b3350; border-color:var(--accent); }
  .switch .lock { color:var(--sub); font-size:12px; padding:4px 0; }
  .controls { display:flex; gap:6px; flex-wrap:wrap; margin:14px 0 18px; }
  .controls a { color:var(--sub); text-decoration:none; padding:5px 12px; border-radius:16px;
                border:1px solid var(--line); font-size:12px; }
  .controls a.on { color:#fff; background:var(--accent); border-color:var(--accent); }
  .controls.loading { opacity:.5; pointer-events:none; }
  .kpis { display:grid; grid-template-columns:repeat(4,1fr); gap:10px; }
  .kpi { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px; }
  .kpi .label { color:var(--sub); font-size:12px; }
  .kpi .val { font-size:22px; font-weight:600; margin-top:4px; }
  .kpi .sub { color:var(--sub); font-size:11px; margin-top:2px; }
  .grid { display:grid; grid-template-columns:1fr 1fr; gap:14px; margin-top:14px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; }
  .card h2 { font-size:14px; margin:0 0 12px; font-weight:600; }
  .card .cap { color:var(--sub); font-size:11px; font-weight:400; margin-left:6px; }
  .full { grid-column:1 / -1; }
  canvas { max-height:280px; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th,td { text-align:left; padding:7px 8px; border-bottom:1px solid var(--line); }
  th { color:var(--sub); font-weight:500; font-size:12px; }
  td.num { text-align:right; font-variant-numeric:tabular-nums; }
  .pill { display:inline-block; padding:1px 8px; border-radius:10px; font-size:11px; }
  .pill.stockout,.pill.overdue { background:rgba(255,92,108,.18); color:var(--red); }
  .pill.critical { background:rgba(245,166,35,.18); color:var(--amber); }
  .pill.warning,.pill.normal { background:rgba(91,140,255,.18); color:var(--accent); }
  .empty { color:var(--sub); padding:24px 0; text-align:center; }
  .foot { color:var(--sub); font-size:11px; margin-top:24px; text-align:center; }
  .foot a { color:var(--sub); }
  @media (max-width:720px){ .kpis{grid-template-columns:repeat(2,1fr);} .grid{grid-template-columns:1fr;} }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>运营看板 <span class="scope" id="scope"></span></h1>
    <span class="scope" id="window"></span>
  </header>

  <div class="switch" id="scopes"></div>
  <div class="controls" id="periods"></div>

  <div class="kpis">
    <div class="kpi"><div class="label">GMV（已付款）</div><div class="val" id="k-gmv">—</div><div class="sub" id="k-aov"></div></div>
    <div class="kpi"><div class="label">订单数</div><div class="val" id="k-orders">—</div></div>
    <div class="kpi"><div class="label">销量</div><div class="val" id="k-units">—</div></div>
    <div class="kpi"><div class="label">待发货 / 超时</div><div class="val" id="k-pend">—</div><div class="sub" id="k-pendsub"></div></div>
  </div>

  <div class="grid">
    <div class="card full"><h2>GMV 趋势 <span class="cap" id="cap-trend"></span></h2><canvas id="c-gmv"></canvas></div>
    <div class="card"><h2>订单 / 销量趋势</h2><canvas id="c-orders"></canvas></div>
    <div class="card"><h2>爆款单品榜 <span class="cap">按销量</span></h2><canvas id="c-top"></canvas></div>
    <div class="card full">
      <h2>待发货预警 <span class="cap" id="cap-pend"></span></h2>
      <div id="pend-wrap"></div>
    </div>
    <div class="card full">
      <h2>断货风险 <span class="cap" id="cap-low"></span></h2>
      <div id="low-wrap"></div>
    </div>
  </div>

  <div class="foot">飞书登录 · 范围按角色锁定 · <a href="/board/auth/feishu/logout">退出</a></div>
</div>

<script>
const BOOT = __DATA__;
const fmtInt = n => (n==null?'—':Number(n).toLocaleString('en-US'));
const fmtMoney = n => (n==null?'—':Number(n).toLocaleString('en-US',{maximumFractionDigits:0}));
const PERIODS = [
  ['today','今天'],['yesterday','昨天'],['this_week','本周'],['last_week','上周'],
  ['last_7d','近7天'],['last_30d','近30天'],['this_month','本月'],
];
const GRID = '#272b38', SUB = '#8a90a2';
Chart.defaults.color = SUB; Chart.defaults.borderColor = GRID;
Chart.defaults.font.family = "-apple-system,'PingFang SC',sans-serif";

const charts = {};
const pbox = document.getElementById('periods');
const sbox = document.getElementById('scopes');
let busy = false;
let curPeriod = BOOT.period;
let curScope = BOOT.scope_key || '';

function buildPeriods(active){
  pbox.innerHTML = '';
  PERIODS.forEach(([key,label])=>{
    const a = document.createElement('a');
    a.textContent = label; a.href = '#';
    if (key===active) a.className = 'on';
    a.addEventListener('click', e=>{ e.preventDefault(); curPeriod=key; load(); });
    pbox.appendChild(a);
  });
}

function buildScopes(D){
  sbox.innerHTML = '';
  if (!D.can_switch){
    // operator：范围锁定，仅展示不可点
    const s = document.createElement('span'); s.className='lock';
    s.textContent = '范围已锁定：' + (D.scopes[0] ? D.scopes[0].label : D.scope);
    sbox.appendChild(s); return;
  }
  (D.scopes||[]).forEach(opt=>{
    const a = document.createElement('a');
    a.textContent = opt.label; a.href = '#';
    if ((opt.key||'')===(D.scope_key||'')) a.className = 'on';
    a.addEventListener('click', e=>{ e.preventDefault(); curScope=opt.key||''; load(); });
    sbox.appendChild(a);
  });
}

async function load(){
  if (busy) return;
  busy = true; pbox.classList.add('loading');
  try {
    const q = new URLSearchParams({period:curPeriod, scope:curScope});
    const r = await fetch('/board/data?'+q.toString());
    if (!r.ok){ if (r.status===401||r.status===403) location.href='/board?'+q.toString(); return; }
    render(await r.json());
    const u = new URLSearchParams({period:curPeriod}); if(curScope) u.set('scope',curScope);
    history.replaceState(null, '', '?'+u.toString());
  } catch(e){ /* 网络抖动：保留当前视图 */ }
  finally { busy = false; pbox.classList.remove('loading'); }
}

function drawChart(id, cfg){
  if (charts[id]) charts[id].destroy();
  cfg.options = Object.assign({animation:false}, cfg.options);
  charts[id] = new Chart(document.getElementById(id), cfg);
}

function render(D){
  curPeriod = D.period; curScope = D.scope_key || '';
  document.getElementById('scope').textContent = '· ' + (D.scope||'');
  const tw = D.trend.window_label || (D.trend.start_date+' ~ '+D.trend.end_date);
  document.getElementById('window').textContent = tw;
  document.getElementById('cap-trend').textContent = tw;

  buildScopes(D);
  buildPeriods(D.period);

  const o = D.overview.orders, inv = D.overview.inventory;
  const fb = D.fulfillment.buckets || {};
  document.getElementById('k-gmv').textContent = fmtMoney(o.gmv);
  document.getElementById('k-aov').textContent = '客单价 ' + fmtMoney(o.avg_order_value);
  document.getElementById('k-orders').textContent = fmtInt(o.order_count);
  document.getElementById('k-units').textContent = fmtInt(o.units_sold);
  document.getElementById('k-pend').textContent = fmtInt(fb.total||0);
  document.getElementById('k-pendsub').textContent = '超时 '+fmtInt(fb.overdue||0)+' · 临界 '+fmtInt(fb.critical||0);

  const pts = D.trend.points || [];
  const labels = pts.map(p=>p.date.slice(5));

  drawChart('c-gmv', {
    type:'line',
    data:{ labels, datasets:[{ label:'GMV', data:pts.map(p=>p.gmv),
      borderColor:'#5b8cff', backgroundColor:'rgba(91,140,255,.12)', fill:true, tension:.3, pointRadius:2 }] },
    options:{ plugins:{legend:{display:false}}, scales:{ y:{beginAtZero:true} } }
  });
  drawChart('c-orders', {
    type:'line',
    data:{ labels, datasets:[
      { label:'订单数', data:pts.map(p=>p.order_count), borderColor:'#3ecf8e', tension:.3, pointRadius:2 },
      { label:'销量', data:pts.map(p=>p.units_sold), borderColor:'#f5a623', tension:.3, pointRadius:2 },
    ]},
    options:{ scales:{ y:{beginAtZero:true} } }
  });
  const items = (D.top.items||[]).slice(0,10);
  drawChart('c-top', {
    type:'bar',
    data:{ labels: items.map(i=> (i.product_name||i.sku_name||i.sku_id||'?').slice(0,18)),
      datasets:[{ label:'销量', data:items.map(i=>i.units_sold), backgroundColor:'#5b8cff', borderRadius:4 }] },
    options:{ indexAxis:'y', plugins:{legend:{display:false}}, scales:{ x:{beginAtZero:true} } }
  });

  // 待发货预警
  document.getElementById('cap-pend').textContent =
    '超时 '+(fb.overdue||0)+' · 临界 '+(fb.critical||0)+' · 正常 '+(fb.normal||0)+'（快照 '+(D.fulfillment.snapshot_at||'—')+'）';
  const pw = document.getElementById('pend-wrap');
  const pend = (D.fulfillment.items||[]);
  const PB = {overdue:'超时', critical:'临界', normal:'正常', unknown:'未知'};
  function bucketOf(it){ return it.bucket || ''; }
  if (!pend.length){ pw.innerHTML = '<div class="empty">暂无待发货订单</div>'; }
  else {
    let h = '<table><thead><tr><th>订单</th><th>店铺</th><th>商品</th><th class="num">件数</th><th class="num">金额</th></tr></thead><tbody>';
    pend.slice(0,20).forEach(it=>{
      h += '<tr><td>'+String(it.order_id||'').slice(-8)+'</td>'
        + '<td>'+(it.shop_id||'—')+'</td>'
        + '<td>'+((it.first_product_name||'—')).slice(0,20)+'</td>'
        + '<td class="num">'+fmtInt(it.item_count)+'</td>'
        + '<td class="num">'+fmtMoney(it.total_amount)+'</td></tr>';
    });
    h += '</tbody></table>'; pw.innerHTML = h;
  }

  // 断货风险
  const low = D.low.items||[];
  document.getElementById('cap-low').textContent =
    '断货 '+(D.low.buckets.stockout||0)+' · 告急 '+(D.low.buckets.critical||0)+' · 预警 '+(D.low.buckets.warning||0)
    + ' · 可售天数 = 可用库存 ÷ 日均销速';
  const wrap = document.getElementById('low-wrap');
  const BLABEL = {stockout:'断货', critical:'告急', warning:'预警'};
  if (!low.length) { wrap.innerHTML = '<div class="empty">暂无断货风险 SKU</div>'; }
  else {
    let html = '<table><thead><tr><th>商品</th><th>风险</th><th class="num">可用库存</th><th class="num">日均销速</th><th class="num">可售天数</th></tr></thead><tbody>';
    low.slice(0,20).forEach(it=>{
      html += '<tr><td>'+(it.product_name||it.sku_id)+'</td>'
        + '<td><span class="pill '+it.bucket+'">'+(BLABEL[it.bucket]||it.bucket)+'</span></td>'
        + '<td class="num">'+fmtInt(it.available_stock)+'</td>'
        + '<td class="num">'+Number(it.daily_velocity).toFixed(1)+'</td>'
        + '<td class="num">'+Number(it.days_of_cover).toFixed(1)+'</td></tr>';
    });
    html += '</tbody></table>'; wrap.innerHTML = html;
  }
}

render(BOOT);
</script>
</body>
</html>"""
