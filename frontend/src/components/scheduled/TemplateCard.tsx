import { useState } from "react";
import { CalendarClock } from "lucide-react";
import { cn } from "@/lib/utils";
import { scheduleLabel, type Template } from "./templates-data";

interface Props {
  template: Template;
  onUse: () => void;
}

// 模板卡（照 forkStoreClaw TemplateCard 风）：hover 时右上角双层装饰块做 520ms 弹性变换。
// 无外链装饰图，用纯色块 + 图标还原层叠错位的动效观感。
export function TemplateCard({ template, onUse }: Props) {
  const [hovered, setHovered] = useState(false);

  return (
    <div
      className="relative min-w-[280px] max-w-[360px] flex-1"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <div
        className={cn(
          "relative flex cursor-pointer flex-col items-start gap-5 overflow-hidden rounded-2xl border-2 border-white p-5 transition-all duration-300",
          hovered
            ? "bg-white shadow-[0_2px_20px_0_rgba(0,0,0,0.06)]"
            : "bg-white/60 shadow-[0_2px_20px_0_rgba(0,0,0,0.02)]",
        )}
        onClick={onUse}
      >
        {/* 装饰：右上角双层色块，hover 时做 520ms 弹性错位（行内 style 确保不被 Tailwind 任意值歧义吞掉） */}
        <div className="pointer-events-none absolute right-[-38px] top-[36px] z-[1] h-[165px] w-[169px]">
          <div
            style={{
              transition: "transform 520ms cubic-bezier(0.34,1.56,0.64,1)",
            }}
            className={cn(
              "absolute left-[10px] top-[30px] h-[116px] w-[155px]",
              hovered && "-translate-x-[10px] translate-y-[9px] -rotate-[4deg] scale-105",
            )}
          >
            <div className="size-full rounded-lg bg-fill" />
          </div>
          <div
            style={{
              transition: "transform 520ms cubic-bezier(0.34,1.56,0.64,1)",
              transitionDelay: "20ms",
            }}
            className={cn(
              "absolute left-[20px] top-[10px] h-[90px] w-[200px]",
              hovered && "translate-x-[15px] -translate-y-[10px] rotate-[7deg] scale-110",
            )}
          >
            <div className="flex size-full items-center justify-end rounded-lg bg-fill-deep pr-4">
              <CalendarClock className="size-5 text-foreground-tertiary" />
            </div>
          </div>
        </div>

        {/* 内容 */}
        <div className="relative z-10">
          <div className="mb-2 inline-flex items-center gap-1.5 text-xs font-medium text-foreground-tertiary">
            <CalendarClock className="size-3.5" />
            {scheduleLabel(template.draft)}
          </div>
          <h4 className="mb-2 text-base font-semibold text-foreground">{template.title}</h4>
          <p className="line-clamp-2 max-w-[220px] text-sm text-foreground-secondary">
            {template.description}
          </p>
        </div>

        {/* 用这个 */}
        <button
          onClick={(e) => {
            e.stopPropagation();
            onUse();
          }}
          className="relative z-10 inline-flex items-center justify-center rounded-lg bg-primary px-3 py-1.5 text-xs font-bold text-primary-foreground transition-opacity hover:opacity-90"
        >
          用这个
        </button>
      </div>
    </div>
  );
}
