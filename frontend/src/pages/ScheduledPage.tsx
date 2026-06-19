import { useState } from "react";
import { ArrowRight, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { CreateTaskDialog } from "@/components/scheduled/CreateTaskDialog";
import { SelectTemplateDialog } from "@/components/scheduled/SelectTemplateDialog";
import { TaskCard } from "@/components/scheduled/TaskCard";
import { TemplateCard } from "@/components/scheduled/TemplateCard";
import {
  TEMPLATES,
  type ScheduledDraft,
  type ScheduledTaskItem,
} from "@/components/scheduled/templates-data";

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
    setTasks((prev) => [{ ...d, id: crypto.randomUUID(), enabled: false }, ...prev]);
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

  const activeCount = tasks.filter((t) => t.enabled).length;

  return (
    <div className="flex h-full flex-col">
      <header className="sticky top-0 z-50 flex h-[68px] shrink-0 items-center justify-between gap-2 border-b border-border-shallow bg-background px-4 sm:px-6">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <h1 className="truncate text-lg font-semibold tracking-tight text-foreground">定时任务</h1>
          {tasks.length > 0 && (
            <span className="ml-1 text-sm text-foreground-tertiary">
              {tasks.length} 个任务 · {activeCount} 个运行中
            </span>
          )}
        </div>
        {tasks.length > 0 && (
          <div className="flex items-center gap-2">
            <button
              onClick={() => setTemplatesOpen(true)}
              className="inline-flex h-8 items-center justify-center gap-1 rounded-lg border border-border px-3 text-sm text-foreground transition-colors hover:bg-fill"
            >
              从模板开始
            </button>
            <Button size="sm" onClick={() => openCreate()}>
              <Plus className="size-4" /> 新建任务
            </Button>
          </div>
        )}
      </header>

      <div className="flex-1 overflow-y-auto p-4 sm:p-6">
        <div className="mx-auto max-w-[1100px]">
          {tasks.length === 0 ? (
            // 空态 Hero
            <div className="flex flex-col items-center gap-6 py-14 text-center">
              <div className="max-w-md">
                <h2 className="text-2xl font-bold tracking-tight text-foreground">
                  用一份每日简报开启每天
                </h2>
                <p className="mt-1.5 text-sm text-foreground-secondary">
                  让数据中枢每天定时帮你盘点 GMV、库存、订单和发货，把信号整理成清单推给你。
                </p>
              </div>
              <Button onClick={() => openCreate()}>
                <Plus className="size-4" /> 创建定时任务
              </Button>
            </div>
          ) : (
            // 任务列表（照 fork：双列 flex-wrap）
            <div className="mb-10 flex flex-wrap gap-3">
              {tasks.map((t) => (
                <TaskCard
                  key={t.id}
                  task={t}
                  onToggle={() => toggle(t.id)}
                  onDelete={() => remove(t.id)}
                />
              ))}
            </div>
          )}

          {/* 模板区 */}
          <section>
            <div className="mb-4 flex items-center justify-between pl-1">
              <h3 className="text-base font-bold text-foreground">从模板开始</h3>
              <button
                onClick={() => setTemplatesOpen(true)}
                className="inline-flex items-center gap-0.5 rounded-lg px-2 py-1 text-sm font-bold text-foreground transition-colors hover:bg-fill"
              >
                更多
                <ArrowRight className="size-4" />
              </button>
            </div>
            <div className="flex flex-wrap gap-5">
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
