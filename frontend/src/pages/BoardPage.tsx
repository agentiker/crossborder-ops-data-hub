import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Boxes, ChevronDown, Flame, Gauge, TrendingUp, Truck } from "lucide-react";
import { api, type BoardData, type LowStockItem, type PendingItem } from "@/api";
import { EChart, useChartTokens } from "@/components/EChart";
import { MetricCard } from "@/components/MetricCard";
import { PageHeader } from "@/components/PageHeader";
import { DataTable, type Column } from "@/components/DataTable";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

const PERIODS: [string, string][] = [
  ["today", "今天"],
  ["last_7d", "近 7 天"],
  ["last_30d", "近 30 天"],
  ["this_month", "本月"],
];

type SectionKey = "overview" | "trend" | "top" | "low" | "fulfillment";
const SECTIONS: { key: SectionKey; label: string; icon: typeof Gauge }[] = [
  { key: "overview", label: "经营概览", icon: Gauge },
  { key: "trend", label: "趋势", icon: TrendingUp },
  { key: "top", label: "爆款榜", icon: Flame },
  { key: "low", label: "库存健康", icon: Boxes },
  { key: "fulfillment", label: "待发货", icon: Truck },
];

const fmtInt = (n: number | undefined) =>
  n == null ? "—" : Number(n).toLocaleString("en-US");
const fmtMoney = (n: number | undefined) =>
  n == null ? "—" : "Rp " + Number(n).toLocaleString("en-US", { maximumFractionDigits: 0 });

export function BoardPage() {
  const [period, setPeriod] = useState("last_30d");
  const [scope, setScope] = useState("");
  const [section, setSection] = useState<SectionKey>("overview");
  const [data, setData] = useState<BoardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    api
      .boardData(period, scope)
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError(String(e)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [period, scope]);

  const periodLabel = PERIODS.find(([k]) => k === period)?.[1] ?? period;
  const updatedAt = data?.fulfillment.snapshot_at || data?.trend.window_label;

  return (
    <div className="flex h-full">
      {/* 页面级左子导航（桌面） */}
      <nav className="hidden w-44 shrink-0 flex-col gap-0.5 border-r bg-card/40 p-3 md:flex">
        {SECTIONS.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            onClick={() => setSection(key)}
            className={cn(
              "flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors",
              section === key
                ? "bg-accent font-medium text-accent-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            <Icon className="size-4" />
            {label}
          </button>
        ))}
      </nav>

      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-5xl px-4 py-6 sm:px-6">
          <PageHeader
            title="运营看板"
            scope={data?.scope}
            period={periodLabel}
            updatedAt={updatedAt}
            actions={
              <div className="flex flex-wrap items-center gap-2">
                {data?.can_switch && data.scopes.length > 1 && (
                  <ScopeSwitcher
                    scopes={data.scopes}
                    value={scope}
                    label={data.scopes.find((s) => (s.key || "") === scope)?.label || "全部范围"}
                    onChange={setScope}
                  />
                )}
                <div className="flex flex-wrap gap-1">
                  {PERIODS.map(([key, label]) => (
                    <button
                      key={key}
                      onClick={() => setPeriod(key)}
                      className={cn(
                        "rounded-full border px-3.5 py-1.5 text-xs font-medium transition-colors",
                        key === period
                          ? "border-primary bg-primary text-primary-foreground"
                          : "bg-card text-muted-foreground hover:border-foreground/30 hover:text-foreground",
                      )}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
            }
          />

          {/* 子导航（移动端横向 chips） */}
          <div className="mt-4 flex gap-1 overflow-x-auto md:hidden">
            {SECTIONS.map(({ key, label }) => (
              <button
                key={key}
                onClick={() => setSection(key)}
                className={cn(
                  "shrink-0 rounded-full border px-3 py-1.5 text-xs font-medium transition-colors",
                  section === key
                    ? "border-primary bg-primary text-primary-foreground"
                    : "bg-card text-muted-foreground",
                )}
              >
                {label}
              </button>
            ))}
          </div>

          {error ? (
            <Card className="mt-6">
              <CardContent className="py-10 text-center text-sm text-destructive">
                加载失败：{error}
              </CardContent>
            </Card>
          ) : (
            <div className="mt-5">
              <Section
                section={section}
                data={data}
                loading={loading}
                periodLabel={periodLabel}
              />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function ScopeSwitcher({
  scopes,
  value,
  label,
  onChange,
}: {
  scopes: { key: string; label: string }[];
  value: string;
  label: string;
  onChange: (k: string) => void;
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger className="flex items-center gap-1 rounded-full border bg-card px-3 py-1.5 text-xs font-medium text-foreground hover:border-foreground/30">
        {label}
        <ChevronDown className="size-3.5 text-muted-foreground" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="max-h-72 overflow-y-auto">
        {scopes.map((s) => (
          <DropdownMenuItem
            key={s.key || "__all__"}
            onClick={() => onChange(s.key || "")}
            className={cn((s.key || "") === value && "font-medium text-primary")}
          >
            {s.label}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function Section({
  section,
  data,
  loading,
  periodLabel,
}: {
  section: SectionKey;
  data: BoardData | null;
  loading: boolean;
  periodLabel: string;
}) {
  switch (section) {
    case "overview":
      return <OverviewSection data={data} loading={loading} periodLabel={periodLabel} />;
    case "trend":
      return <TrendSection data={data} loading={loading} />;
    case "top":
      return <TopSection data={data} loading={loading} />;
    case "low":
      return <LowSection data={data} />;
    case "fulfillment":
      return <FulfillmentSection data={data} />;
  }
}

function OverviewSection({
  data,
  loading,
  periodLabel,
}: {
  data: BoardData | null;
  loading: boolean;
  periodLabel: string;
}) {
  const o = data?.overview.orders;
  const fb = data?.fulfillment.buckets;
  const lb = data?.low.buckets;
  const pts = data?.trend.points ?? [];
  return (
    <>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard loading={loading} label="GMV（已付款）" value={fmtMoney(o?.gmv)} hint={periodLabel} series={pts.map((p) => p.gmv)} />
        <MetricCard loading={loading} label="订单数" value={fmtInt(o?.order_count)} hint={periodLabel} series={pts.map((p) => p.order_count)} />
        <MetricCard loading={loading} label="销量" value={fmtInt(o?.units_sold)} hint={periodLabel} series={pts.map((p) => p.units_sold)} />
        <MetricCard loading={loading} label="客单价" value={fmtMoney(o?.avg_order_value)} hint={periodLabel} />
      </div>
      <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Card>
          <CardContent className="flex items-center justify-between p-5">
            <div>
              <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">待发货</div>
              <div className="tabnum mt-1 text-2xl font-semibold">{fmtInt(fb?.total)}</div>
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
              <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">断货风险</div>
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
    </>
  );
}

function TrendSection({ data, loading }: { data: BoardData | null; loading: boolean }) {
  const t = useChartTokens();
  const pts = data?.trend.points ?? [];
  const labels = pts.map((p) => p.date.slice(5));

  const axis = (extra?: object) => ({
    type: "value" as const,
    splitLine: { lineStyle: { color: t.grid } },
    axisLabel: { color: t.sub },
    ...extra,
  });
  const xAxis = {
    type: "category" as const,
    data: labels,
    boundaryGap: false,
    axisLine: { lineStyle: { color: t.grid } },
    axisLabel: { color: t.sub },
  };

  const gmvOption = useMemo(
    () => ({
      grid: { left: 56, right: 16, top: 20, bottom: 28 },
      tooltip: { trigger: "axis" },
      xAxis,
      yAxis: axis(),
      series: [
        {
          name: "GMV",
          type: "line",
          smooth: true,
          showSymbol: false,
          data: pts.map((p) => p.gmv),
          lineStyle: { color: t.primary, width: 2 },
          areaStyle: { color: t.primary, opacity: 0.1 },
          itemStyle: { color: t.primary },
        },
      ],
    }),
    [data, t],
  );

  const ordersOption = useMemo(
    () => ({
      grid: { left: 44, right: 16, top: 32, bottom: 28 },
      tooltip: { trigger: "axis" },
      legend: { top: 0, textStyle: { color: t.sub }, icon: "roundRect" },
      xAxis,
      yAxis: axis(),
      series: [
        { name: "订单数", type: "line", smooth: true, showSymbol: false, data: pts.map((p) => p.order_count), lineStyle: { color: t.positive, width: 2 }, itemStyle: { color: t.positive } },
        { name: "销量", type: "line", smooth: true, showSymbol: false, data: pts.map((p) => p.units_sold), lineStyle: { color: t.warning, width: 2 }, itemStyle: { color: t.warning } },
      ],
    }),
    [data, t],
  );

  if (loading || !pts.length) {
    return <ChartPlaceholder loading={loading} empty="该时段暂无趋势数据" />;
  }
  return (
    <div className="grid grid-cols-1 gap-3">
      <ChartCard title="GMV 趋势" caption={data?.trend.window_label}>
        <EChart option={gmvOption} height={300} />
      </ChartCard>
      <ChartCard title="订单 / 销量趋势">
        <EChart option={ordersOption} height={280} />
      </ChartCard>
    </div>
  );
}

function TopSection({ data, loading }: { data: BoardData | null; loading: boolean }) {
  const t = useChartTokens();
  const items = (data?.top.items ?? []).slice(0, 10);
  const names = items.map((i) => (i.product_name || i.sku_name || i.sku_id || "?").slice(0, 18));

  const option = useMemo(
    () => ({
      grid: { left: 8, right: 24, top: 12, bottom: 24, containLabel: true },
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
      xAxis: { type: "value", splitLine: { lineStyle: { color: t.grid } }, axisLabel: { color: t.sub } },
      yAxis: { type: "category", inverse: true, data: names, axisLine: { lineStyle: { color: t.grid } }, axisLabel: { color: t.text } },
      series: [
        {
          name: "销量",
          type: "bar",
          data: items.map((i) => i.units_sold),
          itemStyle: { color: t.primary, borderRadius: [0, 4, 4, 0] },
          barWidth: "58%",
        },
      ],
    }),
    [data, t],
  );

  if (loading) return <ChartPlaceholder loading empty="" />;
  if (!items.length) return <ChartPlaceholder loading={false} empty="该时段暂无销量数据" />;
  return (
    <ChartCard title="爆款单品榜" caption="按销量 Top 10">
      <EChart option={option} height={Math.max(240, items.length * 34)} />
    </ChartCard>
  );
}

const LOW_LABEL: Record<string, string> = { stockout: "断货", critical: "告急", warning: "预警" };

function LowSection({ data }: { data: BoardData | null }) {
  const b = data?.low.buckets;
  const items = data?.low.items ?? [];
  const columns: Column<LowStockItem>[] = [
    { key: "name", header: "商品", render: (r) => r.product_name || r.sku_id },
    {
      key: "bucket",
      header: "风险",
      render: (r) => (
        <Badge variant={r.bucket === "warning" ? "warning" : "destructive"}>
          {LOW_LABEL[r.bucket] || r.bucket}
        </Badge>
      ),
    },
    { key: "stock", header: "可用库存", numeric: true, render: (r) => fmtInt(r.available_stock) },
    { key: "vel", header: "日均销速", numeric: true, render: (r) => Number(r.daily_velocity).toFixed(1) },
    { key: "cover", header: "可售天数", numeric: true, render: (r) => Number(r.days_of_cover).toFixed(1) },
  ];
  return (
    <>
      <div className="mb-3 flex flex-wrap gap-2">
        <Badge variant="destructive">断货 {b?.stockout ?? 0}</Badge>
        <Badge variant="warning">告急 {b?.critical ?? 0}</Badge>
        <Badge variant="secondary">预警 {b?.warning ?? 0}</Badge>
        <span className="self-center text-xs text-muted-foreground">可售天数 = 可用库存 ÷ 日均销速</span>
      </div>
      <DataTable columns={columns} rows={items} rowKey={(r) => r.sku_id} empty="暂无断货风险 SKU" />
    </>
  );
}

const PEND_LABEL: Record<string, string> = { overdue: "超时", critical: "临界", normal: "正常", unknown: "未知" };

function FulfillmentSection({ data }: { data: BoardData | null }) {
  const b = data?.fulfillment.buckets;
  const items = data?.fulfillment.items ?? [];
  const columns: Column<PendingItem>[] = [
    { key: "order", header: "订单", render: (r) => <span className="font-mono text-xs">{String(r.order_id).slice(-8)}</span> },
    { key: "shop", header: "店铺", render: (r) => r.shop_id ?? "—" },
    { key: "product", header: "商品", render: (r) => (r.first_product_name || "—").slice(0, 20) },
    {
      key: "bucket",
      header: "状态",
      render: (r) =>
        r.bucket ? (
          <Badge variant={r.bucket === "overdue" ? "destructive" : r.bucket === "critical" ? "warning" : "secondary"}>
            {PEND_LABEL[r.bucket] || r.bucket}
          </Badge>
        ) : (
          "—"
        ),
    },
    { key: "count", header: "件数", numeric: true, render: (r) => fmtInt(r.item_count) },
    { key: "amount", header: "金额", numeric: true, render: (r) => fmtMoney(r.total_amount) },
  ];
  return (
    <>
      <div className="mb-3 flex flex-wrap gap-2">
        <Badge variant="destructive">超时 {b?.overdue ?? 0}</Badge>
        <Badge variant="warning">临界 {b?.critical ?? 0}</Badge>
        <Badge variant="secondary">正常 {b?.normal ?? 0}</Badge>
        {data?.fulfillment.snapshot_at && (
          <span className="self-center text-xs text-muted-foreground">快照 {data.fulfillment.snapshot_at}</span>
        )}
      </div>
      <DataTable columns={columns} rows={items} rowKey={(r) => String(r.order_id)} empty="暂无待发货订单" />
    </>
  );
}

function ChartCard({ title, caption, children }: { title: string; caption?: string; children: ReactNode }) {
  return (
    <Card>
      <CardContent className="p-5">
        <h2 className="mb-3 text-sm font-semibold">
          {title}
          {caption && <span className="ml-2 text-xs font-normal text-muted-foreground">{caption}</span>}
        </h2>
        {children}
      </CardContent>
    </Card>
  );
}

function ChartPlaceholder({ loading, empty }: { loading: boolean; empty: string }) {
  return (
    <Card>
      <CardContent className="py-16 text-center text-sm text-muted-foreground">
        {loading ? "加载中…" : empty}
      </CardContent>
    </Card>
  );
}
