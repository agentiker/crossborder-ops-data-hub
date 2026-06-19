// Skills 页的工具清单。Phase 1 用静态描述占位；Phase 2 改为接 GET /api/admin/tools
// 拉真实的 name/description/启用态（与 web/agent_tools.py 的 TOOL_SPECS 对齐）。

export interface ToolSkill {
  name: string; // 与后端 ops_* 工具名一致
  label: string; // 中文展示名
  category: string;
  description: string;
  detail: string; // 详情弹窗用的更完整说明
  params: string; // 可调参数说明
}

export const TOOL_SKILLS: ToolSkill[] = [
  {
    name: "ops_overview",
    label: "经营概览",
    category: "经营总览",
    description: "一次拉齐店铺的 GMV、订单数、客单价等核心经营指标，回答「最近生意怎么样」这类总览问题。",
    detail:
      "当用户问整体经营状况、某周期内的总成绩时调用。返回 GMV、订单数、客单价等汇总指标，是大多数对话的第一站。",
    params: "period：统计周期（如最近 7/30 天）",
  },
  {
    name: "ops_orders_summary",
    label: "订单汇总",
    category: "订单与销售",
    description: "按周期汇总订单数、销售额与退款情况，回答「这段时间卖了多少」。",
    detail: "聚焦订单维度的汇总：下单量、销售额、退款金额/比例。用于复盘某一时间段的销售结果。",
    params: "period：统计周期",
  },
  {
    name: "ops_orders_trend",
    label: "订单趋势",
    category: "订单与销售",
    description: "按天给出订单量与销售额的走势，回答「最近的销售曲线是涨是跌」。",
    detail: "返回逐日的订单与销售额序列，适合判断趋势、找拐点、对比不同时段。",
    params: "period：统计周期",
  },
  {
    name: "ops_top_skus",
    label: "爆款榜",
    category: "商品",
    description: "按销量/销售额排出卖得最好的商品，回答「哪些商品最能打」。",
    detail: "返回 Top 商品排行（销量、销售额），用于识别主力爆款、安排补货与营销重点。",
    params: "period：统计周期；limit：取前 N 个",
  },
  {
    name: "ops_low_stock",
    label: "断货风险",
    category: "库存与履约",
    description: "找出可售天数过低或已断货的 SKU，回答「哪些要补货了」。",
    detail: "结合近期销速与当前库存，估算可售天数，标出断货/低库存 SKU，给补货决策打底。",
    params: "limit：取风险最高的前 N 个",
  },
  {
    name: "ops_fulfillments_pending",
    label: "待发货",
    category: "库存与履约",
    description: "汇总待发货订单与超时风险，回答「有多少单没发、有没有快超时的」。",
    detail: "按考核口径列出待发货订单、临近取消线的超时风险，帮助盯紧发货 SLA。",
    params: "（无需参数）",
  },
];

export const SKILL_CATEGORIES = ["全部", "经营总览", "订单与销售", "商品", "库存与履约"];
