import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { type ThinkingStep } from "@/api";
import { ChatMessage, type ThinkingStep as ForkStep } from "@/components/chat/ChatMessage";
import { ChatInput } from "@/components/chat/ChatInput";
import { cn } from "@/lib/utils";
import { openAskSession, askFollowUp, useAskSession } from "@/components/board/useAskAiStore";

// 看板内联「问 AI」抽屉：点卡片「问 AI」就地从底部弹起，自动发出该卡片的疑问、流式出答案，
// 并支持老板**继续追问、连续对话**（底部复用聊天页的 ChatInput）；看完叉掉/下拉/Esc 关回看板。
//
// 会话状态住在 module 级 store（useAskAiStore），抽屉只是它的订阅视图：
// - 关掉再开同一卡片 → 命中已有会话，问答还在（不重发首问）。
// - 回答生成慢时关掉 → 后台继续跑完（关闭不再 abort），回来能看到完整结果。
// - 复用 sendChat 流式 / ChatMessage 渲染 / ChatInput 输入，不重造数据层。
//
// 版式自适应：移动端=底部 sheet（占屏 3/4，可上拉铺满），桌面=居中弹窗。

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
  // 对话状态来自持久 store（关闭抽屉不销毁、流式后台继续）。
  const { messages, liveText, liveSteps, streaming, error } = useAskSession(question);
  // 移动端 sheet：固定高度 EXPANDED_VH，靠 translateY 整体平移做到「上滑下滑都跟手」。
  // 两个吸附档：收起（露出 COLLAPSED_VH 高度，下半截平移出视口外）/ 铺满（translateY=0）。
  // 关闭判定「快速下滑 OR 拖得很低」，不再是稍微越过中点就关。translateY 单位用 vh，正值=向下移出。
  const EXPANDED_VH = 95; // sheet 实际高度（铺满档）
  const COLLAPSED_VH = 72; // 收起档可见高度
  const COLLAPSED_OFFSET = EXPANDED_VH - COLLAPSED_VH; // 收起时下移的 vh（露出 72，藏 23）
  const MAX_OFFSET = EXPANDED_VH; // 拖拽下限（最多整体拖出视口）
  const CLOSE_OFFSET = COLLAPSED_OFFSET + 28; // 慢拖：要再下拖 28vh（过大半截）松手才关闭
  const FLICK_VH_PER_S = 55; // 快速下滑速度阈值（vh/秒）：超过即关闭，无论位移
  const [offsetVh, setOffsetVh] = useState(COLLAPSED_OFFSET); // 当前下移量（0=铺满）
  // 拖拽态：起点 + 实时速度跟踪（lastY/lastT/velocity，vh/秒，下拖为正）。
  const dragRef = useRef<{
    startY: number;
    startOffset: number;
    lastY: number;
    lastT: number;
    velocity: number;
  } | null>(null);

  const scrollRef = useRef<HTMLDivElement>(null);

  // 顶部 handle 拖拽：上拖 → offset 减小（整体上移、铺满）；下拖 → offset 增大（整体下移）。
  // 松手关闭条件 =「快速下滑(flick)」OR「慢拖但拖过 CLOSE_OFFSET」，否则吸附最近档（收起/铺满）。
  // movedRef 区分「拖拽」与「轻点」：拖过则吞掉随后的 click（touchend 后浏览器仍补发 click）。
  const movedRef = useRef(false);
  const onDragStart = (y: number) => {
    dragRef.current = { startY: y, startOffset: offsetVh, lastY: y, lastT: performance.now(), velocity: 0 };
    movedRef.current = false;
  };
  const onDragMove = (y: number) => {
    const d = dragRef.current;
    if (!d) return;
    if (Math.abs(y - d.startY) > 4) movedRef.current = true;
    // 实时速度（vh/秒）：用最近一帧位移/耗时，松手时据此判 flick。
    const now = performance.now();
    const dt = now - d.lastT;
    if (dt > 0) {
      const vhPerS = (((y - d.lastY) / window.innerHeight) * 100) / (dt / 1000);
      // 轻度平滑，避免单帧抖动误判。
      d.velocity = d.velocity * 0.4 + vhPerS * 0.6;
      d.lastY = y;
      d.lastT = now;
    }
    const deltaVh = ((y - d.startY) / window.innerHeight) * 100; // 下拖为正
    const next = Math.max(0, Math.min(MAX_OFFSET, d.startOffset + deltaVh));
    setOffsetVh(next);
  };
  const onDragEnd = () => {
    const d = dragRef.current;
    if (!d) return;
    const flickDown = d.velocity > FLICK_VH_PER_S; // 快速下滑
    const flickUp = d.velocity < -FLICK_VH_PER_S; // 快速上滑
    dragRef.current = null;
    setOffsetVh((v) => {
      // 快速下滑 → 关闭；快速上滑 → 直接铺满。
      if (flickDown) {
        onClose();
        return v;
      }
      if (flickUp) return 0;
      // 慢拖：拖过 CLOSE_OFFSET 才关闭，否则吸附最近档（收起/铺满，以中点为界）。
      if (v > CLOSE_OFFSET) {
        onClose();
        return v;
      }
      return v <= COLLAPSED_OFFSET / 2 ? 0 : COLLAPSED_OFFSET;
    });
  };
  const onHandleClick = () => {
    if (movedRef.current) {
      movedRef.current = false;
      return; // 刚才是拖拽，不当点击处理
    }
    setOffsetVh((v) => (v <= COLLAPSED_OFFSET / 2 ? COLLAPSED_OFFSET : 0));
  };
  const expanded = offsetVh <= COLLAPSED_OFFSET / 2;

  // 打开即确保该问题的会话存在（已存在则复用 → 关掉再开问答还在；不存在才建并自动发首问）。
  // 不在卸载时 abort——关闭抽屉后流式由 store 在后台继续，回来能看到完整结果。
  useEffect(() => {
    openAskSession(question);
  }, [question]);

  // 追问：交给 store（续传同一会话）。
  const send = (text: string) => askFollowUp(question, text);

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
        style={{
          height: `${EXPANDED_VH}vh`,
          transform: `translateY(${offsetVh}vh)`,
        }}
        className={cn(
          "flex w-full flex-col rounded-t-2xl bg-card shadow-lg will-change-transform",
          // 拖拽中不加 transition（跟手平移）；松手吸附时由 onDragEnd 触发的 state 变化走过渡。
          dragRef.current ? "" : "transition-transform duration-300 ease-out",
          // 桌面：忽略平移与高度，回居中弹窗。
          "sm:!h-auto sm:!translate-y-0 sm:max-h-[80vh] sm:max-w-lg sm:rounded-2xl",
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
