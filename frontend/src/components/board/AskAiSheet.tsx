import { useEffect, useRef, useState } from "react";
import { X, ChevronUp } from "lucide-react";
import { sendChat, type ThinkingStep } from "@/api";
import { ChatMessage, type ThinkingStep as ForkStep } from "@/components/chat/ChatMessage";
import { cn } from "@/lib/utils";

// 看板内联「问 AI」抽屉：点卡片「问 AI」就地从底部弹起,自动发问、流式出答案,
// 看完可叉掉/下拉/Esc 关闭回看板,不必跳转到对话页(老板手机上更顺手)。
//
// 复用现成基础设施,不重造:
// - sendChat(question, null, signal)：conversationId=null 即开新会话并落库 → 问答存入历史,
//   关掉抽屉后仍能在对话页找到(与跳转方案一致,只是入口换成就地抽屉)。
// - ChatMessage：现成用户气泡 / 助手裸文 / 流式光标 / 折叠步骤,直接渲染。
//
// 版式自适应:移动端=底部 sheet(占屏 3/4,可上拉铺满),桌面=居中弹窗。

// ops_* 工具中文名(与 ChatPage 对齐;抽屉自带一份,避免跨文件耦合私有常量)。
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

function adaptSteps(steps: ThinkingStep[]): ForkStep[] {
  return steps.map((s) => ({
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
  const [liveText, setLiveText] = useState("");
  const [steps, setSteps] = useState<ThinkingStep[]>([]);
  const [streaming, setStreaming] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false); // 移动端上拉铺满
  const scrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  // 打开即对 question 跑一次问答。question 变化(换个卡片再问)则重跑。
  useEffect(() => {
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    let acc = "";
    let curSteps: ThinkingStep[] = [];
    setLiveText("");
    setSteps([]);
    setError(null);
    setStreaming(true);

    (async () => {
      try {
        for await (const ev of sendChat(question, null, ctrl.signal)) {
          if (ev.type === "delta") {
            acc += ev.text;
            setLiveText(acc);
          } else if (ev.type === "tool") {
            const label = TOOL_LABELS[ev.name] || ev.name;
            if (ev.status === "running") {
              if (!curSteps.some((s) => s.name === ev.name && !s.done)) {
                curSteps = [...curSteps, { name: ev.name, label, done: false }];
              }
            } else {
              let marked = false;
              curSteps = curSteps.map((s) =>
                !marked && s.name === ev.name && !s.done
                  ? ((marked = true), { ...s, done: true })
                  : s,
              );
            }
            setSteps(curSteps);
          } else if (ev.type === "error") {
            setError(ev.message);
          }
        }
      } catch (e) {
        // abort 是正常关闭路径,不报错
        if (!ctrl.signal.aborted) setError(String(e));
      }
      // 已 abort(抽屉关闭/换问题)则不再写状态,避免对已卸载/旧实例的无谓 set。
      if (ctrl.signal.aborted) return;
      setSteps((s) => s.map((x) => ({ ...x, done: true })));
      setStreaming(false);
    })();

    return () => ctrl.abort();
  }, [question]);

  // 打开期间锁背景滚动 + Esc 关闭(照搬 AdSpendDialog 经验)。
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onEsc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onEsc);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener("keydown", onEsc);
    };
  }, [onClose]);

  // 流式追加时滚到底,让最新内容可见。
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [liveText, steps, streaming]);

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
        className={cn(
          "flex w-full flex-col rounded-t-2xl bg-card shadow-xl transition-[height] duration-300",
          "sm:h-auto sm:max-h-[80vh] sm:max-w-lg sm:rounded-2xl",
          expanded ? "h-[95vh]" : "h-[75vh]",
        )}
      >
        {/* 顶部条:drag handle(移动端,点击上拉/收起) + 标题 + 关闭 */}
        <div className="shrink-0 border-b border-border-shallow">
          <button
            type="button"
            aria-label={expanded ? "收起" : "展开铺满"}
            onClick={() => setExpanded((v) => !v)}
            className="mx-auto flex w-full justify-center py-2 sm:hidden"
          >
            <span className="h-1 w-10 rounded-full bg-border" />
          </button>
          <div className="flex items-center justify-between px-5 pb-2.5 pt-1 sm:pt-3">
            <div className="flex items-center gap-2">
              <h3 className="text-base font-semibold text-foreground">AI 解答</h3>
              <span className="hidden text-xs text-foreground-tertiary sm:inline">
                依据业务规则解答
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

        {/* 内容:问题气泡 + 流式答案。底部留移动浏览器工具栏安全区。 */}
        <div
          ref={scrollRef}
          className="flex-1 overflow-y-auto px-3 py-4 pb-[max(1rem,calc(env(safe-area-inset-bottom)+0.75rem))]"
        >
          <div className="mx-auto flex max-w-full flex-col gap-6">
            <ChatMessage role="user" content={question} />
            <ChatMessage
              role="assistant"
              content={liveText}
              workingTime={
                streaming ? (steps.length ? "运行中…" : "思考中…") : undefined
              }
              thinkingSteps={adaptSteps(steps)}
              isStreaming={streaming}
              defaultThinkingOpen={streaming}
            />
            {error && <div className="px-2 text-sm text-destructive">⚠️ {error}</div>}
          </div>
        </div>

        {/* 移动端展开提示(仅未铺满时显示,引导可上拉) */}
        {!expanded && (
          <button
            type="button"
            onClick={() => setExpanded(true)}
            className="flex shrink-0 items-center justify-center gap-1 border-t border-border-shallow py-1.5 text-xs text-foreground-tertiary sm:hidden"
          >
            <ChevronUp className="h-3.5 w-3.5" />
            上拉铺满
          </button>
        )}
      </div>
    </div>
  );
}
