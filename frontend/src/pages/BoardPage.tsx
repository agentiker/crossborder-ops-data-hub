import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  ChevronDown,
  DollarSign,
  ShoppingCart,
  TrendingUp,
  Wallet,
} from "lucide-react";
import { api, type BoardData, type LowStockItem, type PendingItem } from "@/api";
import { EChart, useChartTokens } from "@/components/EChart";
import { DataTable, type Column } from "@/components/DataTable";
import { Badge } from "@/components/ui/badge";
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

const fmtInt = (n: number | undefined) =>
  n == null ? "—" : Number(n).toLocaleString("en-US");
const fmtMoney = (n: number | undefined) =>
  n == null
    ? "—"
    : "Rp " + Number(n).toLocaleString("en-US", { maximumFractionDigits: 0 });

export function BoardPage() {
  const [period, setPeriod] = useState("last_30d");
  const [scope, setScope] = useState("");
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

  return (
    <section className="flex h-full flex-col">
      {/* 顶部标题条 */}
      <header className="sticky top-0 z-20 flex h-[68px] shrink-0 items-center justify-between gap-2 border-b border-border-shallow bg-card px-4 sm:px-6">
        <h1 className="truncate text-lg font-medium leading-6 text-foreground">
          运营看板
        </h1>
        {data?.scope && (
          <span className="hidden text-xs text-foreground-tertiary sm:inline">
            范围 · {data.scope}
          </span>
        )}
      </header>

      {/* 筛选条：范围 + 时段 */}
      <div className="flex flex-wrap items-end gap-4 border-b border-border-shallow bg-card px-4 py-3 sm:px-6">
        {data?.can_switch && data.scopes.length > 1 && (
          <FilterField label="范围">
            <ScopeSwitcher
              scopes={data.scopes}
              value={scope}
              label={
                data.scopes.find((s) => (s.key || "") === scope)?.label ||
                "全部范围"
              }
              onChange={setScope}
            />
          </FilterField>
        )}
        <FilterField label="时段">
          <div className="flex gap-1 rounded-lg bg-fill p-0.5">
            {PERIODS.map(([key, label]) => (
              <button
                key={key}
                onClick={() => setPeriod(key)}
                className={cn(
                  "rounded-md px-3 py-1.5 text-xs font-medium transition-colors",
                  key === period
                    ? "bg-card text-foreground shadow-sm"
                    : "text-foreground-tertiary hover:text-foreground",
                )}
              >
                {label}
              </button>
            ))}
          </div>
        </FilterField>
      </div>

      {/* 内容区 */}
      <div className="flex-1 overflow-y-auto p-4 sm:p-6">
        <div className="mx-auto max-w-[1400px] space-y-6">
          {error ? (
            <Panel>
              <div className="py-10 text-center text-sm text-destructive">
                加载失败：{error}
              </div>
            </Panel>
          ) : (
            <>
              <BusinessOverview data={data} loading={loading} />
              <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
                <HotProducts data={data} loading={loading} />
                <InventoryHealth data={data} loading={loading} />
              </div>
              <OrderFulfillment data={data} loading={loading} />
            </>
          )}
        </div>
      </div>
    </section>
  );
}

/* ── 通用壳件 ──────────────────────────────────────────────── */

// 仿 fork：圆角 2xl + 浅边 + 卡底，作为各 Section 容器
function Panel({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "rounded-2xl border border-border-shallow bg-card p-5 shadow-sm",
        className,
      )}
    >
      {children}
    </div>
  );
}

function PanelHead({
  title,
  right,
}: {
  title: string;
  right?: ReactNode;
}) {
  return (
    <div className="mb-4 flex items-center justify-between gap-3">
      <h3 className="text-base font-semibold text-foreground">{title}</h3>
      {right}
    </div>
  );
}

// 分段切换器（fork 的 pill-tab 风格）
function SegTabs<T extends string>({
  tabs,
  value,
  onChange,
}: {
  tabs: { id: T; label: string }[];
  value: T;
  onChange: (id: T) => void;
}) {
  return (
    <div className="flex gap-1 rounded-lg bg-fill p-0.5">
      {tabs.map((t) => (
        <button
          key={t.id}
          onClick={() => onChange(t.id)}
          className={cn(
            "rounded-md px-3 py-1.5 text-xs font-medium transition-colors",
            value === t.id
              ? "bg-card text-foreground shadow-sm"
              : "text-foreground-tertiary hover:text-foreground",
          )}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

function FilterField({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs text-foreground-tertiary">{label}</span>
      {children}
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
      <DropdownMenuTrigger className="flex h-8 items-center gap-1.5 rounded-lg border border-border bg-card px-3 text-sm text-foreground transition-colors hover:border-border-deep">
        {label}
        <ChevronDown className="size-3.5 text-foreground-tertiary" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="max-h-72 overflow-y-auto">
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

// 内嵌 KPI 小卡（fork BusinessOverview 的 MetricCard）
function KpiTile({
  title,
  value,
  icon,
  loading,
}: {
  title: string;
  value: string;
  icon: ReactNode;
  loading?: boolean;
}) {
  return (
    <div className="flex flex-col gap-1 rounded-xl bg-fill-shallow p-4">
      <div className="flex items-center gap-2 text-foreground-tertiary">
        {icon}
        <span className="text-xs">{title}</span>
      </div>
      <div className="tabnum text-2xl font-bold text-foreground">
        {loading ? "…" : value}
      </div>
    </div>
  );
}

function ChartEmpty({
  loading,
  empty,
  height = 220,
}: {
  loading: boolean;
  empty: string;
  height?: number;
}) {
  return (
    <div
      className="flex items-center justify-center text-sm text-foreground-tertiary"
      style={{ height }}
    >
      {loading ? "加载中…" : empty}
    </div>
  );
}

/* ── 经营概览（KPI + 销售/订单趋势 tab）─────────────────────── */

type OverviewTab = "sales" | "traffic";

function BusinessOverview({
  data,
  loading,
}: {
  data: BoardData | null;
  loading: boolean;
}) {
  const t = useChartTokens();
  const [tab, setTab] = useState<OverviewTab>("sales");
  const o = data?.overview.orders;
  const pts = data?.trend.points ?? [];
  const labels = pts.map((p) => p.date.slice(5));

  const baseAxisX = {
    type: "category" as const,
    data: labels,
    boundaryGap: tab === "traffic",
    axisLine: { lineStyle: { color: t.grid } },
    axisLabel: { color: t.sub, rotate: labels.length > 12 ? 45 : 0 },
  };
  const baseAxisY = {
    type: "value" as const,
    axisLine: { show: false },
    splitLine: { lineStyle: { color: t.grid } },
    axisLabel: { color: t.sub },
  };

  const salesOption = useMemo(
    () => ({
      tooltip: { trigger: "axis" as const },
      grid: { top: 12, right: 16, bottom: 28, left: 60 },
      xAxis: baseAxisX,
      yAxis: baseAxisY,
      series: [
        {
          name: "GMV",
          type: "line",
          smooth: true,
          showSymbol: false,
          data: pts.map((p) => p.gmv),
          lineStyle: { color: t.primary, width: 3 },
          itemStyle: { color: t.primary },
          areaStyle: {
            color: {
              type: "linear" as const,
              x: 0,
              y: 0,
              x2: 0,
              y2: 1,
              colorStops: [
                { offset: 0, color: t.primary },
                { offset: 1, color: "transparent" },
              ],
            },
            opacity: 0.18,
          },
        },
      ],
    }),
    [data, t],
  );

  const trafficOption = useMemo(
    () => ({
      tooltip: { trigger: "axis" as const },
      legend: {
        data: ["订单数", "销量"],
        bottom: 0,
        textStyle: { color: t.sub, fontSize: 11 },
        icon: "roundRect",
      },
      grid: { top: 12, right: 16, bottom: 40, left: 50 },
      xAxis: baseAxisX,
      yAxis: baseAxisY,
      series: [
        {
          name: "订单数",
          type: "line",
          smooth: true,
          showSymbol: false,
          data: pts.map((p) => p.order_count),
          lineStyle: { color: t.positive, width: 2 },
          itemStyle: { color: t.positive },
        },
        {
          name: "销量",
          type: "bar",
          data: pts.map((p) => p.units_sold),
          itemStyle: { color: t.warning, borderRadius: [4, 4, 0, 0] },
          barMaxWidth: 22,
        },
      ],
    }),
    [data, t],
  );

  const tabs: { id: OverviewTab; label: string }[] = [
    { id: "sales", label: "销售趋势" },
    { id: "traffic", label: "订单 / 销量" },
  ];

  return (
    <Panel>
      <PanelHead
        title="经营概览"
        right={<SegTabs tabs={tabs} value={tab} onChange={setTab} />}
      />

      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiTile
          loading={loading}
          title="GMV（已付款）"
          value={fmtMoney(o?.gmv)}
          icon={<DollarSign size={14} />}
        />
        <KpiTile
          loading={loading}
          title="订单数"
          value={fmtInt(o?.order_count)}
          icon={<ShoppingCart size={14} />}
        />
        <KpiTile
          loading={loading}
          title="销量"
          value={fmtInt(o?.units_sold)}
          icon={<TrendingUp size={14} />}
        />
        <KpiTile
          loading={loading}
          title="客单价"
          value={fmtMoney(o?.avg_order_value)}
          icon={<Wallet size={14} />}
        />
      </div>

      {loading || !pts.length ? (
        <ChartEmpty loading={loading} empty="该时段暂无趋势数据" height={220} />
      ) : (
        <div className="h-[220px]">
          <EChart
            option={tab === "sales" ? salesOption : trafficOption}
            height={220}
          />
        </div>
      )}
    </Panel>
  );
}

/* ── 爆款商品 TOP（榜单 + 条形）────────────────────────────── */

type RankBy = "sales" | "gmv";

function HotProducts({
  data,
  loading,
}: {
  data: BoardData | null;
  loading: boolean;
}) {
  const t = useChartTokens();
  const [rankBy, setRankBy] = useState<RankBy>("sales");
  const raw = data?.top.items ?? [];

  const items = useMemo(() => {
    const sorted = [...raw].sort((a, b) =>
      rankBy === "gmv"
        ? (b.gmv ?? 0) - (a.gmv ?? 0)
        : b.units_sold - a.units_sold,
    );
    return sorted.slice(0, 10);
  }, [raw, rankBy]);

  const names = items.map((i) =>
    (i.product_name || i.sku_name || i.sku_id || "?").slice(0, 16),
  );

  const option = useMemo(
    () => ({
      grid: { left: 8, right: 24, top: 8, bottom: 8, containLabel: true },
      tooltip: { trigger: "axis" as const, axisPointer: { type: "shadow" as const } },
      xAxis: {
        type: "value" as const,
        axisLine: { show: false },
        splitLine: { lineStyle: { color: t.grid } },
        axisLabel: { color: t.sub },
      },
      yAxis: {
        type: "category" as const,
        inverse: true,
        data: names,
        axisLine: { lineStyle: { color: t.grid } },
        axisTick: { show: false },
        axisLabel: { color: t.text },
      },
      series: [
        {
          name: rankBy === "gmv" ? "GMV" : "销量",
          type: "bar",
          data: items.map((i) =>
            rankBy === "gmv" ? i.gmv ?? 0 : i.units_sold,
          ),
          itemStyle: { color: t.primary, borderRadius: [0, 4, 4, 0] },
          barWidth: "56%",
        },
      ],
    }),
    [items, names, rankBy, t],
  );

  const rankOptions: { id: RankBy; label: string }[] = [
    { id: "sales", label: "按销量" },
    { id: "gmv", label: "按 GMV" },
  ];

  return (
    <Panel>
      <PanelHead
        title="爆款商品 TOP 10"
        right={<SegTabs tabs={rankOptions} value={rankBy} onChange={setRankBy} />}
      />
      {loading ? (
        <ChartEmpty loading empty="" height={340} />
      ) : !items.length ? (
        <ChartEmpty loading={false} empty="该时段暂无销量数据" height={340} />
      ) : (
        <EChart option={option} height={Math.max(280, items.length * 34)} />
      )}
    </Panel>
  );
}

/* ── 库存健康（汇总环图 + 商品明细表）──────────────────────── */

type InventoryView = "summary" | "details";
const LOW_LABEL: Record<string, string> = {
  stockout: "断货",
  critical: "告急",
  warning: "预警",
};

function InventoryHealth({
  data,
  loading,
}: {
  data: BoardData | null;
  loading: boolean;
}) {
  const t = useChartTokens();
  const [view, setView] = useState<InventoryView>("summary");
  const b = data?.low.buckets;
  const items = data?.low.items ?? [];

  const stockout = b?.stockout ?? 0;
  const critical = b?.critical ?? 0;
  const warning = b?.warning ?? 0;
  const atRisk = stockout + critical + warning;

  const donutOption = useMemo(
    () => ({
      tooltip: { trigger: "item" as const },
      legend: {
        bottom: 0,
        textStyle: { color: t.sub, fontSize: 11 },
        icon: "roundRect",
      },
      series: [
        {
          type: "pie",
          radius: ["48%", "72%"],
          center: ["50%", "44%"],
          avoidLabelOverlap: false,
          itemStyle: { borderRadius: 6, borderColor: t.text, borderWidth: 0 },
          label: { show: false },
          data: [
            { name: "断货", value: stockout, itemStyle: { color: t.negative } },
            { name: "告急", value: critical, itemStyle: { color: t.warning } },
            { name: "预警", value: warning, itemStyle: { color: t.positive } },
          ],
        },
      ],
    }),
    [stockout, critical, warning, t],
  );

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
    {
      key: "stock",
      header: "库存",
      numeric: true,
      render: (r) => fmtInt(r.available_stock),
    },
    {
      key: "vel",
      header: "日均销量",
      numeric: true,
      render: (r) => Number(r.daily_velocity).toFixed(1),
    },
    {
      key: "cover",
      header: "可售天数",
      numeric: true,
      render: (r) => Number(r.days_of_cover).toFixed(1),
    },
  ];

  return (
    <Panel>
      <PanelHead
        title="库存健康"
        right={
          <SegTabs
            tabs={[
              { id: "summary", label: "汇总" },
              { id: "details", label: "商品明细" },
            ]}
            value={view}
            onChange={setView}
          />
        }
      />

      {view === "summary" ? (
        loading ? (
          <ChartEmpty loading empty="" height={280} />
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="h-[220px]">
              {atRisk ? (
                <EChart option={donutOption} height={220} />
              ) : (
                <ChartEmpty loading={false} empty="暂无断货风险" height={220} />
              )}
            </div>
            <div className="flex flex-col justify-center gap-3">
              <RiskRow color="bg-negative" label="断货" value={stockout} />
              <RiskRow color="bg-warning" label="告急" value={critical} />
              <RiskRow color="bg-positive" label="预警" value={warning} />
              <p className="mt-1 text-xs text-foreground-tertiary">
                可售天数 = 可用库存 ÷ 日均销量
              </p>
            </div>
          </div>
        )
      ) : (
        <DataTable
          columns={columns}
          rows={items}
          rowKey={(r) => r.sku_id}
          empty="暂无断货风险 SKU"
        />
      )}
    </Panel>
  );
}

function RiskRow({
  color,
  label,
  value,
}: {
  color: string;
  label: string;
  value: number;
}) {
  return (
    <div className="flex items-center gap-3">
      <span className={cn("size-2.5 rounded-full", color)} />
      <span className="flex-1 text-sm text-foreground-secondary">{label}</span>
      <span className="tabnum text-lg font-semibold text-foreground">
        {fmtInt(value)}
      </span>
    </div>
  );
}

/* ── 待发货 / 订单履约（KPI 行 + 明细表）──────────────────── */

const PEND_LABEL: Record<string, string> = {
  overdue: "超时",
  critical: "临界",
  normal: "正常",
  unknown: "未知",
};

function OrderFulfillment({
  data,
  loading,
}: {
  data: BoardData | null;
  loading: boolean;
}) {
  const b = data?.fulfillment.buckets;
  const items = data?.fulfillment.items ?? [];

  const columns: Column<PendingItem>[] = [
    {
      key: "order",
      header: "订单",
      render: (r) => (
        <span className="font-mono text-xs">{String(r.order_id).slice(-8)}</span>
      ),
    },
    { key: "shop", header: "店铺", render: (r) => r.shop_id ?? "—" },
    {
      key: "product",
      header: "商品",
      render: (r) => (r.first_product_name || "—").slice(0, 20),
    },
    {
      key: "bucket",
      header: "状态",
      render: (r) =>
        r.bucket ? (
          <Badge
            variant={
              r.bucket === "overdue"
                ? "destructive"
                : r.bucket === "critical"
                  ? "warning"
                  : "secondary"
            }
          >
            {PEND_LABEL[r.bucket] || r.bucket}
          </Badge>
        ) : (
          "—"
        ),
    },
    {
      key: "count",
      header: "件数",
      numeric: true,
      render: (r) => fmtInt(r.item_count),
    },
    {
      key: "amount",
      header: "金额",
      numeric: true,
      render: (r) => fmtMoney(r.total_amount),
    },
  ];

  return (
    <Panel>
      <PanelHead
        title="待发货订单"
        right={
          data?.fulfillment.snapshot_at ? (
            <span className="text-xs text-foreground-tertiary">
              快照 {data.fulfillment.snapshot_at}
            </span>
          ) : undefined
        }
      />

      <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <KpiStat label="待发货合计" value={fmtInt(b?.total)} loading={loading} />
        <KpiStat
          label="超时"
          value={fmtInt(b?.overdue)}
          tone="negative"
          loading={loading}
        />
        <KpiStat
          label="临界"
          value={fmtInt(b?.critical)}
          tone="warning"
          loading={loading}
        />
        <KpiStat label="正常" value={fmtInt(b?.normal)} loading={loading} />
      </div>

      <DataTable
        columns={columns}
        rows={items}
        rowKey={(r) => String(r.order_id)}
        empty="暂无待发货订单"
      />
    </Panel>
  );
}

function KpiStat({
  label,
  value,
  tone,
  loading,
}: {
  label: string;
  value: string;
  tone?: "negative" | "warning";
  loading?: boolean;
}) {
  return (
    <div className="rounded-xl bg-fill-shallow p-4">
      <div className="text-xs text-foreground-tertiary">{label}</div>
      <div
        className={cn(
          "tabnum mt-1 text-2xl font-bold text-foreground",
          tone === "negative" && "text-negative",
          tone === "warning" && "text-warning",
        )}
      >
        {loading ? "…" : value}
      </div>
    </div>
  );
}
