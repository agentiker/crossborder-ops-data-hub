import { useEffect, useMemo, useRef, useState } from "react";
import { Calendar } from "lucide-react";
import { DayPicker, type DateRange } from "react-day-picker";
import { zhCN } from "react-day-picker/locale";
import "react-day-picker/style.css";
import "./date-range-picker.css";

// 日期范围选择器：单月日历（react-day-picker，框架组件）+ 我方快捷项侧栏。
// 受控：父组件给 value（YYYY-MM-DD 起止），选定后 onChange 回吐同格式字符串——
// 对外契约与旧手写版一致，BoardPage 无需改动。
// 快捷项语义按「含端点天数」：近 7 天 = 今天往前 6 天 ~ 今天（与后端 last_7d 对齐）。

export interface DateRangeValue {
  start: string | null; // YYYY-MM-DD
  end: string | null;
}

function fmt(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(
    d.getDate(),
  ).padStart(2, "0")}`;
}

function parse(s: string | null): Date | null {
  if (!s) return null;
  const [y, m, d] = s.split("-").map(Number);
  return new Date(y, m - 1, d);
}

// 快捷项 → 计算含端点的 [start, end]。
const QUICK: { label: string; calc: () => [Date, Date] }[] = [
  { label: "今天", calc: () => { const t = new Date(); return [t, t]; } },
  { label: "近 7 天", calc: () => { const e = new Date(); const s = new Date(); s.setDate(s.getDate() - 6); return [s, e]; } },
  { label: "近 30 天", calc: () => { const e = new Date(); const s = new Date(); s.setDate(s.getDate() - 29); return [s, e]; } },
  { label: "近 90 天", calc: () => { const e = new Date(); const s = new Date(); s.setDate(s.getDate() - 89); return [s, e]; } },
  { label: "本月", calc: () => { const e = new Date(); return [new Date(e.getFullYear(), e.getMonth(), 1), e]; } },
  { label: "上月", calc: () => { const n = new Date(); const s = new Date(n.getFullYear(), n.getMonth() - 1, 1); const e = new Date(n.getFullYear(), n.getMonth(), 0); return [s, e]; } },
];

export function DateRangePicker({
  value,
  onChange,
}: {
  value: DateRangeValue;
  onChange: (range: { start: string; end: string }) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // rdp 的选区值：{from,to}。由父级 value 派生（受控）。
  const selected = useMemo<DateRange | undefined>(() => {
    const from = parse(value.start);
    const to = parse(value.end);
    if (!from) return undefined;
    return { from, to: to ?? undefined };
  }, [value.start, value.end]);

  // 展示月：定位到区间「结束」月（通常含今天，多数人更关心近几天）；无值回落当月。
  const [month, setMonth] = useState<Date>(() => parse(value.end) ?? new Date());
  // 父级 value 变化（如快捷项/外部默认）→ 同步展示月到结束月。
  useEffect(() => {
    const end = parse(value.end);
    if (end) setMonth(new Date(end.getFullYear(), end.getMonth(), 1));
  }, [value.end]);

  useEffect(() => {
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);

  function handleSelect(range: DateRange | undefined) {
    if (!range?.from) return;
    // 起止都定了才回吐 + 收起；只点了起点时保持打开等第二次点击。
    if (range.from && range.to) {
      onChange({ start: fmt(range.from), end: fmt(range.to) });
      setOpen(false);
    }
  }

  function quick(calc: () => [Date, Date]) {
    const [s, e] = calc();
    onChange({ start: fmt(s), end: fmt(e) });
    setMonth(new Date(e.getFullYear(), e.getMonth(), 1));
    setOpen(false);
  }

  const from = parse(value.start);
  const to = parse(value.end);
  const label = from && to ? `${fmt(from)} ~ ${fmt(to)}` : "选择日期范围";

  return (
    <div className="relative w-full sm:w-auto" ref={ref}>
      <div className="mb-1 flex items-center gap-1.5 text-xs text-foreground-secondary">
        <Calendar size={12} aria-hidden />
        <span>日期</span>
      </div>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label="选择日期范围"
        aria-haspopup="dialog"
        aria-expanded={open}
        className="flex h-8 w-full items-center gap-2 rounded-lg border border-border bg-card px-3 text-sm text-foreground transition-colors hover:border-border-deep focus:border-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:w-auto sm:min-w-[210px] [@media(pointer:coarse)]:h-11"
      >
        <span className={from && to ? "" : "text-foreground-secondary"}>{label}</span>
      </button>

      {open && (
        <div className="absolute left-0 top-full z-50 mt-2 w-[min(440px,calc(100vw-2rem))] animate-fade-up rounded-xl border border-border bg-card p-4 shadow-lg sm:p-5">
          <div className="flex flex-col gap-4 sm:flex-row sm:gap-5">
            {/* 快捷项（窄屏顶部横排 wrap，sm 起左侧竖列） */}
            <div className="flex flex-wrap gap-1 border-b border-border-shallow pb-3 sm:min-w-[92px] sm:flex-col sm:flex-nowrap sm:border-b-0 sm:border-r sm:pb-0 sm:pr-5">
              {QUICK.map((q) => (
                <button
                  key={q.label}
                  onClick={() => quick(q.calc)}
                  className="whitespace-nowrap rounded-lg px-3 py-2 text-left text-sm text-foreground-secondary transition-colors hover:bg-fill-default hover:text-foreground"
                >
                  {q.label}
                </button>
              ))}
            </div>
            {/* 单月日历（react-day-picker，range 模式）。配色由 date-range-picker.css 的 --rdp-* 变量映射到我方绿系 token。 */}
            <DayPicker
              mode="range"
              locale={zhCN}
              month={month}
              onMonthChange={setMonth}
              selected={selected}
              onSelect={handleSelect}
              showOutsideDays
              className="rdp-board"
            />
          </div>
        </div>
      )}
    </div>
  );
}
