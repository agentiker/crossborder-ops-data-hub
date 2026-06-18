import { useEffect, useState } from "react";
import { api, type BoardData } from "@/api";
import { MetricCard } from "@/components/MetricCard";
import { PageHeader } from "@/components/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

const PERIODS: [string, string][] = [
  ["today", "今天"],
  ["last_7d", "近 7 天"],
  ["last_30d", "近 30 天"],
  ["this_month", "本月"],
];

const fmtInt = (n: number | undefined) =>
  n == null ? "—" : Number(n).toLocaleString("en-US");
const fmtMoney = (n: number | undefined) =>
  n == null ? "—" : "Rp " + Number(n).toLocaleString("en-US", { maximumFractionDigits: 0 });

export function BoardPage() {
  const [period, setPeriod] = useState("last_30d");
  const [data, setData] = useState<BoardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    api
      .boardData(period)
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError(String(e)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [period]);

  const o = data?.overview.orders;
  const fb = data?.fulfillment.buckets;
  const lb = data?.low.buckets;
  const pts = data?.trend.points ?? [];
  const gmvSeries = pts.map((p) => p.gmv);
  const orderSeries = pts.map((p) => p.order_count);
  const unitSeries = pts.map((p) => p.units_sold);

  const periodLabel = PERIODS.find(([k]) => k === period)?.[1] ?? period;
  const updatedAt = data?.fulfillment.snapshot_at || data?.trend.window_label;

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-6xl px-4 py-6 sm:px-6">
        <PageHeader
          title="运营看板"
          scope={data?.scope}
          period={periodLabel}
          updatedAt={updatedAt}
          actions={
            <div className="flex flex-wrap gap-1">
              {PERIODS.map(([key, label]) => (
                <button
                  key={key}
                  onClick={() => setPeriod(key)}
                  className={cn(
                    "rounded-full border px-3 py-1 text-xs font-medium transition-colors",
                    key === period
                      ? "border-primary bg-primary/10 text-primary"
                      : "text-muted-foreground hover:bg-accent/60",
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
          }
        />

        {error ? (
          <Card className="mt-6">
            <CardContent className="py-10 text-center text-sm text-destructive">
              加载失败：{error}
            </CardContent>
          </Card>
        ) : (
          <>
            <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <MetricCard
                loading={loading}
                label="GMV（已付款）"
                value={fmtMoney(o?.gmv)}
                hint={periodLabel}
                series={gmvSeries}
              />
              <MetricCard
                loading={loading}
                label="订单数"
                value={fmtInt(o?.order_count)}
                hint={periodLabel}
                series={orderSeries}
              />
              <MetricCard
                loading={loading}
                label="销量"
                value={fmtInt(o?.units_sold)}
                hint={periodLabel}
                series={unitSeries}
              />
              <MetricCard
                loading={loading}
                label="客单价"
                value={fmtMoney(o?.avg_order_value)}
                hint={periodLabel}
              />
            </div>

            <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Card>
                <CardContent className="flex items-center justify-between p-5">
                  <div>
                    <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                      待发货
                    </div>
                    <div className="tabnum mt-1 text-2xl font-semibold">
                      {fmtInt(fb?.total)}
                    </div>
                  </div>
                  <div className="flex flex-col items-end gap-1">
                    <Badge variant="destructive">超时 {fb?.overdue ?? 0}</Badge>
                    <Badge variant="warning">临界 {fb?.critical ?? 0}</Badge>
                  </div>
                </CardContent>
              </Card>
              <Card>
                <CardContent className="flex items-center justify-between p-5">
                  <div>
                    <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                      断货风险
                    </div>
                    <div className="tabnum mt-1 text-2xl font-semibold">
                      {fmtInt((lb?.stockout ?? 0) + (lb?.critical ?? 0) + (lb?.warning ?? 0))}
                    </div>
                  </div>
                  <div className="flex flex-col items-end gap-1">
                    <Badge variant="destructive">断货 {lb?.stockout ?? 0}</Badge>
                    <Badge variant="warning">告急 {lb?.critical ?? 0}</Badge>
                  </div>
                </CardContent>
              </Card>
            </div>

            <Card className="mt-3 border-dashed">
              <CardContent className="py-8 text-center text-sm text-muted-foreground">
                GMV / 订单趋势、爆款榜、库存健康等完整图表将在 Phase B 接入（ECharts）。
                <br />
                现有内联看板仍可访问 <a className="text-primary underline" href="/board">/board</a>。
              </CardContent>
            </Card>
          </>
        )}
      </div>
    </div>
  );
}
