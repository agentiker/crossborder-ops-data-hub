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
}

export interface TopSku {
  sku_id?: string;
  product_name?: string;
  sku_name?: string;
  units_sold: number;
  gmv?: number;
}

export interface LowStockItem {
  sku_id: string;
  product_name?: string;
  bucket: "stockout" | "critical" | "warning";
  available_stock: number;
  daily_velocity: number;
  days_of_cover: number;
}

export interface PendingItem {
  order_id: string | number;
  shop_id?: string | number;
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
  overview: {
    orders: {
      gmv: number;
      order_count: number;
      units_sold: number;
      avg_order_value: number;
    };
    inventory: { total_sku?: number; total_stock?: number; low_stock_count?: number };
    // 广告消耗（结算口径）：无结算数据时 total_ad_spend=0、roas=null，前端降级为「—」。
    ads?: {
      total_ad_spend: number;
      roas: number | null;
      gmv_max_fee: number;
      tap_commission: number;
      affiliate_commission: number;
      currency?: string;
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
  trend: { points: TrendPoint[]; window_label?: string; start_date?: string; end_date?: string };
  top: { items: TopSku[] };
  low: {
    items: LowStockItem[];
    buckets: { stockout: number; critical: number; warning: number };
  };
  fulfillment: {
    items: PendingItem[];
    buckets: { total: number; overdue: number; critical: number; normal: number };
    snapshot_at?: string;
  };
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

export const api = {
  me: () => getJSON<Me>("/api/me"),
  boardData: (period: string, scope = "") =>
    getJSON<BoardData>(
      `/board/data?period=${encodeURIComponent(period)}&scope=${encodeURIComponent(scope)}`,
    ),
  adminRoles: () => getJSON<{ items: RoleRow[] }>("/api/admin/roles"),
  adminScopes: () => getJSON<{ items: AdminScopeOption[] }>("/api/admin/scopes"),
  adminUpsertRole: (body: RoleUpsertBody) => postJSON<RoleRow>("/api/admin/roles", body),
  adminDeactivateRole: (open_id: string, account_id = "ecom-app", channel = "feishu") =>
    postJSON<RoleRow>("/api/admin/roles/deactivate", { open_id, account_id, channel }),
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
