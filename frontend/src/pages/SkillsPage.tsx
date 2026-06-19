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
    <div className="flex h-full flex-col">
      {/* 页头：tab + 说明 */}
      <header className="sticky top-0 z-10 flex h-[68px] shrink-0 items-center justify-between gap-2 border-b border-border-shallow bg-background px-4 sm:px-6">
        <div className="flex items-center gap-7">
          <TabButton active={tab === "hub"} onClick={() => setTab("hub")}>
            技能中枢（{TOOL_SKILLS.length}）
          </TabButton>
          <TabButton active={tab === "my"} onClick={() => setTab("my")}>
            已启用（{enabled.size}）
          </TabButton>
        </div>
        <p className="hidden text-xs text-foreground-tertiary sm:block">
          启用的技能会开放给对话 AI 调用
        </p>
      </header>

      {/* 内容 */}
      <div className="flex-1 overflow-y-auto p-4 sm:p-6">
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
                    : "border border-border text-foreground-secondary hover:bg-fill hover:text-foreground",
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
          ) : (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {display.map((s) => (
                <SkillCard
                  key={s.name}
                  skill={s}
                  enabled={enabled.has(s.name)}
                  onToggle={(on) => toggle(s.name, on)}
                  onClick={() => setSelected(s)}
                />
              ))}
            </div>
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
