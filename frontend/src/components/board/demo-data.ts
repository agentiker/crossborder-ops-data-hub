// 看板「演示数据」模块（流量趋势 / 转化漏斗 / 下单趋势 / 退货分析 / 退款分析）。
//
// ⚠️ 这些是前端内置的**演示数据**，后端暂无对应数据源（见 docs/board-data-backlog.md）。
// 取自 forkStoreClaw/src/components/Dashboard/mockData.ts 的基数数组，去随机化为**确定性常量**
// （不经后端、不随「时段/范围」筛选变化，避免假装真实联动而误导）。对应区块会显示「演示数据」徽章。
//
// 落差替换：
//   ①配色：系列色照搬 fork 原值（#6366f1 靛蓝系 / #ef4444 红 / #f59e0b 橙），1:1 复刻 fork 观感；
//   ①坐标轴：轴线/网格/文字走我方 ChartTokens，与看板真实图保持同一风格（非 fork 的硬编码灰）。
import type { ChartTokens } from "@/components/EChart";

// 演示折线/柱的 X 轴标签（一周七天，与 fork 一致）。
const WEEK_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];

// ── 共用坐标轴/提示构件（系列色由各 option 自带，这里只统一轴系风格）──
const tip = (t: ChartTokens) => ({
  backgroundColor: "#fff",
  borderColor: t.grid,
  textStyle: { color: t.text },
});
const catX = (t: ChartTokens, data: string[]) => ({
  type: "category" as const,
  data,
  axisLine: { lineStyle: { color: t.grid } },
  axisLabel: { color: t.sub, rotate: data.length > 10 ? 45 : 0 },
});
const valY = (t: ChartTokens) => ({
  type: "value" as const,
  axisLine: { show: false },
  splitLine: { lineStyle: { color: t.grid } },
  axisLabel: { color: t.sub },
});
const legend = (t: ChartTokens, data: string[]) => ({
  data,
  bottom: 0,
  textStyle: { color: t.sub, fontSize: 11 },
  icon: "roundRect",
});

/* ── 流量趋势（UV/PV 折线 + 加购柱）─────────────────────────── */
export const DEMO_TRAFFIC = {
  labels: WEEK_LABELS,
  uv: [1200, 1350, 1100, 1450, 1680, 2100, 1890],
  pv: [3600, 4050, 3300, 4350, 5040, 6300, 5670],
  addToCart: [180, 202, 165, 218, 252, 315, 284],
};
export function trafficOption(t: ChartTokens) {
  return {
    tooltip: { trigger: "axis" as const, ...tip(t) },
    legend: legend(t, ["UV", "PV", "加购人数"]),
    grid: { top: 12, right: 16, bottom: 40, left: 50 },
    xAxis: catX(t, DEMO_TRAFFIC.labels),
    yAxis: valY(t),
    series: [
      { name: "UV", type: "line", smooth: true, showSymbol: false, data: DEMO_TRAFFIC.uv, lineStyle: { color: "#6366f1" }, itemStyle: { color: "#6366f1" } },
      { name: "PV", type: "line", smooth: true, showSymbol: false, data: DEMO_TRAFFIC.pv, lineStyle: { color: "#8b5cf6" }, itemStyle: { color: "#8b5cf6" } },
      { name: "加购人数", type: "bar", data: DEMO_TRAFFIC.addToCart, itemStyle: { color: "#a78bfa", borderRadius: [4, 4, 0, 0] }, barMaxWidth: 22 },
    ],
  };
}

/* ── 转化漏斗（浏览→加购→下单→支付）──────────────────────── */
export const DEMO_FUNNEL = [
  { value: 100, name: "浏览" },
  { value: 62, name: "加购" },
  { value: 30, name: "下单" },
  { value: 24, name: "支付" },
];
const FUNNEL_COLORS = ["#6366f1", "#8b5cf6", "#a78bfa", "#c4b5fd"];
export function funnelOption(t: ChartTokens) {
  return {
    tooltip: { trigger: "item" as const, ...tip(t), formatter: "{b}: {c} ({d}%)" },
    series: [
      {
        type: "funnel",
        left: "10%",
        top: 20,
        bottom: 20,
        width: "80%",
        min: 0,
        max: 100,
        sort: "descending" as const,
        gap: 2,
        label: { show: true, position: "inside" as const, color: "#fff", fontSize: 12 },
        itemStyle: { borderWidth: 0 },
        data: DEMO_FUNNEL.map((d, i) => ({ ...d, itemStyle: { color: FUNNEL_COLORS[i] } })),
      },
    ],
  };
}

/* ── 下单趋势（按平台堆叠柱）──────────────────────────────── */
export const DEMO_ORDERS = {
  labels: WEEK_LABELS,
  shopify: [50, 55, 42, 58, 38, 75, 68],
  amazon: [45, 48, 35, 48, 32, 65, 58],
  tiktok: [25, 29, 24, 28, 20, 40, 34],
};
export function ordersStackOption(t: ChartTokens) {
  return {
    tooltip: { trigger: "axis" as const, ...tip(t) },
    legend: legend(t, ["Shopify", "Amazon", "TikTok Shop"]),
    grid: { top: 12, right: 16, bottom: 40, left: 50 },
    xAxis: catX(t, DEMO_ORDERS.labels),
    yAxis: valY(t),
    series: [
      { name: "Shopify", type: "bar", stack: "total", data: DEMO_ORDERS.shopify, itemStyle: { color: "#6366f1" }, barMaxWidth: 28 },
      { name: "Amazon", type: "bar", stack: "total", data: DEMO_ORDERS.amazon, itemStyle: { color: "#8b5cf6" } },
      { name: "TikTok Shop", type: "bar", stack: "total", data: DEMO_ORDERS.tiktok, itemStyle: { color: "#a78bfa", borderRadius: [4, 4, 0, 0] } },
    ],
  };
}

/* ── 退货分析（退货数柱 + 退货率折线，双 Y 轴）+ 原因环图 ──── */
export const DEMO_RETURNS = {
  labels: WEEK_LABELS,
  count: [8, 12, 6, 10, 5, 15, 11],
  rate: [6.7, 9.1, 5.9, 7.5, 5.6, 8.3, 6.9],
  reasons: [
    { value: 35, name: "质量问题" },
    { value: 25, name: "尺寸不符" },
    { value: 20, name: "与描述不符" },
    { value: 14, name: "不想要了" },
    { value: 6, name: "其他" },
  ],
};
const RETURN_REASON_COLORS = ["#ef4444", "#f59e0b", "#6366f1", "#8b5cf6", "#a78bfa"];
export function returnsOption(t: ChartTokens) {
  return {
    tooltip: { trigger: "axis" as const, ...tip(t) },
    legend: legend(t, ["退货数", "退货率"]),
    grid: { top: 12, right: 60, bottom: 40, left: 50 },
    xAxis: catX(t, DEMO_RETURNS.labels),
    yAxis: [
      { type: "value" as const, name: "数量", axisLine: { show: false }, splitLine: { lineStyle: { color: t.grid } }, axisLabel: { color: t.sub } },
      { type: "value" as const, name: "比率", axisLine: { show: false }, splitLine: { show: false }, axisLabel: { color: t.sub, formatter: "{value}%" }, max: 20 },
    ],
    series: [
      { name: "退货数", type: "bar", data: DEMO_RETURNS.count, itemStyle: { color: "#ef4444", borderRadius: [4, 4, 0, 0] }, barMaxWidth: 22 },
      { name: "退货率", type: "line", yAxisIndex: 1, data: DEMO_RETURNS.rate, smooth: true, showSymbol: false, lineStyle: { color: "#f59e0b", width: 2 }, itemStyle: { color: "#f59e0b" } },
    ],
  };
}
export function returnReasonsOption(t: ChartTokens) {
  return {
    tooltip: { trigger: "item" as const, ...tip(t) },
    series: [
      {
        type: "pie",
        radius: ["40%", "70%"],
        avoidLabelOverlap: false,
        itemStyle: { borderRadius: 6, borderColor: "#fff", borderWidth: 2 },
        label: { show: true, fontSize: 11, color: t.sub },
        data: DEMO_RETURNS.reasons.map((d, i) => ({ ...d, itemStyle: { color: RETURN_REASON_COLORS[i] } })),
      },
    ],
  };
}

/* ── 退款分析（退款金额面积 + 退款率虚线，双 Y 轴，月维度）── */
export const DEMO_REFUNDS = {
  months: ["1月", "2月", "3月", "4月", "5月", "6月", "7月"],
  amount: [2400, 1800, 3200, 2100, 2800, 1900, 2600],
  rate: [1.8, 1.2, 2.5, 1.5, 2.0, 1.3, 1.8],
};
export function refundsOption(t: ChartTokens) {
  return {
    tooltip: { trigger: "axis" as const, ...tip(t) },
    legend: legend(t, ["退款金额", "退款率"]),
    grid: { top: 12, right: 60, bottom: 40, left: 60 },
    xAxis: catX(t, DEMO_REFUNDS.months),
    yAxis: [
      { type: "value" as const, name: "金额", axisLine: { show: false }, splitLine: { lineStyle: { color: t.grid } }, axisLabel: { color: t.sub, formatter: "${value}" } },
      { type: "value" as const, name: "比率", axisLine: { show: false }, splitLine: { show: false }, axisLabel: { color: t.sub, formatter: "{value}%" }, max: 10 },
    ],
    series: [
      {
        name: "退款金额",
        type: "line",
        smooth: true,
        showSymbol: false,
        data: DEMO_REFUNDS.amount,
        lineStyle: { color: "#ef4444", width: 3 },
        itemStyle: { color: "#ef4444" },
        areaStyle: { color: { type: "linear" as const, x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: "rgba(239,68,68,0.2)" }, { offset: 1, color: "rgba(239,68,68,0)" }] } },
      },
      { name: "退款率", type: "line", yAxisIndex: 1, smooth: true, showSymbol: false, data: DEMO_REFUNDS.rate, lineStyle: { color: "#f59e0b", width: 2, type: "dashed" as const }, itemStyle: { color: "#f59e0b" } },
    ],
  };
}
