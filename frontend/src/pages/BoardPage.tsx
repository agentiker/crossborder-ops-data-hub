import { useEffect, useMemo, useState, createContext, useContext, type ReactNode } from "react";
import {
  ArrowUpDown,
  Calendar,
  Clock,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  DollarSign,
  Flame,
  Gauge,
  Globe,
  Info,
  MapPin,
  Megaphone,
  MessageCircleQuestion,
  Package,
  Search,
  ShoppingBag,
  ShoppingCart,
  Sparkles,
  Store,
  TriangleAlert,
  X,
  ZoomIn,
} from "lucide-react";
import {
  api,
  type BoardData,
  type BoardQuery,
  type LowStockItem,
  type NewProduct,
  type ProductChannels,
  type ProductDetail,
  type RefundAnalysis,
  type TopSku,
  type TrendPoint,
} from "@/api";
import { DateRangePicker, type DateRangeValue } from "@/components/board/DateRangePicker";
import { InfoTooltip } from "@/components/ui/tooltip";
import { EChart, useChartTokens } from "@/components/EChart";
import { AskAiSheet } from "@/components/board/AskAiSheet";
import { useImageViewer } from "@/components/ImageViewer";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { hiResUrl } from "@/lib/img";

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
// 利润折 CNY 展示（¥ 前缀），与 fmtMoney(Rp/IDR) 区分，避免币种误标。保留两位小数（利润金额较小，
// 整数会掩盖分项差异，如退货几十元四舍五入误差显眼）。
const fmtMoneyCny = (n: number | undefined | null) =>
  n == null
    ? "—"
    : "¥ " +
      Number(n).toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      });
// 占比百分比：≥1 取整（20%）；(0,1) 小值保留 1 位（0.5%），避免 toFixed(0) 把真实退货率
// 0.46% 显示成误导的 0%；(0,0.05) 极小非零显「<0.1%」，不假报 0.0%。
const fmtPct = (n: number) => {
  if (n <= 0) return "0%";
  if (n >= 1) return `${Math.round(n)}%`;
  if (n < 0.05) return "<0.1%";
  return `${n.toFixed(1)}%`;
};

// 下钻：平滑滚动到某锚点卡片并短暂高亮（利润卡「扣点」→ 费率监控卡跨区跳转）。
// 高亮靠临时加 class（board-card-flash，定义在 index.css），1.6s 后移除。
const FEE_RATE_CARD_ID = "fee-rate-monitor-card";
const REFUND_CARD_ID = "refund-cancel-card";
function scrollToCard(id: string) {
  const el = document.getElementById(id);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  el.classList.remove("board-card-flash");
  // 强制重排后再加 class，确保连续点击也能重新触发动画
  void el.offsetWidth;
  el.classList.add("board-card-flash");
  window.setTimeout(() => el.classList.remove("board-card-flash"), 1600);
}

export function BoardPage() {
  const [platform, setPlatform] = useState("tiktok_shop"); // 平台默认 TikTok
  const [region, setRegion] = useState("ID"); // 区域默认 印尼（→country）
  const [scope, setScope] = useState(""); // 店铺（范围 scope_key）
  const [range, setRange] = useState<DateRangeValue>(last7); // 日期默认近 7 天
  const [data, setData] = useState<BoardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0); // 错误态「重试」手动重触发 fetch
  // 「问 AI」抽屉：卡片口径疑问经 AskAiContext 上抛到此,就地弹出 AI 解答(不跳转对话页)。
  const [askQ, setAskQ] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    // 单天（起止同一天，如预设「今天」）→ 趋势切逐小时；多天回退逐日。后端跨天误传会静默回退。
    const granularity =
      range.start && range.start === range.end ? "hour" : undefined;
    api
      .boardData({
        start: range.start ?? undefined,
        end: range.end ?? undefined,
        scope,
        platform: platform || undefined,
        country: region || undefined,
        granularity,
      })
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError(String(e)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [platform, region, scope, range.start, range.end, reloadKey]);

  const canSwitch = !!data?.can_switch && (data?.scopes.length ?? 0) > 1;

  // 区1 区头副说明：回显后端实际取数窗口（data.window，与卡片数字同源，比前端 range 更稳——
  // DateRangeValue 无 preset label 拿不到「近7天」字样）。含今日时点明「当日累计」，精确时刻
  // 交给经营概览卡头已有的 as_of 徽章，区头不重复。
  const dateSectionHint = useMemo(() => {
    const w = data?.window;
    if (!w) return "下方数据均按上方所选日期范围统计。";
    const md = (iso: string) => {
      const [, m, d] = iso.split("-");
      return `${Number(m)}/${Number(d)}`;
    };
    const span = w.start === w.end ? md(w.start) : `${md(w.start)} ~ ${md(w.end)}`;
    const todayNote = w.includes_today ? "（含今日，为当日累计）" : "";
    return `下方数据按所选日期 ${span} 统计${todayNote}。`;
  }, [data?.window]);

  // 上划收起筛选栏（移动端省纵向空间）：文档级滚动改监听 window.scrollY；带迟滞阈值防边界抖动。
  const [filtersCollapsed, setFiltersCollapsed] = useState(false);
  useEffect(() => {
    const onScroll = () => {
      const top = window.scrollY;
      setFiltersCollapsed((prev) => (top > 40 ? true : top < 10 ? false : prev));
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <AskAiContext.Provider value={setAskQ}>
    <section className="flex flex-1 flex-col">
      {/* Header（文档滚动：桌面 sticky 贴顶，移动端随内容滚走、靠全局顶栏导航） */}
      <header className="z-40 flex h-[68px] shrink-0 items-center justify-between gap-2 border-b border-border-shallow bg-background px-4 lg:sticky lg:top-0">
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

      {/* Content（文档级滚动：去内层 overflow，自然撑高让 body 滚动 → iOS 下滑收栏沉浸） */}
      <div className="flex-1 p-4 sm:p-6">
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
              {/* ── 区1：按所选日期（随上方日期筛选变化）── */}
              <SectionHeader title="按所选日期" hint={dateSectionHint} />
              <NoDataBanner data={data} loading={loading} />
              <BusinessOverview data={data} loading={loading} />
              {/* 预估利润 + 渠道分布并列（桌面两列、移动端堆成两行）。利润卡放进两列后自然收窄。 */}
              <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
                <ProfitCard data={data} loading={loading} />
                <ChannelPie data={data} loading={loading} />
              </div>
              <HotProducts
                data={data}
                loading={loading}
                query={{
                  start: range.start ?? undefined,
                  end: range.end ?? undefined,
                  scope,
                  platform: platform || undefined,
                  country: region || undefined,
                }}
              />
              {/* 退款/取消：随日期筛选变化，故归入区1「按所选日期」（原在底部固定口径区，口径错位）。 */}
              <RefundSection data={data} loading={loading} />

              {/* ── 区2：实时·固定口径（不随日期筛选）── */}
              <SectionHeader
                accent
                title="实时 · 固定口径"
                hint="以下数据不随上方日期筛选变化：库存为当前快照、新品为独立近 60 天窗口、费率为固定评估/基准窗口、待发货为当前未发货订单。"
              />
              <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
                <InventoryHealth data={data} loading={loading} />
                <FeeRateMonitor data={data} loading={loading} />
              </div>
              <NewProducts
                query={{
                  scope,
                  platform: platform || undefined,
                  country: region || undefined,
                }}
                reloadKey={reloadKey}
              />
              <OrderSection data={data} loading={loading} />
            </>
          )}
        </div>
      </div>
    </section>
    {askQ !== null && <AskAiSheet question={askQ} onClose={() => setAskQ(null)} />}
    </AskAiContext.Provider>
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
      <Calendar className="mt-0.5 h-4 w-4 shrink-0 text-foreground-tertiary" />
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
// id 可选：作滚动锚点（如费率卡供利润卡「扣点」下钻跳转）。
function BoardCard({ children, id }: { children: ReactNode; id?: string }) {
  return (
    <div id={id} className="rounded-2xl border border-border-shallow bg-card p-5">{children}</div>
  );
}

// 「问 AI」入口：点击就地在看板弹出 AI 解答抽屉（不再跳转对话页）。
// AI 端接 ops_business_rules 工具，依 docs/business-rules.md 权威口径作答。
// 两种形态：link=极小文字链（贴 ⓘ 说明旁，低权重不挤数字区）；button=弹窗底部按钮。
// 移动端：触控热区放大（-m-1 p-1 / py-1.5），与既有 ⓘ 同处呈现，不新增视觉噪声。
//
// 问题经 AskAiContext 上抛到 BoardPage 顶层开抽屉——避免把 onAsk 逐层穿过卡片组件。
const AskAiContext = createContext<(question: string) => void>(() => {});

function AskAiLink({
  question,
  variant = "link",
  className,
  onBeforeAsk,
}: {
  question: string;
  variant?: "link" | "button";
  className?: string;
  // 打开抽屉前的副作用,如关掉所在的广告弹窗(避免抽屉叠在弹窗之上)。
  onBeforeAsk?: () => void;
}) {
  const ask = useContext(AskAiContext);
  const go = (e: React.MouseEvent) => {
    e.stopPropagation(); // 别触发卡片/弹窗自身的点击（如弹窗遮罩关闭）
    onBeforeAsk?.();
    ask(question);
  };
  if (variant === "button") {
    return (
      <button
        type="button"
        onClick={go}
        className={cn(
          "inline-flex items-center gap-1.5 rounded-lg border border-border-shallow px-3 py-1.5 text-xs font-medium text-info transition-colors hover:bg-info/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          className,
        )}
      >
        <MessageCircleQuestion className="h-3.5 w-3.5" />
        还有疑问？问 AI
      </button>
    );
  }
  return (
    <button
      type="button"
      onClick={go}
      aria-label="就此口径问 AI"
      className={cn(
        "-m-1 inline-flex items-center gap-0.5 p-1 text-xs text-info transition-colors hover:text-info/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [@media(pointer:coarse)]:p-1.5",
        className,
      )}
    >
      <MessageCircleQuestion className="h-3.5 w-3.5" />
      问 AI
    </button>
  );
}

// 版面分区区头（区1 按所选日期 / 区2 实时·固定口径）。轻量、非卡片，贴 space-y-6 节奏
// 浮在卡片之上。accent=true（区2）：整块极淡蓝底 + 前导 info 图标，克制地暗示「不随筛选」
// （蓝在本项目 token 语义=监控/信息，非告警，正贴合固定口径的"实时监控"含义）。浅底+图标
// 已承载语义，不用左侧色条（项目 DESIGN.md 头号禁令：>1px 彩色 border-left/right）。
function SectionHeader({
  title,
  hint,
  accent = false,
}: {
  title: ReactNode;
  hint?: ReactNode;
  accent?: boolean;
}) {
  return (
    <div className={"rounded-lg px-3 py-2 " + (accent ? "bg-info/5" : "")}>
      <div className="flex items-center gap-1.5">
        {accent && <Info className="h-3.5 w-3.5 shrink-0 text-info" />}
        <h2 className="text-sm font-semibold text-foreground">{title}</h2>
      </div>
      {hint && <p className="mt-0.5 text-xs text-foreground-secondary">{hint}</p>}
    </div>
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
          label: { show: false, position: "center" },
          // hover：扇区放大 + 中心显名称/占比（颜色解析已在 useChartTokens 规整为逗号 hsl，不再画没）。
          emphasis: {
            scale: true,
            scaleSize: 6,
            label: { show: true, position: "center", fontSize: 13, fontWeight: "bold", color: t.text },
          },
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
  // 结算中区间（近 settle_lag 天，complete=false）：unsettled 样本不完整偏高，虚化+底纹标注，
  // 避免把当天暂态高点误读为涨佣。找第一个 incomplete 点作 markArea 起点（到最后一点）。
  const firstIncompleteIdx = points.findIndex((p) => p.complete === false);
  const settlingLabels =
    firstIncompleteIdx >= 0
      ? points.slice(firstIncompleteIdx).map((p) => p.date.slice(5))
      : [];
  const option = useMemo(
    () => ({
      grid: { left: 8, right: 12, top: 12, bottom: 4, containLabel: true },
      tooltip: {
        trigger: "axis",
        formatter: (ps: { axisValue: string; data: number }[]) => {
          const settling = settlingLabels.includes(ps[0]?.axisValue);
          return `${ps[0]?.axisValue}<br/>预估费率 ${ps[0]?.data?.toFixed(2)}%${
            settling ? "<br/><span style='color:#ce7612'>· 结算中·样本不完整偏高</span>" : ""
          }`;
        },
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
          // 结算中区间：琥珀色浅底纹标注（近 settle_lag 天样本不完整）。有 incomplete 点才加。
          ...(settlingLabels.length
            ? {
                markArea: {
                  silent: true,
                  itemStyle: { color: "rgba(206,118,18,0.08)" },
                  data: [
                    [
                      { xAxis: settlingLabels[0] },
                      { xAxis: settlingLabels[settlingLabels.length - 1] },
                    ],
                  ],
                },
              }
            : {}),
        },
      ],
    }),
    [points, lineColor, t, settlingLabels],
  );
  const insufficient = status === "insufficient";
  return (
    <BoardCard id={FEE_RATE_CARD_ID}>
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
            <div className="flex items-start gap-1.5 rounded-lg bg-info/10 px-3 py-2 text-xs text-info">
              <Clock className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>已结算基准积累中（需 ~2 周结算历史），暂无法判定异常，先展示当前费率水平与构成。</span>
            </div>
          )}
          {/* 趋势 */}
          <EChart option={option} height={150} />
          {/* 异常时点名分项归因 */}
          {status === "alert" && fr?.attributions?.length ? (
            <div className="rounded-xl bg-negative/10 p-3 text-sm">
              <div className="mb-1 flex items-center gap-1.5 font-medium text-negative">
                <MapPin className="h-3.5 w-3.5 shrink-0" />主要涨幅来自
              </div>
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
   ③ 提示区——缺天/未录成本/含今日/口径说明统一成低权重图标行，不再是多面 amber 墙。 */
function ProfitCard({
  data,
  loading,
}: {
  data: BoardData | null;
  loading: boolean;
}) {
  const p = data?.profit;
  const est = p?.estimated;
  const settled = p?.settled;
  const includesToday = !!data?.window?.includes_today;
  // 空状态分两种、给老板看人话（不暴露「聚合/生产店」黑话）：
  // ① 含今日且无预估行 → 今日利润凌晨结算后才有，引导先看其它天；
  // ② 全窗口无预估行 → 数据源还没接通。
  const empty = p?.available
    ? ""
    : includesToday
      ? "今天的利润要等今晚结算后才有，可先选其它日期查看预估利润。"
      : "利润数据还没接好，接通后这里会显示每天的预估利润。";
  // 商品成本未录入（product_cost≈0）→ 利润/利润率虚高，标注「未扣商品成本」，不展示误导性利润率。
  const costMissing = !!est && (!est.product_cost || est.product_cost === 0);
  // 覆盖天数护栏：预聚合表缺天 → 利润静默少算。有数据但覆盖不全时显告警，让缺失可见。
  const coverageIncomplete = !!p?.available && p?.coverage_complete === false;

  // 分项占 GMV 比例（每行独立、皆为事实——成本=0 时该行 0%，不掩盖）。
  const gmv = est?.gmv ?? 0;
  const pctOf = (v: number | undefined) => (gmv > 0 && v != null ? (v / gmv) * 100 : 0);
  const deductions = [
    { label: "扣点", value: est?.commission_fee, onClick: () => scrollToCard(FEE_RATE_CARD_ID) },
    { label: "广告费", value: est?.ad_cost },
    { label: "商品成本", value: est?.product_cost },
    {
      label: "预估退货",
      value: est?.refund_amount,
      onClick: () => scrollToCard(REFUND_CARD_ID),
      info: (
        <InfoTooltip
          align="start"
          content="预估退货 = 退货率 × GMV。退货率优先取本店近 30 天真实退货率（付款后取消金额 ÷ GMV），样本不足时回落到设置的默认退货率。真实退货滞后发生，故用「率 × 当期 GMV」预估、而非直接扣已发生的退货，避免高估当期利润。点击可查看「退款 / 取消」卡的真实退款明细（口径不同：此处为预估占位、退款卡为已发生的真实取消额，两数不相等属正常）。"
        >
          <Info className="h-3.5 w-3.5" />
        </InfoTooltip>
      ),
    },
  ];
  const marginPct = est?.profit_margin ?? pctOf(est?.gross_profit);

  // 一行分项：淡比例条铺底 + 名称 + 金额 + 占比%。tone 决定底色/字色（base 基数 / cost 扣减 / profit 利润）。
  // onClick 可选：传入则整行可点击（hover 变色 + 光标 + 尾部箭头提示），用于下钻到相关卡片（如扣点→费率监控）。
  const Row = ({
    label,
    value,
    pct,
    tone,
    info,
    onClick,
  }: {
    label: string;
    value: number | undefined;
    pct: number;
    tone: "base" | "cost" | "profit";
    info?: ReactNode;
    onClick?: () => void;
  }) => (
    <div
      className={`relative rounded-md ${onClick ? "cursor-pointer transition-colors hover:bg-fill-shallow" : ""}`}
      onClick={onClick}
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
      onKeyDown={
        onClick
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onClick();
              }
            }
          : undefined
      }
    >
      {/* 比例条单独放进裁剪层；行容器本身不 overflow-hidden，否则会把 tooltip 气泡一起裁没。 */}
      <div className="absolute inset-0 overflow-hidden rounded-md" aria-hidden>
        <div
          className={`absolute inset-y-0 left-0 ${tone === "profit" ? "bg-positive/25" : "bg-fill-deep"}`}
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
          {info && (
            // info 图标点击只弹 tooltip，不冒泡触发整行 onClick 跳转（否则想看说明反被滚走）。
            <span
              onClick={(e) => e.stopPropagation()}
              onKeyDown={(e) => e.stopPropagation()}
              className="inline-flex items-center"
            >
              {info}
            </span>
          )}
          {onClick && <ChevronRight className="h-3.5 w-3.5 text-foreground-tertiary" />}
        </span>
        <span className="flex items-baseline gap-2.5">
          <span
            className={`tabnum text-sm ${
              tone === "profit" ? "font-semibold text-positive" : "text-foreground"
            }`}
          >
            {fmtMoneyCny(value)}
          </span>
          <span className="tabnum w-12 text-right text-xs text-foreground-tertiary">
            {fmtPct(pct)}
          </span>
        </span>
      </div>
    </div>
  );

  // 提示区：统一成低权重图标行（暖色只落在小图标、文字用深前景色，避免暖白底浅色文字 washed-out）。
  const notes: { icon: ReactNode; text: ReactNode }[] = [];
  if (coverageIncomplete) {
    const missing = (p?.expected_days ?? 0) - (p?.covered_days ?? 0);
    notes.push({
      icon: <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" />,
      // 缺的多半是今天（今晚结算后补）；缺历史天则如实说明会自动补齐。
      text:
        includesToday && missing <= 1
          ? "今天的利润要等今晚结算后才计入，当前为已结算天数的合计。"
          : `所选 ${p?.expected_days ?? "?"} 天里还差 ${missing} 天的数据，利润暂时偏低，会自动补齐。`,
    });
  }
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
    text: "扣点、广告费取自 TikTok 官方结算数据；退货按近 30 天真实退货率预估（详见「预估退货」说明）。",
  });

  const body = (
    <>
      <CardHead
        title={
          <span className="inline-flex items-center gap-1.5">
            预估利润（折 CNY）
            <InfoTooltip
              align="start"
              content="利润把印尼盾（IDR）金额折算成人民币展示。汇率取自「中国银行外汇牌价」的中行折算价，按业务日、日内多次采样取均值。2026年7月2日起用中行真实牌价折算；此前的历史数据用固定近似汇率，口径略有差异。"
            >
              <Info className="h-3.5 w-3.5 text-foreground-tertiary" />
            </InfoTooltip>
          </span>
        }
        as="h2"
      />
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
                <span className="inline-flex items-center gap-2.5">
                  <InfoTooltip
                    align="start"
                    content="利润卡的 GMV = 买家实付（含运费/税）、按下单统计但排除已取消单；与上方「经营概览」GMV（商品小计、含取消）口径不同，故两个数不相等，属正常。"
                  >
                    <Info className="h-3.5 w-3.5" />
                  </InfoTooltip>
                  <AskAiLink question="看板「预估利润卡」的 GMV 为什么比「经营概览」的 GMV 大？两者口径有什么区别？" />
                </span>
              }
            />
            {deductions.map((d) => (
              <Row
                key={d.label}
                label={d.label}
                value={d.value}
                pct={pctOf(d.value)}
                tone="cost"
                info={"info" in d ? d.info : undefined}
                onClick={"onClick" in d ? d.onClick : undefined}
              />
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
  return <BoardCard>{body}</BoardCard>;
}

/* ── 经营概览（照 fork BusinessOverview：MetricCard 行 + 分段 tab + 趋势图）──── */

// fork MetricCard：图标+标题 / 大数值 / 涨跌行。涨跌行走后端真实环比（当期 vs 紧邻等长上期）：
// 广告费构成弹窗：点击广告/ROAS 卡 ⓘ 打开。比 tooltip 空间大、且 fixed 居中不被滚动容器/卡片裁切。
// 列清付费投放(GMV Max) / 达人带货佣金(TAP+联盟) 各多少 + 营销总支出 + ROAS 口径 + 结算状态。
function AdSpendDialog({
  ads,
  onClose,
}: {
  ads: NonNullable<NonNullable<BoardData["overview"]>["ads"]>;
  onClose: () => void;
}) {
  // 锁背景滚动：mount-only，不依赖 onClose（避免父级重渲染令 onClose 变身→effect 重跑→
  // cleanup 还原成已是 "hidden" 的旧值→关闭后页面卡死）。
  useEffect(() => {
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = "";
    };
  }, []);
  // Esc 关闭（单独 effect，随 onClose 更新，不碰滚动锁）。
  useEffect(() => {
    const onEsc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onEsc);
    return () => window.removeEventListener("keydown", onEsc);
  }, [onClose]);

  const complete = ads.complete !== false;
  const asOf = ads.latest_covered_date ? ads.latest_covered_date.slice(5) : null;
  const noPaid = ads.paid_ad_spend <= 0;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="广告费构成"
      onClick={onClose}
      className="fixed inset-0 z-[60] flex items-end justify-center bg-black/50 sm:items-center sm:p-4"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="max-h-[86vh] w-full overflow-y-auto rounded-t-2xl bg-card p-5 pb-[max(1.25rem,calc(env(safe-area-inset-bottom)+0.75rem))] shadow-lg sm:max-w-md sm:rounded-2xl sm:pb-5"
      >
        <div className="mb-3 flex items-start justify-between">
          <div>
            <h3 className="text-base font-semibold text-foreground">广告费构成</h3>
            <p className="mt-0.5 text-xs text-foreground-secondary">结算口径（成交后由平台结算）</p>
          </div>
          <button
            type="button"
            aria-label="关闭"
            onClick={onClose}
            className="-m-1 rounded-lg p-1 text-foreground-secondary transition-colors hover:bg-fill-shallow hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [@media(pointer:coarse)]:p-2"
          >
            <X className="size-5" />
          </button>
        </div>

        <div className="space-y-0.5">
          <div className="flex items-center justify-between py-1.5">
            <span className="text-sm text-foreground">
              付费投放<span className="ml-1.5 text-xs text-foreground-tertiary">GMV Max 智能广告</span>
            </span>
            <span className="tabnum text-sm font-medium text-foreground">{fmtMoney(ads.paid_ad_spend)}</span>
          </div>
          <div className="flex items-center justify-between py-1.5">
            <span className="text-sm text-foreground">
              达人带货佣金<span className="ml-1.5 text-xs text-foreground-tertiary">成交分佣</span>
            </span>
            <span className="tabnum text-sm font-medium text-foreground">{fmtMoney(ads.creator_commission)}</span>
          </div>
          <div className="flex items-center justify-between py-1 pl-4">
            <span className="text-xs text-foreground-secondary">· TAP（达人代运营）</span>
            <span className="tabnum text-xs text-foreground-secondary">{fmtMoney(ads.tap_commission)}</span>
          </div>
          <div className="flex items-center justify-between py-1 pl-4">
            <span className="text-xs text-foreground-secondary">· 联盟（开放达人）</span>
            <span className="tabnum text-xs text-foreground-secondary">{fmtMoney(ads.affiliate_commission)}</span>
          </div>
          <div className="mt-1 flex items-center justify-between border-t border-border-shallow pt-2">
            <span className="text-sm font-semibold text-foreground">营销总支出</span>
            <span className="tabnum text-sm font-semibold text-foreground">{fmtMoney(ads.total_ad_spend)}</span>
          </div>
        </div>

        <div className="mt-4 space-y-2 rounded-lg bg-fill-shallow p-3 text-xs leading-relaxed text-foreground-secondary">
          <p>
            <span className="font-medium text-foreground">ROAS 口径：</span>GMV ÷ 付费投放（仅 GMV Max）。TAP
            与联盟都是达人带货佣金、成交才付、跟着 GMV 走，<span className="font-medium">不计入 ROAS</span>
            （否则等于在算佣金率倒数）。{noPaid ? "本店未投 GMV Max，故 ROAS 留空。" : ""}
          </p>
          <p>
            <span className="font-medium text-foreground">结算状态：</span>
            {complete
              ? "本窗口广告费已结算完整。"
              : `广告费结算滞后约两周，近几天的单仍在结算填充${
                  asOf ? `（数据截至 ${asOf}）` : ""
                }，故数字偏低、ROAS 暂不可比。`}
          </p>
        </div>

        <div className="mt-4 flex justify-end">
          <AskAiLink
            variant="button"
            onBeforeAsk={onClose}
            question="看板广告卡：付费投放（GMV Max）和达人带货佣金（TAP / 联盟）有什么区别？为什么我的 ROAS 有时显示『结算中』、暂不可比？"
          />
        </div>
      </div>
    </div>
  );
}

// 升=绿↑、降=红↓、持平=灰−；change 为 null/undefined（上期无基准或旧后端无该字段）时整行不渲染，不臆造。
function MetricCard({
  title,
  value,
  icon,
  change,
  loading,
  subtitle,
  info,
  className,
}: {
  title: string;
  value: string;
  icon: ReactNode;
  change?: number | null;
  loading?: boolean;
  // 可选副标注：广告卡用「结算口径」标口径、降级时用「暂无结算数据」提示，避免误导。
  subtitle?: string;
  // 可选标题旁信息气泡（口径说明），如广告卡拆分付费投放/达人佣金。
  info?: ReactNode;
  // 可选额外类名（如 col-span-2 让综合指标占满整行）。
  className?: string;
}) {
  const dir = change == null ? null : change > 0 ? "up" : change < 0 ? "down" : "flat";
  return (
    <div className={cn("flex h-full flex-col gap-1 rounded-xl bg-fill-shallow p-4", className)}>
      <div className="flex items-center gap-2 text-foreground-secondary">
        {icon}
        <span className="text-xs">{title}</span>
        {info}
      </div>
      {loading ? (
        <Skeleton className="my-1 h-7 w-24" />
      ) : (
        <div className="tabnum whitespace-nowrap text-xl font-bold leading-tight text-foreground lg:text-2xl">
          {value}
        </div>
      )}
      {/* 底部信息块贴底对齐（mt-auto）：卡片等高时，各卡的副标注/环比落在同一基线，
          不因上方 value 折行或有无副标注而错位（GMV 无副标注、广告只有副标注也能对齐）。 */}
      {!loading && (subtitle || dir) && (
        <div className="mt-auto flex flex-col gap-1 pt-1">
          {subtitle && <div className="text-xs text-foreground-tertiary">{subtitle}</div>}
          {dir && (
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
      )}
    </div>
  );
}

type OverviewTab = "sales" | "orders";

function BusinessOverview({ data, loading }: { data: BoardData | null; loading: boolean }) {
  const t = useChartTokens();
  const [activeTab, setActiveTab] = useState<OverviewTab>("sales");
  const [adDialogOpen, setAdDialogOpen] = useState(false); // 广告费构成弹窗（点击广告/ROAS 卡 ⓘ 打开）
  const o = data?.overview.orders;
  const ads = data?.overview.ads;
  const ch = data?.overview.change;
  // 订单数灰字：把从「订单数」到销量口径成交单的两个扣减项都摊开（已取消 + 未付款），
  // 避免客户只看到取消数、算 订单数−取消 仍 > 销量 而误以为矛盾（未付款单藏在差里）。
  const orderDeductions = [
    o?.cancelled_count ? `已取消 ${fmtInt(o.cancelled_count)}` : null,
    o?.unpaid_count ? `未付款 ${fmtInt(o.unpaid_count)}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  const orderSubtitle = orderDeductions ? `含${orderDeductions}` : undefined;
  const pts = data?.trend.points ?? [];
  // 「当前进行中的小时」(partial)：这个钟点才走了几分钟、数据天然不完整。不再断开不画（会显断崖），
  // 改为「进行中点」——值照显，但半透明 + 末段虚线 + 一枚小圆点，视觉上标明这个钟点还没走完。
  // 逐日/历史点 partial 恒 false，走原样（plain number），线条/柱子照常。
  const partialIdx = pts.findIndex((p) => p.partial);
  const lineData = (pick: (p: TrendPoint) => number, color: string) =>
    pts.map((p) =>
      p.partial
        ? {
            value: pick(p),
            itemStyle: { color, opacity: 0.55 },
            // data-item 的 lineStyle 作用于「连入该点」的那段线 → 末段（上一整点→当前钟点）虚线化。
            lineStyle: { color, type: "dashed" as const, opacity: 0.5 },
          }
        : pick(p),
    );
  const barData = (pick: (p: TrendPoint) => number) =>
    pts.map((p) => (p.partial ? { value: pick(p), itemStyle: { opacity: 0.4 } } : pick(p)));
  // 上期对比线（销售趋势）：单天=前一天逐小时、多天=等长上一期逐日。后端可能不返回（取数失败）。
  const prevPts = data?.trend.prev_points ?? [];
  // 无结算数据降级：广告消耗 0/缺失 → 卡值「—」+「暂无结算数据」；roas 为 null → 「—」。
  const hasAdSpend = !!ads && ads.total_ad_spend > 0;
  const adCostValue = hasAdSpend ? fmtMoney(ads!.total_ad_spend) : "—";
  const roasValue = ads && ads.roas != null ? `${ads.roas.toFixed(2)}×` : "—";
  // 广告口径拆分 + 结算护栏（2026-06-28）：付费投放(GMV Max+TAP) vs 达人佣金(CPS)；
  // complete=false → 窗口落在结算滞后区，广告/ROAS 不完整，标注「结算中·截至 X」避免误读。
  const adComplete = !ads || ads.complete !== false;
  const adAsOf = ads?.latest_covered_date ? ads.latest_covered_date.slice(5) : null;
  const settlingNote = !adComplete && adAsOf ? `结算中·截至 ${adAsOf}` : null;
  // 有营销支出但付费投放(GMV Max)为 0 = 全靠达人带货、没投智能广告 → ROAS 无从谈起，诚实标注。
  const noPaidSpend = hasAdSpend && !!ads && ads.paid_ad_spend <= 0;
  // 广告/ROAS 卡口径信息量大 → 点击 ⓘ 开居中弹窗（空间大、不被滚动容器/卡片裁切），不用易遮挡的 tooltip。
  const adInfoBtn = ads ? (
    <button
      type="button"
      aria-label="广告费构成说明"
      className="-m-1 inline-flex items-center p-1 text-foreground-secondary transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [@media(pointer:coarse)]:p-1.5"
      onClick={(e) => {
        e.stopPropagation();
        setAdDialogOpen(true);
      }}
    >
      <Info className="h-3.5 w-3.5" />
    </button>
  ) : undefined;
  // 逐小时趋势（单天）后端给 label="HH:00"；逐日则用 date 的 MM-DD。一行兼容两态。
  // 当天日期已在卡头 window_label 标明（印尼时间 X 月 X 日），tooltip 标题用 HH:00 已足够清晰。
  const labels = pts.map((p) => p.label ?? p.date.slice(5));

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
  // 上期对比虚线：单天=前一天逐小时、多天=等长上一期逐日。对齐口径按粒度分两种：
  //   - 逐小时（单天）：按 label（HH:00）对齐。今天只到当前小时（如 17 点→0~16 点），
  //     前一天却是整天 24 点，长度必然不等——旧的「长度相等才画」会导致「看当天永远不显示
  //     对比线」（2026-07-01 真机发现的 bug）。改为按当期每个小时去前一天取同一小时的值，
  //     对齐成与当期等长的数组（前一天缺该小时则 null，不画未来时段），语义=当天累计 vs
  //     昨天同一时段，与日报 intraday 环比同口径。
  //   - 逐日（多天）：上期是「等长上一期」，其 date 与当期不同、无法按 label 配对，仍按索引
  //     位置对齐（第 i 天 vs 上期第 i 天），要求等长；不等长（异常）则不画。
  const isHourly = data?.trend.granularity === "hour";
  const prevAligned: (number | null)[] = isHourly
    ? (() => {
        const byLabel = new Map(prevPts.map((p) => [p.label ?? p.date.slice(5), p.gmv]));
        return pts.map((p) => {
          const v = byLabel.get(p.label ?? p.date.slice(5));
          return v == null ? null : v;
        });
      })()
    : prevPts.length === pts.length
      ? prevPts.map((p) => p.gmv)
      : [];
  // 至少有一个对齐上的点才画对比线（逐小时按 label 交集、逐日按等长索引）。
  const hasPrev = prevAligned.some((v) => v != null);
  const salesPrevLabel = hasPrev ? (isHourly ? "前一天" : "上期") : null;
  const salesOption = useMemo(
    () => ({
      tooltip: {
        trigger: "axis" as const,
        ...tip,
        formatter: (ps: { axisValue: string; seriesName: string; value: number | null; color: string }[]) => {
          if (!ps.length) return "";
          const row = (name: string, val: number | null | undefined, color: string) =>
            val == null
              ? ""
              : `<br/><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${color};margin-right:6px;"></span>${name}　${fmtMoney(val)}`;
          const cur = ps.find((p) => p.seriesName === "GMV");
          const prev = ps.find((p) => p.seriesName === (salesPrevLabel ?? "__none__"));
          return `${cur?.axisValue ?? ""}${row("当期", cur?.value, "#6366f1")}${salesPrevLabel ? row(salesPrevLabel, prev?.value, "#a5b4fc") : ""}`;
        },
      },
      legend: hasPrev
        ? {
            data: ["GMV", salesPrevLabel as string],
            top: 0,
            left: "center",
            textStyle: { color: t.sub, fontSize: 11 },
            itemWidth: 16,
            itemHeight: 2,
          }
        : undefined,
      grid: { top: hasPrev ? 30 : 12, right: 16, bottom: 28, left: 60 },
      xAxis: axisX(false),
      yAxis: axisY,
      series: [
        {
          name: "GMV",
          type: "line",
          smooth: true,
          showSymbol: true,
          // 仅「进行中」点显圆点(size>0)，其余点 size=0 不显 → 保留原本干净的平滑线。
          symbolSize: (_v: unknown, p: { dataIndex: number }) => (p.dataIndex === partialIdx ? 7 : 0),
          data: lineData((p) => p.gmv, "#6366f1"),
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
        // 上期对比虚线：仅 hasPrev 时出现。无面积填充（参照线不应与主体争夺视觉权重）。
        ...(hasPrev
          ? [
              {
                name: salesPrevLabel as string,
                type: "line" as const,
                smooth: true,
                showSymbol: false,
                data: prevAligned,
                // 同色靛蓝浅化（indigo-300 #a5b4fc）+ dashed + 更细，明确「参照」语义。
                lineStyle: { color: "#a5b4fc", width: 2, type: "dashed" as const },
                itemStyle: { color: "#a5b4fc" },
                // 隐藏悬停圆点放大（emphasis），保持参照线的安静观感。
                emphasis: { disabled: true },
              },
            ]
          : []),
      ],
    }),
    [data, t, hasPrev, prevAligned, salesPrevLabel, partialIdx],
  );

  // fork 的「流量趋势」槽位无 UV/PV 数据源 → 换成我方真实的「订单数 + 销量」双系列。
  const ordersOption = useMemo(
    () => ({
      tooltip: { trigger: "axis" as const, ...tip },
      // 图例置顶（原 bottom:0 会和 X 轴标签重叠压字）：与 X 轴彻底分离。
      legend: {
        data: ["订单数", "销量（件）"],
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
          showSymbol: true,
          symbolSize: (_v: unknown, p: { dataIndex: number }) => (p.dataIndex === partialIdx ? 6 : 0),
          data: lineData((p) => p.order_count, t.positive),
          lineStyle: { color: t.positive, width: 2 },
          itemStyle: { color: t.positive },
        },
        {
          name: "销量（件）",
          type: "bar",
          data: barData((p) => p.units_sold),
          itemStyle: { color: t.warning, borderRadius: [4, 4, 0, 0] },
          barMaxWidth: 22,
        },
      ],
    }),
    [data, t, partialIdx],
  );

  const tabs: { id: OverviewTab; label: string }[] = [
    { id: "sales", label: "销售趋势" },
    { id: "orders", label: "订单 / 销量（件）" },
  ];

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

      {/* KPI 响应式：移动端 2 列、PC(lg) 用 11 列网格让 GMV 占 1.5 卡宽（GMV=3、其余各=2 →
          3+2+2+2+2=11），GMV 金额位数最多（十亿级 Rp）故略宽避免折行、又不像整 2 列那样过空。
          卡片等高 + 底部信息块贴底（见 MetricCard），使各卡「副标注 / vs 上期」落在同一基线、不错位。
          ROI 口径未定，不留占位死格。 */}
      <div className="mb-4 grid auto-rows-fr grid-cols-2 gap-3 lg:grid-cols-11">
        <MetricCard
          className="col-span-2 lg:col-span-3"
          loading={loading}
          change={ch?.gmv}
          title="GMV"
          value={fmtMoney(o?.gmv)}
          icon={<DollarSign size={14} />}
          info={
            <InfoTooltip
              align="start"
              content="按下单时间统计，包含所有订单（含已取消、货到付款在途单），金额为商品小计（不含运费/税/优惠）——与 TikTok 后台的 GMV 口径一致。"
            >
              <Info className="h-3.5 w-3.5" />
            </InfoTooltip>
          }
        />
        <MetricCard
          className="lg:col-span-2"
          loading={loading}
          change={ch?.order_count}
          title="订单数"
          value={fmtInt(o?.order_count)}
          subtitle={orderSubtitle}
          icon={<ShoppingCart size={14} />}
          info={
            <InfoTooltip
              align="start"
              content="按下单时间统计、含所有状态（含已取消、未付款），与 TikTok 后台「订单管理」列表口径一致。「订单数 − 已取消 − 未付款 = 已成交单」，故会大于按已付款统计的销量口径成交单数。"
            >
              <Info className="h-3.5 w-3.5" />
            </InfoTooltip>
          }
        />
        <MetricCard
          className="lg:col-span-2"
          loading={loading}
          change={ch?.units_sold}
          title="销量（件）"
          value={fmtInt(o?.units_sold)}
          subtitle="不含已取消和未付款"
          icon={<Package size={14} />}
          info={
            <InfoTooltip
              align="start"
              content="实际售出的商品件数（买 3 件同款算 3），按已付款口径统计、排除已取消/未付款单，与后台「数据罗盘 / Analytics」的 Items sold 口径一致。一单可含多件，故与订单数不成简单倍数关系。"
            >
              <Info className="h-3.5 w-3.5" />
            </InfoTooltip>
          }
        />
        <MetricCard
          className="lg:col-span-2"
          loading={loading}
          change={hasAdSpend && adComplete ? ch?.ad_cost : undefined}
          title="广告消耗"
          value={adCostValue}
          subtitle={hasAdSpend ? settlingNote ?? "结算口径·含达人佣金" : "暂无结算数据"}
          info={hasAdSpend ? adInfoBtn : undefined}
          icon={<Megaphone size={14} />}
        />
        <MetricCard
          className="lg:col-span-2"
          loading={loading}
          change={noPaidSpend || !adComplete ? undefined : ch?.roas}
          title="ROAS"
          value={noPaidSpend || !adComplete ? "—" : roasValue}
          subtitle={
            noPaidSpend ? "未投 GMV Max（全靠达人带货）" : adComplete ? "仅付费投放" : settlingNote ?? "结算中·暂不可比"
          }
          info={adInfoBtn}
          icon={<Gauge size={14} />}
        />
      </div>

      {adDialogOpen && ads && (
        <AdSpendDialog ads={ads} onClose={() => setAdDialogOpen(false)} />
      )}

      {/* Tab 紧贴图表右上（移动端不再隔着指标卡，便于触达） */}
      <div className="mb-2 flex items-center justify-end gap-2">
        <TabPills tabs={tabs} value={activeTab} onChange={setActiveTab} />
      </div>

      {loading || !pts.length ? (
        <ChartEmpty loading={loading} empty="该时段暂无趋势数据" height={220} />
      ) : (
        <div className="h-[220px]">
          <EChart option={activeTab === "sales" ? salesOption : ordersOption} height={220} />
        </div>
      )}
    </BoardCard>
  );
}

/* ── 爆款商品（商品级：小图 + 标题 + 款号 + 单量，点击行弹出商品详情弹窗）────────
   客户诉求落地：① 小图(image_url，缺图→序号色块) ② 标题(长名 2 行截断) ③ 款号(seller_sku，
   次要灰字；多规格显「N 个规格」) ④ 单量/GMV ⑤ 点击行弹出轻量弹窗：大图 + 完整商品名 +
   各 SKU 销量占比 + 渠道 4 分饼(达人/自营素材/商品卡/店铺页)，懒加载 /board/product-detail。
   弹窗整合所有详情(空间大、移动端体验好)，不再行内展开。 */

type RankBy = "sales" | "gmv";

// 爆款卡主行：客户要看款号(seller_sku)比商品名更直观。优先显 seller_sku；同商品多 SKU
// (sku_count>1) 时标「809-KHM-L 等N个规格」避免代表款号以偏概全；无款号回落商品名/id。
function productLabel(i: TopSku): string {
  if (i.seller_sku) {
    return (i.sku_count ?? 0) > 1 ? `${i.seller_sku} 等${i.sku_count}个规格` : i.seller_sku;
  }
  return i.product_name || i.product_id || i.sku_id || "?";
}

// 次要行：主行已显款号，这里补充商品名(灰字)做可读性；主行回落到商品名时不再重复。
function styleCodeLabel(i: TopSku): string | null {
  if (i.seller_sku && i.product_name) return i.product_name;
  return null;
}

// 商品小图：有图显缩略图(object-cover)，加载失败/无图回落「序号色块」。前 3 名色块用实心强调。
// 有图时可点击看高清大图（origin 原图，全局灯箱）。
function ProductThumb({ src, rank }: { src?: string; rank: number }) {
  const [failed, setFailed] = useState(false);
  const { open } = useImageViewer();
  const showImg = src && !failed;
  return (
    <div className="relative size-11 shrink-0 overflow-hidden rounded-lg">
      {showImg ? (
        <img
          src={src}
          alt=""
          loading="lazy"
          onError={() => setFailed(true)}
          onClick={() => open(hiResUrl(src), "商品大图")}
          className="size-full cursor-zoom-in object-cover transition-opacity hover:opacity-80"
        />
      ) : (
        <div
          className={
            "flex size-full items-center justify-center text-sm font-bold " +
            (rank <= 3
              ? "bg-foreground text-primary-foreground"
              : "bg-fill-default text-foreground-secondary")
          }
        >
          {rank}
        </div>
      )}
      {/* 有图时也叠一枚小序号角标，保留排名信息 */}
      {showImg && (
        <span
          className={
            "absolute left-0 top-0 flex size-4 items-center justify-center rounded-br-md text-[10px] font-bold " +
            (rank <= 3 ? "bg-foreground text-primary-foreground" : "bg-fill-deep text-foreground-secondary")
          }
        >
          {rank}
        </span>
      )}
    </div>
  );
}

// 渠道配色：显式高彩度 hex（主题 --primary 是近黑墨绿、彩度太低区分不开，故单品环图改用
// 鲜明配色）。达人=靛蓝系、自营=翠绿系，子项渐浅，使「细分」视觉上仍归到「粗分」的组里。
const CHANNEL_HEX: Record<string, string> = {
  // 粗分
  affiliate: "#6366f1", // 靛蓝
  seller_content: "#10b981", // 翠绿
  product_card: "#f59e0b", // 琥珀
  shop_tab: "#64748b", // 石板灰
  // 细分（达人 靛蓝渐浅 / 自营 翠绿渐浅）
  affiliate_live: "#6366f1",
  affiliate_video: "#818cf8",
  affiliate_other: "#c7d2fe",
  seller_live: "#10b981",
  seller_video: "#6ee7b7",
};
const channelHex = (key: string) => CHANNEL_HEX[key] ?? "#94a3b8";

// 渠道环图（数据由弹窗 fetch 后传入）。channels 可为粗分 4 或细分 7。
// hover：扇区放大 + 中心显名称/占比；下方常驻图例列出 色点·名称·占比%（比纯 hover 直观）。
function ChannelDonut({ channels }: { channels: ProductChannels["channels"] }) {
  // 按占比降序：占比越高越靠前。饼图扇区与下方常驻图例同源此数组，一处排序两处生效。
  const slices = useMemo(
    () => channels.filter((c) => c.gmv > 0).sort((a, b) => b.gmv - a.gmv),
    [channels],
  );
  const option = useMemo(
    () => ({
      tooltip: {
        trigger: "item",
        backgroundColor: "#fff",
        borderColor: "#e5e7eb",
        textStyle: { color: "#374151", fontSize: 12 },
        formatter: (p: { name: string; value: number; percent: number }) =>
          `${p.name}<br/>${fmtMoney(p.value)} (${p.percent}%)`,
      },
      series: [
        {
          type: "pie",
          radius: ["54%", "80%"],
          center: ["50%", "50%"],
          avoidLabelOverlap: false,
          itemStyle: { borderRadius: 6, borderColor: "#fff", borderWidth: 2 },
          label: { show: false, position: "center" },
          emphasis: {
            scale: true,
            scaleSize: 6,
            label: {
              show: true,
              position: "center",
              formatter: "{b}\n{d}%",
              fontSize: 14,
              fontWeight: "bold",
              color: "#111827",
              lineHeight: 20,
            },
          },
          data: slices.map((c) => ({
            name: c.label,
            value: c.gmv,
            itemStyle: { color: channelHex(c.key) },
          })),
        },
      ],
    }),
    [slices],
  );
  return (
    <div>
      <EChart option={option} height={180} />
      {/* 常驻图例：色点 + 名称 + 占比%（始终可见，不必 hover） */}
      <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1.5">
        {slices.map((c) => (
          <div key={c.key} className="flex items-center gap-1.5 text-xs">
            <span
              className="size-2.5 shrink-0 rounded-sm"
              style={{ backgroundColor: channelHex(c.key) }}
            />
            <span className="min-w-0 flex-1 truncate text-foreground-secondary">{c.label}</span>
            <span className="tabnum shrink-0 font-semibold text-foreground">{c.pct}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// 商品详情弹窗（轻量，照 forkStoreClaw Dialog：backdrop + Esc + body 锁滚 + fade-up）。
// 点击爆款行打开：大图 + 完整商品名 + 各 SKU 销量占比条 + 渠道 4 分饼（懒加载 /board/product-detail）。
function ProductDetailDialog({
  product,
  query,
  onClose,
}: {
  product: TopSku;
  query: BoardQuery;
  onClose: () => void;
}) {
  const [state, setState] = useState<{ loading: boolean; data: ProductDetail | null; error: string | null }>(
    { loading: true, data: null, error: null },
  );
  const [imgFailed, setImgFailed] = useState(false);
  const [granularity, setGranularity] = useState<"coarse" | "fine">("coarse"); // 渠道粒度：粗 4 / 细 6
  const [skuOpen, setSkuOpen] = useState(false); // SKU 明细默认收起，点击展开
  const { open: openImage } = useImageViewer(); // 主图灯箱走全局查看器（高清 origin）

  // body 锁滚（fork Dialog 行为）
  useEffect(() => {
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = "";
    };
  }, []);

  // Esc 关弹窗。（大图灯箱走全局 ImageViewer，自己处理 Esc/遮罩关闭；此处只管本弹窗。）
  useEffect(() => {
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onEsc);
    return () => window.removeEventListener("keydown", onEsc);
  }, [onClose]);

  // 懒加载：打开时拉详情（渠道 + 各 SKU）
  useEffect(() => {
    if (!product.product_id) return;
    let alive = true;
    setState({ loading: true, data: null, error: null });
    api
      .productDetail(product.product_id, query)
      .then((d) => alive && setState({ loading: false, data: d, error: null }))
      .catch((e) => alive && setState({ loading: false, data: null, error: String(e) }));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [product.product_id, JSON.stringify(query)]);

  const code = styleCodeLabel(product);
  const skus = state.data?.skus ?? [];
  const skuTotal = skus.reduce((s, k) => s + (k.units_sold || 0), 0);
  const channels = state.data?.channels;
  const showImg = product.image_url && !imgFailed;
  const hasFine = !!(channels?.available && channels.fine && channels.fine.length > 0);
  const donutData = granularity === "fine" && channels?.fine ? channels.fine : channels?.channels ?? [];
  const PREVIEW = 3; // 收起态预览前 3 个规格
  const skusShown = skuOpen ? skus : skus.slice(0, PREVIEW);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />
      <div
        role="dialog"
        aria-modal="true"
        className="relative flex max-h-[88vh] w-full max-w-md animate-fade-up flex-col rounded-2xl border border-border-shallow bg-background shadow-lg"
      >
        {/* 头部：大图（可点开看原图）+ 完整名 + 款号/规格 + 关闭 */}
        <div className="flex items-start gap-3.5 border-b border-border-shallow p-4">
          {showImg ? (
            <button
              type="button"
              onClick={() => openImage(hiResUrl(product.image_url), productLabel(product))}
              title="查看大图"
              aria-label="查看大图"
              className="group relative size-20 shrink-0 overflow-hidden rounded-xl ring-1 ring-border-shallow focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <img
                src={product.image_url}
                alt=""
                onError={() => setImgFailed(true)}
                className="size-full object-cover transition-transform group-hover:scale-105"
              />
              <span className="absolute inset-0 flex items-center justify-center bg-black/0 text-transparent transition-colors group-hover:bg-black/35 group-hover:text-white">
                <ZoomIn className="size-5" />
              </span>
            </button>
          ) : (
            <div className="flex size-20 shrink-0 items-center justify-center rounded-xl bg-fill-default text-foreground-secondary">
              <ShoppingBag size={26} />
            </div>
          )}
          <div className="min-w-0 flex-1">
            {/* 完整商品名（不截断） */}
            <div className="text-sm font-semibold leading-snug text-foreground">{productLabel(product)}</div>
            <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-foreground-secondary">
              {code && <span>{code}</span>}
              <span className="tabnum">{fmtInt(product.units_sold)} 件</span>
              <span className="tabnum">{fmtMoney(product.gmv)}</span>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭"
            className="-mr-1 -mt-1 shrink-0 rounded-lg p-1.5 text-foreground-tertiary transition-colors hover:bg-fill hover:text-foreground"
          >
            <X className="size-5" />
          </button>
        </div>

        {/* 内容：渠道饼（提到顶部，可切粗/细）→ 各 SKU 明细（默认收起，点击展开） */}
        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-4">
          {state.loading ? (
            <div className="flex items-center justify-center gap-2 py-10 text-sm text-foreground-secondary">
              <span className="size-3.5 animate-spin rounded-full border-2 border-border border-t-foreground" />
              加载中…
            </div>
          ) : state.error ? (
            <div className="py-8 text-center text-sm text-foreground-secondary">详情加载失败</div>
          ) : (
            <>
              {/* 渠道构成（顶部）+ 粗/细 切换 */}
              <div>
                <div className="mb-1 flex items-center justify-between gap-2">
                  <div className="text-xs font-medium text-foreground-secondary">渠道构成（按 GMV）</div>
                  {hasFine && (
                    <TabPills
                      tabs={[
                        { id: "coarse", label: "粗分" },
                        { id: "fine", label: "细分" },
                      ]}
                      value={granularity}
                      onChange={setGranularity}
                    />
                  )}
                </div>
                {channels?.available ? (
                  <ChannelDonut channels={donutData} />
                ) : (
                  <div className="py-6 text-center text-xs text-foreground-tertiary">该商品暂无渠道数据</div>
                )}
              </div>

              {/* 各 SKU 销量占比（默认收起；点击展开全部） */}
              <div>
                <button
                  type="button"
                  onClick={() => setSkuOpen((v) => !v)}
                  aria-expanded={skuOpen}
                  className="flex w-full items-center justify-between gap-2 rounded-md py-1 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <span className="text-xs font-medium text-foreground-secondary">
                    各 SKU 销量占比
                    {skus.length > 0 && (
                      <span className="ml-1.5 text-foreground-tertiary">{skus.length} 个规格</span>
                    )}
                  </span>
                  {skus.length > PREVIEW && (
                    <span className="flex shrink-0 items-center gap-0.5 text-xs text-foreground-tertiary">
                      {skuOpen ? "收起" : "展开全部"}
                      <ChevronDown
                        className={"size-3.5 transition-transform " + (skuOpen ? "rotate-180" : "")}
                      />
                    </span>
                  )}
                </button>
                {skus.length === 0 ? (
                  <div className="py-3 text-center text-xs text-foreground-tertiary">无 SKU 明细</div>
                ) : (
                  <div className="mt-1.5 space-y-1.5">
                    {skusShown.map((k, i) => {
                      const pct = skuTotal ? (k.units_sold / skuTotal) * 100 : 0;
                      return (
                        <div key={(k.sku_id || "") + i} className="relative rounded-md">
                          <div className="absolute inset-0 overflow-hidden rounded-md" aria-hidden>
                            <div className="absolute inset-y-0 left-0 bg-fill-deep" style={{ width: `${pct}%` }} />
                          </div>
                          <div className="relative flex items-center justify-between gap-2 px-2 py-1.5">
                            <span className="min-w-0 flex-1 truncate">
                              {/* 主：款号(seller_sku) 与爆款卡主行一致；副：规格名(sku_name)灰字补充 */}
                              <span className="text-sm text-foreground">
                                {k.seller_sku || k.sku_name || k.sku_id || "—"}
                              </span>
                              {k.seller_sku && k.sku_name && (
                                <span className="ml-2 text-xs text-foreground-tertiary">{k.sku_name}</span>
                              )}
                            </span>
                            <span className="flex shrink-0 items-baseline gap-2.5">
                              <span className="tabnum text-sm text-foreground">{fmtInt(k.units_sold)} 件</span>
                              <span className="tabnum w-9 text-right text-xs text-foreground-tertiary">
                                {pct.toFixed(0)}%
                              </span>
                            </span>
                          </div>
                        </div>
                      );
                    })}
                    {!skuOpen && skus.length > PREVIEW && (
                      <button
                        type="button"
                        onClick={() => setSkuOpen(true)}
                        className="w-full rounded-md py-1.5 text-center text-xs text-foreground-secondary transition-colors hover:bg-fill-shallow"
                      >
                        还有 {skus.length - PREVIEW} 个规格，点击展开
                      </button>
                    )}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function HotProducts({
  data,
  loading,
  query,
}: {
  data: BoardData | null;
  loading: boolean;
  query: BoardQuery;
}) {
  const [rankBy, setRankBy] = useState<RankBy>("sales");
  const [openProduct, setOpenProduct] = useState<TopSku | null>(null); // 详情弹窗当前商品
  const [expanded, setExpanded] = useState(false); // 默认折叠：先显 5 个，点「更多」展开后 5 个
  const raw = data?.top.items ?? [];

  const items = useMemo(() => {
    const sorted = [...raw].sort((a, b) =>
      rankBy === "gmv" ? (b.gmv ?? 0) - (a.gmv ?? 0) : b.units_sold - a.units_sold,
    );
    return sorted.slice(0, 10);
  }, [raw, rankBy]);

  // 切换排序时重置折叠，避免「按 GMV 已展开 → 切销量仍展开」的错位观感。
  const visible = expanded ? items : items.slice(0, 5);

  // fork 排序含「按利润」；我方无利润数据源 → 仅保留 销量/GMV。
  const rankOptions: { id: RankBy; label: string }[] = [
    { id: "sales", label: "按销量" },
    { id: "gmv", label: "按 GMV" },
  ];

  return (
    <BoardCard>
      <CardHead
        title={
          <span className="inline-flex items-center gap-1.5">
            爆款商品
            <InfoTooltip
              align="start"
              content="按商品售价统计、已排除取消单，与上方「GMV」总额口径不同（GMV 含取消、按商品小计）；此处只展示销量最高的部分商品，非全店汇总。"
            >
              <Info className="h-3.5 w-3.5 text-foreground-secondary" />
            </InfoTooltip>
          </span>
        }
        right={
          <TabPills
            tabs={rankOptions}
            value={rankBy}
            onChange={(v) => {
              setRankBy(v);
              setExpanded(false);
            }}
          />
        }
      />

      {loading ? (
        <ChartEmpty loading empty="" height={320} />
      ) : !items.length ? (
        <ChartEmpty loading={false} empty="该时段暂无销量数据" height={320} />
      ) : (
        <div className="space-y-1">
          {visible.map((p, index) => {
            const rowKey = (p.product_id || p.sku_id || "") + index;
            const code = styleCodeLabel(p);
            const canOpen = !!p.product_id;
            return (
              <button
                type="button"
                key={rowKey}
                onClick={() => canOpen && setOpenProduct(p)}
                disabled={!canOpen}
                title={canOpen ? "查看商品详情" : undefined}
                className={
                  "flex w-full items-center gap-3 rounded-lg p-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [@media(pointer:coarse)]:p-2.5 " +
                  (canOpen ? "cursor-pointer hover:bg-fill-shallow" : "cursor-default")
                }
              >
                <ProductThumb src={p.image_url} rank={index + 1} />
                <div className="min-w-0 flex-1">
                  <div className="line-clamp-2 text-sm font-medium leading-snug text-foreground">
                    {productLabel(p)}
                  </div>
                  {code && (
                    <div className="mt-0.5 truncate text-xs text-foreground-secondary">{code}</div>
                  )}
                </div>
                <div className="shrink-0 text-right">
                  <div className="tabnum text-sm font-semibold text-foreground">
                    {rankBy === "gmv" ? fmtMoney(p.gmv) : `${fmtInt(p.units_sold)} 件`}
                  </div>
                  <div className="tabnum text-xs text-foreground-secondary">
                    {rankBy === "gmv" ? `${fmtInt(p.units_sold)} 件` : fmtMoney(p.gmv)}
                  </div>
                </div>
                {canOpen && (
                  <ChevronRight className="size-4 shrink-0 text-foreground-tertiary" />
                )}
              </button>
            );
          })}
          {items.length > 5 && (
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              className="mt-1 flex w-full items-center justify-center gap-1 rounded-lg py-2 text-sm font-medium text-foreground-secondary transition-colors hover:bg-fill-shallow hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [@media(pointer:coarse)]:py-2.5"
            >
              {expanded ? "收起" : `查看更多 ${items.length - 5} 个`}
              <ChevronDown
                className={"size-4 transition-transform " + (expanded ? "rotate-180" : "")}
              />
            </button>
          )}
        </div>
      )}

      {openProduct && (
        <ProductDetailDialog
          product={openProduct}
          query={query}
          onClose={() => setOpenProduct(null)}
        />
      )}
    </BoardCard>
  );
}

/* ── 近 N 天新品（懒加载：每个新品日销量曲线 + 单日破阈爆单提醒）────────────────
   窗口天数由端点 window.lookback_days 下发（settings.new_product_lookback_days，默认 60）。
   口径见 docs/business-rules §4.4：近 N 天上线在售商品、付款口径销量、爆单阈值与飞书告警同源
   （settings.hotsell_daily_units_threshold=50）。界面爆单徽章由端点确定性计算，不依赖告警 timer。 */

// 短日期 MM-DD（series.date 为 ISO yyyy-mm-dd）。
function mmdd(iso: string): string {
  const p = iso.split("-");
  return p.length === 3 ? `${p[1]}-${p[2]}` : iso;
}

// 迷你销量曲线（行内 sparkline，无轴）。爆单品用警示红、峰值打点；常态用主墨绿。
function NewProductSparkline({ p }: { p: NewProduct }) {
  const t = useChartTokens();
  const option = useMemo(() => {
    const units = p.series.map((s) => s.units);
    const color = p.burst ? t.negative : t.primary;
    const peakIdx = p.peak_date ? p.series.findIndex((s) => s.date === p.peak_date) : -1;
    return {
      animation: false,
      grid: { left: 2, right: 2, top: 6, bottom: 4 },
      xAxis: { type: "category", show: false, data: p.series.map((s) => s.date) },
      yAxis: { type: "value", show: false, min: 0 },
      series: [
        {
          type: "line",
          data: units,
          smooth: true,
          symbol: "none",
          lineStyle: { width: 2, color },
          areaStyle: { color, opacity: 0.1 },
          markPoint:
            p.burst && peakIdx >= 0
              ? {
                  symbol: "circle",
                  symbolSize: 7,
                  itemStyle: { color: t.negative },
                  label: { show: false },
                  data: [{ coord: [peakIdx, p.peak_units] }],
                }
              : undefined,
        },
      ],
    };
  }, [p, t]);
  return <EChart option={option} height={44} />;
}

// 展开后的完整曲线：带轴 + 爆单阈值虚线 + 峰值打点 + tooltip。
function NewProductChart({ p, threshold }: { p: NewProduct; threshold: number }) {
  const t = useChartTokens();
  const option = useMemo(() => {
    const peakIdx = p.peak_date ? p.series.findIndex((s) => s.date === p.peak_date) : -1;
    const color = p.burst ? t.negative : t.primary;
    return {
      animation: false,
      grid: { left: 36, right: 14, top: 24, bottom: 24 },
      tooltip: {
        trigger: "axis",
        backgroundColor: t.card,
        borderColor: t.grid,
        textStyle: { color: t.text, fontSize: 12 },
        formatter: (ps: { dataIndex: number; value: number }[]) => {
          const i = ps[0]?.dataIndex ?? 0;
          return `${mmdd(p.series[i].date)}　${p.series[i].units} 件`;
        },
      },
      xAxis: {
        type: "category",
        data: p.series.map((s) => mmdd(s.date)),
        axisLine: { lineStyle: { color: t.grid } },
        axisLabel: { color: t.sub, fontSize: 10, hideOverlap: true },
        axisTick: { show: false },
      },
      yAxis: {
        type: "value",
        min: 0,
        splitLine: { lineStyle: { color: t.grid, type: "dashed" } },
        axisLabel: { color: t.sub, fontSize: 10 },
      },
      series: [
        {
          type: "line",
          data: p.series.map((s) => s.units),
          smooth: true,
          symbol: "circle",
          symbolSize: 4,
          lineStyle: { width: 2, color },
          itemStyle: { color },
          areaStyle: { color, opacity: 0.08 },
          markLine: {
            silent: true,
            symbol: "none",
            lineStyle: { color: t.warning, type: "dashed", width: 1 },
            label: {
              show: true,
              position: "insideEndTop",
              formatter: `爆单线 ${threshold}`,
              color: t.warning,
              fontSize: 10,
            },
            data: [{ yAxis: threshold }],
          },
          markPoint:
            peakIdx >= 0 && p.burst
              ? {
                  symbol: "pin",
                  symbolSize: 34,
                  itemStyle: { color: t.negative },
                  label: { color: "#fff", fontSize: 10, formatter: "{c}" },
                  data: [{ coord: [peakIdx, p.peak_units], value: p.peak_units }],
                }
              : undefined,
        },
      ],
    };
  }, [p, t, threshold]);
  return <EChart option={option} height={180} />;
}

// 爆单徽章：图标 + 文案（不靠纯色传达，色盲友好）。
function BurstBadge({ peak }: { peak: number }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-negative/10 px-2 py-0.5 text-xs font-medium text-negative">
      <Flame className="size-3.5" aria-hidden />
      爆单 · 峰值 {peak}
    </span>
  );
}

function NewProductRow({
  p,
  rank,
  threshold,
  lookbackDays,
}: {
  p: NewProduct;
  rank: number;
  threshold: number;
  lookbackDays: number;
}) {
  const [open, setOpen] = useState(false);
  const code =
    p.sku_count > 1 ? `${p.sku_count} 个规格` : p.seller_sku ? `款号 ${p.seller_sku}` : null;
  return (
    <div className="rounded-lg border border-border-shallow">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-3 rounded-lg p-2 text-left transition-colors hover:bg-fill-shallow focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [@media(pointer:coarse)]:p-2.5"
      >
        <ProductThumb src={p.image_url ?? undefined} rank={rank} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <div className="line-clamp-1 text-sm font-medium leading-snug text-foreground">
              {p.title}
            </div>
            {p.burst && <BurstBadge peak={p.peak_units} />}
          </div>
          <div className="mt-0.5 truncate text-xs text-foreground-secondary">
            上线 {p.days_online} 天{code ? ` · ${code}` : ""}
          </div>
        </div>
        {/* 桌面显行内 sparkline；窄屏（<sm）隐藏让位数字，避免压字 */}
        <div className="hidden w-[120px] shrink-0 sm:block">
          <NewProductSparkline p={p} />
        </div>
        <div className="shrink-0 text-right">
          <div className="tabnum text-sm font-semibold text-foreground">{fmtInt(p.total_units)} 件</div>
          <div className="tabnum text-xs text-foreground-secondary">{fmtMoney(p.total_gmv)}</div>
        </div>
        <ChevronRight
          className={
            "size-4 shrink-0 text-foreground-tertiary transition-transform " +
            (open ? "rotate-90" : "")
          }
          aria-hidden
        />
      </button>
      {open && (
        <div className="border-t border-border-shallow p-3">
          <NewProductChart p={p} threshold={threshold} />
          <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-foreground-secondary">
            {p.source_create_time && <span>上线日 {mmdd(p.source_create_time.slice(0, 10))}</span>}
            {p.peak_date && <span>峰值日 {mmdd(p.peak_date)} · {p.peak_units} 件</span>}
            <span>近 {lookbackDays} 天累计 {fmtInt(p.total_units)} 件</span>
          </div>
        </div>
      )}
    </div>
  );
}

function NewProducts({ query, reloadKey }: { query: BoardQuery; reloadKey: number }) {
  const [data, setData] = useState<NewProduct[] | null>(null);
  const [threshold, setThreshold] = useState(50);
  const [lookbackDays, setLookbackDays] = useState(60); // 新品窗口天数，由端点 window.lookback_days 下发
  const [loading, setLoading] = useState(true);
  const [available, setAvailable] = useState(true);

  useEffect(() => {
    const ctrl = new AbortController();
    setLoading(true);
    api
      .newProducts(query)
      .then((res) => {
        if (ctrl.signal.aborted) return;
        setData(res.items);
        setThreshold(res.threshold);
        setLookbackDays(res.window.lookback_days);
        setAvailable(res.available);
      })
      .catch(() => {
        if (ctrl.signal.aborted) return;
        setData([]);
        setAvailable(false);
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setLoading(false);
      });
    return () => ctrl.abort();
    // query 各字段 + reloadKey 变化时重取
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query.scope, query.platform, query.country, reloadKey]);

  const burstCount = useMemo(() => (data ?? []).filter((p) => p.burst).length, [data]);

  return (
    <BoardCard>
      <CardHead
        title={
          <span className="inline-flex items-center gap-2">
            <Sparkles className="size-4 text-positive" aria-hidden />
            近 {lookbackDays} 天新品
          </span>
        }
        right={
          burstCount > 0 ? (
            <span className="inline-flex items-center gap-1 rounded-full bg-negative/10 px-2 py-0.5 text-xs font-medium text-negative">
              <Flame className="size-3.5" aria-hidden />
              {burstCount} 款爆单
            </span>
          ) : (
            <span className="text-xs text-foreground-tertiary">单日破 {threshold} 件即提醒</span>
          )
        }
      />
      {loading ? (
        <div className="space-y-2" aria-hidden>
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-[60px] animate-pulse rounded-lg bg-fill-shallow" />
          ))}
        </div>
      ) : !available ? (
        <ChartEmpty loading={false} empty="新品数据暂不可用" height={120} />
      ) : !data || !data.length ? (
        <div className="flex flex-col items-center gap-1 py-10 text-center">
          <Sparkles className="size-6 text-foreground-tertiary" aria-hidden />
          <div className="text-sm text-foreground-secondary">近 {lookbackDays} 天暂无起量的新上线款号</div>
          <div className="text-xs text-foreground-tertiary">
            新款上线并产生销量后，会在此追踪曲线并提醒单日爆单
          </div>
        </div>
      ) : (
        <div className="space-y-2">
          {data.map((p, i) => (
            <NewProductRow
              key={p.product_id}
              p={p}
              rank={i + 1}
              threshold={threshold}
              lookbackDays={lookbackDays}
            />
          ))}
        </div>
      )}
    </BoardCard>
  );
}

/* ── 库存健康（照 fork InventoryHealth：汇总=仪表盘+分布 / 明细=搜索排序表）──── */

type InventoryView = "summary" | "details";

const LOW_BADGE: Record<string, { label: string; cls: string }> = {
  stockout: { label: "缺货", cls: "bg-negative/15 text-negative" },
  critical: { label: "告急", cls: "bg-warning/15 text-warning" },
  warning: { label: "偏低", cls: "bg-caution/15 text-caution" },
  ok: { label: "充足", cls: "bg-positive/15 text-positive" },
  idle: { label: "无销量", cls: "bg-fill-default text-foreground-secondary" },
};

type SortField = "days_of_cover" | "available_stock" | "name";

function InventoryHealth({ data, loading }: { data: BoardData | null; loading: boolean }) {
  const t = useChartTokens();
  const [view, setView] = useState<InventoryView>("summary");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [sortField, setSortField] = useState<SortField>("days_of_cover");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [page, setPage] = useState(1);
  const PAGE_SIZE = 5;

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
  const healthyCount = Math.max(skuCount - lowCount, 0);
  // 无销量（近期不动销）= 总在售 − 有销量参与判断的（buckets.total 是有销量且落风险桶的，
  // 但充足的有销量 SKU 不在 buckets 里）→ idle 只能粗估，图例里单独按「总 − 健康 − 风险」兜底。
  const idleCount = Math.max(skuCount - healthyCount - atRisk, 0);
  const criticalDays = data?.low?.critical_days ?? 3;
  const warningDays = data?.low?.warning_days ?? 7;
  const velocityDays = data?.low?.velocity_window_days ?? 7;

  // 健康度分档变色：≥85 绿 / 60–85 黄 / <60 红（阈值为默认值，可按客户观感调）。
  const HEALTH_GOOD = 85;
  const HEALTH_WARN = 60;
  const healthColor =
    healthPct == null
      ? t.positive
      : healthPct >= HEALTH_GOOD
        ? t.positive
        : healthPct >= HEALTH_WARN
          ? t.warning
          : t.negative;

  const gaugeOption = useMemo(
    () => ({
      series: [
        {
          type: "gauge",
          startAngle: 200,
          endAngle: -20,
          min: 0,
          max: 100,
          progress: { show: true, width: 18, itemStyle: { color: healthColor } },
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
            color: healthColor,
          },
          data: [{ value: healthPct ?? 0, name: "库存健康度" }],
        },
      ],
    }),
    [healthPct, healthColor, t],
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
          label: { show: false, position: "center" },
          emphasis: {
            scale: true,
            scaleSize: 6,
            label: { show: true, position: "center", fontSize: 13, fontWeight: "bold", color: t.text },
          },
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
          (it.sku_name || "").toLowerCase().includes(q) ||
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

  // 前端分页：搜索/筛选/排序变化时回到第 1 页，避免停在越界的空页。
  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  useEffect(() => {
    setPage(1);
  }, [query, status, sortField, sortDir]);
  const safePage = Math.min(page, pageCount);
  const paged = filtered.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

  return (
    <BoardCard>
      <CardHead
        title={
          <span className="inline-flex items-center gap-1.5">
            库存健康
            <InfoTooltip
              align="start"
              content={`健康度 = 不缺货的在售商品占比。对近期有销量的商品，按「可售天数 = 当前库存 ÷ 日均销量」判断：断货（库存 0）、告急（可售 < ${criticalDays} 天）、偏低（可售 < ${warningDays} 天）算风险，其余算健康。近期无销量的商品不参与风险判断。日均销量 = 近 ${velocityDays} 天已付款销量 ÷ ${velocityDays}。`}
            >
              <Info className="h-3.5 w-3.5 text-foreground-tertiary" />
            </InfoTooltip>
            <AskAiLink question={`看板「库存健康」里的「日均销量/销售速度」是用多大的时间窗口算的？「可售天数」和「断货/告急/偏低」的口径分别是什么？`} />
          </span>
        }
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
          <>
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
            {/* 口径图例：把仪表盘的百分比拆成健康/风险/无销量的绝对数，让老板一眼看懂构成。 */}
            {skuCount > 0 && (
              <div className="mt-1 flex flex-wrap items-center justify-center gap-x-4 gap-y-1 text-xs text-foreground-secondary">
                <span className="inline-flex items-center gap-1.5">
                  <span className="size-2 rounded-full" style={{ backgroundColor: t.positive }} />
                  健康 {fmtInt(healthyCount)}
                </span>
                <span className="inline-flex items-center gap-1.5">
                  <span className="size-2 rounded-full" style={{ backgroundColor: t.negative }} />
                  风险 {fmtInt(atRisk)}
                  <span className="text-foreground-tertiary">
                    （断货 {fmtInt(stockout)}·告急 {fmtInt(critical)}·偏低 {fmtInt(warning)}）
                  </span>
                </span>
                <span className="inline-flex items-center gap-1.5">
                  <span className="size-2 rounded-full bg-fill-default" />
                  无销量 {fmtInt(idleCount)}
                </span>
              </div>
            )}
          </>
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
              <option value="ok">充足</option>
              <option value="idle">无销量</option>
            </select>
          </div>

          {filtered.length === 0 ? (
            <div className="rounded-lg border border-border-shallow px-3 py-8 text-center text-sm text-foreground-secondary">
              暂无匹配的 SKU
            </div>
          ) : (
            <>
              {/* PC 端：可排序表（表头点击排序）。窄屏改卡片式，见下方 md:hidden。 */}
              <div className="hidden overflow-hidden rounded-lg border border-border-shallow md:block">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="bg-fill-shallow">
                      <SortableTh label="商品 / SKU" active={sortField === "name"} dir={sortDir} onClick={() => handleSort("name")} />
                      <SortableTh label="库存" numeric active={sortField === "available_stock"} dir={sortDir} onClick={() => handleSort("available_stock")} />
                      <th className="px-3 py-2 text-right font-medium text-foreground-secondary">日均销量</th>
                      <SortableTh label="可售天数" numeric active={sortField === "days_of_cover"} dir={sortDir} onClick={() => handleSort("days_of_cover")} />
                      <th className="px-3 py-2 text-center font-medium text-foreground-secondary">状态</th>
                    </tr>
                  </thead>
                  <tbody>
                    {paged.map((it) => {
                      const badge = LOW_BADGE[it.bucket] || { label: it.bucket, cls: "bg-fill-default" };
                      return (
                        <tr
                          key={it.sku_id}
                          className="border-t border-border-shallow transition-colors hover:bg-fill-shallow"
                        >
                          <td className="px-3 py-2">
                            <div className="flex items-center gap-2.5">
                              <StockThumb src={it.image_url} />
                              <div className="min-w-0">
                                <TruncatedName
                                  text={it.product_name || it.sku_id}
                                  className="font-medium leading-snug text-foreground"
                                />
                                <div className="truncate text-xs text-foreground-secondary" title={it.sku_name || it.sku_id}>
                                  {it.sku_name || it.sku_id}
                                </div>
                              </div>
                            </div>
                          </td>
                          <td className="px-3 py-2 text-right text-foreground">{fmtInt(it.available_stock)}</td>
                          <td className="px-3 py-2 text-right text-foreground-secondary">
                            {Number(it.daily_velocity).toFixed(1)}
                          </td>
                          <td className="px-3 py-2 text-right text-foreground">{coverLabel(it.days_of_cover)}</td>
                          <td className="px-3 py-2 text-center">
                            <span className={"inline-flex rounded px-2 py-0.5 text-xs font-medium " + badge.cls}>
                              {badge.label}
                            </span>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              {/* 移动端：卡片式列表（不横滚），小图 + 名 + 指标堆叠。 */}
              <div className="space-y-2 md:hidden">
                {paged.map((it) => {
                  const badge = LOW_BADGE[it.bucket] || { label: it.bucket, cls: "bg-fill-default" };
                  return (
                    <div
                      key={it.sku_id}
                      className="flex gap-3 rounded-lg border border-border-shallow p-3"
                    >
                      <StockThumb src={it.image_url} />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-start justify-between gap-2">
                          <TruncatedName
                            text={it.product_name || it.sku_id}
                            className="text-sm font-medium leading-snug text-foreground"
                          />
                          <span
                            className={"shrink-0 inline-flex rounded px-2 py-0.5 text-xs font-medium " + badge.cls}
                          >
                            {badge.label}
                          </span>
                        </div>
                        <div className="mt-0.5 truncate text-xs text-foreground-secondary" title={it.sku_name || it.sku_id}>
                          {it.sku_name || it.sku_id}
                        </div>
                        <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-foreground-secondary">
                          <span>库存 <span className="tabnum text-foreground">{fmtInt(it.available_stock)}</span></span>
                          <span>日均 <span className="tabnum text-foreground">{Number(it.daily_velocity).toFixed(1)}</span></span>
                          <span>可售 <span className="tabnum text-foreground">{coverLabel(it.days_of_cover)}</span> 天</span>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* 分页：> 一页才显示 */}
              {pageCount > 1 && (
                <div className="mt-3 flex items-center justify-between gap-3 text-sm text-foreground-secondary">
                  <span>共 {fmtInt(filtered.length)} 条</span>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => setPage((p) => Math.max(1, p - 1))}
                      disabled={safePage <= 1}
                      aria-label="上一页"
                      className="inline-flex h-8 items-center gap-1 rounded-lg border border-border px-2.5 text-foreground transition-colors hover:bg-fill-shallow disabled:cursor-not-allowed disabled:opacity-40 [@media(pointer:coarse)]:h-11"
                    >
                      <ChevronLeft className="size-4" />
                      上一页
                    </button>
                    <span className="tabnum">
                      {safePage} / {pageCount}
                    </span>
                    <button
                      type="button"
                      onClick={() => setPage((p) => Math.min(pageCount, p + 1))}
                      disabled={safePage >= pageCount}
                      aria-label="下一页"
                      className="inline-flex h-8 items-center gap-1 rounded-lg border border-border px-2.5 text-foreground transition-colors hover:bg-fill-shallow disabled:cursor-not-allowed disabled:opacity-40 [@media(pointer:coarse)]:h-11"
                    >
                      下一页
                      <ChevronRight className="size-4" />
                    </button>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </BoardCard>
  );

  function skuLabel(i: LowStockItem) {
    return i.product_name || i.sku_id;
  }
}

// 可售天数展示：null（近期无销量）显「—」，否则 1 位小数。
function coverLabel(days: number | null | undefined): string {
  if (days == null) return "—";
  return Number(days).toFixed(1);
}

// 商品名截断：默认只显前 max 个字符 + 省略号；超长时用 InfoTooltip（hover / 触屏点击，
// 气泡默认在文字上方）预览全称。不超长则纯文本、无浮层开销。
function TruncatedName({
  text,
  max = 10,
  className,
}: {
  text: string;
  max?: number;
  className?: string;
}) {
  const over = text.length > max;
  if (!over) return <span className={className}>{text}</span>;
  const short = text.slice(0, max) + "…";
  return (
    <InfoTooltip content={text} side="top" align="start" triggerClassName={className}>
      <span className="cursor-default">{short}</span>
    </InfoTooltip>
  );
}

// 库存明细行小图：缺图 / 加载失败 → ShoppingBag 占位（自管失败态）。
function StockThumb({ src }: { src?: string | null }) {
  const [failed, setFailed] = useState(false);
  const { open } = useImageViewer();
  const ok = src && !failed;
  return ok ? (
    <img
      src={src}
      alt=""
      loading="lazy"
      onError={() => setFailed(true)}
      onClick={() => open(hiResUrl(src), "商品大图")}
      className="size-10 shrink-0 cursor-zoom-in rounded-lg object-cover ring-1 ring-border-shallow transition-opacity hover:opacity-80"
    />
  ) : (
    <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-fill-default text-foreground-tertiary">
      <ShoppingBag size={16} />
    </div>
  );
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

/* ── 待发货履约（满宽单卡）：真实履约数据（统计分桶 + 明细表 + 分页）。
      退款/取消已抽成独立 RefundSection 上移至区1（随日期筛选，口径归位）。────────── */

const PEND_BADGE: Record<string, { label: string; cls: string }> = {
  overdue: { label: "超时", cls: "bg-negative/15 text-negative" },
  critical: { label: "临界", cls: "bg-warning/15 text-warning" },
  normal: { label: "正常", cls: "bg-positive/15 text-positive" },
  unknown: { label: "未知", cls: "bg-fill-default text-foreground-secondary" },
};

const PAGE_SIZES: { value: number; label: string }[] = [
  { value: 5, label: "5" },
  { value: 10, label: "10" },
  { value: 20, label: "20" },
  { value: 0, label: "全部" },
];

// 待发货表分页条：左=范围+每页条数，右=翻页+页码。客户端分页，遵循 DESIGN.md
// （bg-fill/bg-accent 填充区分、tabnum、触控 44px、窄屏页码收敛）。
function FulfillmentPager({
  total,
  rangeStart,
  rangeEnd,
  page,
  pageCount,
  pageSize,
  onPageSize,
  onPage,
}: {
  total: number;
  rangeStart: number;
  rangeEnd: number;
  page: number;
  pageCount: number;
  pageSize: number;
  onPageSize: (n: number) => void;
  onPage: (updater: (p: number) => number) => void;
}) {
  const btn =
    "inline-flex h-8 min-w-8 items-center justify-center gap-1 rounded-lg px-2 text-sm text-foreground-secondary transition-colors hover:bg-fill hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-40 [@media(pointer:coarse)]:h-11 [@media(pointer:coarse)]:min-w-11";

  // 页码窗口：当前页 ±1，首尾恒显，省略段用 …（多页时不溢出窄屏）。
  const pages: (number | "…")[] = [];
  if (pageCount <= 7) {
    for (let i = 1; i <= pageCount; i++) pages.push(i);
  } else {
    const near = [1, 2, page - 1, page, page + 1, pageCount - 1, pageCount].filter(
      (n) => n >= 1 && n <= pageCount,
    );
    const uniq = [...new Set(near)].sort((a, b) => a - b);
    let prev = 0;
    for (const n of uniq) {
      if (n - prev > 1) pages.push("…");
      pages.push(n);
      prev = n;
    }
  }

  return (
    <div className="mt-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      {/* 左：范围 + 每页条数 */}
      <div className="flex items-center gap-3 text-xs text-foreground-secondary">
        <span className="tabnum">
          第 <span className="text-foreground">{rangeStart}</span>–
          <span className="text-foreground">{rangeEnd}</span> 条 · 共{" "}
          <span className="tabnum text-foreground">{total}</span> 条
        </span>
        <label className="flex items-center gap-1.5">
          <span className="hidden sm:inline">每页</span>
          <select
            value={pageSize}
            onChange={(e) => onPageSize(Number(e.target.value))}
            aria-label="每页条数"
            className="h-8 rounded-lg border border-border bg-card px-2 text-xs text-foreground transition-colors hover:border-border-deep focus:border-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-ring [@media(pointer:coarse)]:h-11"
          >
            {PAGE_SIZES.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {/* 右：翻页 + 页码（pageSize=全部 或单页时隐藏） */}
      {pageSize !== 0 && pageCount > 1 && (
        <div className="flex items-center gap-1">
          <button
            type="button"
            className={btn}
            onClick={() => onPage((p) => Math.max(1, p - 1))}
            disabled={page <= 1}
            aria-label="上一页"
          >
            <ChevronLeft className="size-4" />
          </button>
          {pages.map((p, i) =>
            p === "…" ? (
              <span key={`gap-${i}`} className="px-1 text-xs text-foreground-tertiary">
                …
              </span>
            ) : (
              <button
                key={p}
                type="button"
                onClick={() => onPage(() => p)}
                aria-label={`第 ${p} 页`}
                aria-current={p === page ? "page" : undefined}
                className={
                  "inline-flex h-8 min-w-8 items-center justify-center rounded-lg px-2 text-sm tabnum transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [@media(pointer:coarse)]:h-11 [@media(pointer:coarse)]:min-w-11 " +
                  (p === page
                    ? "bg-accent font-medium text-foreground"
                    : "text-foreground-secondary hover:bg-fill hover:text-foreground")
                }
              >
                {p}
              </button>
            ),
          )}
          <button
            type="button"
            className={btn}
            onClick={() => onPage((p) => Math.min(pageCount, p + 1))}
            disabled={page >= pageCount}
            aria-label="下一页"
          >
            <ChevronRight className="size-4" />
          </button>
        </div>
      )}
    </div>
  );
}

function OrderSection({ data, loading }: { data: BoardData | null; loading: boolean }) {
  const b = data?.fulfillment.buckets;
  const items = data?.fulfillment.items ?? [];

  // 客户端分页（数据一次性到前端，切页零延迟）。0 = 全部。
  const [pageSize, setPageSize] = useState(5);
  const [page, setPage] = useState(1);
  const total = items.length;
  const pageCount = pageSize === 0 ? 1 : Math.max(1, Math.ceil(total / pageSize));
  // 页码越界纠正（换筛选/条数后 total 变小时）；数据变化时回第 1 页。
  const safePage = Math.min(page, pageCount);
  useEffect(() => {
    setPage(1);
  }, [total, pageSize]);
  const pageItems =
    pageSize === 0 ? items : items.slice((safePage - 1) * pageSize, safePage * pageSize);
  const rangeStart = total === 0 ? 0 : (safePage - 1) * (pageSize || total) + 1;
  const rangeEnd = pageSize === 0 ? total : Math.min(safePage * pageSize, total);

  return (
    <BoardCard>
      <CardHead title="待发货履约" />

      {/* 待发货：真实履约数据（统计分桶 + 明细表） */}
      <div className="mb-3 text-xs text-foreground-secondary">
        当前快照 · 不随上方日期筛选变化
        {data?.fulfillment.snapshot_at && `（同步于 ${data.fulfillment.snapshot_at}）`}
      </div>
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
              pageItems.map((r) => {
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
                    <td className="px-3 py-2 text-foreground-secondary">
                      {r.shop_name || r.shop_id || "—"}
                    </td>
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
      {/* 分页条：非加载、有数据时显示。左=范围+每页条数，右=翻页/页码。 */}
      {!loading && total > 0 && (
        <FulfillmentPager
          total={total}
          rangeStart={rangeStart}
          rangeEnd={rangeEnd}
          page={safePage}
          pageCount={pageCount}
          pageSize={pageSize}
          onPageSize={setPageSize}
          onPage={setPage}
        />
      )}
    </BoardCard>
  );
}

// 退款/取消独立卡：RefundPanel 裸内容套上 BoardCard + 标题。退款随日期筛选变化，
// 故放区1「按所选日期」（原随「待发货」挤在底部固定口径区，口径错位，本次归位）。
function RefundSection({ data, loading }: { data: BoardData | null; loading: boolean }) {
  return (
    <BoardCard id={REFUND_CARD_ID}>
      <CardHead title="退款 / 取消" />
      <RefundPanel refund={data?.refund ?? null} loading={loading} />
    </BoardCard>
  );
}

// 退款/取消面板：退款=付款后取消（真实退款），附取消构成拆分。数据基于订单状态派生
// （该店不走平台退货流程，故无 return_refund 口径；见 docs/board-data-backlog）。
function RefundPanel({ refund, loading }: { refund: RefundAnalysis | null; loading: boolean }) {
  const t = useChartTokens();
  const { open } = useImageViewer();
  const trend = refund?.trend ?? [];
  const hasTrend = trend.some((p) => p.refund_amount > 0 || p.refund_order_count > 0);
  const ratePct =
    refund?.refund_rate == null ? null : (refund.refund_rate * 100).toFixed(2) + "%";

  const trendOption = useMemo(
    () => ({
      tooltip: {
        trigger: "axis" as const,
        backgroundColor: t.card,
        borderWidth: 0,
        textStyle: { color: t.text, fontSize: 12 },
        formatter: (ps: { axisValue: string; data: number }[]) => {
          const p = ps?.[0];
          if (!p) return "";
          return `${p.axisValue}<br/>退款金额　${fmtMoney(p.data)}`;
        },
      },
      grid: { top: 16, right: 12, bottom: 24, left: 8, containLabel: true },
      xAxis: {
        type: "category" as const,
        data: trend.map((p) => p.date.slice(5)),
        axisLine: { lineStyle: { color: t.grid } },
        axisLabel: { color: t.sub, fontSize: 11 },
        axisTick: { show: false },
      },
      yAxis: {
        type: "value" as const,
        splitLine: { lineStyle: { color: t.grid, type: "dashed" as const } },
        axisLabel: {
          color: t.sub,
          fontSize: 11,
          formatter: (v: number) => (v >= 1000 ? `${Math.round(v / 1000)}k` : String(v)),
        },
      },
      series: [
        {
          name: "退款金额",
          type: "line" as const,
          smooth: true,
          symbol: "none" as const,
          data: trend.map((p) => p.refund_amount),
          lineStyle: { color: t.negative, width: 2 },
          areaStyle: { color: t.negative, opacity: 0.1 },
        },
      ],
    }),
    [trend, t],
  );

  // 取消构成拆分见下方三分堆叠条。
  const cancelledTotal = refund?.cancelled_total ?? 0;

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-xs text-foreground-secondary">
        <span>退款 = 付款后取消（买家付款后取消/拒收）</span>
        <InfoTooltip
          align="start"
          content="该店买家多以「取消」完成售后，不走平台「签收后申请退货」流程（平台退货口径为 0）。故退款按订单状态派生：退款 = 已付款后被取消的订单，金额取商品小计（与展示 GMV 同口径），退款率 = 退款额 ÷ 展示 GMV。未付款取消（买家付款前取消，含 COD 拒收——货已发出到货拒付、及下单未付）不计入退款。"
        >
          <Info className="h-3.5 w-3.5" />
        </InfoTooltip>
        <span className="text-foreground-tertiary">·</span>
        <span className="text-foreground-tertiary">随上方日期筛选变化</span>
      </div>

      <div className="mb-4 flex flex-wrap gap-x-8 gap-y-3">
        <Stat label="退款金额" value={fmtMoney(refund?.refund_amount)} tone="negative" loading={loading} />
        <Stat label="退款率" value={ratePct ?? "—"} tone="negative" loading={loading} />
        <Stat label="退款单数" value={fmtInt(refund?.refund_order_count)} loading={loading} />
        <Stat label="未付款取消" value={fmtInt(refund?.unpaid_cancelled)} loading={loading} />
      </div>

      {/* 退款金额趋势（按日） */}
      {loading || !hasTrend ? (
        <ChartEmpty
          loading={loading}
          empty="该时段无付款后取消（退款）记录"
          height={200}
        />
      ) : (
        <EChart option={trendOption} height={200} />
      )}

      {/* 取消构成三分：付款后取消(退款) / COD 拒收 / 其它未付款流失。堆叠条按占比。 */}
      {cancelledTotal > 0 && (() => {
        const paid = refund?.paid_cancelled ?? 0;
        const cod = refund?.cod_cancelled ?? 0;
        // 其它未付款流失 = 未付款取消 − COD 拒收（COD 取消实测全为未付款/拒收）
        const otherLost = Math.max(0, (refund?.unpaid_cancelled ?? 0) - cod);
        const pct = (n: number) => (cancelledTotal ? (n / cancelledTotal) * 100 : 0);
        const seg = [
          { label: "付款后取消（退款）", n: paid, cls: "bg-negative/70", dot: "bg-negative/70" },
          { label: "COD 拒收", n: cod, cls: "bg-warning/60", dot: "bg-warning/60" },
          { label: "其它未付款流失", n: otherLost, cls: "bg-fill-deep", dot: "bg-fill-deep" },
        ].filter((s) => s.n > 0);
        return (
          <div className="mt-4">
            <div className="mb-1.5 text-xs text-foreground-secondary">
              取消构成（共 {fmtInt(cancelledTotal)} 单）
            </div>
            <div className="flex h-2.5 overflow-hidden rounded-full bg-fill">
              {seg.map((s) => (
                <div key={s.label} className={s.cls} style={{ width: `${pct(s.n)}%` }} title={`${s.label} ${s.n} 单`} />
              ))}
            </div>
            <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-xs">
              {seg.map((s) => (
                <span key={s.label} className="flex items-center gap-1.5">
                  <span className={"inline-block size-2 rounded-full " + s.dot} />
                  {s.label}
                  <span className="tabnum text-foreground">{fmtInt(s.n)}</span>
                </span>
              ))}
            </div>
          </div>
        );
      })()}

      {/* 退款归因：按商品 Top（付款后取消最多的商品）。有退款单才显示。 */}
      {(refund?.top_products?.length ?? 0) > 0 && (
        <div className="mt-5">
          <div className="mb-2 text-xs font-medium text-foreground-secondary">
            退款最多的商品（付款后取消）
          </div>
          <div className="divide-y divide-border-shallow rounded-lg border border-border-shallow">
            {refund!.top_products!.map((p) => (
              <div key={p.product_id} className="flex items-center gap-3 px-3 py-2">
                {p.image_url ? (
                  <img
                    src={p.image_url}
                    alt=""
                    onClick={() => open(hiResUrl(p.image_url), p.product_name || "商品大图")}
                    className="size-9 shrink-0 cursor-zoom-in rounded-md object-cover transition-opacity hover:opacity-80"
                  />
                ) : (
                  <div className="size-9 shrink-0 rounded-md bg-fill" />
                )}
                <span className="min-w-0 flex-1 truncate text-sm text-foreground">
                  {p.product_name || p.product_id}
                </span>
                <span className="shrink-0 text-right">
                  <span className="tabnum text-sm font-semibold text-negative">{fmtInt(p.refund_order_count)}</span>
                  <span className="ml-1 text-xs text-foreground-tertiary">单</span>
                  <span className="ml-2 tabnum text-xs text-foreground-secondary">{fmtMoney(p.refund_amount)}</span>
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
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
