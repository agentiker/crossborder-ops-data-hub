"""客户运营看板（路线 A：飞书内嵌 H5）。

服务端复用现有 /api/data 路由函数取数，把结果内嵌进自包含 HTML，用 Chart.js 画趋势/榜单。

**鉴权 = 签名 token（不靠 127.0.0.1 旁路）**：一旦走 cloudflared 隧道即公网可达，旁路=裸奔。
入口 `?t=<token>`，验签拿 open_id（见 web/signed_link）；验签失败 → 401 错误页。

**强制软隔离**：只认服务端按 token 里 open_id 查到的 binding scope，**忽略 URL 里传的
scope_id/shop_id**（钉死为 None），防客户改 URL 越权。全表 tenant_id 硬隔离留待 plan/09。

取数直接 await 现有路由处理函数（get_overview / get_orders_trend / get_orders_top_skus /
get_low_stock），所有参数显式传齐，避免 FastAPI 的 Query(None) 默认值泄漏成 Query 对象。
"""

import json

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, JSONResponse

from core.tenancy import DEFAULT_ACCOUNT, set_current_account
from web.routes.data import (
    get_low_stock,
    get_orders_top_skus,
    get_orders_trend,
    get_overview,
)
from web.signed_link import verify_token

router = APIRouter()


async def _collect(open_id: str, period: str, account_id: str = DEFAULT_ACCOUNT) -> dict:
    """按 open_id 的 binding scope 取看板四块数据，组装成前端用的 dict。

    强制软隔离：scope_id/shop_ids/shop_id 一律钉 None，只按 token 里的 open_id 解析范围。
    服务端取数显式传齐每个参数，绝不让 FastAPI 的 Query(None) 默认值泄漏成 Query 对象。

    多租户：渲染路径不经 /api/data 的 bind_account_context，故这里按 token 解出的 account
    显式写进请求级 contextvar，下游 _resolve_scope 据此按本租户隔离取数。
    """
    set_current_account(account_id)
    overview = await get_overview(
        platform=None, country=None, shop_id=None,
        scope_id=None, shop_ids=None, open_id=open_id,
    )
    trend = await get_orders_trend(
        start_date=None, end_date=None, period=period,
        platform=None, country=None, shop_id=None,
        scope_id=None, shop_ids=None, open_id=open_id,
    )
    top = await get_orders_top_skus(
        start_date=None, end_date=None, period=period,
        platform=None, country=None, shop_id=None,
        scope_id=None, shop_ids=None, limit=10, open_id=open_id,
    )
    low = await get_low_stock(
        platform=None, country=None, shop_id=None,
        scope_id=None, shop_ids=None,
        critical_days=None, warning_days=None, open_id=open_id,
    )

    # 路由函数返回类型不一：overview 是 dict，其余是 Pydantic 模型——统一成 dict
    overview = _asdict(overview)
    return {
        "scope": overview.get("scope") or "全部范围",
        "period": period,
        "overview": overview,
        "trend": _asdict(trend),
        "top": _asdict(top),
        "low": _asdict(low),
    }


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def dashboard(
    t: str = Query("", description="签名 token（含 open_id + 过期）"),
    period: str = Query("last_30d", description="趋势/榜单时间窗口"),
):
    tok = verify_token(t)
    if not tok:
        return HTMLResponse(_render_error(), status_code=401)
    open_id, account = tok
    data = await _collect(open_id, period, account)
    return HTMLResponse(_render(data))


@router.get("/dashboard/data", include_in_schema=False)
async def dashboard_data(
    t: str = Query("", description="签名 token（含 open_id + 过期）"),
    period: str = Query("last_30d", description="趋势/榜单时间窗口"),
):
    """日期切换用的 JSON 端点：前端 AJAX 拉新 period 数据、局部重绘，避免整页跳转。

    同样验签 + 强制隔离（只认 token 里的 open_id，忽略 URL 其它参数）。验签失败 → 401 JSON。
    """
    tok = verify_token(t)
    if not tok:
        return JSONResponse({"error": "invalid_token"}, status_code=401)
    open_id, account = tok
    data = await _collect(open_id, period, account)
    return JSONResponse(data)


def _asdict(obj):
    """兼容 Pydantic 模型 / 普通 dict 两种返回。"""
    return obj.model_dump() if hasattr(obj, "model_dump") else obj


def _render(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return _PAGE.replace("__DATA__", payload)


def _render_error() -> str:
    """链接失效/越权时的自包含深色错误页（与看板同色系）。"""
    return _ERROR_PAGE


_ERROR_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>链接已失效</title>
<style>
  body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
         background:#0f1117; color:#e6e8ee;
         font:14px/1.6 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif; }
  .box { text-align:center; padding:32px 24px; }
  .icon { font-size:40px; margin-bottom:12px; }
  h1 { font-size:18px; margin:0 0 8px; font-weight:600; }
  p { color:#8a90a2; margin:0; font-size:13px; }
</style>
</head>
<body>
  <div class="box">
    <div class="icon">🔒</div>
    <h1>链接已失效</h1>
    <p>请回到对话里重新获取看板链接。</p>
  </div>
</body>
</html>"""


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
  .pill.stockout { background:rgba(255,92,108,.18); color:var(--red); }
  .pill.critical { background:rgba(245,166,35,.18); color:var(--amber); }
  .pill.warning  { background:rgba(91,140,255,.18); color:var(--accent); }
  .empty { color:var(--sub); padding:24px 0; text-align:center; }
  .foot { color:var(--sub); font-size:11px; margin-top:24px; text-align:center; }
  @media (max-width:720px){ .kpis{grid-template-columns:repeat(2,1fr);} .grid{grid-template-columns:1fr;} }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>运营看板 <span class="scope" id="scope"></span></h1>
    <span class="scope" id="window"></span>
  </header>

  <div class="controls" id="periods"></div>

  <div class="kpis">
    <div class="kpi"><div class="label">GMV（已付款）</div><div class="val" id="k-gmv">—</div><div class="sub" id="k-aov"></div></div>
    <div class="kpi"><div class="label">订单数</div><div class="val" id="k-orders">—</div></div>
    <div class="kpi"><div class="label">销量</div><div class="val" id="k-units">—</div></div>
    <div class="kpi"><div class="label">库存 SKU / 低库存</div><div class="val" id="k-sku">—</div><div class="sub" id="k-stock"></div></div>
  </div>

  <div class="grid">
    <div class="card full"><h2>GMV 趋势 <span class="cap" id="cap-trend"></span></h2><canvas id="c-gmv"></canvas></div>
    <div class="card"><h2>订单 / 销量趋势</h2><canvas id="c-orders"></canvas></div>
    <div class="card"><h2>爆款单品榜 <span class="cap">按销量</span></h2><canvas id="c-top"></canvas></div>
    <div class="card full">
      <h2>断货风险 <span class="cap" id="cap-low"></span></h2>
      <div id="low-wrap"></div>
    </div>
  </div>

  <div class="foot">已签名鉴权 · 范围按账号锁定</div>
</div>

<script>
const BOOT = __DATA__;
const TOKEN = new URLSearchParams(location.search).get('t') || '';
const fmtInt = n => (n==null?'—':Number(n).toLocaleString('en-US'));
const fmtMoney = n => (n==null?'—':Number(n).toLocaleString('en-US',{maximumFractionDigits:0}));
const PERIODS = [
  ['today','今天'],['yesterday','昨天'],['this_week','本周'],['last_week','上周'],
  ['last_7d','近7天'],['last_30d','近30天'],['this_month','本月'],
];

const GRID = '#272b38', SUB = '#8a90a2';
Chart.defaults.color = SUB;
Chart.defaults.borderColor = GRID;
Chart.defaults.font.family = "-apple-system,'PingFang SC',sans-serif";

const charts = {};        // 复用 canvas 上的图表实例，切日期时只重绘、不整页重载
const pbox = document.getElementById('periods');
let busy = false;

// 日期切换条：点击走 AJAX（不再 <a href> 整页跳转 → 不堆历史记录、不重下 Chart.js CDN）
function buildPeriods(active){
  pbox.innerHTML = '';
  PERIODS.forEach(([key,label])=>{
    const a = document.createElement('a');
    a.textContent = label; a.href = '#';
    if (key===active) a.className = 'on';
    a.addEventListener('click', e=>{ e.preventDefault(); load(key); });
    pbox.appendChild(a);
  });
}

async function load(period){
  if (busy) return;
  busy = true; pbox.classList.add('loading');
  try {
    const r = await fetch('/dashboard/data?t='+encodeURIComponent(TOKEN)+'&period='+encodeURIComponent(period));
    if (!r.ok){ if (r.status===401) location.reload(); return; }   // token 过期 → 退回错误页
    render(await r.json());
    // 原地更新 URL（同步 period，不新增历史记录 → 返回时无需关一堆页面）
    const q = new URLSearchParams(location.search); q.set('period', period);
    history.replaceState(null, '', '?'+q.toString());
  } catch(e){ /* 网络抖动：静默，保留当前视图 */ }
  finally { busy = false; pbox.classList.remove('loading'); }
}

function drawChart(id, cfg){
  if (charts[id]) charts[id].destroy();                          // 同一 canvas 不能并存两个实例
  cfg.options = Object.assign({animation:false}, cfg.options);   // 关动画，切换瞬时
  charts[id] = new Chart(document.getElementById(id), cfg);
}

function render(D){
  // 范围 + 窗口
  document.getElementById('scope').textContent = '· ' + (D.scope||'');
  const tw = D.trend.window_label || (D.trend.start_date+' ~ '+D.trend.end_date);
  document.getElementById('window').textContent = tw;
  document.getElementById('cap-trend').textContent = tw;

  buildPeriods(D.period);   // 重建切换条并高亮当前

  // KPI
  const o = D.overview.orders, inv = D.overview.inventory;
  document.getElementById('k-gmv').textContent = fmtMoney(o.gmv);
  document.getElementById('k-aov').textContent = '客单价 ' + fmtMoney(o.avg_order_value);
  document.getElementById('k-orders').textContent = fmtInt(o.order_count);
  document.getElementById('k-units').textContent = fmtInt(o.units_sold);
  document.getElementById('k-sku').textContent = fmtInt(inv.total_sku);
  document.getElementById('k-stock').textContent = '总库存 '+fmtInt(inv.total_stock)+' · 低库存 '+fmtInt(inv.low_stock_count);

  const pts = D.trend.points || [];
  const labels = pts.map(p=>p.date.slice(5));

  // GMV 趋势
  drawChart('c-gmv', {
    type:'line',
    data:{ labels, datasets:[{ label:'GMV', data:pts.map(p=>p.gmv),
      borderColor:'#5b8cff', backgroundColor:'rgba(91,140,255,.12)', fill:true, tension:.3, pointRadius:2 }] },
    options:{ plugins:{legend:{display:false}}, scales:{ y:{beginAtZero:true} } }
  });

  // 订单 / 销量
  drawChart('c-orders', {
    type:'line',
    data:{ labels, datasets:[
      { label:'订单数', data:pts.map(p=>p.order_count), borderColor:'#3ecf8e', tension:.3, pointRadius:2 },
      { label:'销量', data:pts.map(p=>p.units_sold), borderColor:'#f5a623', tension:.3, pointRadius:2 },
    ]},
    options:{ scales:{ y:{beginAtZero:true} } }
  });

  // 爆款榜
  const items = (D.top.items||[]).slice(0,10);
  drawChart('c-top', {
    type:'bar',
    data:{ labels: items.map(i=> (i.product_name||i.sku_name||i.sku_id||'?').slice(0,18)),
      datasets:[{ label:'销量', data:items.map(i=>i.units_sold),
        backgroundColor:'#5b8cff', borderRadius:4 }] },
    options:{ indexAxis:'y', plugins:{legend:{display:false}}, scales:{ x:{beginAtZero:true} } }
  });

  // 断货风险表
  const low = D.low.items||[];
  document.getElementById('cap-low').textContent =
    '断货 '+(D.low.buckets.stockout||0)+' · 告急 '+(D.low.buckets.critical||0)+' · 预警 '+(D.low.buckets.warning||0)
    + ' · 可售天数 = 可用库存 ÷ 日均销速';
  const wrap = document.getElementById('low-wrap');
  const BLABEL = {stockout:'断货', critical:'告急', warning:'预警'};
  if (!low.length) {
    wrap.innerHTML = '<div class="empty">暂无断货风险 SKU</div>';
  } else {
    let html = '<table><thead><tr><th>商品</th><th>风险</th><th class="num">可用库存</th><th class="num">日均销速</th><th class="num">可售天数</th></tr></thead><tbody>';
    low.slice(0,20).forEach(it=>{
      html += '<tr><td>'+(it.product_name||it.sku_id)+'</td>'
        + '<td><span class="pill '+it.bucket+'">'+(BLABEL[it.bucket]||it.bucket)+'</span></td>'
        + '<td class="num">'+fmtInt(it.available_stock)+'</td>'
        + '<td class="num">'+Number(it.daily_velocity).toFixed(1)+'</td>'
        + '<td class="num">'+Number(it.days_of_cover).toFixed(1)+'</td></tr>';
    });
    html += '</tbody></table>';
    wrap.innerHTML = html;
  }
}

// 首屏：用内嵌数据直接渲染（无需二次请求）
render(BOOT);
</script>
</body>
</html>"""
