import { useState } from "react";
import { SkillCard } from "@/components/skills/SkillCard";
import { SkillDetailDialog } from "@/components/skills/SkillDetailDialog";
import { SKILL_CATEGORIES, TOOL_SKILLS, type ToolSkill } from "@/components/skills/skills-data";
import { cn } from "@/lib/utils";

type Tab = "hub" | "my";

// 技能页（照 forkStoreClaw SkillsPage 风）。Phase 1：开关走前端 state（默认全开）；
// Phase 2 接 GET/PUT /api/admin/tools 真正持久化并夹住对话 AI 的可用工具集。
export function SkillsPage() {
  const [tab, setTab] = useState<Tab>("hub");
  const [cat, setCat] = useState("全部");
  const [enabled, setEnabled] = useState<Set<string>>(
    () => new Set(TOOL_SKILLS.map((t) => t.name)),
  );
  const [selected, setSelected] = useState<ToolSkill | null>(null);

  const byCat = cat === "全部" ? TOOL_SKILLS : TOOL_SKILLS.filter((s) => s.category === cat);
  const display = tab === "hub" ? byCat : byCat.filter((s) => enabled.has(s.name));

  function toggle(name: string, on: boolean) {
    setEnabled((prev) => {
      const next = new Set(prev);
      if (on) next.add(name);
      else next.delete(name);
      return next;
    });
  }

  return (
    <div className="flex flex-1 flex-col">
      {/* 页头：tab + 说明（文档滚动：桌面 sticky 贴顶，移动端随内容滚走、靠全局顶栏导航） */}
      <header className="z-40 flex h-[68px] shrink-0 items-center justify-between gap-2 border-b border-border-shallow bg-background px-4 sm:px-6 lg:sticky lg:top-0">
        <div className="flex items-center gap-7">
          <TabButton active={tab === "hub"} onClick={() => setTab("hub")}>
            技能中枢（{TOOL_SKILLS.length}）
          </TabButton>
          <TabButton active={tab === "my"} onClick={() => setTab("my")}>
            已启用（{enabled.size}）
          </TabButton>
        </div>
        <div className="flex items-center gap-2">
          <span className="shrink-0 rounded-full bg-caution/15 px-2 py-0.5 text-xs font-medium text-caution">
            待开发
          </span>
          <p className="hidden text-xs text-foreground-tertiary sm:block">
            按需启用待开发；当前对话 AI 默认可调用全部技能
          </p>
        </div>
      </header>

      {/* 内容（文档级滚动：不再内层 overflow，自然撑高让 body 滚动 → iOS 下滑收栏沉浸） */}
      <div className="flex-1 p-4 sm:p-6">
        <div className="mx-auto max-w-[1200px]">
          {/* 分类筛选 */}
          <div className="mb-6 flex flex-wrap gap-2">
            {SKILL_CATEGORIES.map((c) => (
              <button
                key={c}
                onClick={() => setCat(c)}
                className={cn(
                  "rounded-lg px-3 py-1.5 text-xs font-medium transition-colors",
                  cat === c
                    ? "bg-primary text-primary-foreground"
                    : "border border-border text-foreground hover:bg-fill",
                )}
              >
                {c}
              </button>
            ))}
          </div>

          {display.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 text-center text-sm text-foreground-tertiary">
              {tab === "my" ? "还没有启用任何技能，去「技能中枢」开启。" : "该分类下暂无技能。"}
            </div>
          ) : cat === "全部" ? (
            // 「全部」时按分类分组展示
            <div className="space-y-8">
              {SKILL_CATEGORIES.slice(1).map((c) => {
                const group = display.filter((s) => s.category === c);
                if (group.length === 0) return null;
                return (
                  <div key={c}>
                    <h2 className="mb-4 text-lg font-semibold text-foreground">{c}</h2>
                    <SkillGrid skills={group} enabled={enabled} toggle={toggle} onSelect={setSelected} />
                  </div>
                );
              })}
            </div>
          ) : (
            <SkillGrid skills={display} enabled={enabled} toggle={toggle} onSelect={setSelected} />
          )}
        </div>
      </div>

      {selected && (
        <SkillDetailDialog
          skill={selected}
          enabled={enabled.has(selected.name)}
          onToggle={(on) => toggle(selected.name, on)}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}

function SkillGrid({
  skills,
  enabled,
  toggle,
  onSelect,
}: {
  skills: ToolSkill[];
  enabled: Set<string>;
  toggle: (name: string, on: boolean) => void;
  onSelect: (s: ToolSkill) => void;
}) {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
      {skills.map((s) => (
        <SkillCard
          key={s.name}
          skill={s}
          enabled={enabled.has(s.name)}
          onToggle={(on) => toggle(s.name, on)}
          onClick={() => onSelect(s)}
        />
      ))}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "relative h-8 text-sm font-medium transition-colors",
        active ? "text-foreground" : "text-foreground-tertiary hover:text-foreground-secondary",
      )}
    >
      {children}
      {active && (
        <span className="absolute -bottom-px left-1/2 h-0.5 w-4 -translate-x-1/2 rounded-t-full bg-foreground" />
      )}
    </button>
  );
}
