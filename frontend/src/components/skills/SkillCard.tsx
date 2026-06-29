import { useEffect, useRef, useState } from "react";
import { MoreHorizontal, ScrollText } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ToolSkill } from "./skills-data";

interface Props {
  skill: ToolSkill;
  enabled: boolean;
  onToggle: (enabled: boolean) => void;
  onClick: () => void;
}

// 工具卡片（照 forkStoreClaw SkillCard 风）：标题 + iOS 风格启用开关 + 描述 + 徽章 + 卡片菜单。
export function SkillCard({ skill, enabled, onToggle, onClick }: Props) {
  const [showMenu, setShowMenu] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setShowMenu(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  return (
    <div
      onClick={onClick}
      className="relative flex cursor-pointer flex-col rounded-2xl border border-border-shallow bg-card p-5 text-card-foreground transition-shadow hover:border-border hover:shadow"
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

      <p className="mb-3 line-clamp-3 flex-1 text-sm text-foreground-secondary">
        {skill.description}
      </p>

      <div className="flex items-center justify-between">
        <span
          className={cn(
            "inline-flex items-center rounded px-2 py-0.5 text-xs font-medium",
            skill.badge === "官方"
              ? "bg-foreground/10 text-foreground"
              : "bg-caution/15 text-caution",
          )}
        >
          {skill.badge}
        </span>

        <div className="relative ml-auto" ref={menuRef}>
          <button
            onClick={(e) => {
              e.stopPropagation();
              setShowMenu((s) => !s);
            }}
            aria-label="更多"
            className="rounded-lg p-1 text-foreground-tertiary transition-colors hover:bg-fill hover:text-foreground"
          >
            <MoreHorizontal size={16} />
          </button>

          {showMenu && (
            <div className="absolute bottom-full right-0 z-20 mb-1 w-32 rounded-xl border border-border bg-card py-1 shadow-lg">
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  setShowMenu(false);
                  onClick();
                }}
                className="flex w-full items-center gap-2 px-3 py-2 text-sm text-foreground transition-colors hover:bg-fill"
              >
                <ScrollText size={14} />
                查看详情
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
