import { useEffect, useRef, useState } from "react";
import { ArrowUp, Sparkles, Wrench } from "lucide-react";
import { api, sendChat, type ConversationItem, type Message } from "@/api";
import { ConversationList } from "@/components/chat/ConversationList";
import { Markdown } from "@/components/Markdown";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

// ops_* 工具的中文展示名（与后端 agent_tools 对齐）。
const TOOL_LABELS: Record<string, string> = {
  ops_overview: "经营概览",
  ops_orders_summary: "订单汇总",
  ops_orders_trend: "订单趋势",
  ops_top_skus: "爆款榜",
  ops_low_stock: "断货风险",
  ops_fulfillments_pending: "待发货",
};

// 建议 chips（首页命令栏）。
const PRESETS = [
  "最近 7 天整体经营情况怎么样？",
  "近 30 天卖得最好的 10 个商品",
  "现在有多少待发货订单，有超时的吗？",
];

// 首页 2 张真实快捷卡：点击直接在控制台发起对话（展示 agent 取数）。
const QUICK_CARDS = [
  {
    title: "今日经营简报",
    desc: "GMV、订单数、待发货一眼看全",
    q: "今天的 GMV、订单数和待发货情况怎么样？",
  },
  {
    title: "断货风险速览",
    desc: "哪些 SKU 快卖断了、该补货了",
    q: "有哪些 SKU 快断货了？",
  },
];

// 按本机时刻给时段名 + 问候语（StoreClaw 时段 pill）。
function timeOfDay(): { period: string; greeting: string } {
  const h = new Date().getHours();
  if (h < 5) return { period: "夜深", greeting: "夜深了，看看今天的生意" };
  if (h < 8) return { period: "清晨", greeting: "早，先看看昨天的收成" };
  if (h < 11) return { period: "上午", greeting: "上午好，今天想看哪块数据" };
  if (h < 13) return { period: "午后", greeting: "午间好，店铺跑得怎么样" };
  if (h < 18) return { period: "下午", greeting: "下午好，盯一眼经营节奏" };
  if (h < 23) return { period: "傍晚", greeting: "傍晚好，盘点今天这一单单" };
  return { period: "夜深", greeting: "夜深了，看看今天的生意" };
}

export function ChatPage() {
  const [conversations, setConversations] = useState<ConversationItem[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [liveText, setLiveText] = useState("");
  const [toolStatus, setToolStatus] = useState<string | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [tod] = useState(timeOfDay);

  useEffect(() => {
    refreshConversations();
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, liveText, toolStatus]);

  function refreshConversations() {
    api.conversations().then((r) => setConversations(r.items)).catch(() => {});
  }

  async function openConversation(id: number) {
    if (streaming) return;
    setActiveId(id);
    setError(null);
    setLiveText("");
    try {
      const detail = await api.conversation(id);
      setMessages(detail.messages);
    } catch {
      setMessages([]);
    }
  }

  function newConversation() {
    if (streaming) return;
    setActiveId(null);
    setMessages([]);
    setLiveText("");
    setError(null);
  }

  async function deleteConversation(id: number) {
    await api.remove(id);
    if (id === activeId) newConversation();
    refreshConversations();
  }

  async function send(text: string) {
    if (streaming) return;
    setError(null);
    setMessages((m) => [...m, { role: "user", content: text }]);
    setStreaming(true);
    setLiveText("");
    setToolStatus(null);

    const convId = activeId;
    let acc = "";
    try {
      for await (const ev of sendChat(text, convId)) {
        if (ev.type === "meta") {
          if (activeId === null) setActiveId(ev.conversation_id);
        } else if (ev.type === "delta") {
          acc += ev.text;
          setLiveText(acc);
          setToolStatus(null);
        } else if (ev.type === "tool") {
          const label = TOOL_LABELS[ev.name] || ev.name;
          setToolStatus(ev.status === "running" ? `正在查询：${label}…` : null);
        } else if (ev.type === "error") {
          setError(ev.message);
        }
      }
    } catch (e) {
      setError(String(e));
    }

    setMessages((m) => [...m, { role: "assistant", content: acc }]);
    setLiveText("");
    setToolStatus(null);
    setStreaming(false);
    refreshConversations();
  }

  const isEmpty = messages.length === 0 && !streaming;

  return (
    <div className="flex h-full">
      <ConversationList
        conversations={conversations}
        activeId={activeId}
        onSelect={openConversation}
        onNew={newConversation}
        onDelete={deleteConversation}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        {isEmpty ? (
          // ── 首页命令栏 launcher ────────────────────────────────────────────
          <div className="flex-1 overflow-y-auto">
            <div className="mx-auto w-full max-w-2xl px-6 pb-16 pt-[14vh]">
              <div className="inline-flex items-center gap-1.5 rounded-full border bg-card px-3 py-1 text-xs font-medium text-muted-foreground">
                <Sparkles className="size-3.5 text-[hsl(var(--gold))]" />
                {tod.period}
              </div>

              <h1 className="font-display mt-5 text-4xl font-semibold leading-[1.15] tracking-tight sm:text-[2.75rem]">
                {tod.greeting}
              </h1>
              <p className="mt-2 text-sm text-muted-foreground">
                问我店铺的 GMV、订单、爆款、库存与待发货——按你的权限范围答。
              </p>

              <div className="mt-6">
                <Composer onSend={send} streaming={streaming} autoFocus size="home" />
              </div>

              <div className="mt-4 flex flex-wrap gap-2">
                {PRESETS.map((p) => (
                  <button
                    key={p}
                    onClick={() => send(p)}
                    className="rounded-full border bg-card px-3.5 py-1.5 text-xs text-muted-foreground transition-colors hover:border-foreground/30 hover:text-foreground"
                  >
                    {p}
                  </button>
                ))}
              </div>

              <div className="mt-7 grid grid-cols-1 gap-3 sm:grid-cols-2">
                {QUICK_CARDS.map((c) => (
                  <button
                    key={c.title}
                    onClick={() => send(c.q)}
                    className="group rounded-2xl border bg-card p-4 text-left transition-colors hover:border-foreground/30"
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-display text-base font-semibold">{c.title}</span>
                      <ArrowUp className="size-4 rotate-45 text-muted-foreground transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5" />
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">{c.desc}</p>
                  </button>
                ))}
              </div>
            </div>
          </div>
        ) : (
          // ── 对话视图 ───────────────────────────────────────────────────────
          <>
            <div ref={scrollRef} className="flex-1 overflow-y-auto">
              <div className="mx-auto max-w-3xl px-4 py-8 sm:px-6">
                {messages.map((m, i) => (
                  <Bubble key={m.id ?? i} role={m.role} content={m.content} />
                ))}

                {streaming && (
                  <div className="mb-8">
                    {toolStatus && (
                      <div className="mb-2 inline-flex items-center gap-1.5 rounded-full bg-muted px-2.5 py-1 text-xs font-medium text-muted-foreground">
                        <Wrench className="size-3" />
                        {toolStatus}
                      </div>
                    )}
                    {liveText ? (
                      <Markdown text={liveText} />
                    ) : (
                      !toolStatus && <span className="text-sm text-muted-foreground">思考中…</span>
                    )}
                  </div>
                )}

                {error && <div className="mb-6 text-sm text-destructive">⚠️ {error}</div>}
              </div>
            </div>

            <div className="bg-background/80 px-4 pb-3 pt-1 backdrop-blur sm:px-6">
              <div className="mx-auto max-w-3xl">
                <Composer onSend={send} streaming={streaming} />
                <p className="mt-2 text-center text-xs text-muted-foreground">
                  AI 可能出错，请核对重要数据。
                </p>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// 命令栏输入（首页居中大号 / 对话底部 docked 同款）。自管 textarea，提交后清空。
function Composer({
  onSend,
  streaming,
  autoFocus,
  size = "docked",
}: {
  onSend: (text: string) => void;
  streaming: boolean;
  autoFocus?: boolean;
  size?: "home" | "docked";
}) {
  const ref = useRef<HTMLTextAreaElement>(null);

  function submit() {
    const el = ref.current;
    if (!el) return;
    const text = el.value.trim();
    if (!text || streaming) return;
    el.value = "";
    el.style.height = "auto";
    onSend(text);
  }

  return (
    <div
      className={cn(
        "flex items-end gap-2 rounded-2xl border border-input bg-card shadow-sm transition-shadow focus-within:ring-2 focus-within:ring-ring",
        size === "home" ? "px-3 py-2.5" : "px-3 py-2",
      )}
    >
      <textarea
        ref={ref}
        rows={1}
        autoFocus={autoFocus}
        placeholder="问我店铺经营数据，Enter 发送，Shift+Enter 换行"
        className={cn(
          "max-h-40 flex-1 resize-none bg-transparent px-1.5 py-1.5 text-sm placeholder:text-muted-foreground focus-visible:outline-none",
          size === "home" && "text-base",
        )}
        onInput={(e) => {
          const t = e.currentTarget;
          t.style.height = "auto";
          t.style.height = Math.min(t.scrollHeight, 160) + "px";
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            submit();
          }
        }}
      />
      <Button
        size="icon"
        className={cn("shrink-0 rounded-xl", size === "home" ? "h-10 w-10" : "h-9 w-9")}
        disabled={streaming}
        onClick={submit}
        aria-label="发送"
      >
        <ArrowUp className="size-4" />
      </Button>
    </div>
  );
}

function Bubble({ role, content }: { role: string; content: string }) {
  const isUser = role === "user";
  if (isUser) {
    return (
      <div className="mb-8 flex justify-end">
        <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-muted px-4 py-2.5 text-sm">
          {content}
        </div>
      </div>
    );
  }
  // 助手：无气泡裸文（StoreClaw 风）
  return (
    <div className="mb-8">
      <Markdown text={content} />
    </div>
  );
}
