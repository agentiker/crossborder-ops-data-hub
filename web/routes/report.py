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
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from core.config import settings
from core.timezone import business_today, describe_window, resolve_period
from web.routes.data import (
    get_ad_spend,
    get_ad_spend_trend,
    get_low_stock,
    get_orders_top_skus,
    get_orders_trend,
    get_overview,
)
from web.signed_link import verify_token
from web.web_session import verify_session_cookie

_LOGIN_PATH = "/board/auth/feishu/login"

router = APIRouter()

# 印尼时区 UTC+7
_JAKARTA_TZ = timezone(timedelta(hours=7))


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

    # 当前窗口数据
    overview = _asdict(await get_overview(
        platform=None, country=None, shop_id=None,
        scope_id=None, shop_ids=None, open_id=open_id,
    ))
    trend = _asdict(await get_orders_trend(
        start_date=sd.isoformat(), end_date=ed.isoformat(), period=None,
        platform=None, country=None, shop_id=None,
        scope_id=None, shop_ids=None, open_id=open_id,
    ))
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
    ad = _asdict(await get_ad_spend(
        start_date=sd.isoformat(), end_date=ed.isoformat(), period=None,
        platform=None, country=None, shop_id=None,
        scope_id=None, shop_ids=None, open_id=open_id,
    ))
    ad_trend = _asdict(await get_ad_spend_trend(
        start_date=sd.isoformat(), end_date=ed.isoformat(), period=None,
        platform=None, country=None, shop_id=None,
        scope_id=None, shop_ids=None, open_id=open_id,
    ))

    # 前一期窗口（算环比）
    window_days = (ed - sd).days or 1
    prev_ed = sd
    prev_sd = prev_ed - timedelta(days=window_days)

    prev_trend = _asdict(await get_orders_trend(
        start_date=prev_sd.isoformat(), end_date=prev_ed.isoformat(), period=None,
        platform=None, country=None, shop_id=None,
        scope_id=None, shop_ids=None, open_id=open_id,
    ))
    prev_ad = _asdict(await get_ad_spend(
        start_date=prev_sd.isoformat(), end_date=prev_ed.isoformat(), period=None,
        platform=None, country=None, shop_id=None,
        scope_id=None, shop_ids=None, open_id=open_id,
    ))

    # 汇总前一期的 GMV 和订单数
    prev_gmv = sum(p.get("gmv", 0) for p in prev_trend.get("points", []))
    prev_orders = sum(p.get("order_count", 0) for p in prev_trend.get("points", []))
    prev_ad_spend = prev_ad.get("total_ad_spend", 0) or 0

    # 当前窗口汇总
    cur_gmv = overview.get("orders", {}).get("gmv", 0)
    cur_orders = overview.get("orders", {}).get("order_count", 0)
    cur_ad_spend = ad.get("total_ad_spend", 0) or 0
    cur_roas = ad.get("roas")

    # 前一期 ROAS
    prev_roas = None
    if prev_ad_spend and prev_ad_spend > 0:
        prev_roas_val = prev_gmv / prev_ad_spend
        prev_roas = round(prev_roas_val, 2)

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

    # Top 5 SKU
    top_items = []
    for item in top.get("items", [])[:5]:
        top_items.append({
            "name": item.get("product_name") or item.get("sku_name") or item.get("sku_id") or "?",
            "units": item.get("units_sold", 0),
            "gmv": item.get("gmv", 0),
        })

    # 断货预警：按严重度（断货 > 告急 > 预警）排序，同级最紧急（可售天数最少）在前
    low_items = []
    level_map = {"stockout": "断货", "critical": "告急", "warning": "预警"}
    _severity = {"stockout": 0, "critical": 1, "warning": 2}
    low_sorted = sorted(
        low.get("items", []),
        key=lambda it: (_severity.get(it.get("bucket", ""), 9), it.get("days_of_cover", 0)),
    )
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

    return {
        "scope": overview.get("scope") or "全店",
        "period_label": period_label,
        "generated_at": generated_at,
        "kpi": {
            "gmv": {
                "value": cur_gmv,
                "change": _calc_change(cur_gmv, prev_gmv),
                "currency": "IDR",
            },
            "orders": {
                "value": cur_orders,
                "change": _calc_change(cur_orders, prev_orders),
            },
            "ad_spend": {
                "value": cur_ad_spend,
                "change": _calc_change(cur_ad_spend, prev_ad_spend),
                "currency": "IDR",
            },
            "roas": {
                "value": cur_roas,
                "change": _calc_change(cur_roas, prev_roas) if cur_roas and prev_roas else None,
            },
            "sku_count": overview.get("inventory", {}).get("total_sku", 0),
            "low_stock_count": overview.get("inventory", {}).get("low_stock_count", 0),
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
<title>经营日报</title>
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
    <h1>经营日报</h1>
    <div class="meta" id="meta"></div>
  </header>

  <div class="kpis" id="kpis"></div>

  <div class="card">
    <h2>GMV / 广告 / 订单趋势 <small id="trend-title-note"></small></h2>
    <div id="trend-chart"></div>
  </div>

  <div class="card">
    <h2>Top 5 爆款</h2>
    <table><thead><tr><th>商品</th><th class="num">销量</th><th class="num">GMV</th></tr></thead>
    <tbody id="top-body"></tbody></table>
  </div>

  <div class="card">
    <h2>断货预警</h2>
    <div id="low-wrap"></div>
  </div>

  <div class="foot" id="foot"></div>
</div>

<script>
const DATA = __DATA__;

// -- header --
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

const kpiDefs = [
  {label:'GMV', val: fmtMoney(DATA.kpi.gmv.value), chg: chgHtml(DATA.kpi.gmv.change)},
  {label:'订单数', val: fmtInt(DATA.kpi.orders.value), chg: chgHtml(DATA.kpi.orders.change)},
  {label:'广告消耗', val: fmtMoney(DATA.kpi.ad_spend.value), chg: chgHtml(DATA.kpi.ad_spend.change)},
  {label:'ROAS', val: fmtDec(DATA.kpi.roas.value), chg: chgHtml(DATA.kpi.roas.change)},
  {label:'库存 SKU', val: fmtInt(DATA.kpi.sku_count), chg: ''},
  {label:'断货风险', val: fmtInt(DATA.kpi.low_stock_count), chg: ''},
];
const kpisEl = document.getElementById('kpis');
kpiDefs.forEach(d => {
  const div = document.createElement('div');
  div.className = 'kpi';
  div.innerHTML = '<div class="label">' + d.label + '</div>'
    + '<div class="val">' + d.val + '</div>' + d.chg;
  kpisEl.appendChild(div);
});

// -- Trend chart (dual Y: GMV + 广告消耗 lines share money axis, Orders bar on right) --
// 配色对齐 SPA token：GMV=墨绿主色 / 广告=橙警示 / 订单=绿
const C_GMV = 'rgb(25,36,32)', C_AD = 'rgb(206,118,18)', C_ORD = 'rgb(44,140,95)';
const C_SUB = 'rgba(25,36,32,.5)', C_GRID = 'rgba(0,0,0,.06)';
const chart = echarts.init(document.getElementById('trend-chart'));
const _dates = DATA.trend.dates || [];
const _isSingleDay = _dates.length <= 1;
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
  grid: { top:30, left:48, right:44, bottom:36 },
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
      areaStyle:{color:'rgba(25,36,32,.06)'} },
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
(DATA.top_skus || []).forEach(s => {
  const tr = document.createElement('tr');
  tr.innerHTML = '<td>' + s.name + '</td>'
    + '<td class="num">' + fmtInt(s.units) + '</td>'
    + '<td class="num">' + fmtMoney(s.gmv) + '</td>';
  topBody.appendChild(tr);
});
if (!DATA.top_skus || !DATA.top_skus.length) {
  topBody.innerHTML = '<tr><td colspan="3" style="text-align:center;color:var(--sub)">暂无数据</td></tr>';
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
    html += '<tr><td>' + it.name + '</td>'
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
</script>
</body>
</html>"""
