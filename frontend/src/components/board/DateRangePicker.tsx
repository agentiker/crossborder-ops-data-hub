import { useEffect, useRef, useState } from "react";
import { Calendar, ChevronLeft, ChevronRight } from "lucide-react";

// 日历范围选择器（移植 forkStoreClaw demo，换我方 token）。
// 受控：父组件给 value（YYYY-MM-DD 起止），选定后 onChange 回吐同格式字符串。
// 快捷项语义按"含端点天数"：近 7 天 = 今天往前 6 天 ~ 今天（与后端 last_7d 对齐）。

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

function daysInMonth(y: number, m: number) {
  return new Date(y, m + 1, 0).getDate();
}
function firstDayOfMonth(y: number, m: number) {
  return new Date(y, m, 1).getDay();
}
function sameDay(a: Date, b: Date) {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}
function inRange(d: Date, s: Date | null, e: Date | null) {
  if (!s || !e) return false;
  return d.getTime() >= s.getTime() && d.getTime() <= e.getTime();
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

const MONTHS = ["1月", "2月", "3月", "4月", "5月", "6月", "7月", "8月", "9月", "10月", "11月", "12月"];
const DOW = ["日", "一", "二", "三", "四", "五", "六"];

export function DateRangePicker({
  value,
  onChange,
}: {
  value: DateRangeValue;
  onChange: (range: { start: string; end: string }) => void;
}) {
  const [open, setOpen] = useState(false);
  const init = parse(value.start) ?? new Date();
  const [cm, setCm] = useState(init.getMonth());
  const [cy, setCy] = useState(init.getFullYear());
  const [hover, setHover] = useState<Date | null>(null);
  const [selecting, setSelecting] = useState(false);
  const [draft, setDraft] = useState<{ start: Date | null; end: Date | null }>({
    start: parse(value.start),
    end: parse(value.end),
  });
  const ref = useRef<HTMLDivElement>(null);

  // 父级 value 变化（如初始默认）→ 同步草稿，保证按钮文案与外部一致。
  useEffect(() => {
    setDraft({ start: parse(value.start), end: parse(value.end) });
  }, [value.start, value.end]);

  useEffect(() => {
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);

  function emit(s: Date, e: Date) {
    onChange({ start: fmt(s), end: fmt(e) });
  }

  function clickDay(d: Date) {
    if (!selecting) {
      setDraft({ start: d, end: null });
      setSelecting(true);
    } else {
      const s = draft.start!;
      const next = d < s ? { start: d, end: s } : { start: s, end: d };
      setDraft(next);
      setSelecting(false);
      emit(next.start, next.end);
      setOpen(false);
    }
  }

  function quick(calc: () => [Date, Date]) {
    const [s, e] = calc();
    setDraft({ start: s, end: e });
    setSelecting(false);
    setCm(e.getMonth());
    setCy(e.getFullYear());
    emit(s, e);
    setOpen(false);
  }

  function prev() {
    if (cm === 0) { setCm(11); setCy(cy - 1); } else setCm(cm - 1);
  }
  function next() {
    if (cm === 11) { setCm(0); setCy(cy + 1); } else setCm(cm + 1);
  }

  const dim = daysInMonth(cy, cm);
  const fd = firstDayOfMonth(cy, cm);
  const today = new Date();
  // 选区进行中用 hover 预览另一端
  const ds = selecting && hover && draft.start && hover < draft.start ? hover : draft.start;
  const de = selecting && hover && draft.start && hover > draft.start ? hover : draft.end;

  const label =
    draft.start && draft.end ? `${fmt(draft.start)} ~ ${fmt(draft.end)}` : "选择日期范围";

  return (
    <div className="relative" ref={ref}>
      <div className="mb-1 flex items-center gap-1.5 text-xs text-foreground-tertiary">
        <Calendar size={12} />
        <span>日期</span>
      </div>
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex h-8 min-w-[210px] items-center gap-2 rounded-lg border border-border bg-card px-3 text-sm text-foreground transition-colors hover:border-border-deep focus:border-primary focus:outline-none"
      >
        <span className={draft.start && draft.end ? "" : "text-foreground-tertiary"}>{label}</span>
      </button>

      {open && (
        <div
          className="absolute left-0 top-full z-50 mt-2 animate-fade-up rounded-xl border border-border bg-card p-5 shadow-lg"
          style={{ minWidth: 480 }}
        >
          <div className="flex gap-5">
            {/* 快捷项 */}
            <div className="flex min-w-[110px] flex-col gap-1 border-r border-border-shallow pr-5">
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
            {/* 日历 */}
            <div className="flex-1">
              <div className="mb-4 flex items-center justify-between">
                <button onClick={prev} className="rounded-lg p-1.5 hover:bg-fill-default">
                  <ChevronLeft size={18} />
                </button>
                <span className="text-sm font-semibold text-foreground">
                  {cy}年{MONTHS[cm]}
                </span>
                <button onClick={next} className="rounded-lg p-1.5 hover:bg-fill-default">
                  <ChevronRight size={18} />
                </button>
              </div>
              <div className="mb-1 grid grid-cols-7 gap-0">
                {DOW.map((d) => (
                  <div
                    key={d}
                    className="flex h-8 w-10 items-center justify-center text-xs font-medium text-foreground-tertiary"
                  >
                    {d}
                  </div>
                ))}
              </div>
              <div className="grid grid-cols-7 gap-0">
                {Array.from({ length: fd }).map((_, i) => (
                  <div key={`e-${i}`} className="h-10 w-10" />
                ))}
                {Array.from({ length: dim }).map((_, i) => {
                  const day = i + 1;
                  const date = new Date(cy, cm, day);
                  const isToday = sameDay(date, today);
                  const isStart = ds && sameDay(date, ds);
                  const isEnd = de && sameDay(date, de);
                  const within = inRange(date, ds, de);
                  const sel = isStart || isEnd;
                  return (
                    <button
                      key={day}
                      onClick={() => clickDay(date)}
                      onMouseEnter={() => setHover(date)}
                      className={
                        "flex h-10 w-10 items-center justify-center rounded-lg text-sm text-foreground transition-colors " +
                        (sel
                          ? "bg-foreground font-medium text-background "
                          : within
                            ? "bg-fill-default "
                            : "hover:bg-fill-default ") +
                        (isToday && !sel ? "ring-1 ring-foreground" : "")
                      }
                    >
                      {day}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
