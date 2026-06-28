import { useEffect, useMemo, useState, type ReactNode, type UIEvent } from "react";
import {
  ArrowUpDown,
  Calendar,
  ChevronDown,
  DollarSign,
  Gauge,
  Globe,
  Info,
  Megaphone,
  Percent,
  Search,
  ShoppingBag,
  ShoppingCart,
  Store,
  TrendingUp,
  TriangleAlert,
} from "lucide-react";
import { api, type BoardData, type LowStockItem, type TopSku } from "@/api";
import { DateRangePicker, type DateRangeValue } from "@/components/board/DateRangePicker";
import { InfoTooltip } from "@/components/ui/tooltip";
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

// 区域→后端 country（ISO alpha-2）；平台→后端 platform。当前单租户单平台，"全部"与具体值
// 返回同一份数据（预期），选项为前向占位 + 真实透传。空串=全部（后端按 None 处理）。
const REGIONS: { value: string; label: string }[] = [
  { value: "", label: "全部" },
  { value: "ID", label: "印尼" },
];
const PLATFORMS: { value: string; label: string }[] = [
  { value: "", label: "全部" },
  { value: "tiktok_shop", label: "TikTok" },
];

// 默认日期窗口：近 7 天（今天往前 6 天 ~ 今天，与后端 last_7d 对齐）。
function last7(): DateRangeValue {
  const fmt = (d: Date) =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const end = new Date();
  const start = new Date();
  start.setDate(start.getDate() - 6);
  return { start: fmt(start), end: fmt(end) };
}

const fmtInt = (n: number | undefined) =>
  n == null ? "—" : Number(n).toLocaleString("en-US");
const fmtMoney = (n: number | undefined) =>
  n == null
    ? "—"
    : "Rp " + Number(n).toLocaleString("en-US", { maximumFractionDigits: 0 });
// 利润折 CNY 展示（¥ 前缀），与 fmtMoney(Rp/IDR) 区分，避免币种误标。
const fmtMoneyCny = (n: number | undefined | null) =>
  n == null
    ? "—"
    : "¥ " + Number(n).toLocaleString("en-US", { maximumFractionDigits: 0 });

export function BoardPage() {
  const [platform, setPlatform] = useState("tiktok_shop"); // 平台默认 TikTok
  const [region, setRegion] = useState("ID"); // 区域默认 印尼（→country）
  const [scope, setScope] = useState(""); // 店铺（范围 scope_key）
  const [range, setRange] = useState<DateRangeValue>(last7); // 日期默认近 7 天
  const [data, setData] = useState<BoardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0); // 错误态「重试」手动重触发 fetch

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    api
      .boardData({
        start: range.start ?? undefined,
        end: range.end ?? undefined,
        scope,
        platform: platform || undefined,
        country: region || undefined,
      })
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError(String(e)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [platform, region, scope, range.start, range.end, reloadKey]);

  const canSwitch = !!data?.can_switch && (data?.scopes.length ?? 0) > 1;

  // 上划收起筛选栏（移动端省纵向空间）：内容区滚动驱动；带迟滞阈值防边界抖动。
  const [filtersCollapsed, setFiltersCollapsed] = useState(false);
  const onContentScroll = (e: UIEvent<HTMLDivElement>) => {
    const top = e.currentTarget.scrollTop;
    setFiltersCollapsed((prev) => (top > 40 ? true : top < 10 ? false : prev));
  };

  return (
    <section className="flex h-full flex-col">
      {/* Header（照 fork DashboardPage：h-[68px] + 底边） */}
      <header className="sticky top-0 z-50 flex h-[68px] shrink-0 items-center justify-between gap-2 border-b border-border-shallow bg-background px-4">
        <div className="flex min-w-0 flex-1 items-center gap-1">
          <h1 className="truncate text-lg font-medium leading-6 text-foreground">运营看板</h1>
        </div>
      </header>

      {/* Filter bar（区域 / 平台 / 店铺 / 日期）：上划时整条收起（max-h+opacity 过渡），回顶展开 */}
      <div
        className={
          "overflow-visible border-b border-border-shallow bg-background transition-all duration-300 ease-out " +
          (filtersCollapsed
            ? "max-h-0 overflow-hidden border-b-0 py-0 opacity-0"
            : "max-h-60 opacity-100")
        }
      >
        <div className="flex flex-wrap items-end gap-4 px-4 py-3 sm:px-6">
          <FilterSelect
            icon={<Globe size={12} />}
            label="区域"
            value={region}
            onChange={setRegion}
            options={REGIONS}
          />
          <FilterSelect
            icon={<ShoppingBag size={12} />}
            label="平台"
            value={platform}
            onChange={setPlatform}
            options={PLATFORMS}
          />
          {canSwitch && (
            <FilterSelect
              icon={<Store size={12} />}
              label="店铺"
              value={scope}
              onChange={setScope}
              options={data!.scopes.map((s) => ({ value: s.key || "", label: s.label }))}
            />
          )}
          <DateRangePicker value={range} onChange={setRange} />
          {data?.scope && (
            <div className="ml-auto hidden self-center text-xs text-foreground-secondary sm:block">
              范围 · {data.scope}
            </div>
          )}
        </div>
      </div>

      {/* Content（照 fork：max-w-[1400px] + 满宽概览 + 2 列 + 满宽底部段） */}
      <div className="flex-1 overflow-y-auto p-4 sm:p-6" onScroll={onContentScroll}>
        <div className="mx-auto max-w-[1400px] space-y-6">
          {error ? (
            <BoardCard>
              <div className="flex flex-col items-center gap-3 py-10 text-center">
                <div className="text-sm text-destructive">加载失败：{error}</div>
                <button
                  type="button"
                  onClick={() => setReloadKey((k) => k + 1)}
                  className="rounded-lg border border-border bg-card px-4 py-2 text-sm font-medium text-foreground transition-colors hover:bg-fill-shallow focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [@media(pointer:coarse)]:min-h-[44px]"
                >
                  重试
                </button>
              </div>
            </BoardCard>
          ) : (
            <>
              <NoDataBanner data={data} loading={loading} />
              <BusinessOverview data={data} loading={loading} />
              <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
                <HotProducts data={data} loading={loading} />
                <InventoryHealth data={data} loading={loading} />
              </div>
              <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
                <ChannelPie data={data} loading={loading} />
                <FeeRateMonitor data={data} loading={loading} />
              </div>
              <OrderSection data={data} loading={loading} />
            </>
          )}
        </div>
      </div>
    </section>
  );
}

/* ── 无订单数据横幅 ──────────────────────────────────────────────
   区分「该时间范围真没单」与「加载失败」（后者走上方红色 Card）。数据加载成功但
   order_count=0 时给出友好提示 + 所选窗口，避免客户把全 0 页面误当系统坏掉。
   常见诱因：选到了同步窗口之外的早期单日（prod 仅手动同步了最近一段时间的订单）。 */
function NoDataBanner({ data, loading }: { data: BoardData | null; loading: boolean }) {
  if (loading || !data) return null;
  if ((data.overview.orders?.order_count ?? 0) > 0) return null;
  const win = data.trend.window_label || `${data.trend.start_date ?? ""} ~ ${data.trend.end_date ?? ""}`;
  return (
    <div className="flex items-start gap-3 rounded-2xl border border-border-shallow bg-fill-shallow p-4 text-sm">
      <span className="text-base leading-5">📅</span>
      <div>
        <div className="font-medium text-foreground">所选时间范围暂无订单数据</div>
        <div className="mt-0.5 text-foreground-secondary">
          {win} 内没有已付款订单——并非加载失败。请尝试扩大日期范围或换一个日期再看。
        </div>
      </div>
    </div>
  );
}

/* ── 通用壳件（照 fork Dashboard 卡片/分段 tab）────────────────── */

// 看板专用容器 BoardCard（区别于 ui/card.tsx 的通用 Card）：fork StoreClaw 观感——
// p-5 rounded-2xl bg-card border border-border-shallow、无阴影、靠色调分层浮起。
function BoardCard({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-2xl border border-border-shallow bg-card p-5">{children}</div>
  );
}

function CardHead({
  title,
  right,
  as: As = "h2",
}: {
  title: ReactNode;
  right?: ReactNode;
  as?: "h2" | "h3";
}) {
  return (
    <div className="mb-4 flex items-center justify-between">
      <As className="text-base font-semibold text-foreground">{title}</As>
      {right}
    </div>
  );
}

// 演示数据徽章（琥珀 pill）：后端暂无数据源的演示模块在标题/Tab 旁标注，避免被误当真实数据。
function DemoBadge() {
  return (
    <span className="rounded bg-caution/15 px-1.5 py-0.5 text-[11px] font-medium text-caution">
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
          type="button"
          onClick={() => onChange(tab.id)}
          aria-pressed={value === tab.id}
          className={
            "inline-flex items-center rounded-md px-3 py-1.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [@media(pointer:coarse)]:min-h-[44px] " +
            (value === tab.id
              ? "bg-card text-foreground shadow-sm"
              : "text-foreground-secondary hover:text-foreground")
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
      <div className="mb-1 flex items-center gap-1.5 text-xs text-foreground-secondary">
        {icon}
        <span>{label}</span>
      </div>
      <div className="relative">
        <select
          value={value}
          onChange={(e) => onChange(e.target.value)}
          aria-label={label}
          className="h-8 cursor-pointer appearance-none rounded-lg border border-border bg-card pl-3 pr-8 text-sm text-foreground transition-colors hover:border-border-deep focus:border-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-ring [@media(pointer:coarse)]:h-11"
        >
          {options.map((o) => (
            <option key={o.value || "__all__"} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <ChevronDown
          size={14}
          aria-hidden
          className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-foreground-secondary"
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
      className="flex items-center justify-center text-sm text-foreground-secondary"
      style={{ height }}
    >
      {loading ? "加载中…" : empty}
    </div>
  );
}

/* ── 渠道分布（plan/17 阶段5：直播/视频/商品卡 GMV 占比环图）──────────── */

// 数据走 /board/data 的 channels 字段（店铺级 overview 相减法）。沙箱店无 analytics
// 数据时 available=false → 显示「暂无数据」。移动端：父级 space-y-6 单列堆叠 + EChart
// 的 ResizeObserver 宽度自适应，无需额外媒体查询；legend 置底防窄屏溢出。
function ChannelPie({ data, loading }: { data: BoardData | null; loading: boolean }) {
  const t = useChartTokens();
  const cb = data?.channels;
  const palette: Record<string, string> = {
    live: t.negative,
    video: t.primary,
    product_card: t.positive,
  };
  const option = useMemo(
    () => ({
      tooltip: {
        trigger: "item",
        formatter: (p: { name: string; value: number; percent: number }) =>
          `${p.name}<br/>${fmtMoney(p.value)} (${p.percent}%)`,
      },
      legend: { bottom: 0, textStyle: { color: t.sub } },
      series: [
        {
          type: "pie",
          radius: ["45%", "70%"],
          center: ["50%", "44%"],
          itemStyle: { borderRadius: 6, borderColor: t.card, borderWidth: 2 },
          label: { show: false },
          data: (cb?.channels || []).map((c) => ({
            name: c.label,
            value: c.gmv,
            itemStyle: { color: palette[c.key] || t.sub },
          })),
        },
      ],
    }),
    [cb, t],
  );
  const empty = !cb?.available ? "暂无渠道数据（需接生产店）" : "";
  return (
    <BoardCard>
      <CardHead title="渠道分布（GMV 占比）" />
      {loading || empty ? (
        <ChartEmpty loading={loading} empty={empty} height={280} />
      ) : (
        <EChart option={option} height={280} />
      )}
    </BoardCard>
  );
}

/* ── 费率监控卡（plan/19 W1：实时算、复用 B1 及时口径，三态徽章 + 趋势 + 分项归因）── */

// 数据走 /board/data 的 fee_rate 字段。当前预估费率(unsettled 口径) vs 已结算历史基准。
// 三态：normal 正常(绿) / alert 异常升高(红，挂分项归因) / insufficient 数据积累中(灰)。
// 与告警同源（services/fee_rate_metrics.get_fee_rate_monitor），有无告警都能展示。
function FeeRateMonitor({ data, loading }: { data: BoardData | null; loading: boolean }) {
  const t = useChartTokens();
  const fr = data?.fee_rate;
  const pct = (n: number | undefined | null) =>
    n == null ? "—" : `${(n * 100).toFixed(2)}%`;
  const status = fr?.status ?? "insufficient";
  const badge =
    status === "alert"
      ? { label: "异常升高", cls: "bg-negative/15 text-negative" }
      : status === "normal"
        ? { label: "正常", cls: "bg-positive/15 text-positive" }
        : status === "baseline_pending"
          ? { label: "监控中", cls: "bg-info/15 text-info" }
          : { label: "数据积累中", cls: "bg-fill-shallow text-foreground-secondary" };
  const lineColor = status === "alert" ? t.negative : t.primary;
  // baseline_pending：有当前预估费率/构成/趋势，仅已结算基准不足→展示主体但不判异常、不显升幅。
  const baselinePending = status === "baseline_pending";
  const points = (fr?.trend || []).filter((p) => p.rate != null);
  const option = useMemo(
    () => ({
      grid: { left: 8, right: 12, top: 12, bottom: 4, containLabel: true },
      tooltip: {
        trigger: "axis",
        formatter: (ps: { axisValue: string; data: number }[]) =>
          `${ps[0]?.axisValue}<br/>预估费率 ${ps[0]?.data?.toFixed(2)}%`,
      },
      xAxis: {
        type: "category",
        data: points.map((p) => p.date.slice(5)),
        axisLine: { lineStyle: { color: t.grid } },
        axisLabel: { color: t.sub, fontSize: 10 },
      },
      yAxis: {
        type: "value",
        axisLabel: { color: t.sub, fontSize: 10, formatter: "{value}%" },
        splitLine: { lineStyle: { color: t.grid } },
      },
      series: [
        {
          type: "line",
          smooth: true,
          symbol: "circle",
          symbolSize: 5,
          data: points.map((p) => Number(((p.rate as number) * 100).toFixed(3))),
          lineStyle: { color: lineColor, width: 2 },
          itemStyle: { color: lineColor },
          areaStyle: { color: lineColor, opacity: 0.08 },
          // hover 时禁用 emphasis：否则默认高亮态会覆盖 lineStyle/areaStyle 致折线"消失"。
          // tooltip 由 axisPointer 独立驱动，不受影响。
          emphasis: { disabled: true },
        },
      ],
    }),
    [points, lineColor, t],
  );
  const insufficient = status === "insufficient";
  return (
    <BoardCard>
      <CardHead
        title="费率监控（平台扣点率）"
        right={
          <span
            className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ${badge.cls}`}
          >
            {/* 实时监控呼吸灯（模仿对话欢迎页绿点）：alert 红、其余绿；数据积累中不显 */}
            {status !== "insufficient" && (
              <span
                className={`inline-block size-1.5 rounded-full animate-pulse-slow ${
                  status === "alert" ? "bg-negative" : "bg-positive"
                }`}
              />
            )}
            {badge.label}
          </span>
        }
      />
      {loading ? (
        <ChartEmpty loading={loading} empty="" height={260} />
      ) : insufficient ? (
        <ChartEmpty
          loading={false}
          empty={fr?.skip_reason ? `数据积累中：${fr.skip_reason}` : "数据积累中（待未结算费用同步）"}
          height={260}
        />
      ) : (
        <div className="space-y-4">
          {/* 当前预估费率 / 已结算基准 */}
          <div className="grid grid-cols-2 gap-4">
            <div className="rounded-xl bg-fill-shallow p-4">
              <div className="text-xs text-foreground-secondary">
                当前预估费率（{fr?.eval_window}）
              </div>
              <div className="tabnum text-2xl font-bold text-foreground">
                {pct(fr?.current_rate)}
              </div>
              {!baselinePending && fr && fr.abs_delta !== 0 && (
                <div
                  className={`text-xs ${fr.abs_delta > 0 ? "text-negative" : "text-positive"}`}
                >
                  {fr.abs_delta > 0 ? "↑" : "↓"} {pct(Math.abs(fr.abs_delta))}
                  （相对 {pct(Math.abs(fr.rel_delta))}）vs 基准
                </div>
              )}
            </div>
            <div className="rounded-xl bg-fill-shallow p-4">
              <div className="text-xs text-foreground-secondary">
                已结算基准（{fr?.baseline_window}）
              </div>
              {baselinePending ? (
                <div className="pt-1 text-sm text-foreground-secondary">积累中（历史不足）</div>
              ) : (
                <div className="tabnum text-2xl font-bold text-foreground">
                  {pct(fr?.baseline_rate)}
                </div>
              )}
              <div className="text-xs text-foreground-secondary">{fr?.currency}</div>
            </div>
          </div>
          {baselinePending && (
            <div className="rounded-lg bg-info/10 px-3 py-2 text-xs text-info">
              ⏳ 已结算基准积累中（需 ~2 周结算历史），暂无法判定异常，先展示当前费率水平与构成。
            </div>
          )}
          {/* 趋势 */}
          <EChart option={option} height={150} />
          {/* 异常时点名分项归因 */}
          {status === "alert" && fr?.attributions?.length ? (
            <div className="rounded-xl bg-negative/10 p-3 text-sm">
              <div className="mb-1 font-medium text-negative">📍 主要涨幅来自</div>
              {fr.attributions.map((a) => (
                <div key={a.key} className="flex justify-between text-foreground">
                  <span>{a.name}</span>
                  <span className="tabnum text-negative">
                    +{pct(a.delta)}（{pct(a.from)}→{pct(a.to)}）
                  </span>
                </div>
              ))}
            </div>
          ) : null}
          {/* 当前主要扣费构成 */}
          {fr?.components?.length ? (
            <div className="space-y-1">
              <div className="text-xs text-foreground-secondary">当前主要扣费构成（占 GMV）</div>
              {fr.components.slice(0, 4).map((c) => (
                <div key={c.key} className="flex justify-between text-sm">
                  <span className="text-foreground-secondary">{c.name}</span>
                  <span className="tabnum text-foreground">{pct(c.share)}</span>
                </div>
              ))}
            </div>
          ) : null}
          <div className="text-xs text-foreground-secondary">
            预估口径：基于未结算订单 TikTok 官方预估费率，反映最新费率政策，结算前即可发现调佣
          </div>
        </div>
      )}
    </BoardCard>
  );
}

/* ── 预估利润卡（plan/17 阶段3a，折 CNY）────────────────────────────────────────
   结构（shape）：① 预估利润 hero（利润率随附；settled 有值才显真实利润，否则一行小字说明，
   不再留半幅空 tile）；② 分项构成——GMV 为基数，逐项扣减，每行带「占 GMV%」与淡比例条，
   利润行高亮，一眼看懂「钱去哪了、利润占多少」（对照 fork DailyReport 的项目/金额/占比，
   但用淡条而非 ECharts 环图：更轻、移动端友好、成本未录入时不会画出误导的大绿块）；
   ③ 提示区——缺天/未录成本/含今日/口径说明统一成低权重图标行，不再是多面 amber 墙。
   bare=true：内嵌「经营概览」第四行，去外框避免卡中卡。 */
function ProfitCard({
  data,
  loading,
  bare = false,
}: {
  data: BoardData | null;
  loading: boolean;
  bare?: boolean;
}) {
  const p = data?.profit;
  const est = p?.estimated;
  const settled = p?.settled;
  const empty = !p?.available ? "暂无利润数据（需接生产店并跑聚合）" : "";
  // 商品成本未录入（product_cost≈0）→ 利润/利润率虚高，标注「未扣商品成本」，不展示误导性利润率。
  const costMissing = !!est && (!est.product_cost || est.product_cost === 0);
  // 覆盖天数护栏：预聚合表缺天 → 利润静默少算。有数据但覆盖不全时显告警，让缺失可见。
  const coverageIncomplete = !!p?.available && p?.coverage_complete === false;
  const includesToday = !!data?.window?.includes_today;

  // 分项占 GMV 比例（每行独立、皆为事实——成本=0 时该行 0%，不掩盖）。
  const gmv = est?.gmv ?? 0;
  const pctOf = (v: number | undefined) => (gmv > 0 && v != null ? (v / gmv) * 100 : 0);
  const deductions = [
    { label: "扣点", value: est?.commission_fee },
    { label: "广告费", value: est?.ad_cost },
    { label: "商品成本", value: est?.product_cost },
    { label: "预估退货", value: est?.refund_amount },
  ];
  const marginPct = est?.profit_margin ?? pctOf(est?.gross_profit);

  // 一行分项：淡比例条铺底 + 名称 + 金额 + 占比%。tone 决定底色/字色（base 基数 / cost 扣减 / profit 利润）。
  const Row = ({
    label,
    value,
    pct,
    tone,
    info,
  }: {
    label: string;
    value: number | undefined;
    pct: number;
    tone: "base" | "cost" | "profit";
    info?: ReactNode;
  }) => (
    <div className="relative rounded-md">
      {/* 比例条单独放进裁剪层；行容器本身不 overflow-hidden，否则会把 tooltip 气泡一起裁没。 */}
      <div className="absolute inset-0 overflow-hidden rounded-md" aria-hidden>
        <div
          className={`absolute inset-y-0 left-0 ${tone === "profit" ? "bg-positive/15" : "bg-fill-shallow"}`}
          style={{ width: `${Math.max(0, Math.min(100, pct))}%` }}
        />
      </div>
      <div className="relative flex items-center justify-between px-2 py-1.5">
        <span
          className={`inline-flex items-center gap-1 text-sm ${
            tone === "profit"
              ? "font-semibold text-positive"
              : tone === "base"
                ? "font-medium text-foreground"
                : "text-foreground-secondary"
          }`}
        >
          {label}
          {info}
        </span>
        <span className="flex items-baseline gap-2.5">
          <span
            className={`tabnum text-sm ${
              tone === "profit" ? "font-semibold text-positive" : "text-foreground"
            }`}
          >
            {fmtMoneyCny(value)}
          </span>
          <span className="tabnum w-9 text-right text-xs text-foreground-tertiary">
            {pct.toFixed(0)}%
          </span>
        </span>
      </div>
    </div>
  );

  // 提示区：统一成低权重图标行（暖色只落在小图标、文字用深前景色，避免暖白底浅色文字 washed-out）。
  const notes: { icon: ReactNode; text: ReactNode }[] = [];
  if (coverageIncomplete)
    notes.push({
      icon: <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" />,
      text: `近 ${p?.expected_days ?? "?"} 天缺 ${(p?.expected_days ?? 0) - (p?.covered_days ?? 0)} 天数据，利润偏低，将自动补齐。`,
    });
  if (costMissing)
    notes.push({
      icon: <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" />,
      text: "未录入商品成本，利润偏高。",
    });
  if (includesToday)
    notes.push({
      icon: <Calendar className="mt-0.5 h-3.5 w-3.5 shrink-0 text-foreground-tertiary" />,
      text: "含今日，为当日累计、次日凌晨定稿。",
    });
  notes.push({
    icon: <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-foreground-tertiary" />,
    text: "扣点、广告费取自 TikTok 官方，退货按设定退货率预估。",
  });

  const body = (
    <>
      <CardHead title="预估利润（折 CNY）" as={bare ? "h3" : "h2"} />
      {loading || empty ? (
        <ChartEmpty loading={loading} empty={empty} height={200} />
      ) : (
        <div className="space-y-4">
          {/* ① 预估利润 hero（单块，不留空 tile） */}
          <div className="rounded-xl bg-fill-shallow p-4">
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-xs text-foreground-secondary">
                预估利润{costMissing && "（未扣商品成本）"}
              </span>
              {!costMissing && est?.profit_margin != null && (
                <span className="text-xs text-foreground-secondary">
                  利润率 {est.profit_margin.toFixed(1)}%
                </span>
              )}
            </div>
            <div className="tabnum text-3xl font-bold text-foreground">
              {fmtMoneyCny(est?.gross_profit)}
            </div>
            {settled ? (
              <div className="mt-1 text-xs text-foreground-secondary">
                结算后真实利润{" "}
                <span className="tabnum font-medium text-foreground">
                  {fmtMoneyCny(settled.gross_profit)}
                </span>
              </div>
            ) : (
              <div className="mt-1 text-xs text-foreground-tertiary">
                结算后真实利润将在订单结算（通常数日后）后显示
              </div>
            )}
          </div>

          {/* ② 分项构成：GMV 基数 → 逐项扣减 → 利润 */}
          <div className="space-y-1">
            <Row
              label="GMV"
              value={est?.gmv}
              pct={100}
              tone="base"
              info={
                <InfoTooltip
                  align="start"
                  content="按下单时间统计，含货到付款（COD）尚未付款的在途订单，所以会比上方「经营概览」里只算已付款的 GMV 大。"
                >
                  <Info className="h-3.5 w-3.5" />
                </InfoTooltip>
              }
            />
            {deductions.map((d) => (
              <Row key={d.label} label={d.label} value={d.value} pct={pctOf(d.value)} tone="cost" />
            ))}
            <div className="!mt-2 border-t border-border-shallow pt-1">
              <Row label="预估利润" value={est?.gross_profit} pct={marginPct} tone="profit" />
            </div>
          </div>

          {/* ③ 提示区：统一低权重图标行 */}
          <div className="space-y-1.5 border-t border-border-shallow pt-3">
            {notes.map((n, i) => (
              <div key={i} className="flex items-start gap-2 text-xs text-foreground-secondary">
                {n.icon}
                <span>{n.text}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  );
  // bare：作「经营概览」第四行内嵌，顶部分隔线与 KPI 区分，不再套卡边框（避免卡中卡）。
  return bare ? (
    <div className="mt-4 border-t border-border-shallow pt-4">{body}</div>
  ) : (
    <BoardCard>{body}</BoardCard>
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
      <div className="flex items-center gap-2 text-foreground-secondary">
        {icon}
        <span className="text-xs">{title}</span>
      </div>
      <div className="tabnum text-2xl font-bold text-foreground">{loading ? "…" : value}</div>
      {!loading && subtitle && (
        <div className="text-xs text-foreground-secondary">{subtitle}</div>
      )}
      {!loading && dir && (
        <div
          className={`flex items-center gap-1 text-xs ${
            dir === "up" ? "text-positive" : dir === "down" ? "text-negative" : "text-foreground-secondary"
          }`}
        >
          <span>{dir === "up" ? "↑" : dir === "down" ? "↓" : "−"}</span>
          <span className="tabnum">{Math.abs(change as number).toFixed(1)}%</span>
          <span className="text-foreground-secondary">vs 上期</span>
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
    backgroundColor: t.card,
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
      // 图例置顶（原 bottom:0 会和 X 轴标签重叠压字）：与 X 轴彻底分离。
      legend: {
        data: ["订单数", "销量"],
        top: 0,
        left: "center",
        textStyle: { color: t.sub, fontSize: 11 },
        icon: "roundRect",
      },
      grid: { top: 36, right: 16, bottom: 28, left: 50 },
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

  // 窗口含今日：当期含半天今天,环比已在后端按 intraday 公平比较（不再假暴跌），此处再显眼
  // 标一枚徽章告诉客户「今天还没走完、为当日累计」，避免把今天的低值当成下降。
  const asOf = data?.window?.includes_today ? data.window.as_of_label : null;

  return (
    <BoardCard>
      <CardHead
        title="经营概览"
        right={
          asOf ? (
            // 暖色底作「留意」信号,文字用深前景色保证小字可读（amber-on-amber 小字不达 AA）。
            <span className="inline-flex items-center gap-1.5 rounded-full bg-warning/10 px-2.5 py-1 text-xs text-foreground-secondary">
              <Calendar className="h-3.5 w-3.5 shrink-0 text-warning" />
              {asOf}
            </span>
          ) : undefined
        }
      />

      {/* KPI 固定 2 列：第一行 GMV/广告消耗、第二行 订单/销量、第三行 ROI/ROAS（客单价暂去） */}
      <div className="mb-4 grid grid-cols-2 gap-3">
        <MetricCard loading={loading} change={ch?.gmv} title="GMV（已付款）" value={fmtMoney(o?.gmv)} icon={<DollarSign size={14} />} />
        <MetricCard
          loading={loading}
          change={hasAdSpend ? ch?.ad_cost : undefined}
          title="广告消耗"
          value={adCostValue}
          subtitle={hasAdSpend ? "结算口径" : "暂无结算数据"}
          icon={<Megaphone size={14} />}
        />
        <MetricCard loading={loading} change={ch?.order_count} title="订单数" value={fmtInt(o?.order_count)} icon={<ShoppingCart size={14} />} />
        <MetricCard loading={loading} change={ch?.units_sold} title="销量" value={fmtInt(o?.units_sold)} icon={<TrendingUp size={14} />} />
        {/* ROI：口径待定，先占位（与 ROAS 并列第三行）。定口径后填值。 */}
        <MetricCard loading={loading} title="ROI" value="—" subtitle="口径待定" icon={<Percent size={14} />} />
        <MetricCard
          loading={loading}
          change={ch?.roas}
          title="ROAS"
          value={roasValue}
          subtitle="结算口径"
          icon={<Gauge size={14} />}
        />
      </div>

      {/* 第四行：预估利润大卡（bare 内嵌，紧随 KPI、趋势图之前） */}
      <ProfitCard data={data} loading={loading} bare />

      {/* Tab 紧贴图表右上（移动端不再隔着指标卡，便于触达） */}
      <div className="mb-2 flex items-center justify-end gap-2">
        {isDemo && <DemoBadge />}
        <TabPills tabs={tabs} value={activeTab} onChange={setActiveTab} />
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
    </BoardCard>
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
    <BoardCard>
      <CardHead
        title="爆款商品 TOP 10"
        right={<TabPills tabs={rankOptions} value={rankBy} onChange={setRankBy} />}
      />

      {loading ? (
        <ChartEmpty loading empty="" height={320} />
      ) : !items.length ? (
        <ChartEmpty loading={false} empty="该时段暂无销量数据" height={320} />
      ) : (
        <div className="flex flex-col gap-4 lg:flex-row">
          {/* 排行列表（照 fork：序号徽章 + 名称 + 数值；前 3 名 bg-foreground 实心） */}
          <div className="max-h-[320px] flex-1 space-y-1.5 overflow-y-auto">
            {items.map((p, index) => {
              const val = rankBy === "gmv" ? p.gmv ?? 0 : p.units_sold;
              return (
                <button
                  type="button"
                  key={(p.sku_id || "") + index}
                  onClick={() => setSelected(index)}
                  aria-pressed={selected === index}
                  className={
                    "flex w-full cursor-pointer items-center gap-3 rounded-lg p-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [@media(pointer:coarse)]:p-3 " +
                    (selected === index ? "bg-fill-default" : "hover:bg-fill-shallow")
                  }
                >
                  <span
                    className={
                      "flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-bold " +
                      (index < 3
                        ? "bg-foreground text-primary-foreground"
                        : "bg-fill-default text-foreground-secondary")
                    }
                  >
                    {index + 1}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium text-foreground">{skuName(p)}</div>
                    <div className="text-xs text-foreground-secondary">
                      {rankBy === "gmv" ? fmtMoney(val) : `${fmtInt(val)} 件`}
                    </div>
                  </div>
                </button>
              );
            })}
          </div>

          {/* 右侧明细面板：fork 是单品 7 天趋势图；我方无单品时序 → 换成选中品的真实占比/数值（不造假）。
              移动端：窄屏堆到列表下方满宽，lg 起回到右侧固定宽。 */}
          <div className="w-full shrink-0 lg:w-[240px]">
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
                  <div className="pt-1 text-xs text-foreground-secondary">SKU · {sel.sku_id}</div>
                )}
              </div>
            ) : (
              <div className="flex h-full items-center justify-center py-6 text-center text-sm text-foreground-secondary lg:py-0">
                点击商品查看明细
              </div>
            )}
          </div>
        </div>
      )}
    </BoardCard>
  );
}

function DetailStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl bg-fill-shallow p-3">
      <div className="text-xs text-foreground-secondary">{label}</div>
      <div className="tabnum mt-0.5 text-lg font-bold text-foreground">{value}</div>
    </div>
  );
}

/* ── 库存健康（照 fork InventoryHealth：汇总=仪表盘+分布 / 明细=搜索排序表）──── */

type InventoryView = "summary" | "details";

const LOW_BADGE: Record<string, { label: string; cls: string }> = {
  stockout: { label: "缺货", cls: "bg-negative/15 text-negative" },
  critical: { label: "告急", cls: "bg-warning/15 text-warning" },
  warning: { label: "偏低", cls: "bg-caution/15 text-caution" },
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
      tooltip: { trigger: "item" as const, backgroundColor: t.card, borderColor: t.grid, textStyle: { color: t.text } },
      legend: { bottom: 0, textStyle: { color: t.sub, fontSize: 11 }, icon: "roundRect" },
      series: [
        {
          type: "pie",
          radius: ["45%", "70%"],
          center: ["50%", "44%"],
          avoidLabelOverlap: false,
          itemStyle: { borderRadius: 6, borderColor: t.card, borderWidth: 2 },
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
    <BoardCard>
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
                aria-hidden
                className="absolute left-3 top-1/2 -translate-y-1/2 text-foreground-secondary"
              />
              <input
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="搜索商品名或 SKU…"
                aria-label="搜索商品名或 SKU"
                className="h-8 w-full rounded-lg border border-border bg-card pl-8 pr-3 text-sm text-foreground placeholder:text-foreground-secondary focus:border-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-ring [@media(pointer:coarse)]:h-11"
              />
            </div>
            <select
              value={status}
              onChange={(e) => setStatus(e.target.value)}
              aria-label="按库存状态筛选"
              className="h-8 appearance-none rounded-lg border border-border bg-card px-3 text-sm text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring [@media(pointer:coarse)]:h-11"
            >
              <option value="all">全部状态</option>
              <option value="stockout">缺货</option>
              <option value="critical">告急</option>
              <option value="warning">偏低</option>
            </select>
          </div>

          {/* 可排序表（照 fork：表头点击排序 + ArrowUpDown）。窄屏横滚防 6 列压扁/裁切。 */}
          <div className="overflow-x-auto rounded-lg border border-border-shallow">
            <table className="w-full min-w-[640px] text-sm">
              <thead>
                <tr className="bg-fill-shallow">
                  <SortableTh label="商品" active={sortField === "name"} dir={sortDir} onClick={() => handleSort("name")} />
                  <th className="px-3 py-2 text-left font-medium text-foreground-secondary">SKU</th>
                  <SortableTh label="库存" numeric active={sortField === "available_stock"} dir={sortDir} onClick={() => handleSort("available_stock")} />
                  <th className="px-3 py-2 text-right font-medium text-foreground-secondary">日均销量</th>
                  <SortableTh label="可售天数" numeric active={sortField === "days_of_cover"} dir={sortDir} onClick={() => handleSort("days_of_cover")} />
                  <th className="px-3 py-2 text-center font-medium text-foreground-secondary">状态</th>
                </tr>
              </thead>
              <tbody>
                {filtered.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="px-3 py-8 text-center text-foreground-secondary">
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
                        <td className="px-3 py-2 text-foreground-secondary">{it.sku_id}</td>
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
    </BoardCard>
  );

  function skuLabel(i: LowStockItem) {
    return i.product_name || i.sku_id;
  }
}

function SortableTh({
  label,
  numeric,
  active,
  dir,
  onClick,
}: {
  label: string;
  numeric?: boolean;
  active?: boolean;
  dir?: "asc" | "desc";
  onClick: () => void;
}) {
  return (
    <th
      scope="col"
      aria-sort={active ? (dir === "asc" ? "ascending" : "descending") : "none"}
      className={"px-3 py-2 font-medium text-foreground-secondary " + (numeric ? "text-right" : "text-left")}
    >
      <button
        type="button"
        onClick={onClick}
        className={
          "flex items-center gap-1 rounded font-medium hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [@media(pointer:coarse)]:min-h-[44px] " +
          (numeric ? "ml-auto justify-end" : "")
        }
      >
        {label}
        <ArrowUpDown size={12} aria-hidden className={active ? "text-foreground" : ""} />
      </button>
    </th>
  );
}

/* ── 订单与履约（满宽多 tab）：待发货=真实履约数据；下单/退货/退款=fork 演示数据
      （后端暂无源，见 docs/board-data-backlog.md，演示 tab 标「演示数据」徽章）─────── */

const PEND_BADGE: Record<string, { label: string; cls: string }> = {
  overdue: { label: "超时", cls: "bg-negative/15 text-negative" },
  critical: { label: "临界", cls: "bg-warning/15 text-warning" },
  normal: { label: "正常", cls: "bg-positive/15 text-positive" },
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
    <BoardCard>
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
            <div className="mb-3 text-xs text-foreground-secondary">
              快照 {data.fulfillment.snapshot_at}
            </div>
          )}
          <div className="mb-4 flex flex-wrap gap-x-8 gap-y-3">
            <Stat label="待发货合计" value={fmtInt(b?.total)} loading={loading} />
            <Stat label="超时" value={fmtInt(b?.overdue)} tone="negative" loading={loading} />
            <Stat label="临界" value={fmtInt(b?.critical)} tone="warning" loading={loading} />
            <Stat label="正常" value={fmtInt(b?.normal)} loading={loading} />
          </div>
          <div className="overflow-x-auto rounded-lg border border-border-shallow">
            <table className="w-full min-w-[640px] text-sm">
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
                    <td colSpan={6} className="px-3 py-8 text-center text-foreground-secondary">
                      加载中…
                    </td>
                  </tr>
                ) : items.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="px-3 py-8 text-center text-foreground-secondary">
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
    </BoardCard>
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
      <div className="mb-1 text-xs text-foreground-secondary">{label}</div>
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
