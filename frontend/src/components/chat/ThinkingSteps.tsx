import { useState } from "react";
import { Check, ChevronDown, ChevronRight, Database, Loader2, Zap } from "lucide-react";
import type { ThinkingStep } from "@/api";
import { cn } from "@/lib/utils";

// 后端 SSE 的 tool 事件只有 name+status，没有 fork 那套 type/detail。
// 这里把 tool name 粗分类成「数据查询(api)/技能(skill)」两类来选图标——纯视觉降级，不伪造文案。
function stepIcon(name: string, done: boolean) {
  if (!done) return <Loader2 className="size-3.5 shrink-0 animate-spin text-foreground-tertiary" />;
  // ops_* 都是后端取数工具，归 api（绿）；其余兜底归 skill（蓝）。
  if (name.startsWith("ops_")) return <Database className="size-3.5 shrink-0 text-positive" />;
  return <Zap className="size-3.5 shrink-0 text-blue-500" />;
}

// 助手消息的工具调用轨迹。
// live=实时流式（默认展开、转圈/打勾）；否则=历史折叠（点击展开）。
// 折叠交互照搬 fork：默认只显示前 3 步，多余的「展开更多」。
export function ThinkingSteps({
  steps,
  live,
  workedMs,
}: {
  steps: ThinkingStep[];
  live?: boolean;
  workedMs?: number;
}) {
  const [open, setOpen] = useState(!!live);
  const [showMore, setShowMore] = useState(false);
  const allDone = steps.every((s) => s.done);

  const visible = showMore ? steps : steps.slice(0, 3);

  // 表头文案：流式时「正在查询…」；完成后优先显示真实耗时（客户端计时），否则显示查询项数。
  const header = live && !allDone ? "正在查询数据…" : workedMs ? worked(workedMs) : `已查询 ${steps.length} 项数据`;

  return (
    <div className="mb-2">
      <button
        onClick={() => setOpen((o) => !o)}
        className="inline-flex items-center gap-1.5 rounded-full bg-fill px-2.5 py-1 text-xs font-medium text-foreground-secondary transition-colors hover:text-foreground"
      >
        <ChevronDown className={cn("size-3 opacity-60 transition-transform", !open && "-rotate-90")} />
        {header}
      </button>

      {open && (
        <ul className="ml-1 mt-1.5 space-y-1.5 border-l-2 border-border-shallow pl-3.5 animate-fade-up">
          {visible.map((s, i) => (
            <li
              key={`${s.name}-${i}`}
              className="flex items-center gap-2 text-xs text-foreground-secondary"
            >
              <span className="mt-0.5 shrink-0">{stepIcon(s.name, s.done)}</span>
              <span className="flex-1 truncate font-medium text-foreground">{s.label}</span>
              {s.done && <Check className="size-3.5 shrink-0 text-positive" />}
            </li>
          ))}

          {steps.length > 3 && !showMore && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                setShowMore(true);
              }}
              className="mt-1 flex items-center gap-1 text-xs text-foreground-secondary transition-colors hover:text-foreground"
            >
              <ChevronRight className="size-3" />
              展开其余 {steps.length - 3} 项
            </button>
          )}
        </ul>
      )}
    </div>
  );
}

// 客户端计算的真实耗时（流式开始→结束）。后端不提供「Worked for」，这里如实换算。
function worked(ms: number): string {
  const sec = Math.round(ms / 1000);
  if (sec < 60) return `用时 ${sec}s · 查询已完成`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `用时 ${m}m ${s}s · 查询已完成`;
}
