import { useState } from "react";
import { ChevronDown, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { WEEKDAYS, type Freq, type ScheduledDraft } from "./templates-data";

interface Props {
  initial?: Partial<ScheduledDraft> | null;
  onClose: () => void;
  onSubmit: (draft: ScheduledDraft) => void;
}

// 创建任务弹窗（照 forkStoreClaw CreateTaskDialog 风：手写 overlay + fade-up + 频率/时间控件）。
export function CreateTaskDialog({ initial, onClose, onSubmit }: Props) {
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [prompt, setPrompt] = useState(initial?.prompt ?? "");
  const [freq, setFreq] = useState<Freq>(initial?.freq ?? "daily");
  const [time, setTime] = useState(initial?.time ?? "09:00");
  const [weekday, setWeekday] = useState(initial?.weekday ?? 1);
  const [error, setError] = useState<string | null>(null);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return setError("任务名称不能为空");
    if (!prompt.trim()) return setError("执行指令不能为空");
    onSubmit({
      name: name.trim(),
      description: description.trim(),
      prompt: prompt.trim(),
      freq,
      time,
      weekday,
    });
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />

      <div className="relative flex max-h-[90vh] w-[780px] max-w-[90vw] animate-fade-up flex-col rounded-2xl bg-white shadow-lg">
        {/* Header */}
        <div className="flex min-h-[72px] items-center justify-between px-6 py-3">
          <div className="text-lg font-semibold leading-6 text-foreground">创建定时任务</div>
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
          <form onSubmit={submit} className="flex flex-col gap-4">
            <Field label="任务名称">
              <TextInput
                value={name}
                onChange={setName}
                placeholder="例如：晨间经营简报"
                autoFocus
              />
            </Field>

            <Field label="描述（可选）">
              <TextInput
                value={description}
                onChange={setDescription}
                placeholder="简要说明这个任务干嘛的"
              />
            </Field>

            <Field label="执行指令">
              <textarea
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                rows={6}
                placeholder="告诉 AI 到点该查什么、怎么汇报…"
                className="flex w-full resize-none rounded-lg border border-border bg-card px-3 py-2 text-sm text-foreground transition-colors placeholder:text-foreground-tertiary hover:border-border-deep focus:border-primary focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1"
              />
            </Field>

            <Field label="执行频率">
              <SelectNative value={freq} onChange={(v) => setFreq(v as Freq)}>
                <option value="daily">每天</option>
                <option value="weekly">每周</option>
              </SelectNative>
            </Field>

            <Field label="执行时间">
              <input
                type="time"
                value={time}
                onChange={(e) => setTime(e.target.value)}
                className="flex h-8 w-full rounded-lg border border-border bg-card px-3 py-1 text-sm text-foreground transition-colors hover:border-border-deep focus:border-primary focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1"
              />
            </Field>

            {freq === "weekly" && (
              <Field label="星期几">
                <SelectNative value={String(weekday)} onChange={(v) => setWeekday(Number(v))}>
                  {WEEKDAYS.map((w, i) => (
                    <option key={i} value={i}>
                      {w}
                    </option>
                  ))}
                </SelectNative>
              </Field>
            )}

            {error && (
              <div className="rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                {error}
              </div>
            )}

            <div className="flex justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={onClose}
                className="rounded-lg border border-border px-4 py-2 text-sm font-medium text-foreground transition-colors hover:bg-fill"
              >
                取消
              </button>
              <button
                type="submit"
                className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90"
              >
                创建
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <label className="flex items-center text-sm font-normal leading-5 text-foreground">
        {label}
      </label>
      {children}
    </div>
  );
}

function TextInput({
  value,
  onChange,
  placeholder,
  autoFocus,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  autoFocus?: boolean;
}) {
  return (
    <input
      type="text"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      autoFocus={autoFocus}
      className="flex h-8 w-full rounded-lg border border-border bg-card px-3 py-1 text-sm text-foreground transition-colors placeholder:text-foreground-tertiary hover:border-border-deep focus:border-primary focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1"
    />
  );
}

// 原生 select 套 fork 输入样式 + 右侧 ChevronDown。
function SelectNative({
  value,
  onChange,
  children,
}: {
  value: string;
  onChange: (v: string) => void;
  children: React.ReactNode;
}) {
  return (
    <div className="relative">
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={cn(
          "flex h-8 w-full cursor-pointer appearance-none rounded-lg border border-border bg-card px-3 py-1 text-sm text-foreground transition-colors",
          "hover:border-border-deep focus:border-primary focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1",
        )}
      >
        {children}
      </select>
      <ChevronDown
        size={16}
        className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-foreground-tertiary"
      />
    </div>
  );
}
