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

export interface Message {
  id?: number;
  role: string;
  content: string;
  tool_calls?: unknown;
}

const LOGIN_PATH = "/board/auth/feishu/login";

function gotoLogin() {
  window.location.href = LOGIN_PATH;
}

async function getJSON<T>(url: string): Promise<T> {
  const r = await fetch(url, { credentials: "same-origin" });
  if (r.status === 401) {
    gotoLogin();
    throw new Error("unauthenticated");
  }
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export const api = {
  me: () => getJSON<Me>("/api/me"),
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
