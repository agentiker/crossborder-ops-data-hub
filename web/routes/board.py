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

from services.scope_resolution import ScopeError, list_scopes
from services.user_authz import AuthzError, UserPermission, resolve_authorized_scope
from web.routes.data import (
    get_fulfillments_pending,
    get_low_stock,
    get_orders_top_skus,
    get_orders_trend,
    get_overview,
)
from web.web_security import require_web_user

logger = logging.getLogger(__name__)
router = APIRouter()


def _asdict(obj):
    return obj.model_dump() if hasattr(obj, "model_dump") else obj


def _scope_options(perm: UserPermission) -> list[dict]:
    """范围切换条数据。boss：全部范围 + 所有 scope；operator：仅其 allowed（锁定单项）。"""
    if perm.is_boss:
        opts = [{"key": "", "label": "全部范围"}]
        opts += [{"key": s["scope_key"], "label": s["scope_name"]} for s in list_scopes()]
        return opts
    # operator：只暴露被授权的那个 scope，不可切换到其它
    allowed = perm.allowed_scope_key
    label = allowed
    for s in list_scopes():
        if s["scope_key"] == allowed:
            label = s["scope_name"]
            break
    return [{"key": allowed, "label": label}]


async def _collect(perm: UserPermission, period: str, requested_scope_key: str) -> dict:
    """按权限闸夹紧范围后取看板各块数据。越界由 resolve_authorized_scope 抛 ScopeError。"""
    filters = resolve_authorized_scope(
        perm, requested_scope_key=requested_scope_key or None
    )
    # 夹紧后的具体店集合作为显式条件传入；open_id/scope_id 钉 None，绕开会话 binding。
    shop_ids = ",".join(filters.shop_ids) if filters.shop_ids else None
    platform, country = filters.platform, filters.country

    overview = await get_overview(
        platform=platform, country=country, shop_id=None,
        scope_id=None, shop_ids=shop_ids, open_id=None,
    )
    trend = await get_orders_trend(
        start_date=None, end_date=None, period=period,
        platform=platform, country=country, shop_id=None,
        scope_id=None, shop_ids=shop_ids, open_id=None,
    )
    top = await get_orders_top_skus(
        start_date=None, end_date=None, period=period,
        platform=platform, country=country, shop_id=None,
        scope_id=None, shop_ids=shop_ids, limit=10, open_id=None,
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
    return {
        "scope": filters.display_text,
        "scope_key": requested_scope_key or "",
        "can_switch": perm.is_boss,
        "scopes": _scope_options(perm),
        "role": perm.role,
        "period": period,
        "overview": overview,
        "trend": _asdict(trend),
        "top": _asdict(top),
        "low": _asdict(low),
        "fulfillment": _asdict(fulfillment),
    }


@router.get("/board", response_class=HTMLResponse, include_in_schema=False)
async def board(
    perm: UserPermission = Depends(require_web_user),
    period: str = Query("last_30d", description="趋势/榜单时间窗口"),
    scope: str = Query("", description="范围切换 scope_key（boss 任意 / operator 限其授权）"),
):
    try:
        data = await _collect(perm, period, scope)
    except (ScopeError, AuthzError) as exc:
        return HTMLResponse(_render_denied(str(exc)), status_code=403)
    return HTMLResponse(_PAGE.replace("__DATA__", json.dumps(data, ensure_ascii=False)))


@router.get("/board/data", include_in_schema=False)
async def board_data(
    perm: UserPermission = Depends(require_web_user),
    period: str = Query("last_30d"),
    scope: str = Query(""),
):
    """切换日期/范围用的 JSON 端点：前端 AJAX 局部重绘。越界 → 403 JSON。"""
    try:
        data = await _collect(perm, period, scope)
    except (ScopeError, AuthzError) as exc:
        return JSONResponse({"error": "forbidden", "detail": str(exc)}, status_code=403)
    return JSONResponse(data)


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
