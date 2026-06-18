import { useEffect, useRef, useState } from "react";
import { Send, Wrench } from "lucide-react";
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

const PRESETS = [
  "最近 7 天整体经营情况怎么样？",
  "今天的 GMV 和订单数是多少？",
  "近 30 天卖得最好的 10 个商品",
  "有哪些 SKU 快断货了？",
  "现在有多少待发货订单，有超时的吗？",
];

export function ChatPage() {
  const [conversations, setConversations] = useState<ConversationItem[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [liveText, setLiveText] = useState("");
  const [toolStatus, setToolStatus] = useState<string | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

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
    inputRef.current?.focus();
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

    let convId = activeId;
    let acc = "";
    try {
      for await (const ev of sendChat(text, convId)) {
        if (ev.type === "meta") {
          convId = ev.conversation_id;
          if (activeId === null) setActiveId(convId);
        } else if (ev.type === "delta") {
          acc += ev.text;
          setLiveText(acc);
          setToolStatus(null);
        } else if (ev.type === "tool") {
          const label = TOOL_LABELS[ev.name] || ev.name;
          setToolStatus(ev.status === "running" ? `正在查询：${label}…` : null);
        } else if (ev.type === "error") {
          setError(ev.message);
        } else if (ev.type === "done") {
          convId = ev.conversation_id;
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

  function submit() {
    const el = inputRef.current;
    if (!el) return;
    const text = el.value.trim();
    if (!text || streaming) return;
    el.value = "";
    el.style.height = "auto";
    send(text);
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
        <div ref={scrollRef} className="flex-1 overflow-y-auto">
          {isEmpty ? (
            <div className="mx-auto mt-[12vh] max-w-2xl px-6 text-center">
              <h2 className="text-xl font-semibold tracking-tight">问我店铺经营数据</h2>
              <p className="mt-1 text-sm text-muted-foreground">试试这些：</p>
              <div className="mt-5 flex flex-col items-center gap-2">
                {PRESETS.map((p) => (
                  <button
                    key={p}
                    onClick={() => send(p)}
                    className="w-full max-w-md rounded-lg border bg-card px-4 py-2.5 text-left text-sm transition-colors hover:border-primary hover:bg-accent/50"
                  >
                    {p}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="mx-auto max-w-3xl px-4 py-6 sm:px-6">
              {messages.map((m, i) => (
                <Bubble key={m.id ?? i} role={m.role} content={m.content} />
              ))}

              {streaming && (
                <div className="mb-5 flex justify-start">
                  <div className="max-w-[90%] rounded-2xl border bg-card px-4 py-3 text-sm">
                    {toolStatus && (
                      <div className="mb-2 inline-flex items-center gap-1.5 rounded-full bg-primary/12 px-2.5 py-1 text-xs font-medium text-primary">
                        <Wrench className="size-3" />
                        {toolStatus}
                      </div>
                    )}
                    {liveText ? (
                      <Markdown text={liveText} />
                    ) : (
                      !toolStatus && <span className="text-muted-foreground">思考中…</span>
                    )}
                  </div>
                </div>
              )}

              {error && (
                <div className="mb-5 text-sm text-destructive">⚠️ {error}</div>
              )}
            </div>
          )}
        </div>

        <div className="border-t bg-background/80 px-4 py-3 backdrop-blur sm:px-6">
          <div className="mx-auto flex max-w-3xl items-end gap-2">
            <textarea
              ref={inputRef}
              rows={1}
              placeholder="输入问题，Enter 发送，Shift+Enter 换行"
              className="max-h-40 flex-1 resize-none rounded-xl border border-input bg-card px-3.5 py-2.5 text-sm shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
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
            <Button size="icon" className="h-11 w-11 rounded-xl" disabled={streaming} onClick={submit}>
              <Send className="size-4" />
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

function Bubble({ role, content }: { role: string; content: string }) {
  const isUser = role === "user";
  return (
    <div className={cn("mb-5 flex", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[90%] px-4 py-3 text-sm",
          isUser
            ? "rounded-2xl rounded-br-md bg-primary text-primary-foreground whitespace-pre-wrap"
            : "rounded-2xl rounded-bl-md border bg-card",
        )}
      >
        {isUser ? content : <Markdown text={content} />}
      </div>
    </div>
  );
}
