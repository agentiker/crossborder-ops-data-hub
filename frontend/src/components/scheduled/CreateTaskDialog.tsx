import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import { WEEKDAYS, type Freq, type ScheduledDraft } from "./templates-data";

interface Props {
  initial?: Partial<ScheduledDraft> | null;
  onClose: () => void;
  onSubmit: (draft: ScheduledDraft) => void;
}

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
    onSubmit({ name: name.trim(), description: description.trim(), prompt: prompt.trim(), freq, time, weekday });
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>创建定时任务</DialogTitle>
          <DialogDescription>到点自动跑一次取数对话，并把结果推送给你。</DialogDescription>
        </DialogHeader>

        <form onSubmit={submit} className="grid gap-4">
          <div className="grid gap-1.5">
            <Label htmlFor="t-name">任务名称</Label>
            <Input
              id="t-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例如：晨间经营简报"
              autoFocus
            />
          </div>

          <div className="grid gap-1.5">
            <Label htmlFor="t-desc">描述（可选）</Label>
            <Input
              id="t-desc"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="简要说明这个任务干嘛的"
            />
          </div>

          <div className="grid gap-1.5">
            <Label htmlFor="t-prompt">执行指令</Label>
            <textarea
              id="t-prompt"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={5}
              placeholder="告诉 AI 到点该查什么、怎么汇报…"
              className="flex w-full resize-none rounded-md border border-input bg-transparent px-3 py-2 text-sm placeholder:text-foreground-tertiary focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="grid gap-1.5">
              <Label htmlFor="t-freq">执行频率</Label>
              <SelectNative id="t-freq" value={freq} onChange={(v) => setFreq(v as Freq)}>
                <option value="daily">每天</option>
                <option value="weekly">每周</option>
              </SelectNative>
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="t-time">执行时间</Label>
              <Input id="t-time" type="time" value={time} onChange={(e) => setTime(e.target.value)} />
            </div>
          </div>

          {freq === "weekly" && (
            <div className="grid gap-1.5">
              <Label htmlFor="t-weekday">星期几</Label>
              <SelectNative
                id="t-weekday"
                value={String(weekday)}
                onChange={(v) => setWeekday(Number(v))}
              >
                {WEEKDAYS.map((w, i) => (
                  <option key={i} value={i}>
                    {w}
                  </option>
                ))}
              </SelectNative>
            </div>
          )}

          {error && (
            <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {error}
            </div>
          )}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              取消
            </Button>
            <Button type="submit">创建</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// 原生 select 套 Input 样式（项目无 Select primitive，与 AdminPage 一致）。
function SelectNative({
  id,
  value,
  onChange,
  children,
}: {
  id?: string;
  value: string;
  onChange: (v: string) => void;
  children: React.ReactNode;
}) {
  return (
    <select
      id={id}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={cn(
        "flex h-9 w-full rounded-md border border-input bg-transparent px-2 text-sm shadow-sm",
        "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
      )}
    >
      {children}
    </select>
  );
}
