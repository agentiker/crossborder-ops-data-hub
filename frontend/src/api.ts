// API 客户端 + SSE 流式解析（plan/15 Phase A）。
// 所有请求带 cookie（同源）；401 → 跳飞书登录。

export interface Me {
  open_id: string;
  role: string;
  is_boss: boolean;
  scope_label: string;
}

export interface ConversationItem {
  id: number;
  title: string;
  updated_at: string | null;
}

// 助手消息的「思考步骤」：工具调用轨迹。纯前端内存字段（流式时累积），不持久化到后端。
export interface ThinkingStep {
  name: string;
  label: string;
  done: boolean;
}

export interface Message {
  id?: number;
  role: string;
  content: string;
  tool_calls?: unknown;
  steps?: ThinkingStep[];
}

const LOGIN_PATH = "/board/auth/feishu/login";

function gotoLogin() {
  // 带上当前 /app 路径作 next，登录后回跳到 WebUI 而非默认 /board（后端 _safe_next 白名单含 /app）。
  const next = window.location.pathname + window.location.search;
  window.location.href = `${LOGIN_PATH}?next=${encodeURIComponent(next)}`;
}

// 把 FastAPI 的 {detail: "..."} 反解出来再抛；后端中文错误能直接展示给 boss。
async function extractError(r: Response): Promise<string> {
  try {
    const body = await r.json();
    if (body && typeof body.detail === "string") return body.detail;
  } catch {
    // 非 JSON 响应（如 502），落回 HTTP 状态
  }
  return `HTTP ${r.status}`;
}

async function getJSON<T>(url: string): Promise<T> {
  const r = await fetch(url, { credentials: "same-origin" });
  if (r.status === 401) {
    gotoLogin();
    throw new Error("unauthenticated");
  }
  if (!r.ok) throw new Error(await extractError(r));
  return r.json();
}

async function postJSON<T>(url: string, body: unknown, method = "POST"): Promise<T> {
  const r = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(body),
  });
  if (r.status === 401) {
    gotoLogin();
    throw new Error("unauthenticated");
  }
  if (!r.ok) throw new Error(await extractError(r));
  return r.json();
}

// ── 看板数据（plan/15 UI 地基 · Board 垂直切片）──
// 复用 board.py 既有的 cookie 鉴权 JSON 端点 /board/data（同源带 cookie），
// 而非内部令牌守卫的 /api/data/*。范围由后端按登录身份夹紧，前端不传 scope。
export interface TrendPoint {
  date: string;
  gmv: number;
  order_count: number;
  units_sold: number;
  // 逐小时趋势时为 "HH:00" 展示串；逐日时缺省（x 轴：label ?? date.slice(5)）。
  label?: string;
}

export interface TopSku {
  sku_id?: string;
  product_id?: string;
  product_name?: string;
  sku_name?: string;
  seller_sku?: string; // 款号（同商品多 SKU 时取其一，配合 sku_count 显示）
  sku_count?: number; // 该商品的 SKU/规格数；>1 时前端显「N 个规格」而非单一款号
  units_sold: number;
  gmv?: number;
  image_url?: string; // 主图缩略图（爆款小图，可空 → 前端占位）
}

// 单品渠道分布。channels=粗分 4（达人/自营素材/商品卡/店铺页）；
// fine=细分 6（达人直播/达人视频/自营直播/自营视频/商品卡/店铺页），两粒度总额一致，前端可切换。
// available=false → 该商品暂无渠道数据。
export interface ChannelSlice {
  key: string;
  label: string;
  gmv: number;
  pct: number;
}
export interface ProductChannels {
  channels: ChannelSlice[];
  fine?: ChannelSlice[];
  total_gmv: number;
  currency: string | null;
  available: boolean;
}

// 商品内某 SKU 的已付款销量/GMV（详情弹窗「各 SKU 占比」用）。
export interface SkuBreakdown {
  sku_id?: string;
  sku_name?: string;
  seller_sku?: string;
  units_sold: number;
  gmv: number;
}

// 商品详情弹窗数据（懒加载）：渠道 4 分 + 各 SKU 销量。
export interface ProductDetail {
  channels: ProductChannels;
  skus: SkuBreakdown[];
}

// 近 30 天新品（懒加载）：每个新品带每日销量曲线 + 爆单判定（峰值单日 ≥ threshold）。
export interface NewProductPoint {
  date: string;
  units: number;
}
export interface NewProduct {
  product_id: string;
  title: string;
  seller_sku?: string | null;
  sku_count: number;
  image_url?: string | null;
  source_create_time?: string | null;
  days_online: number;
  total_units: number;
  total_gmv: number;
  series: NewProductPoint[];
  peak_units: number;
  peak_date?: string | null;
  burst: boolean;
}
export interface NewProducts {
  items: NewProduct[];
  threshold: number;
  window: { lookback_days: number; as_of: string };
  available: boolean;
}

export interface LowStockItem {
  sku_id: string;
  sku_name?: string; // SKU 变体名（颜色/尺码），缺失回退 sku_id
  product_name?: string;
  image_url?: string | null; // 商品主图缩略图，缺图前端占位
  bucket: "stockout" | "critical" | "warning" | "ok" | "idle";
  available_stock: number;
  daily_velocity: number;
  days_of_cover: number;
}

export interface PendingItem {
  order_id: string | number;
  shop_id?: string | number;
  shop_name?: string; // 店铺可读名（后端从 platform_tokens.seller_name 富化），缺失回落 shop_id
  first_product_name?: string;
  item_count: number;
  total_amount: number;
  bucket?: string;
}

export interface ScopeOption {
  key: string;
  label: string;
}

export interface BoardData {
  scope: string;
  scope_key: string;
  can_switch: boolean;
  scopes: ScopeOption[];
  role: string;
  period: string;
  // 当期窗口元信息。includes_today=true 时窗口含半天今天,前端显「当日累计」徽章 + 利润卡提示。
  window?: {
    start: string;
    end: string;
    includes_today: boolean;
    as_of_label: string | null;
  };
  overview: {
    orders: {
      gmv: number;
      order_count: number;
      units_sold: number;
      avg_order_value: number;
      cancelled_count?: number;
      unpaid_count?: number;
    };
    inventory: { total_sku?: number; total_stock?: number; low_stock_count?: number };
    // 广告消耗（结算口径）：无结算数据时 total_ad_spend=0、roas=null，前端降级为「—」。
    // 拆两类：付费投放(paid_ad_spend=仅 GMV Max，ROAS 口径) vs 达人佣金(creator_commission=TAP+联盟，CPS)。
    // complete=false → 窗口落在结算滞后区，广告/ROAS 不完整，前端标注「结算中·截至 latest_covered_date」。
    ads?: {
      total_ad_spend: number;
      paid_ad_spend: number;
      creator_commission: number;
      roas: number | null;
      gmv_max_fee: number;
      tap_commission: number;
      affiliate_commission: number;
      currency?: string;
      complete?: boolean;
      settled_through?: string | null;
      latest_covered_date?: string | null;
    };
    // 环比：当期 vs 紧邻等长上期的百分比；上期无基准（为 0）时为 null，不渲染、不臆造。
    change?: {
      gmv: number | null;
      order_count: number | null;
      units_sold: number | null;
      avg_order_value: number | null;
      ad_cost?: number | null;
      roas?: number | null;
    };
  };
  trend: { points: TrendPoint[]; window_label?: string; start_date?: string; end_date?: string; granularity?: string; prev_points?: TrendPoint[]; prev_window_label?: string };
  top: { items: TopSku[] };
  low: {
    items: LowStockItem[];
    buckets: { stockout: number; critical: number; warning: number };
    critical_days?: number; // 告急阈值（可售天数），健康度图例/tooltip 用
    warning_days?: number; // 偏低阈值（可售天数）
    velocity_window_days?: number; // 日均销量的统计窗口天数（近 N 天已付款销量 ÷ N）
  };
  fulfillment: {
    items: PendingItem[];
    buckets: { total: number; overdue: number; critical: number; normal: number };
    snapshot_at?: string;
  };
  // 渠道 GMV 占比（直播/视频/商品卡）。沙箱店无 analytics 数据时 available=false。
  channels?: {
    channels: { key: string; label: string; gmv: number; pct: number }[];
    total_gmv: number;
    currency: string | null;
    available: boolean;
  };
  // 预估利润（折 CNY）。estimated=今早预估（主口径）；settled=结算后真实（3b 回填，本期 null）。
  // 无聚合数据时 available=false（前端显「暂无利润数据」）。
  profit?: {
    available: boolean;
    currency: string;
    estimated: ProfitBreakdown | null;
    settled: ProfitBreakdown | null;
    // 覆盖天数护栏：窗口应有 expected_days 天，预聚合表实际覆盖 covered_days 天。
    // coverage_complete=false 时前端显「数据不完整」告警（缺天静默少算的可见化）。
    expected_days?: number;
    covered_days?: number;
    coverage_complete?: boolean;
  };
  // 费率监控（实时算、复用 B1 及时口径）。status：normal 正常 / alert 异常升高 / insufficient 数据积累中。
  fee_rate?: FeeRateMonitor;
}

export interface FeeRateComponent {
  key: string;
  name: string;
  share: number; // 占 GMV 比例（0-1）
}

export interface FeeRateAttribution {
  key: string;
  name: string;
  from: number; // 基准占比
  to: number; // 当前占比
  delta: number; // 升幅（占 GMV 百分点，小数）
}

export interface FeeRateMonitor {
  // baseline_pending：有当前预估费率/构成/趋势，但已结算基准不足、暂无法判异常。
  status: "normal" | "alert" | "baseline_pending" | "insufficient";
  currency: string | null;
  skip_reason: string | null;
  current_rate: number; // 当前预估费率（unsettled 口径）
  baseline_rate: number; // 已结算历史基准
  abs_delta: number; // 绝对升幅（小数，0.03=3pct）
  rel_delta: number; // 相对升幅
  eval_gmv: number;
  baseline_gmv: number;
  order_count: number;
  eval_window: string; // MM/DD~MM/DD
  baseline_window: string;
  components: FeeRateComponent[];
  attributions: FeeRateAttribution[];
  trend: { date: string; rate: number | null }[];
}

export interface ProfitBreakdown {
  gmv: number;
  gross_profit: number;
  commission_fee: number;
  ad_cost: number;
  product_cost: number;
  refund_amount: number;
  order_count: number;
  units_sold: number;
  profit_margin: number | null;
}

// ── 角色管理（plan/15 Phase C · boss-only CRUD）──
export interface RoleRow {
  open_id: string;
  role: string; // boss / operator / pending（待审批，自助申请落库的哨兵）
  allowed_scope_key: string | null;
  note: string | null;
  is_active: boolean;
  account_id: string;
  channel: string;
  created_at?: string | null; // ISO，自助申请时间；用于待审批排序/显示
}

export interface AdminScopeOption {
  scope_key: string;
  scope_name: string;
}

export interface RoleUpsertBody {
  open_id: string;
  role: "boss" | "operator";
  scope_key?: string | null;
  note?: string | null;
  account_id?: string;
  channel?: string;
}

// ── 业务阈值配置（boss-only，按租户覆盖 core/config 默认）──
export interface BizConfigRow {
  config_key: string;
  label: string;
  unit: string | null;
  type: "int" | "float";
  group: string;
  hint?: string | null;
  min?: number | null;
  max?: number | null;
  default_value: number;
  current_value: number;
  is_overridden: boolean;
}

// 看板筛选参数：start/end 显式日期（覆盖 period）；platform/country 平台/区域；scope 店铺。
// 空值不拼进 querystring（让后端走默认/全部）。period 作无显式日期时的回退。
export interface BoardQuery {
  start?: string;
  end?: string;
  period?: string;
  scope?: string;
  platform?: string;
  country?: string;
  granularity?: string;
}

export const api = {
  me: () => getJSON<Me>("/api/me"),
  boardData: (q: BoardQuery = {}) => {
    const params = new URLSearchParams();
    if (q.start) params.set("start_date", q.start);
    if (q.end) params.set("end_date", q.end);
    if (q.period) params.set("period", q.period);
    if (q.scope) params.set("scope", q.scope);
    if (q.platform) params.set("platform", q.platform);
    if (q.country) params.set("country", q.country);
    if (q.granularity) params.set("granularity", q.granularity);
    return getJSON<BoardData>(`/board/data?${params.toString()}`);
  },
  // 商品详情（懒加载）：点击爆款卡某商品时才请求，返回渠道 4 分 + 各 SKU 销量。窗口/范围与 boardData 同源。
  productDetail: (productId: string, q: BoardQuery = {}) => {
    const params = new URLSearchParams();
    params.set("product_id", productId);
    if (q.start) params.set("start_date", q.start);
    if (q.end) params.set("end_date", q.end);
    if (q.period) params.set("period", q.period);
    if (q.scope) params.set("scope", q.scope);
    if (q.platform) params.set("platform", q.platform);
    if (q.country) params.set("country", q.country);
    return getJSON<ProductDetail>(`/board/product-detail?${params.toString()}`);
  },
  // 近 30 天新品（懒加载）：窗口/阈值后端固定，仅传范围/平台/区域过滤。看板首载后再请求，不拖慢首屏。
  newProducts: (q: BoardQuery = {}) => {
    const params = new URLSearchParams();
    if (q.scope) params.set("scope", q.scope);
    if (q.platform) params.set("platform", q.platform);
    if (q.country) params.set("country", q.country);
    return getJSON<NewProducts>(`/board/new-products?${params.toString()}`);
  },
  adminRoles: () => getJSON<{ items: RoleRow[] }>("/api/admin/roles"),
  adminScopes: () => getJSON<{ items: AdminScopeOption[] }>("/api/admin/scopes"),
  adminUpsertRole: (body: RoleUpsertBody) => postJSON<RoleRow>("/api/admin/roles", body),
  adminDeactivateRole: (open_id: string, account_id = "ecom-app", channel = "feishu") =>
    postJSON<RoleRow>("/api/admin/roles/deactivate", { open_id, account_id, channel }),
  bizConfigs: () => getJSON<{ items: BizConfigRow[] }>("/api/admin/biz-configs"),
  bizConfigUpsert: (config_key: string, value: number) =>
    postJSON<BizConfigRow>("/api/admin/biz-configs", { config_key, value }),
  bizConfigReset: (config_key: string) =>
    postJSON<BizConfigRow>("/api/admin/biz-configs/reset", { config_key }),
  conversations: () => getJSON<{ items: ConversationItem[] }>("/api/conversations"),
  conversation: (id: number) =>
    getJSON<{ id: number; title: string; messages: Message[] }>(
      `/api/conversations/${id}`,
    ),
  rename: async (id: number, title: string) => {
    await fetch(`/api/conversations/${id}/rename`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ title }),
    });
  },
  remove: async (id: number) => {
    await fetch(`/api/conversations/${id}`, {
      method: "DELETE",
      credentials: "same-origin",
    });
  },
};

export type SSEEvent =
  | { type: "meta"; conversation_id: number; title: string }
  | { type: "delta"; text: string }
  | { type: "tool"; name: string; status: string }
  | { type: "done"; conversation_id: number }
  | { type: "error"; message: string };

// 发消息并以异步生成器吐出 SSE 事件。
export async function* sendChat(
  message: string,
  conversationId: number | null,
  signal?: AbortSignal,
): AsyncGenerator<SSEEvent> {
  const r = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    signal,
    body: JSON.stringify({ message, conversation_id: conversationId }),
  });
  if (r.status === 401) {
    gotoLogin();
    return;
  }
  if (!r.ok || !r.body) {
    yield { type: "error", message: `HTTP ${r.status}` };
    return;
  }

  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    // SSE 以空行分隔事件
    let idx: number;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const ev = parseEvent(chunk);
      if (ev) yield ev;
    }
  }
}

function parseEvent(chunk: string): SSEEvent | null {
  let event = "message";
  let data = "";
  for (const line of chunk.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data += line.slice(5).trim();
  }
  if (!data) return null;
  try {
    const obj = JSON.parse(data);
    return { type: event, ...obj } as SSEEvent;
  } catch {
    return null;
  }
}
