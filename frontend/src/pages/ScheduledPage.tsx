import { useState } from "react";
import { ArrowRight, CalendarClock, Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { CreateTaskDialog } from "@/components/scheduled/CreateTaskDialog";
import { SelectTemplateDialog } from "@/components/scheduled/SelectTemplateDialog";
import { TemplateCard } from "@/components/scheduled/TemplateCard";
import {
  scheduleLabel,
  TEMPLATES,
  type ScheduledDraft,
  type ScheduledTaskItem,
} from "@/components/scheduled/templates-data";
import { cn } from "@/lib/utils";

// 定时任务页（照 forkStoreClaw ScheduledPage 风）。Phase 1：任务暂存前端 state；
// Phase 3 接 /api/scheduled CRUD + systemd 扫表执行 + task_runs 执行历史 + 数据管道只读监控。
export function ScheduledPage() {
  const [tasks, setTasks] = useState<ScheduledTaskItem[]>([]);
  const [createOpen, setCreateOpen] = useState(false);
  const [draft, setDraft] = useState<Partial<ScheduledDraft> | null>(null);
  const [templatesOpen, setTemplatesOpen] = useState(false);

  function openCreate(initial?: Partial<ScheduledDraft> | null) {
    setDraft(initial ?? null);
    setCreateOpen(true);
  }

  function handleCreate(d: ScheduledDraft) {
    setTasks((prev) => [{ ...d, id: crypto.randomUUID(), enabled: true }, ...prev]);
    setCreateOpen(false);
    setDraft(null);
  }

  function useTemplate(d: ScheduledDraft) {
    setTemplatesOpen(false);
    openCreate(d);
  }

  function toggle(id: string) {
    setTasks((prev) => prev.map((t) => (t.id === id ? { ...t, enabled: !t.enabled } : t)));
  }
  function remove(id: string) {
    setTasks((prev) => prev.filter((t) => t.id !== id));
  }

  return (
    <div className="flex h-full flex-col">
      <header className="sticky top-0 z-10 flex h-[68px] shrink-0 items-center justify-between gap-2 border-b border-border-shallow bg-background px-4 sm:px-6">
        <h1 className="text-lg font-semibold tracking-tight">定时任务</h1>
        {tasks.length > 0 && (
          <Button size="sm" onClick={() => openCreate()}>
            <Plus className="size-4" /> 新建任务
          </Button>
        )}
      </header>

      <div className="flex-1 overflow-y-auto p-4 sm:p-6">
        <div className="mx-auto max-w-[1100px]">
          {tasks.length === 0 ? (
            // 空态 Hero
            <div className="flex flex-col items-center gap-6 py-14 text-center">
              <div className="max-w-md">
                <h2 className="text-2xl font-bold tracking-tight">用一份每日简报开启每天</h2>
                <p className="mt-1.5 text-sm text-foreground-secondary">
                  让数据中枢每天定时帮你盘点 GMV、库存、订单和发货，把信号整理成清单推给你。
                </p>
              </div>
              <Button onClick={() => openCreate()}>
                <Plus className="size-4" /> 创建定时任务
              </Button>
            </div>
          ) : (
            // 任务列表
            <div className="mb-10 space-y-2">
              {tasks.map((t) => (
                <div
                  key={t.id}
                  className="flex items-center gap-3 rounded-xl border border-border-shallow bg-card px-4 py-3"
                >
                  <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-fill-deep">
                    <CalendarClock className="size-4 text-foreground-secondary" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate font-medium">{t.name}</span>
                      <span className="shrink-0 text-xs text-foreground-tertiary">
                        {scheduleLabel(t)}
                      </span>
                    </div>
                    {t.description && (
                      <p className="truncate text-xs text-foreground-secondary">{t.description}</p>
                    )}
                  </div>
                  <button
                    onClick={() => toggle(t.id)}
                    role="switch"
                    aria-checked={t.enabled}
                    aria-label={t.enabled ? "停用" : "启用"}
                    className={cn(
                      "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors",
                      t.enabled ? "bg-primary" : "border border-border bg-fill-deep",
                    )}
                  >
                    <span
                      className={cn(
                        "block size-5 rounded-full bg-white shadow-md transition-transform",
                        t.enabled ? "translate-x-[22px]" : "translate-x-0.5",
                      )}
                    />
                  </button>
                  <button
                    onClick={() => remove(t.id)}
                    className="shrink-0 rounded-md p-1.5 text-foreground-tertiary transition-colors hover:bg-fill hover:text-destructive"
                    title="删除"
                  >
                    <Trash2 className="size-4" />
                  </button>
                </div>
              ))}
            </div>
          )}

          {/* 模板区 */}
          <section>
            <div className="mb-4 flex items-center justify-between pl-1">
              <h3 className="text-base font-bold">从模板开始</h3>
              <button
                onClick={() => setTemplatesOpen(true)}
                className="inline-flex items-center gap-0.5 rounded-lg px-2 py-1 text-sm font-medium text-foreground transition-colors hover:bg-fill"
              >
                更多
                <ArrowRight className="size-4" />
              </button>
            </div>
            <div className="flex flex-wrap gap-4">
              {TEMPLATES.map((t) => (
                <TemplateCard key={t.id} template={t} onUse={() => openCreate(t.draft)} />
              ))}
            </div>
          </section>
        </div>
      </div>

      {createOpen && (
        <CreateTaskDialog
          initial={draft}
          onClose={() => {
            setCreateOpen(false);
            setDraft(null);
          }}
          onSubmit={handleCreate}
        />
      )}
      {templatesOpen && (
        <SelectTemplateDialog onClose={() => setTemplatesOpen(false)} onPick={useTemplate} />
      )}
    </div>
  );
}
