import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Bell,
  CheckCircle2,
  ChevronRight,
  Clock3,
  Database,
  FileClock,
  RefreshCw,
  ShieldCheck,
  X,
} from "lucide-react";
import { api, type SystemTaskCapability, type SystemTaskRun, type SystemTaskSnapshot } from "@/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const GROUP_ICON: Record<string, typeof Database> = {
  数据同步: Database,
  经营计算: Activity,
  主动推送: Bell,
  系统维护: ShieldCheck,
};

function statusLabel(status: string) {
  if (status === "ok") return "正常";
  if (status === "failed") return "失败";
  if (status === "disabled") return "已停用";
  if (status === "missing") return "未配置";
  if (status === "degraded") return "状态不可用";
  return "未知";
}

function statusClass(status: string) {
  if (status === "ok") return "border-emerald-500/20 bg-emerald-500/10 text-emerald-700";
  if (status === "failed") return "border-destructive/20 bg-destructive/10 text-destructive";
  if (status === "disabled" || status === "missing") {
    return "border-foreground-tertiary/20 bg-fill text-foreground-secondary";
  }
  return "border-caution/20 bg-caution/10 text-caution";
}

function fmtTime(value?: string | null) {
  if (!value) return "—";
  const systemd = value.match(/^(?:[A-Z][a-z]{2} )?(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})(?::\d{2})?(?: .*)?$/);
  if (systemd) {
    const [, year, month, day, hour, minute] = systemd;
    const d = new Date(Number(year), Number(month) - 1, Number(day));
    const week = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"][d.getDay()];
    return `${Number(month)}月${Number(day)}日 ${week} ${hour}:${minute}`;
  }
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value.replace(/ CST| UTC/g, "");
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(d);
}

function CapabilityCard({ item, runs }: { item: SystemTaskCapability; runs: SystemTaskRun[] }) {
  const Icon = GROUP_ICON[item.group] ?? FileClock;
  const related = item.task_ids.map((id) => runs.find((r) => r.id === id)).filter(Boolean);
  const next = related.map((r) => r?.next_run).filter(Boolean).sort()[0] ?? null;
  return (
    <div className="rounded-lg border border-border-shallow bg-background px-4 py-3 transition-colors hover:bg-fill/50">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-fill-deep text-foreground-secondary">
              <Icon className="size-4" />
            </span>
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold text-foreground">{item.name}</div>
              <div className="mt-0.5 text-xs text-foreground-tertiary">{item.summary}</div>
            </div>
          </div>
        </div>
        <Badge className={cn("shrink-0 border", statusClass(item.status))}>
          {statusLabel(item.status)}
        </Badge>
      </div>
      <div className="mt-3 flex items-center justify-between text-xs text-foreground-tertiary">
        <span>{item.touches_customer ? "通过飞书发送" : "仅更新数据"}</span>
        <span>下次 {fmtTime(next)}</span>
      </div>
    </div>
  );
}

function TaskRow({ task, onOpen }: { task: SystemTaskRun; onOpen: () => void }) {
  const Icon = GROUP_ICON[task.group] ?? FileClock;
  return (
    <button
      type="button"
      onClick={onOpen}
      className="grid w-full grid-cols-[minmax(220px,1.4fr)_110px_120px_120px_110px_32px] items-center gap-3 border-b border-border-shallow px-4 py-3 text-left text-sm transition-colors hover:bg-fill/60 max-lg:grid-cols-[1fr_auto] max-lg:gap-y-1"
    >
      <div className="flex min-w-0 items-center gap-3">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-fill text-foreground-secondary">
          <Icon className="size-4" />
        </span>
        <div className="min-w-0">
          <div className="truncate font-medium text-foreground">{task.name}</div>
          <div className="mt-0.5 truncate text-xs text-foreground-tertiary">
            {task.description}
          </div>
        </div>
      </div>
      <Badge className={cn("w-fit border max-lg:justify-self-end", statusClass(task.status))}>
        {statusLabel(task.status)}
      </Badge>
      <div className="text-foreground-secondary max-lg:hidden">{task.source}</div>
      <div className="text-foreground-secondary max-lg:hidden">{fmtTime(task.last_run)}</div>
      <div className="text-foreground-secondary max-lg:hidden">{fmtTime(task.next_run)}</div>
      <ChevronRight className="size-4 justify-self-end text-foreground-tertiary max-lg:hidden" />
      <div className="col-span-2 hidden text-xs text-foreground-tertiary max-lg:block">
        上次 {fmtTime(task.last_run)} · 下次 {fmtTime(task.next_run)}
      </div>
    </button>
  );
}

function DetailDrawer({ task, onClose }: { task: SystemTaskRun | null; onClose: () => void }) {
  return (
    <div
      className={cn(
        "fixed inset-0 z-50 transition-opacity",
        task ? "pointer-events-auto opacity-100" : "pointer-events-none opacity-0",
      )}
    >
      <button className="absolute inset-0 bg-foreground/15" onClick={onClose} aria-label="关闭" />
      <aside
        className={cn(
          "absolute right-0 top-0 h-full w-full max-w-[520px] bg-background shadow-2xl transition-transform duration-200 ease-out",
          task ? "translate-x-0" : "translate-x-full",
        )}
      >
        {task && (
          <div className="flex h-full flex-col">
            <header className="flex h-16 items-center justify-between border-b border-border-shallow px-5">
              <div className="min-w-0">
                <div className="truncate text-base font-semibold text-foreground">{task.name}</div>
                <div className="mt-0.5 text-xs text-foreground-tertiary">{task.group} · {task.source}</div>
              </div>
              <button
                type="button"
                onClick={onClose}
                className="flex size-8 items-center justify-center rounded-lg text-foreground-secondary transition-colors hover:bg-fill hover:text-foreground"
              >
                <X className="size-4" />
              </button>
            </header>
            <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
              <div className="grid grid-cols-2 gap-3 text-sm">
                <Info label="状态" value={statusLabel(task.status)} />
                <Info label="触达" value={task.touches_customer ? "通过飞书发送" : "不发送飞书"} />
                <Info label="上次运行" value={fmtTime(task.last_run)} />
                <Info label="下次运行" value={fmtTime(task.next_run)} />
                <Info label="最近结果" value={task.last_result || "—"} />
                <Info label="启用状态" value={task.enabled ? "已启用" : "未启用"} />
              </div>
              {task.unit && <InfoBlock title="底层任务" value={task.unit} />}
              {task.schedule && <InfoBlock title="调度" value={task.schedule} />}
              {task.recipient_summary && (
                <InfoBlock
                  title="收件配置"
                  value={
                    task.recipient_summary.recipient_names?.length
                      ? task.recipient_summary.recipient_names.join("、")
                      : "—"
                  }
                />
              )}
              {task.error && <InfoBlock title="状态错误" value={task.error} tone="danger" />}
              {task.log_excerpt && <InfoBlock title="最近日志摘要" value={task.log_excerpt} mono />}
            </div>
          </div>
        )}
      </aside>
    </div>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-fill px-3 py-2">
      <div className="text-xs text-foreground-tertiary">{label}</div>
      <div className="mt-1 truncate font-medium text-foreground">{value}</div>
    </div>
  );
}

function InfoBlock({ title, value, mono, tone }: { title: string; value: string; mono?: boolean; tone?: "danger" }) {
  return (
    <section className="mt-5">
      <div className="mb-2 text-xs font-medium text-foreground-tertiary">{title}</div>
      <div
        className={cn(
          "whitespace-pre-wrap rounded-lg border border-border-shallow bg-fill px-3 py-2 text-sm text-foreground-secondary",
          mono && "font-mono text-xs leading-relaxed",
          tone === "danger" && "border-destructive/20 bg-destructive/10 text-destructive",
        )}
      >
        {value}
      </div>
    </section>
  );
}

export function SystemScheduledPage() {
  const [data, setData] = useState<SystemTaskSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<SystemTaskRun | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setData(await api.systemTasks());
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const grouped = useMemo(() => {
    const out = new Map<string, SystemTaskRun[]>();
    for (const task of data?.runs ?? []) {
      out.set(task.group, [...(out.get(task.group) ?? []), task]);
    }
    return Array.from(out.entries());
  }, [data]);

  return (
    <div className="flex flex-1 flex-col">
      <header className="z-40 flex h-[68px] shrink-0 items-center justify-between gap-3 border-b border-border-shallow bg-background/85 px-4 backdrop-blur-xl sm:px-6 lg:sticky lg:top-0">
        <div className="min-w-0">
          <h1 className="truncate text-lg font-semibold tracking-tight text-foreground">系统任务</h1>
          <p className="mt-0.5 text-xs text-foreground-tertiary">
            数据同步、经营计算、主动推送
          </p>
        </div>
        <Button size="sm" variant="outline" onClick={() => void load()} disabled={loading}>
          <RefreshCw className={cn("size-4", loading && "animate-spin")} /> 刷新
        </Button>
      </header>

      <main className="mx-auto flex w-full max-w-[1180px] flex-1 flex-col gap-5 p-4 sm:p-6">
        {error && (
          <div className="rounded-lg border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        )}

        <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <SummaryCard icon={CheckCircle2} label="已启用" value={data ? `${data.summary.enabled}/${data.summary.total}` : "—"} />
          <SummaryCard icon={AlertTriangle} label="最近失败" value={data ? String(data.summary.failed) : "—"} tone={data?.summary.failed ? "danger" : undefined} />
          <SummaryCard icon={Bell} label="飞书发送任务" value={data ? String(data.summary.customer_touching) : "—"} />
          <SummaryCard
            icon={Clock3}
            label="下一次触发"
            value={fmtTime(data?.summary.next_run)}
            caption={data?.summary.next_run_task_name ?? undefined}
          />
        </section>

        <section>
          <div className="mb-3 flex items-end justify-between">
            <div>
              <h2 className="text-base font-semibold text-foreground">托管能力</h2>
            </div>
          </div>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {(data?.capabilities ?? []).map((cap) => (
              <CapabilityCard key={cap.id} item={cap} runs={data?.runs ?? []} />
            ))}
            {loading && Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="h-[98px] animate-pulse rounded-lg bg-fill" />
            ))}
          </div>
        </section>

        <section className="min-h-0 overflow-hidden rounded-lg border border-border-shallow bg-background">
          <div className="flex items-center justify-between border-b border-border-shallow px-4 py-3">
            <div>
              <h2 className="text-base font-semibold text-foreground">运行台账</h2>
            </div>
            <Badge variant="secondary">只读</Badge>
          </div>
          <div className="grid grid-cols-[minmax(220px,1.4fr)_110px_120px_120px_110px_32px] gap-3 border-b border-border-shallow bg-fill/50 px-4 py-2 text-xs font-medium text-foreground-tertiary max-lg:hidden">
            <span>任务</span><span>状态</span><span>来源</span><span>上次（北京）</span><span>下次（北京）</span><span />
          </div>
          {grouped.map(([group, tasks]) => (
            <div key={group}>
              <div className="bg-fill/35 px-4 py-2 text-xs font-semibold text-foreground-secondary">{group}</div>
              {tasks.map((task) => (
                <TaskRow key={task.id} task={task} onOpen={() => setSelected(task)} />
              ))}
            </div>
          ))}
          {!loading && !data?.runs.length && (
            <div className="px-4 py-10 text-center text-sm text-foreground-tertiary">暂无系统任务状态</div>
          )}
        </section>
      </main>
      <DetailDrawer task={selected} onClose={() => setSelected(null)} />
    </div>
  );
}

function SummaryCard({
  icon: Icon,
  label,
  value,
  caption,
  tone,
}: {
  icon: typeof CheckCircle2;
  label: string;
  value: string;
  caption?: string;
  tone?: "danger";
}) {
  return (
    <div className="rounded-lg border border-border-shallow bg-background px-4 py-3">
      <div className="flex items-center justify-between">
        <span className="text-xs text-foreground-tertiary">{label}</span>
        <Icon className={cn("size-4 text-foreground-tertiary", tone === "danger" && "text-destructive")} />
      </div>
      <div className={cn("mt-2 text-xl font-semibold text-foreground", tone === "danger" && "text-destructive")}>
        {value}
      </div>
      {caption && <div className="mt-1 truncate text-xs text-foreground-tertiary">{caption}</div>}
    </div>
  );
}
