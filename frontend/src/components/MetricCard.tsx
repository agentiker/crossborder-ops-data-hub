import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Sparkline } from "@/components/Sparkline";
import { cn } from "@/lib/utils";

// 招牌 KPI 卡（plan/15 UI 地基）：标签 + 等宽数字大值 + 涨跌 + 内嵌迷你趋势。
// 「数据中枢」的身份靠数字呈现立住，看板/概览复用同一张卡。
interface Props {
  label: string;
  value: string;
  hint?: string;        // 副标，如 "近 7 天"
  delta?: number;       // 百分比，正负染色；不传则不显示
  series?: number[];    // 迷你趋势数据
  loading?: boolean;
  className?: string;
}

export function MetricCard({ label, value, hint, delta, series, loading, className }: Props) {
  if (loading) {
    return (
      <Card className={cn("p-5", className)}>
        <Skeleton className="h-3.5 w-16" />
        <Skeleton className="mt-3 h-7 w-28" />
        <Skeleton className="mt-3 h-7 w-full" />
      </Card>
    );
  }

  const hasDelta = typeof delta === "number" && Number.isFinite(delta);
  const up = hasDelta && delta! > 0;
  const down = hasDelta && delta! < 0;

  return (
    <Card className={cn("p-5", className)}>
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {label}
        </span>
        {hint && <span className="text-xs text-muted-foreground">{hint}</span>}
      </div>

      <div className="mt-2 flex items-end justify-between gap-3">
        <div className="tabnum text-2xl font-semibold leading-tight">{value}</div>
        {series && series.length >= 2 && <Sparkline data={series} className="shrink-0" />}
      </div>

      {hasDelta && (
        <div
          className={cn(
            "tabnum mt-2 text-xs font-medium",
            up && "text-positive",
            down && "text-negative",
            !up && !down && "text-muted-foreground",
          )}
        >
          {up ? "▲" : down ? "▼" : "—"} {Math.abs(delta!).toFixed(1)}%
          <span className="ml-1 font-normal text-muted-foreground">环比</span>
        </div>
      )}
    </Card>
  );
}
