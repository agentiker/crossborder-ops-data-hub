import { cn } from "@/lib/utils";
import type { ToolSkill } from "./skills-data";

interface Props {
  skill: ToolSkill;
  enabled: boolean;
  onToggle: (enabled: boolean) => void;
  onClick: () => void;
}

// 工具卡片（照 forkStoreClaw SkillCard 风）：标题 + 启用开关 + 描述 + 分类徽标。
export function SkillCard({ skill, enabled, onToggle, onClick }: Props) {
  return (
    <div
      onClick={onClick}
      className="flex cursor-pointer flex-col rounded-2xl border border-border-shallow bg-card p-5 transition-shadow hover:border-border hover:shadow"
    >
      <div className="mb-2 flex items-start justify-between gap-3">
        <h3 className="text-base font-semibold text-foreground">{skill.label}</h3>
        <button
          onClick={(e) => {
            e.stopPropagation();
            onToggle(!enabled);
          }}
          role="switch"
          aria-checked={enabled}
          aria-label={enabled ? "停用" : "启用"}
          className={cn(
            "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors",
            enabled ? "bg-primary" : "border border-border bg-fill-deep",
          )}
        >
          <span
            className={cn(
              "block size-5 rounded-full bg-white shadow-md transition-transform",
              enabled ? "translate-x-[22px]" : "translate-x-0.5",
            )}
          />
        </button>
      </div>

      <p className="mb-3 line-clamp-3 flex-1 text-sm text-foreground-secondary">
        {skill.description}
      </p>

      <div className="flex items-center justify-between">
        <span className="inline-flex items-center rounded px-2 py-0.5 text-xs font-medium text-foreground-secondary bg-fill">
          {skill.category}
        </span>
        <span className="font-mono text-xs text-foreground-tertiary">{skill.name}</span>
      </div>
    </div>
  );
}
