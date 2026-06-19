import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowUp, Check, ChevronDown, Lightbulb, Loader2 } from "lucide-react";
import { api, sendChat, type Message, type ThinkingStep } from "@/api";
import { useShell } from "@/components/shell/AppShell";
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

// 建议 chips（首页命令栏，横滚）。
const PRESETS = [
  "最近 7 天整体经营情况怎么样？",
  "近 30 天卖得最好的 10 个商品",
  "现在有多少待发货订单，有超时的吗？",
  "有哪些 SKU 快断货了？",
];

// 首页 2 张真实快捷卡：点击直接发起对话（展示 agent 取数）。
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

// 按本机时刻给时段名 + 氛围标签 + 问候 + emoji（StoreClaw 式时段徽标 ● 两段式）。
function timeOfDay(): { period: string; tag: string; greeting: string; emoji: string } {
  const h = new Date().getHours();
  if (h < 5) return { period: "夜深", tag: "夜猫子档", greeting: "夜深了，看看今天的生意", emoji: "🌙" };
  if (h < 8) return { period: "清晨", tag: "醒得早", greeting: "早，先看看昨天的收成", emoji: "🌅" };
  if (h < 11) return { period: "上午", tag: "开工时段", greeting: "上午好，今天想看哪块数据", emoji: "☀️" };
  if (h < 13) return { period: "午后", tag: "午间小憩", greeting: "午间好，店铺跑得怎么样", emoji: "🍵" };
  if (h < 18) return { period: "下午", tag: "下午场", greeting: "下午好，盯一眼经营节奏", emoji: "📊" };
  if (h < 23) return { period: "傍晚", tag: "收工盘点", greeting: "傍晚好，盘点今天这一单单", emoji: "🌆" };
  return { period: "夜深", tag: "夜猫子档", greeting: "夜深了，看看今天的生意", emoji: "🌙" };
}

export function ChatPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { refreshConversations } = useShell();
  const convId = id ? Number(id) : null;

  const [messages, setMessages] = useState<Message[]>([]);
  const [liveText, setLiveText] = useState("");
  const [liveSteps, setLiveSteps] = useState<ThinkingStep[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const loadedRef = useRef<number | null>(null);
  const [tod] = useState(timeOfDay);

  // 会话切换由 URL /c/:id 驱动；loadedRef 防止「发消息后导航到新 id」时重复加载。
  useEffect(() => {
    if (convId === null) {
      if (loadedRef.current !== null) {
        setMessages([]);
        setError(null);
        loadedRef.current = null;
      }
      return;
    }
    if (loadedRef.current === convId) return;
    loadedRef.current = convId;
    setError(null);
    api
      .conversation(convId)
      .then((d) => setMessages(d.messages))
      .catch(() => setMessages([]));
  }, [convId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, liveText, liveSteps]);

  async function send(text: string) {
    if (streaming) return;
    setError(null);
    setMessages((m) => [...m, { role: "user", content: text }]);
    setStreaming(true);
    setLiveText("");
    setLiveSteps([]);

    let acc = "";
    let steps: ThinkingStep[] = [];
    try {
      for await (const ev of sendChat(text, convId)) {
        if (ev.type === "meta") {
          // 新会话：标记已加载并把 URL 切到该会话（loadedRef 防重载）。
          if (convId === null) {
            loadedRef.current = ev.conversation_id;
            navigate(`/c/${ev.conversation_id}`, { replace: true });
          }
        } else if (ev.type === "delta") {
          acc += ev.text;
          setLiveText(acc);
        } else if (ev.type === "tool") {
          const label = TOOL_LABELS[ev.name] || ev.name;
          if (ev.status === "running") {
            if (!steps.some((s) => s.name === ev.name && !s.done)) {
              steps = [...steps, { name: ev.name, label, done: false }];
            }
          } else {
            let marked = false;
            steps = steps.map((s) =>
              !marked && s.name === ev.name && !s.done
                ? ((marked = true), { ...s, done: true })
                : s,
            );
          }
          setLiveSteps(steps);
        } else if (ev.type === "error") {
          setError(ev.message);
        }
      }
    } catch (e) {
      setError(String(e));
    }

    steps = steps.map((s) => ({ ...s, done: true }));
    setMessages((m) => [
      ...m,
      { role: "assistant", content: acc, steps: steps.length ? steps : undefined },
    ]);
    setLiveText("");
    setLiveSteps([]);
    setStreaming(false);
    refreshConversations();
  }

  const isEmpty = messages.length === 0 && !streaming;

  return (
    <div className="flex h-full flex-col">
      {isEmpty ? (
        // ── 首页命令栏 launcher ──────────────────────────────────────────────
        <div className="flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-2xl animate-fade-up px-6 pb-16 pt-[14vh]">
            <span className="inline-flex items-center gap-2 rounded-full border border-border-shallow bg-card px-3 py-1 text-xs font-medium text-foreground-secondary">
              <span className="relative flex size-2">
                <span className="inline-flex size-full rounded-full bg-positive animate-pulse-dot" />
              </span>
              {tod.period} · {tod.tag}
            </span>

            <h1 className="mt-5 text-4xl font-bold leading-[1.15] tracking-tight sm:text-[2.75rem]">
              {tod.greeting}
              <span className="ml-2 inline-block animate-wiggle">{tod.emoji}</span>
            </h1>
            <p className="mt-2 text-sm text-foreground-secondary">
              问我店铺的 GMV、订单、爆款、库存与待发货——按你的权限范围答。
            </p>

            <div className="mt-6">
              <Composer onSend={send} streaming={streaming} autoFocus size="home" />
            </div>

            <div className="mt-4 flex items-center gap-2 overflow-x-auto pb-0.5 scrollbar-hide">
              <Lightbulb className="size-3.5 shrink-0 text-foreground-tertiary" />
              {PRESETS.map((p) => (
                <button
                  key={p}
                  onClick={() => send(p)}
                  className="shrink-0 whitespace-nowrap rounded-full border border-border bg-card px-3.5 py-1.5 text-xs text-foreground-secondary transition-colors hover:border-foreground/30 hover:text-foreground"
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
                  className="group rounded-2xl border border-border-shallow bg-card p-4 text-left shadow-sm transition-all hover:-translate-y-0.5 hover:border-border hover:shadow-md"
                >
                  <div className="flex items-center justify-between">
                    <span className="text-base font-semibold">{c.title}</span>
                    <ArrowUp className="size-4 rotate-45 text-foreground-tertiary transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5" />
                  </div>
                  <p className="mt-1 text-xs text-foreground-secondary">{c.desc}</p>
                </button>
              ))}
            </div>
          </div>
        </div>
      ) : (
        // ── 对话视图 ─────────────────────────────────────────────────────────
        <>
          <div ref={scrollRef} className="flex-1 overflow-y-auto">
            <div className="mx-auto max-w-3xl px-4 py-8 sm:px-6">
              {messages.map((m, i) => (
                <Bubble key={m.id ?? i} role={m.role} content={m.content} steps={m.steps} />
              ))}

              {streaming && (
                <div className="mb-8">
                  {liveSteps.length > 0 && <ThinkingSteps steps={liveSteps} live />}
                  {liveText ? (
                    <Markdown text={liveText} />
                  ) : (
                    liveSteps.length === 0 && (
                      <span className="inline-flex items-center gap-2 text-sm text-foreground-tertiary">
                        <Loader2 className="size-3.5 animate-spin" />
                        思考中…
                      </span>
                    )
                  )}
                </div>
              )}

              {error && <div className="mb-6 text-sm text-destructive">⚠️ {error}</div>}
            </div>
          </div>

          <div className="bg-background/80 px-4 pb-3 pt-1 backdrop-blur sm:px-6">
            <div className="mx-auto max-w-3xl">
              <Composer onSend={send} streaming={streaming} />
              <p className="mt-2 text-center text-xs text-foreground-tertiary">
                AI 可能出错，请核对重要数据。
              </p>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// 助手消息的工具调用轨迹。live=实时流式（转圈/打勾，常展开）；否则=历史折叠（点击展开）。
function ThinkingSteps({ steps, live }: { steps: ThinkingStep[]; live?: boolean }) {
  const [open, setOpen] = useState(!!live);
  const allDone = steps.every((s) => s.done);

  return (
    <div className="mb-2">
      <button
        onClick={() => setOpen((o) => !o)}
        className="inline-flex items-center gap-1.5 rounded-full bg-fill px-2.5 py-1 text-xs font-medium text-foreground-secondary transition-colors hover:text-foreground"
      >
        <ChevronDown className={cn("size-3 transition-transform", !open && "-rotate-90")} />
        {live && !allDone ? "正在查询数据…" : `已查询 ${steps.length} 项数据`}
      </button>
      {open && (
        <ul className="mt-1.5 space-y-1 border-l border-border pl-3.5">
          {steps.map((s, i) => (
            <li
              key={`${s.name}-${i}`}
              className="flex items-center gap-2 text-xs text-foreground-secondary"
            >
              {s.done ? (
                <Check className="size-3.5 shrink-0 text-positive" />
              ) : (
                <Loader2 className="size-3.5 shrink-0 animate-spin" />
              )}
              {s.label}
            </li>
          ))}
        </ul>
      )}
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
        "flex items-end gap-2 rounded-2xl border border-input bg-card shadow-sm transition-shadow",
        "focus-within:border-foreground/20 focus-within:shadow-md",
        size === "home" ? "px-3 py-2.5" : "px-3 py-2",
      )}
    >
      <textarea
        ref={ref}
        rows={1}
        autoFocus={autoFocus}
        placeholder="问我店铺经营数据，Enter 发送，Shift+Enter 换行"
        className={cn(
          "max-h-40 flex-1 resize-none bg-transparent px-1.5 py-1.5 text-sm placeholder:text-foreground-tertiary focus-visible:outline-none",
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
      {size === "home" ? (
        <Button className="h-10 shrink-0 gap-1.5 rounded-xl px-4" disabled={streaming} onClick={submit}>
          发送
          <ArrowUp className="size-4" />
        </Button>
      ) : (
        <Button
          size="icon"
          className="h-9 w-9 shrink-0 rounded-xl"
          disabled={streaming}
          onClick={submit}
          aria-label="发送"
        >
          <ArrowUp className="size-4" />
        </Button>
      )}
    </div>
  );
}

function Bubble({ role, content, steps }: { role: string; content: string; steps?: ThinkingStep[] }) {
  const isUser = role === "user";
  if (isUser) {
    // 用户：白底带边框，右下角不圆（StoreClaw 风），右对齐。
    return (
      <div className="mb-8 flex justify-end">
        <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-none border border-border-shallow bg-card px-4 py-2.5 text-sm shadow-sm">
          {content}
        </div>
      </div>
    );
  }
  // 助手：可折叠工具轨迹 + 无气泡裸文（StoreClaw 风）。
  return (
    <div className="mb-8">
      {steps && steps.length > 0 && <ThinkingSteps steps={steps} />}
      <Markdown text={content} />
    </div>
  );
}
