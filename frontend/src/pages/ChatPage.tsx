import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, sendChat, type Message, type ThinkingStep } from "@/api";
import { useMe, useShell } from "@/components/shell/AppShell";
import { WelcomeScreen } from "@/components/chat/WelcomeScreen";
import { ChatInput } from "@/components/chat/ChatInput";
import { QuickActions } from "@/components/chat/QuickActions";
import { ChatMessage, type ThinkingStep as ForkStep } from "@/components/chat/ChatMessage";

// ops_* 工具的中文展示名（与后端 agent_tools 对齐）。
const TOOL_LABELS: Record<string, string> = {
  ops_overview: "经营概览",
  ops_orders_summary: "订单汇总",
  ops_orders_trend: "订单趋势",
  ops_top_skus: "爆款榜",
  ops_low_stock: "断货风险",
  ops_fulfillments_pending: "待发货",
  ops_business_rules: "业务规则",
};

// 建议 chips（首页命令栏，横滚）。
const PRESETS = [
  "最近 7 天整体经营情况怎么样？",
  "近 30 天卖得最好的 10 个商品",
  "现在有多少待发货订单，有超时的吗？",
  "有哪些 SKU 快断货了？",
];

// ── 数据适配层（解耦薄壳，将来换 agent 框架后端只动这里）──
// 当前 SSE 的 tool 事件只有 {name,label,done}；fork ChatMessage 吃 {type,label,detail?,status}。
// 我方 6 个工具全是 ops_* 取数调用 → type 一律归 'api'（Database 绿标，语义如实）；
// detail 无真实来源故留空（不造假）；status 由 done 降级映射。
function adaptSteps(steps?: ThinkingStep[]): ForkStep[] {
  return (steps || []).map((s) => ({
    type: "api" as const,
    label: s.label,
    status: s.done ? ("done" as const) : ("running" as const),
  }));
}

// 客户端如实计时 → fork 的「Worked for…」折叠标题文案。
function fmtWorked(ms: number): string {
  const s = Math.round(ms / 1000);
  if (s < 60) return `用时 ${s} 秒`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return r ? `用时 ${m} 分 ${r} 秒` : `用时 ${m} 分`;
}

// 助手消息的折叠标题：有真实耗时→用时；仅有历史步骤无耗时→中性「运行过程」（不编造时长）；无步骤→不显示折叠区。
function workingLabel(steps?: ThinkingStep[], workedMs?: number): string | undefined {
  if (workedMs != null) return fmtWorked(workedMs);
  if (steps && steps.length) return "运行过程";
  return undefined;
}

// 客户端逐条元信息（时间戳 + 助手真实耗时），与 messages 等长平行存放。
interface MsgMeta {
  ts?: string;
  workedMs?: number;
}

function nowTs(): string {
  return new Date().toLocaleString("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function ChatPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const me = useMe();
  const { refreshConversations } = useShell();
  const convId = id ? Number(id) : null;

  // 看板「问 AI」深链：?ask=<问题> 预填进输入框（不自动发，老板可改可补）。
  // 读后即清掉 query（replace，不留历史），避免刷新/返回重复预填。
  const [prefill, setPrefill] = useState<string>("");
  useEffect(() => {
    const ask = searchParams.get("ask");
    if (ask) {
      setPrefill(ask);
      const next = new URLSearchParams(searchParams);
      next.delete("ask");
      setSearchParams(next, { replace: true });
    }
  }, [searchParams, setSearchParams]);

  const [messages, setMessages] = useState<Message[]>([]);
  const [metas, setMetas] = useState<MsgMeta[]>([]);
  const [convTitle, setConvTitle] = useState<string>("");
  const [liveText, setLiveText] = useState("");
  const [liveSteps, setLiveSteps] = useState<ThinkingStep[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const loadedRef = useRef<number | null>(null);

  // 会话切换由 URL /c/:id 驱动；loadedRef 防止「发消息后导航到新 id」时重复加载。
  useEffect(() => {
    if (convId === null) {
      if (loadedRef.current !== null) {
        setMessages([]);
        setMetas([]);
        setConvTitle("");
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
      .then((d) => {
        setMessages(d.messages);
        setConvTitle(d.title || "");
        // 历史消息没有客户端时间戳/耗时，留空即可。
        setMetas(d.messages.map(() => ({})));
      })
      .catch(() => {
        setMessages([]);
        setMetas([]);
      });
  }, [convId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, liveText, liveSteps, streaming]);

  async function send(text: string) {
    if (streaming) return;
    setError(null);
    setMessages((m) => [...m, { role: "user", content: text }]);
    setMetas((x) => [...x, { ts: nowTs() }]);
    setStreaming(true);
    setLiveText("");
    setLiveSteps([]);

    const startedAt = Date.now();
    let acc = "";
    let steps: ThinkingStep[] = [];
    try {
      for await (const ev of sendChat(text, convId)) {
        if (ev.type === "meta") {
          if (!convTitle) setConvTitle(ev.title || "");
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
    // 客户端如实计时：流式开始→结束的真实耗时，仅当有工具步骤时才展示「用时」。
    const workedMs = steps.length ? Date.now() - startedAt : undefined;
    setMessages((m) => [
      ...m,
      { role: "assistant", content: acc, steps: steps.length ? steps : undefined },
    ]);
    setMetas((x) => [...x, { ts: nowTs(), workedMs }]);
    setLiveText("");
    setLiveSteps([]);
    setStreaming(false);
    refreshConversations();
  }

  const isEmpty = messages.length === 0 && !streaming;
  // 当前 Me 无姓名字段：boss 称「老板」，operator 用其范围标签；兜底「老板」。
  const who = me?.is_boss ? "老板" : me?.scope_label?.trim() || "老板";

  // ── 首页 launcher（空态）：照 fork App 的 chat 分支组合 WelcomeScreen + ChatInput + QuickActions ──
  if (isEmpty) {
    return (
      <section className="flex flex-col h-full">
        <div className="flex-1 flex flex-col items-center justify-center pb-24 px-4 sm:px-6 md:px-8">
          <div className="w-full max-w-[1038px] flex flex-col items-center">
            <WelcomeScreen userName={who} />
            <ChatInput
              onSend={send}
              disabled={streaming}
              initialValue={prefill}
              onInitialValueConsumed={() => setPrefill("")}
            />
            <QuickActions presets={PRESETS} onPick={send} />
            {error && <p className="mt-4 text-sm text-destructive">⚠️ {error}</p>}
          </div>
        </div>
      </section>
    );
  }

  // ── 会话视图：照 fork ChatPage 版式（h-[68px] 头 + max-w-4xl 消息区 gap-9 + sticky 输入）──
  return (
    <section className="flex flex-col h-full">
      {/* Header */}
      <header className="flex items-center justify-between gap-2 px-4 h-[68px] shrink-0 border-b border-border-shallow sticky z-50 top-0 bg-background-solid">
        <div className="flex items-center gap-1 flex-1 min-w-0">
          <div className="flex-1 flex flex-col justify-center min-w-0">
            <h1 className="text-lg font-medium text-foreground leading-6 truncate">
              {convTitle || "新对话"}
            </h1>
          </div>
        </div>
      </header>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-4xl px-0 py-3 pb-24 md:py-6 md:px-4 md:pb-32">
          <div className="px-1.5 md:px-6">
            <div className="flex flex-col gap-9 pb-20 pt-2 transition-[min-height] duration-500 ease-out mx-auto w-full max-w-4xl">
              {messages.map((m, i) => (
                <ChatMessage
                  key={m.id ?? i}
                  role={m.role === "user" ? "user" : "assistant"}
                  content={m.content}
                  timestamp={metas[i]?.ts}
                  workingTime={
                    m.role === "user" ? undefined : workingLabel(m.steps, metas[i]?.workedMs)
                  }
                  thinkingSteps={m.role === "user" ? undefined : adaptSteps(m.steps)}
                />
              ))}

              {/* 流式进行中的助手回合 */}
              {streaming && (
                <ChatMessage
                  role="assistant"
                  content={liveText}
                  workingTime={liveSteps.length ? "运行中…" : "思考中…"}
                  thinkingSteps={adaptSteps(liveSteps)}
                  isStreaming
                  defaultThinkingOpen
                />
              )}

              {error && <div className="px-2 text-sm text-destructive">⚠️ {error}</div>}
            </div>
          </div>
        </div>
      </div>

      {/* Input */}
      <div className="sticky bottom-0 bg-background-solid px-4 pb-3 pt-1">
        <ChatInput onSend={send} disabled={streaming} />
        <p className="mt-2 text-center text-xs text-foreground-tertiary">
          AI 可能出错，请核对重要数据。
        </p>
      </div>
    </section>
  );
}
