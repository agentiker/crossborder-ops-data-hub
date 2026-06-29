import { X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ToolSkill } from "./skills-data";

interface Props {
  skill: ToolSkill;
  enabled: boolean;
  onToggle: (enabled: boolean) => void;
  onClose: () => void;
}

// 详情弹窗（照 forkStoreClaw SkillDetailDialog 风：手写 overlay + fade-up + 信息块 + 核心能力分点）。
export function SkillDetailDialog({ skill, enabled, onToggle, onClose }: Props) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />

      {/* Dialog */}
      <div className="relative flex max-h-[90vh] w-[640px] max-w-[90vw] animate-fade-up flex-col rounded-2xl bg-card shadow-lg">
        {/* Header */}
        <div className="flex min-h-[72px] items-center justify-between px-6 py-3">
          <div className="text-lg font-semibold leading-6 text-foreground">{skill.label}</div>
          <button
            onClick={onClose}
            aria-label="关闭"
            className="rounded-lg p-1 transition-colors hover:bg-fill"
          >
            <X size={20} className="text-foreground-secondary" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-6 pb-6">
          <div className="flex flex-col gap-6">
            {/* 技能信息块 */}
            <div className="flex flex-col gap-3 rounded-xl border border-border-shallow bg-fill-shallow p-4">
              <div className="flex flex-col gap-0.5">
                <div className="flex items-center gap-3">
                  <span className="min-w-0 flex-1 truncate font-mono text-sm font-bold text-foreground">
                    {skill.name}
                  </span>
                  <span className="inline-flex shrink-0 items-center gap-0.5 rounded-md bg-fill px-1.5 py-0.5 text-xs font-normal text-foreground">
                    {skill.freq}
                  </span>
                </div>
                <p className="text-xs text-foreground-tertiary">{skill.detail}</p>
              </div>

              <span
                className={cn(
                  "inline-flex self-start items-center rounded px-2 py-0.5 text-xs font-medium",
                  skill.badge === "官方"
                    ? "bg-foreground/10 text-foreground"
                    : "bg-caution/15 text-caution",
                )}
              >
                {skill.badge}
              </span>
            </div>

            {/* 核心能力 */}
            {skill.features.length > 0 && (
              <div>
                <h3 className="mb-3 text-base font-semibold text-foreground">核心能力</h3>
                <div className="flex flex-col gap-4">
                  {skill.features.map((feature, i) => (
                    <div key={i} className="flex flex-col gap-1">
                      <h4 className="text-sm font-semibold text-foreground">{feature.title}</h4>
                      <p className="text-sm text-foreground-secondary">{feature.description}</p>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* 可调参数 */}
            <div>
              <h3 className="mb-1.5 text-base font-semibold text-foreground">可调参数</h3>
              <p className="text-sm text-foreground-secondary">{skill.params}</p>
            </div>

            {/* 启用状态 */}
            <div className="flex items-center justify-between rounded-xl border border-border-shallow bg-fill-shallow px-4 py-3">
              <div>
                <div className="text-sm font-medium text-foreground">
                  {enabled ? "已启用" : "已停用"}
                </div>
                <div className="text-xs text-foreground-tertiary">
                  启用后，对话 AI 可调用此技能取数
                </div>
              </div>
              <button
                onClick={() => onToggle(!enabled)}
                role="switch"
                aria-checked={enabled}
                aria-label={enabled ? "停用" : "启用"}
                className={cn(
                  "relative inline-flex h-[26px] w-[46px] shrink-0 cursor-pointer items-center rounded-full transition-all duration-300 ease-in-out",
                  enabled ? "bg-primary" : "border border-border bg-fill-deep",
                )}
              >
                <span
                  className={cn(
                    "pointer-events-none block size-[22px] rounded-full bg-white shadow-md ring-0 transition-transform duration-300 ease-in-out",
                    enabled ? "translate-x-[22px]" : "translate-x-[2px]",
                  )}
                />
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
