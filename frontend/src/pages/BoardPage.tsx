import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  ArrowUpDown,
  CalendarRange,
  ChevronDown,
  DollarSign,
  Gauge,
  Megaphone,
  Search,
  ShoppingCart,
  Store,
  TrendingUp,
  Wallet,
} from "lucide-react";
import { api, type BoardData, type LowStockItem, type TopSku } from "@/api";
import { EChart, useChartTokens } from "@/components/EChart";
import {
  DEMO_ORDERS,
  DEMO_REFUNDS,
  DEMO_RETURNS,
  funnelOption,
  ordersStackOption,
  refundsOption,
  returnReasonsOption,
  returnsOption,
  trafficOption,
} from "@/components/board/demo-data";

// 照搬 forkStoreClaw/src/components/Dashboard/* 的版式/卡片/分段 tab/图表观感（1:1）。
// 三处按本项目落差替换并注释：
//   ①数据：接真实 api.boardData()（period/scope 维度），fork 的 mock filter（区域/平台/店铺/日期）
//          收敛到我方真实的「时段 + 范围」两维。
//   ①数据缺口（不造假）：fork 的「转化漏斗 / 流量 UV·PV / 单品 7 天趋势 / 利润排序 / 环比涨跌 /
//          退货·退款·平台拆分」后端无对应数据源 → 对应 tab/行降级或省略；底部满宽段换成我方真实的
//          「待发货履约」（fork OrderTrends 的卡片骨架 + 我方履约数据）。
//   ③品牌：销售趋势线照搬 fork 的靛蓝 #6366f1（用户拍板 1:1 复刻彩色面积效果）；
//          其余自创/降级区块（订单·销量、库存仪表盘/环图）走自有色系 token（绿 t.positive / 橙 t.warning）。

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

  const canSwitch = !!data?.can_switch && (data?.scopes.length ?? 0) > 1;

  return (
    <section className="flex h-full flex-col">
      {/* Header（照 fork DashboardPage：h-[68px] + 底边） */}
      <header className="sticky top-0 z-50 flex h-[68px] shrink-0 items-center justify-between gap-2 border-b border-border-shallow bg-background px-4">
        <div className="flex min-w-0 flex-1 items-center gap-1">
          <h1 className="truncate text-lg font-medium leading-6 text-foreground">运营看板</h1>
        </div>
      </header>

      {/* Filter bar（照 fork DashboardFilter：带图标标签的 select；mock 维度→真实 时段/范围） */}
      <div className="flex flex-wrap items-end gap-4 border-b border-border-shallow bg-background px-4 py-3 sm:px-6">
        <FilterSelect
          icon={<CalendarRange size={12} />}
          label="时段"
          value={period}
          onChange={setPeriod}
          options={PERIODS.map(([value, label]) => ({ value, label }))}
        />
        {canSwitch && (
          <FilterSelect
            icon={<Store size={12} />}
            label="范围"
            value={scope}
            onChange={setScope}
            options={data!.scopes.map((s) => ({ value: s.key || "", label: s.label }))}
          />
        )}
        {data?.scope && (
          <div className="ml-auto hidden self-center text-xs text-foreground-tertiary sm:block">
            范围 · {data.scope}
          </div>
        )}
      </div>

      {/* Content（照 fork：max-w-[1400px] + 满宽概览 + 2 列 + 满宽底部段） */}
      <div className="flex-1 overflow-y-auto p-4 sm:p-6">
        <div className="mx-auto max-w-[1400px] space-y-6">
          {error ? (
            <Card>
              <div className="py-10 text-center text-sm text-destructive">加载失败：{error}</div>
            </Card>
          ) : (
            <>
              <BusinessOverview data={data} loading={loading} />
              <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
                <HotProducts data={data} loading={loading} />
                <InventoryHealth data={data} loading={loading} />
              </div>
              <OrderSection data={data} loading={loading} />
            </>
          )}
        </div>
      </div>
    </section>
  );
}

/* ── 通用壳件（照 fork Dashboard 卡片/分段 tab）────────────────── */

// fork 卡片：p-5 rounded-2xl bg-white border border-border-shallow（无阴影）。
function Card({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-2xl border border-border-shallow bg-card p-5">{children}</div>
  );
}

function CardHead({ title, right }: { title: string; right?: ReactNode }) {
  return (
    <div className="mb-4 flex items-center justify-between">
      <h3 className="text-base font-semibold text-foreground">{title}</h3>
      {right}
    </div>
  );
}

// 演示数据徽章（琥珀 pill）：后端暂无数据源的演示模块在标题/Tab 旁标注，避免被误当真实数据。
function DemoBadge() {
  return (
    <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-700">
      演示数据
    </span>
  );
}

// fork 的分段 tab：bg-fill-default 容器 + 选中 bg-white shadow-sm。
function TabPills<T extends string>({
  tabs,
  value,
  onChange,
}: {
  tabs: { id: T; label: string }[];
  value: T;
  onChange: (id: T) => void;
}) {
  return (
    <div className="flex gap-1 rounded-lg bg-fill-default p-0.5">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          onClick={() => onChange(tab.id)}
          className={
            "rounded-md px-3 py-1.5 text-xs font-medium transition-colors " +
            (value === tab.id
              ? "bg-card text-foreground shadow-sm"
              : "text-foreground-tertiary hover:text-foreground")
          }
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

// fork DashboardFilter 的带图标标签 select。
function FilterSelect({
  icon,
  label,
  value,
  onChange,
  options,
}: {
  icon: ReactNode;
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <div className="relative">
      <div className="mb-1 flex items-center gap-1.5 text-xs text-foreground-tertiary">
        {icon}
        <span>{label}</span>
      </div>
      <div className="relative">
        <select
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="h-8 cursor-pointer appearance-none rounded-lg border border-border bg-card pl-3 pr-8 text-sm text-foreground transition-colors hover:border-border-deep focus:border-primary focus:outline-none"
        >
          {options.map((o) => (
            <option key={o.value || "__all__"} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <ChevronDown
          size={14}
          className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-foreground-tertiary"
        />
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

/* ── 经营概览（照 fork BusinessOverview：MetricCard 行 + 分段 tab + 趋势图）──── */

// fork MetricCard：图标+标题 / 大数值 / 涨跌行。涨跌行走后端真实环比（当期 vs 紧邻等长上期）：
// 升=绿↑、降=红↓、持平=灰−；change 为 null/undefined（上期无基准或旧后端无该字段）时整行不渲染，不臆造。
function MetricCard({
  title,
  value,
  icon,
  change,
  loading,
  subtitle,
}: {
  title: string;
  value: string;
  icon: ReactNode;
  change?: number | null;
  loading?: boolean;
  // 可选副标注：广告卡用「结算口径」标口径、降级时用「暂无结算数据」提示，避免误导。
  subtitle?: string;
}) {
  const dir = change == null ? null : change > 0 ? "up" : change < 0 ? "down" : "flat";
  return (
    <div className="flex flex-col gap-1 rounded-xl bg-fill-shallow p-4">
      <div className="flex items-center gap-2 text-foreground-tertiary">
        {icon}
        <span className="text-xs">{title}</span>
      </div>
      <div className="tabnum text-2xl font-bold text-foreground">{loading ? "…" : value}</div>
      {!loading && subtitle && (
        <div className="text-xs text-foreground-tertiary">{subtitle}</div>
      )}
      {!loading && dir && (
        <div
          className={`flex items-center gap-1 text-xs ${
            dir === "up" ? "text-green-600" : dir === "down" ? "text-red-600" : "text-foreground-tertiary"
          }`}
        >
          <span>{dir === "up" ? "↑" : dir === "down" ? "↓" : "−"}</span>
          <span className="tabnum">{Math.abs(change as number).toFixed(1)}%</span>
          <span className="text-foreground-tertiary">vs 上期</span>
        </div>
      )}
    </div>
  );
}

type OverviewTab = "sales" | "orders" | "traffic" | "funnel";

function BusinessOverview({ data, loading }: { data: BoardData | null; loading: boolean }) {
  const t = useChartTokens();
  const [activeTab, setActiveTab] = useState<OverviewTab>("sales");
  const o = data?.overview.orders;
  const ads = data?.overview.ads;
  const ch = data?.overview.change;
  const pts = data?.trend.points ?? [];
  // 无结算数据降级：广告消耗 0/缺失 → 卡值「—」+「暂无结算数据」；roas 为 null → 「—」。
  const hasAdSpend = !!ads && ads.total_ad_spend > 0;
  const adCostValue = hasAdSpend ? fmtMoney(ads!.total_ad_spend) : "—";
  const roasValue = ads && ads.roas != null ? `${ads.roas.toFixed(2)}×` : "—";
  const labels = pts.map((p) => p.date.slice(5));

  const axisX = (boundaryGap: boolean) => ({
    type: "category" as const,
    data: labels,
    boundaryGap,
    axisLine: { lineStyle: { color: t.grid } },
    axisLabel: { color: t.sub, rotate: labels.length > 12 ? 45 : 0 },
  });
  const axisY = {
    type: "value" as const,
    axisLine: { show: false },
    splitLine: { lineStyle: { color: t.grid } },
    axisLabel: { color: t.sub },
  };
  const tip = {
    backgroundColor: "#fff",
    borderColor: t.grid,
    textStyle: { color: t.text },
  };

  // fork 的「销售趋势」：GMV 平滑面积折线。配色照搬 fork StoreClaw 的靛蓝 #6366f1
  // （用户拍板：此处不走品牌墨绿，1:1 复刻 fork 的鲜亮彩色面积效果）。
  const salesOption = useMemo(
    () => ({
      tooltip: { trigger: "axis" as const, ...tip },
      grid: { top: 12, right: 16, bottom: 28, left: 60 },
      xAxis: axisX(false),
      yAxis: axisY,
      series: [
        {
          name: "GMV",
          type: "line",
          smooth: true,
          showSymbol: false,
          data: pts.map((p) => p.gmv),
          lineStyle: { color: "#6366f1", width: 3 },
          itemStyle: { color: "#6366f1" },
          areaStyle: {
            color: {
              type: "linear" as const,
              x: 0,
              y: 0,
              x2: 0,
              y2: 1,
              colorStops: [
                { offset: 0, color: "rgba(99,102,241,0.2)" },
                { offset: 1, color: "rgba(99,102,241,0)" },
              ],
            },
          },
        },
      ],
    }),
    [data, t],
  );

  // fork 的「流量趋势」槽位无 UV/PV 数据源 → 换成我方真实的「订单数 + 销量」双系列。
  const ordersOption = useMemo(
    () => ({
      tooltip: { trigger: "axis" as const, ...tip },
      legend: {
        data: ["订单数", "销量"],
        bottom: 0,
        textStyle: { color: t.sub, fontSize: 11 },
        icon: "roundRect",
      },
      grid: { top: 12, right: 16, bottom: 40, left: 50 },
      xAxis: axisX(true),
      yAxis: axisY,
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
    { id: "orders", label: "订单 / 销量" },
    { id: "traffic", label: "流量趋势" },
    { id: "funnel", label: "转化漏斗" },
  ];
  // traffic/funnel 为演示数据 tab（后端无源），选中时标注徽章、走 demo-data 的 option。
  const isDemo = activeTab === "traffic" || activeTab === "funnel";

  return (
    <Card>
      <CardHead
        title="经营概览"
        right={
          <div className="flex items-center gap-2">
            {isDemo && <DemoBadge />}
            <TabPills tabs={tabs} value={activeTab} onChange={setActiveTab} />
          </div>
        }
      />

      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-3">
        <MetricCard loading={loading} change={ch?.gmv} title="GMV（已付款）" value={fmtMoney(o?.gmv)} icon={<DollarSign size={14} />} />
        <MetricCard loading={loading} change={ch?.order_count} title="订单数" value={fmtInt(o?.order_count)} icon={<ShoppingCart size={14} />} />
        <MetricCard loading={loading} change={ch?.units_sold} title="销量" value={fmtInt(o?.units_sold)} icon={<TrendingUp size={14} />} />
        <MetricCard loading={loading} change={ch?.avg_order_value} title="客单价" value={fmtMoney(o?.avg_order_value)} icon={<Wallet size={14} />} />
        <MetricCard
          loading={loading}
          change={hasAdSpend ? ch?.ad_cost : undefined}
          title="广告消耗"
          value={adCostValue}
          subtitle={hasAdSpend ? "结算口径" : "暂无结算数据"}
          icon={<Megaphone size={14} />}
        />
        <MetricCard
          loading={loading}
          change={ch?.roas}
          title="ROAS"
          value={roasValue}
          subtitle="结算口径"
          icon={<Gauge size={14} />}
        />
      </div>

      {/* 演示 tab（流量/转化）走前端内置 demo 数据，不依赖后端 pts；真实 tab 仍按 pts 空态降级。 */}
      {activeTab === "traffic" ? (
        <div className="h-[220px]">
          <EChart option={trafficOption(t)} height={220} />
        </div>
      ) : activeTab === "funnel" ? (
        <div className="h-[220px]">
          <EChart option={funnelOption(t)} height={220} />
        </div>
      ) : loading || !pts.length ? (
        <ChartEmpty loading={loading} empty="该时段暂无趋势数据" height={220} />
      ) : (
        <div className="h-[220px]">
          <EChart option={activeTab === "sales" ? salesOption : ordersOption} height={220} />
        </div>
      )}
    </Card>
  );
}

/* ── 爆款商品（照 fork HotProducts：排行列表 + 右侧明细面板）──────────── */

type RankBy = "sales" | "gmv";

function skuName(i: TopSku): string {
  return i.product_name || i.sku_name || i.sku_id || "?";
}

function HotProducts({ data, loading }: { data: BoardData | null; loading: boolean }) {
  const [rankBy, setRankBy] = useState<RankBy>("sales");
  const [selected, setSelected] = useState<number | null>(null);
  const raw = data?.top.items ?? [];

  const items = useMemo(() => {
    const sorted = [...raw].sort((a, b) =>
      rankBy === "gmv" ? (b.gmv ?? 0) - (a.gmv ?? 0) : b.units_sold - a.units_sold,
    );
    return sorted.slice(0, 10);
  }, [raw, rankBy]);

  const totalUnits = useMemo(() => items.reduce((s, i) => s + (i.units_sold || 0), 0), [items]);
  const sel = selected != null ? items[selected] : null;

  // fork 排序含「按利润」；我方无利润数据源 → 仅保留 销量/GMV。
  const rankOptions: { id: RankBy; label: string }[] = [
    { id: "sales", label: "按销量" },
    { id: "gmv", label: "按 GMV" },
  ];

  return (
    <Card>
      <CardHead
        title="爆款商品 TOP 10"
        right={<TabPills tabs={rankOptions} value={rankBy} onChange={setRankBy} />}
      />

      {loading ? (
        <ChartEmpty loading empty="" height={320} />
      ) : !items.length ? (
        <ChartEmpty loading={false} empty="该时段暂无销量数据" height={320} />
      ) : (
        <div className="flex gap-4">
          {/* 排行列表（照 fork：序号徽章 + 名称 + 数值；前 3 名 bg-foreground 实心） */}
          <div className="max-h-[320px] flex-1 space-y-1.5 overflow-y-auto">
            {items.map((p, index) => {
              const val = rankBy === "gmv" ? p.gmv ?? 0 : p.units_sold;
              return (
                <div
                  key={(p.sku_id || "") + index}
                  onClick={() => setSelected(index)}
                  className={
                    "flex cursor-pointer items-center gap-3 rounded-lg p-2 transition-colors " +
                    (selected === index ? "bg-fill-default" : "hover:bg-fill-shallow")
                  }
                >
                  <span
                    className={
                      "flex h-6 w-6 items-center justify-center rounded-full text-xs font-bold " +
                      (index < 3
                        ? "bg-foreground text-white"
                        : "bg-fill-default text-foreground-secondary")
                    }
                  >
                    {index + 1}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium text-foreground">{skuName(p)}</div>
                    <div className="text-xs text-foreground-tertiary">
                      {rankBy === "gmv" ? fmtMoney(val) : `${fmtInt(val)} 件`}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          {/* 右侧明细面板：fork 是单品 7 天趋势图；我方无单品时序 → 换成选中品的真实占比/数值（不造假） */}
          <div className="w-[240px] shrink-0">
            {sel ? (
              <div className="space-y-3">
                <div className="truncate text-sm font-medium text-foreground">{skuName(sel)}</div>
                <DetailStat label="销量" value={`${fmtInt(sel.units_sold)} 件`} />
                <DetailStat label="GMV" value={fmtMoney(sel.gmv)} />
                <DetailStat
                  label="占榜单销量"
                  value={totalUnits ? `${Math.round((sel.units_sold / totalUnits) * 100)}%` : "—"}
                />
                {sel.sku_id && (
                  <div className="pt-1 text-xs text-foreground-tertiary">SKU · {sel.sku_id}</div>
                )}
              </div>
            ) : (
              <div className="flex h-full items-center justify-center text-center text-sm text-foreground-tertiary">
                点击商品查看明细
              </div>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}

function DetailStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl bg-fill-shallow p-3">
      <div className="text-xs text-foreground-tertiary">{label}</div>
      <div className="tabnum mt-0.5 text-lg font-bold text-foreground">{value}</div>
    </div>
  );
}

/* ── 库存健康（照 fork InventoryHealth：汇总=仪表盘+分布 / 明细=搜索排序表）──── */

type InventoryView = "summary" | "details";

const LOW_BADGE: Record<string, { label: string; cls: string }> = {
  stockout: { label: "缺货", cls: "bg-red-100 text-red-700" },
  critical: { label: "告急", cls: "bg-orange-100 text-orange-700" },
  warning: { label: "偏低", cls: "bg-yellow-100 text-yellow-700" },
};

type SortField = "days_of_cover" | "available_stock" | "name";

function InventoryHealth({ data, loading }: { data: BoardData | null; loading: boolean }) {
  const t = useChartTokens();
  const [view, setView] = useState<InventoryView>("summary");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [sortField, setSortField] = useState<SortField>("days_of_cover");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");

  const inv = data?.overview.inventory;
  const b = data?.low.buckets;
  const items = data?.low.items ?? [];

  const stockout = b?.stockout ?? 0;
  const critical = b?.critical ?? 0;
  const warning = b?.warning ?? 0;
  const atRisk = stockout + critical + warning;

  // fork 的「库存健康度」仪表盘：我方用 (总SKU − 风险SKU) / 总SKU 真实派生。
  // 后端 get_overview 返回字段名是 total_sku（早前误读成 sku_count 导致恒为 0）。
  const skuCount = inv?.total_sku ?? 0;
  const lowCount = inv?.low_stock_count ?? atRisk;
  const healthPct = skuCount > 0 ? Math.round(((skuCount - lowCount) / skuCount) * 100) : null;

  const gaugeOption = useMemo(
    () => ({
      series: [
        {
          type: "gauge",
          startAngle: 200,
          endAngle: -20,
          min: 0,
          max: 100,
          progress: { show: true, width: 18, itemStyle: { color: t.positive } },
          axisLine: { lineStyle: { width: 18, color: [[1, t.grid]] } },
          axisTick: { show: false },
          splitLine: { show: false },
          axisLabel: { show: false },
          pointer: { show: false },
          title: { show: true, offsetCenter: [0, "70%"], fontSize: 13, color: t.sub },
          detail: {
            valueAnimation: true,
            fontSize: 28,
            fontWeight: "bold",
            offsetCenter: [0, "40%"],
            formatter: "{value}%",
            color: t.positive,
          },
          data: [{ value: healthPct ?? 0, name: "库存健康度" }],
        },
      ],
    }),
    [healthPct, t],
  );

  // fork 的右侧是「按品类分布」堆叠条；我方无品类维度 → 换成真实的三档风险分布环图。
  const donutOption = useMemo(
    () => ({
      tooltip: { trigger: "item" as const, backgroundColor: "#fff", borderColor: t.grid, textStyle: { color: t.text } },
      legend: { bottom: 0, textStyle: { color: t.sub, fontSize: 11 }, icon: "roundRect" },
      series: [
        {
          type: "pie",
          radius: ["45%", "70%"],
          center: ["50%", "44%"],
          avoidLabelOverlap: false,
          itemStyle: { borderRadius: 6, borderColor: "#fff", borderWidth: 2 },
          label: { show: false },
          data: [
            { name: "缺货", value: stockout, itemStyle: { color: t.negative } },
            { name: "告急", value: critical, itemStyle: { color: t.warning } },
            { name: "偏低", value: warning, itemStyle: { color: t.positive } },
          ],
        },
      ],
    }),
    [stockout, critical, warning, t],
  );

  function handleSort(field: SortField) {
    if (sortField === field) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSortField(field);
      setSortDir("asc");
    }
  }

  const filtered = useMemo(() => {
    const q = query.toLowerCase();
    return items
      .filter((it) => {
        const matchesQ =
          !q ||
          (it.product_name || "").toLowerCase().includes(q) ||
          it.sku_id.toLowerCase().includes(q);
        const matchesS = status === "all" || it.bucket === status;
        return matchesQ && matchesS;
      })
      .sort((a, b2) => {
        const dir = sortDir === "asc" ? 1 : -1;
        if (sortField === "name") return skuLabel(a).localeCompare(skuLabel(b2)) * dir;
        return ((a[sortField] as number) - (b2[sortField] as number)) * dir;
      });
  }, [items, query, status, sortField, sortDir]);

  return (
    <Card>
      <CardHead
        title="库存健康"
        right={
          <TabPills
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
          <ChartEmpty loading empty="" height={200} />
        ) : (
          <div className="grid grid-cols-2 gap-4">
            <div className="h-[200px]">
              {healthPct == null ? (
                <ChartEmpty loading={false} empty="暂无健康度数据" height={200} />
              ) : (
                <EChart option={gaugeOption} height={200} />
              )}
            </div>
            <div className="h-[200px]">
              {atRisk ? (
                <EChart option={donutOption} height={200} />
              ) : (
                <ChartEmpty loading={false} empty="暂无断货风险" height={200} />
              )}
            </div>
          </div>
        )
      ) : (
        <div>
          {/* 搜索 + 状态筛选（照 fork InventoryHealth 明细头） */}
          <div className="mb-3 flex gap-3">
            <div className="relative flex-1">
              <Search
                size={14}
                className="absolute left-3 top-1/2 -translate-y-1/2 text-foreground-tertiary"
              />
              <input
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="搜索商品名或 SKU…"
                className="h-8 w-full rounded-lg border border-border bg-card pl-8 pr-3 text-sm text-foreground placeholder:text-foreground-tertiary focus:border-primary focus:outline-none"
              />
            </div>
            <select
              value={status}
              onChange={(e) => setStatus(e.target.value)}
              className="h-8 appearance-none rounded-lg border border-border bg-card px-3 text-sm text-foreground"
            >
              <option value="all">全部状态</option>
              <option value="stockout">缺货</option>
              <option value="critical">告急</option>
              <option value="warning">偏低</option>
            </select>
          </div>

          {/* 可排序表（照 fork：表头点击排序 + ArrowUpDown） */}
          <div className="overflow-hidden rounded-lg border border-border-shallow">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-fill-shallow">
                  <SortableTh label="商品" onClick={() => handleSort("name")} />
                  <th className="px-3 py-2 text-left font-medium text-foreground-secondary">SKU</th>
                  <SortableTh label="库存" numeric onClick={() => handleSort("available_stock")} />
                  <th className="px-3 py-2 text-right font-medium text-foreground-secondary">日均销量</th>
                  <SortableTh label="可售天数" numeric onClick={() => handleSort("days_of_cover")} />
                  <th className="px-3 py-2 text-center font-medium text-foreground-secondary">状态</th>
                </tr>
              </thead>
              <tbody>
                {filtered.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="px-3 py-8 text-center text-foreground-tertiary">
                      暂无断货风险 SKU
                    </td>
                  </tr>
                ) : (
                  filtered.map((it) => {
                    const badge = LOW_BADGE[it.bucket] || { label: it.bucket, cls: "bg-fill-default" };
                    return (
                      <tr
                        key={it.sku_id}
                        className="border-t border-border-shallow transition-colors hover:bg-fill-shallow"
                      >
                        <td className="px-3 py-2 font-medium text-foreground">
                          {it.product_name || it.sku_id}
                        </td>
                        <td className="px-3 py-2 text-foreground-tertiary">{it.sku_id}</td>
                        <td className="px-3 py-2 text-right text-foreground">
                          {fmtInt(it.available_stock)}
                        </td>
                        <td className="px-3 py-2 text-right text-foreground-secondary">
                          {Number(it.daily_velocity).toFixed(1)}
                        </td>
                        <td className="px-3 py-2 text-right text-foreground">
                          {Number(it.days_of_cover).toFixed(1)}
                        </td>
                        <td className="px-3 py-2 text-center">
                          <span
                            className={
                              "inline-flex rounded px-2 py-0.5 text-xs font-medium " + badge.cls
                            }
                          >
                            {badge.label}
                          </span>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </Card>
  );

  function skuLabel(i: LowStockItem) {
    return i.product_name || i.sku_id;
  }
}

function SortableTh({
  label,
  numeric,
  onClick,
}: {
  label: string;
  numeric?: boolean;
  onClick: () => void;
}) {
  return (
    <th
      onClick={onClick}
      className={
        "cursor-pointer px-3 py-2 font-medium text-foreground-secondary hover:text-foreground " +
        (numeric ? "text-right" : "text-left")
      }
    >
      <div className={"flex items-center gap-1 " + (numeric ? "justify-end" : "")}>
        {label}
        <ArrowUpDown size={12} />
      </div>
    </th>
  );
}

/* ── 订单与履约（满宽多 tab）：待发货=真实履约数据；下单/退货/退款=fork 演示数据
      （后端暂无源，见 docs/board-data-backlog.md，演示 tab 标「演示数据」徽章）─────── */

const PEND_BADGE: Record<string, { label: string; cls: string }> = {
  overdue: { label: "超时", cls: "bg-red-100 text-red-700" },
  critical: { label: "临界", cls: "bg-orange-100 text-orange-700" },
  normal: { label: "正常", cls: "bg-green-100 text-green-700" },
  unknown: { label: "未知", cls: "bg-fill-default text-foreground-secondary" },
};

type OrderTab = "fulfillment" | "orders" | "returns" | "refunds";

const ORDER_TABS: { id: OrderTab; label: string }[] = [
  { id: "fulfillment", label: "待发货" },
  { id: "orders", label: "下单趋势" },
  { id: "returns", label: "退货分析" },
  { id: "refunds", label: "退款分析" },
];

const sum = (a: number[]) => a.reduce((x, y) => x + y, 0);

function OrderSection({ data, loading }: { data: BoardData | null; loading: boolean }) {
  const t = useChartTokens();
  const [tab, setTab] = useState<OrderTab>("fulfillment");
  const b = data?.fulfillment.buckets;
  const items = data?.fulfillment.items ?? [];
  const isDemo = tab !== "fulfillment";
  const returnRate = (sum(DEMO_RETURNS.rate) / DEMO_RETURNS.rate.length).toFixed(1);

  return (
    <Card>
      <CardHead
        title="订单与履约"
        right={
          <div className="flex items-center gap-2">
            {isDemo && <DemoBadge />}
            <TabPills tabs={ORDER_TABS} value={tab} onChange={setTab} />
          </div>
        }
      />

      {/* 待发货：真实履约数据（统计分桶 + 明细表） */}
      {tab === "fulfillment" && (
        <>
          {data?.fulfillment.snapshot_at && (
            <div className="mb-3 text-xs text-foreground-tertiary">
              快照 {data.fulfillment.snapshot_at}
            </div>
          )}
          <div className="mb-4 flex flex-wrap gap-x-8 gap-y-3">
            <Stat label="待发货合计" value={fmtInt(b?.total)} loading={loading} />
            <Stat label="超时" value={fmtInt(b?.overdue)} tone="negative" loading={loading} />
            <Stat label="临界" value={fmtInt(b?.critical)} tone="warning" loading={loading} />
            <Stat label="正常" value={fmtInt(b?.normal)} loading={loading} />
          </div>
          <div className="overflow-hidden rounded-lg border border-border-shallow">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-fill-shallow">
                  <th className="px-3 py-2 text-left font-medium text-foreground-secondary">订单</th>
                  <th className="px-3 py-2 text-left font-medium text-foreground-secondary">店铺</th>
                  <th className="px-3 py-2 text-left font-medium text-foreground-secondary">商品</th>
                  <th className="px-3 py-2 text-center font-medium text-foreground-secondary">状态</th>
                  <th className="px-3 py-2 text-right font-medium text-foreground-secondary">件数</th>
                  <th className="px-3 py-2 text-right font-medium text-foreground-secondary">金额</th>
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  <tr>
                    <td colSpan={6} className="px-3 py-8 text-center text-foreground-tertiary">
                      加载中…
                    </td>
                  </tr>
                ) : items.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="px-3 py-8 text-center text-foreground-tertiary">
                      暂无待发货订单
                    </td>
                  </tr>
                ) : (
                  items.map((r) => {
                    const badge = r.bucket ? PEND_BADGE[r.bucket] : null;
                    return (
                      <tr
                        key={String(r.order_id)}
                        className="border-t border-border-shallow transition-colors hover:bg-fill-shallow"
                      >
                        <td className="px-3 py-2">
                          <span className="font-mono text-xs text-foreground">
                            {String(r.order_id).slice(-8)}
                          </span>
                        </td>
                        <td className="px-3 py-2 text-foreground-secondary">{r.shop_id ?? "—"}</td>
                        <td className="px-3 py-2 text-foreground">
                          {(r.first_product_name || "—").slice(0, 20)}
                        </td>
                        <td className="px-3 py-2 text-center">
                          {badge ? (
                            <span
                              className={"inline-flex rounded px-2 py-0.5 text-xs font-medium " + badge.cls}
                            >
                              {badge.label}
                            </span>
                          ) : (
                            "—"
                          )}
                        </td>
                        <td className="px-3 py-2 text-right text-foreground">{fmtInt(r.item_count)}</td>
                        <td className="px-3 py-2 text-right text-foreground">{fmtMoney(r.total_amount)}</td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </>
      )}

      {/* 下单趋势：演示（按平台堆叠柱） */}
      {tab === "orders" && (
        <>
          <div className="mb-4 flex flex-wrap gap-x-8 gap-y-3">
            <Stat label="合计下单" value={fmtInt(sum(DEMO_ORDERS.shopify) + sum(DEMO_ORDERS.amazon) + sum(DEMO_ORDERS.tiktok))} />
            <Stat label="Shopify" value={fmtInt(sum(DEMO_ORDERS.shopify))} />
            <Stat label="Amazon" value={fmtInt(sum(DEMO_ORDERS.amazon))} />
            <Stat label="TikTok Shop" value={fmtInt(sum(DEMO_ORDERS.tiktok))} />
          </div>
          <div className="h-[220px]">
            <EChart option={ordersStackOption(t)} height={220} />
          </div>
        </>
      )}

      {/* 退货分析：演示（退货数/率双 Y + 原因环图，两列） */}
      {tab === "returns" && (
        <>
          <div className="mb-4 flex flex-wrap gap-x-8 gap-y-3">
            <Stat label="合计退货" value={fmtInt(sum(DEMO_RETURNS.count))} />
            <Stat label="退货率" value={`${returnRate}%`} />
            <Stat label="主要原因" value={DEMO_RETURNS.reasons[0].name} />
          </div>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <div className="h-[220px]">
              <EChart option={returnsOption(t)} height={220} />
            </div>
            <div className="h-[220px]">
              <EChart option={returnReasonsOption(t)} height={220} />
            </div>
          </div>
        </>
      )}

      {/* 退款分析：演示（退款金额面积 + 退款率虚线，月维度） */}
      {tab === "refunds" && (
        <>
          <div className="mb-4 flex flex-wrap gap-x-8 gap-y-3">
            <Stat label="累计退款" value={`$${fmtInt(sum(DEMO_REFUNDS.amount))}`} />
            <Stat label="最新退款率" value={`${DEMO_REFUNDS.rate[DEMO_REFUNDS.rate.length - 1]}%`} />
          </div>
          <div className="h-[220px]">
            <EChart option={refundsOption(t)} height={220} />
          </div>
        </>
      )}
    </Card>
  );
}

function Stat({
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
    <div>
      <div className="mb-1 text-xs text-foreground-tertiary">{label}</div>
      <div
        className={
          "tabnum text-xl font-bold text-foreground " +
          (tone === "negative" ? "!text-negative " : tone === "warning" ? "!text-warning " : "")
        }
      >
        {loading ? "…" : value}
      </div>
    </div>
  );
}
