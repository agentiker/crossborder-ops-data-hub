import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { api, sendChat, type Message, type ThinkingStep } from "@/api";
import { useMe, useShell } from "@/components/shell/AppShell";
import { Markdown } from "@/components/Markdown";
import { WelcomeScreen } from "@/components/chat/WelcomeScreen";
import { Composer } from "@/components/chat/Composer";
import { Bubble } from "@/components/chat/Bubble";
import { ThinkingSteps } from "@/components/chat/ThinkingSteps";

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

// 客户端的逐条元信息（时间戳 + 助手真实耗时），与 messages 等长平行存放。
// Message 类型是全局共享冻结文件，不动它；这些纯展示字段放这里。
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
  const me = useMe();
  const { refreshConversations } = useShell();
  const convId = id ? Number(id) : null;

  const [messages, setMessages] = useState<Message[]>([]);
  const [metas, setMetas] = useState<MsgMeta[]>([]);
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
        // 历史消息没有客户端时间戳/耗时，留空即可（hover 时不显示时间，仅复制）。
        setMetas(d.messages.map(() => ({})));
      })
      .catch(() => {
        setMessages([]);
        setMetas([]);
      });
  }, [convId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, liveText, liveSteps]);

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

  return (
    <div className="flex h-full flex-col">
      {isEmpty ? (
        <WelcomeScreen
          scopeLabel={me?.scope_label}
          presets={PRESETS}
          quickCards={QUICK_CARDS}
          onSend={send}
          streaming={streaming}
        />
      ) : (
        <>
          <div ref={scrollRef} className="flex-1 overflow-y-auto">
            <div className="mx-auto max-w-3xl px-4 py-8 sm:px-6">
              {messages.map((m, i) => (
                <Bubble
                  key={m.id ?? i}
                  role={m.role}
                  content={m.content}
                  steps={m.steps}
                  ts={metas[i]?.ts}
                  workedMs={metas[i]?.workedMs}
                />
              ))}

              {streaming && (
                <div className="mb-8">
                  {liveSteps.length > 0 && <ThinkingSteps steps={liveSteps} live />}
                  {liveText ? (
                    <div className="text-sm leading-7">
                      <Markdown text={liveText} />
                      <span className="ml-0.5 inline-block h-4 w-1.5 translate-y-0.5 animate-pulse bg-foreground align-middle" />
                    </div>
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
