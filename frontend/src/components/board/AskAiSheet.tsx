import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { sendChat, type Message, type ThinkingStep } from "@/api";
import { ChatMessage, type ThinkingStep as ForkStep } from "@/components/chat/ChatMessage";
import { ChatInput } from "@/components/chat/ChatInput";
import { cn } from "@/lib/utils";

// 看板内联「问 AI」抽屉：点卡片「问 AI」就地从底部弹起，自动发出该卡片的疑问、流式出答案，
// 并支持老板**继续追问、连续对话**（底部复用聊天页的 ChatInput）；看完叉掉/下拉/Esc 关回看板。
//
// 复用现成基础设施，不重造：
// - sendChat(message, conversationId, signal)：首问 conversationId=null 开新会话并落库，
//   meta 事件回传 conversation_id，后续追问续传同一 id → 一段连续对话（关掉后在对话页历史可见）。
// - ChatMessage：现成用户气泡 / 助手裸文 / 流式光标 / 折叠步骤。
// - ChatInput：现成输入框（自增高 / 发送态 / 移动端键盘），抽屉底部直接嵌。
//
// 版式自适应：移动端=底部 sheet（占屏 3/4，可上拉铺满），桌面=居中弹窗。

// ops_* 工具中文名（与 ChatPage 对齐；抽屉自带一份，避免跨文件耦合私有常量）。
const TOOL_LABELS: Record<string, string> = {
  ops_overview: "经营概览",
  ops_orders_summary: "订单汇总",
  ops_orders_trend: "订单趋势",
  ops_top_skus: "爆款榜",
  ops_low_stock: "断货风险",
  ops_fulfillments_pending: "待发货",
  ops_business_rules: "业务规则",
  ops_report: "经营报告",
};

function adaptSteps(steps?: ThinkingStep[]): ForkStep[] {
  return (steps || []).map((s) => ({
    type: "api" as const,
    label: s.label,
    status: s.done ? ("done" as const) : ("running" as const),
  }));
}

export function AskAiSheet({
  question,
  onClose,
}: {
  question: string;
  onClose: () => void;
}) {
  // 多轮对话状态：已落定的消息 + 当前流式回合。
  const [messages, setMessages] = useState<Message[]>([]);
  const [liveText, setLiveText] = useState("");
  const [liveSteps, setLiveSteps] = useState<ThinkingStep[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 移动端 sheet 高度（vh）。两个吸附档：75（默认）/ 95（铺满）。拖拽时跟手设连续值，松手吸附。
  const COLLAPSED = 75;
  const EXPANDED = 95;
  const [sheetVh, setSheetVh] = useState(COLLAPSED);
  const dragRef = useRef<{ startY: number; startVh: number } | null>(null);

  const scrollRef = useRef<HTMLDivElement>(null);
  const convIdRef = useRef<number | null>(null); // 连续对话续传的会话 id
  const abortRef = useRef<AbortController | null>(null);
  const sentFirstRef = useRef(false); // 防 StrictMode/重渲染重复发首问

  // 顶部 handle 区的拖拽：往上拖 → 高度增大（铺满方向），松手吸附到最近档位。
  // 用 vh 计算（拖拽位移 / 视口高 * 100），夹紧在 [COLLAPSED, EXPANDED]。
  // movedRef 区分「拖拽」与「轻点」：拖过则吞掉随后的 click（touchend 后浏览器仍补发 click）。
  const movedRef = useRef(false);
  const onDragStart = (y: number) => {
    dragRef.current = { startY: y, startVh: sheetVh };
    movedRef.current = false;
  };
  const onDragMove = (y: number) => {
    const d = dragRef.current;
    if (!d) return;
    if (Math.abs(y - d.startY) > 4) movedRef.current = true;
    const deltaVh = ((d.startY - y) / window.innerHeight) * 100; // 上拖为正
    const next = Math.max(COLLAPSED, Math.min(EXPANDED, d.startVh + deltaVh));
    setSheetVh(next);
  };
  const onDragEnd = () => {
    if (!dragRef.current) return;
    dragRef.current = null;
    // 吸附到最近档位（中点为界）。
    setSheetVh((v) => (v >= (COLLAPSED + EXPANDED) / 2 ? EXPANDED : COLLAPSED));
  };
  const onHandleClick = () => {
    if (movedRef.current) {
      movedRef.current = false;
      return; // 刚才是拖拽，不当点击处理
    }
    setSheetVh((v) => (v >= (COLLAPSED + EXPANDED) / 2 ? COLLAPSED : EXPANDED));
  };
  const expanded = sheetVh >= (COLLAPSED + EXPANDED) / 2;

  // 发一轮（首问或追问）：复用 sendChat，续传 convIdRef。
  async function send(text: string) {
    if (streaming || !text.trim()) return;
    setError(null);
    setMessages((m) => [...m, { role: "user", content: text }]);
    setStreaming(true);
    setLiveText("");
    setLiveSteps([]);

    const ctrl = new AbortController();
    abortRef.current = ctrl;
    let acc = "";
    let steps: ThinkingStep[] = [];
    try {
      for await (const ev of sendChat(text, convIdRef.current, ctrl.signal)) {
        if (ev.type === "meta") {
          convIdRef.current = ev.conversation_id; // 记下会话 id，后续追问续传
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
              !marked && s.name === ev.name && !s.done ? ((marked = true), { ...s, done: true }) : s,
            );
          }
          setLiveSteps(steps);
        } else if (ev.type === "error") {
          setError(ev.message);
        }
      }
    } catch (e) {
      if (!ctrl.signal.aborted) setError(String(e));
    }
    if (ctrl.signal.aborted) return; // 关闭/卸载则不落定
    steps = steps.map((s) => ({ ...s, done: true }));
    setMessages((m) => [
      ...m,
      { role: "assistant", content: acc, steps: steps.length ? steps : undefined },
    ]);
    setLiveText("");
    setLiveSteps([]);
    setStreaming(false);
  }

  // 打开即发出卡片带来的首问（一次性）。question 变化（换卡片再问）重置为新会话。
  useEffect(() => {
    sentFirstRef.current = false;
    convIdRef.current = null;
    setMessages([]);
    setLiveText("");
    setLiveSteps([]);
    setError(null);
    if (!sentFirstRef.current && question) {
      sentFirstRef.current = true;
      void send(question);
    }
    return () => abortRef.current?.abort();
    // 仅随 question 触发；send 闭包用 ref 读最新会话 id，无需入依赖。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [question]);

  // 锁背景滚动：mount-only，不依赖 onClose（否则父级重渲染→effect 重跑→cleanup 还原成已
  // 是 "hidden" 的旧值→关闭后页面卡死）。
  useEffect(() => {
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = "";
    };
  }, []);

  // Esc 关闭（单独 effect，可随 onClose 更新，不碰滚动锁）。
  useEffect(() => {
    const onEsc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onEsc);
    return () => window.removeEventListener("keydown", onEsc);
  }, [onClose]);

  // 新消息/流式追加时滚到底，让最新内容可见。
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, liveText, liveSteps, streaming]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="AI 解答"
      onClick={onClose}
      className="fixed inset-0 z-[80] flex items-end justify-center bg-black/50 sm:items-center sm:p-4"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{ height: `${sheetVh}vh` }}
        className={cn(
          "flex w-full flex-col rounded-t-2xl bg-card shadow-xl",
          // 拖拽中不加 transition（跟手）；松手吸附时由 onDragEnd 触发的 state 变化走过渡。
          dragRef.current ? "" : "transition-[height] duration-300",
          // 桌面：忽略 sheetVh 高度，回居中弹窗。
          "sm:!h-auto sm:max-h-[80vh] sm:max-w-lg sm:rounded-2xl",
        )}
      >
        {/* 顶部条：drag handle（移动端可拖拽改高度 + 点击切换档位） + 标题 + 关闭 */}
        <div className="shrink-0 border-b border-border-shallow">
          <div
            role="button"
            aria-label={expanded ? "收起" : "展开铺满"}
            onClick={onHandleClick}
            onTouchStart={(e) => onDragStart(e.touches[0].clientY)}
            onTouchMove={(e) => onDragMove(e.touches[0].clientY)}
            onTouchEnd={onDragEnd}
            className="mx-auto flex w-full cursor-grab touch-none justify-center py-3 active:cursor-grabbing sm:hidden"
          >
            <span className="h-1 w-10 rounded-full bg-border" />
          </div>
          <div className="flex items-center justify-between px-5 pb-2.5 pt-1 sm:pt-3">
            <div className="flex items-center gap-2">
              <h3 className="text-base font-semibold text-foreground">AI 解答</h3>
              <span className="hidden text-xs text-foreground-tertiary sm:inline">
                可继续追问
              </span>
            </div>
            <button
              type="button"
              aria-label="关闭"
              onClick={onClose}
              className="-m-1 rounded-lg p-1 text-foreground-secondary transition-colors hover:bg-fill-shallow hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [@media(pointer:coarse)]:p-2"
            >
              <X className="size-5" />
            </button>
          </div>
        </div>

        {/* 消息区：多轮问答 + 当前流式回合。可上下滑动。 */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto px-3 py-4">
          <div className="mx-auto flex max-w-full flex-col gap-6">
            {messages.map((m, i) => (
              <ChatMessage
                key={m.id ?? i}
                role={m.role === "user" ? "user" : "assistant"}
                content={m.content}
                workingTime={m.role === "user" ? undefined : m.steps?.length ? "运行过程" : undefined}
                thinkingSteps={m.role === "user" ? undefined : adaptSteps(m.steps)}
              />
            ))}
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

        {/* 输入区：复用聊天页 ChatInput，支持继续追问。底部留移动浏览器工具栏安全区。 */}
        <div className="shrink-0 border-t border-border-shallow px-3 pt-2 pb-[max(0.5rem,calc(env(safe-area-inset-bottom)+0.25rem))]">
          <ChatInput onSend={send} disabled={streaming} placeholder="继续追问……" />
        </div>
      </div>
    </div>
  );
}
