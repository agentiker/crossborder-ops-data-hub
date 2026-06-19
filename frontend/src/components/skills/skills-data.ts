// Skills 页的工具清单。Phase 1 用静态描述占位；Phase 2 改为接 GET /api/admin/tools
// 拉真实的 name/description/启用态（与 web/agent_tools.py 的 TOOL_SPECS 对齐）。

export interface SkillFeature {
  title: string;
  description: string;
}

export interface ToolSkill {
  name: string; // 与后端 ops_* 工具名一致
  label: string; // 中文展示名
  category: string;
  badge: "官方" | "社区精选"; // 照 forkStoreClaw 的 Official / Community Picks 徽章
  freq: string; // 调用频率提示（详情弹窗用）
  description: string;
  detail: string; // 详情弹窗用的更完整说明
  params: string; // 可调参数说明
  features: SkillFeature[]; // 详情弹窗「核心能力」分点
}

export const TOOL_SKILLS: ToolSkill[] = [
  {
    name: "ops_overview",
    label: "经营概览",
    category: "经营总览",
    badge: "官方",
    freq: "随时调用",
    description: "一次拉齐店铺的 GMV、订单数、客单价等核心经营指标，回答「最近生意怎么样」这类总览问题。",
    detail:
      "当用户问整体经营状况、某周期内的总成绩时调用。返回 GMV、订单数、客单价等汇总指标，是大多数对话的第一站。",
    params: "period：统计周期（如最近 7/30 天）",
    features: [
      { title: "核心指标一把抓", description: "GMV、订单数、客单价等关键经营数据一次返回，省去逐项追问。" },
      { title: "对话的第一站", description: "用户问「最近生意怎么样」时优先调用，作为后续深挖的起点。" },
    ],
  },
  {
    name: "ops_orders_summary",
    label: "订单汇总",
    category: "订单与销售",
    badge: "官方",
    freq: "随时调用",
    description: "按周期汇总订单数、销售额与退款情况，回答「这段时间卖了多少」。",
    detail: "聚焦订单维度的汇总：下单量、销售额、退款金额/比例。用于复盘某一时间段的销售结果。",
    params: "period：统计周期",
    features: [
      { title: "销售结果复盘", description: "下单量、销售额、退款金额与比例一次给齐，方便复盘时段表现。" },
      { title: "退款口径清晰", description: "把退款单独拆出，避免把退款混进净销售额造成误判。" },
    ],
  },
  {
    name: "ops_orders_trend",
    label: "订单趋势",
    category: "订单与销售",
    badge: "官方",
    freq: "随时调用",
    description: "按天给出订单量与销售额的走势，回答「最近的销售曲线是涨是跌」。",
    detail: "返回逐日的订单与销售额序列，适合判断趋势、找拐点、对比不同时段。",
    params: "period：统计周期",
    features: [
      { title: "逐日走势", description: "按天返回订单量与销售额序列，涨跌一眼看清。" },
      { title: "找拐点对比", description: "适合判断趋势、定位拐点、对比不同时段的表现差异。" },
    ],
  },
  {
    name: "ops_top_skus",
    label: "爆款榜",
    category: "商品",
    badge: "官方",
    freq: "随时调用",
    description: "按销量/销售额排出卖得最好的商品，回答「哪些商品最能打」。",
    detail: "返回 Top 商品排行（销量、销售额），用于识别主力爆款、安排补货与营销重点。",
    params: "period：统计周期；limit：取前 N 个",
    features: [
      { title: "主力爆款识别", description: "按销量或销售额排出 Top 商品，快速锁定主力贡献者。" },
      { title: "驱动补货营销", description: "为补货优先级与营销资源分配提供数据依据。" },
    ],
  },
  {
    name: "ops_low_stock",
    label: "断货风险",
    category: "库存与履约",
    badge: "官方",
    freq: "随时调用",
    description: "找出可售天数过低或已断货的 SKU，回答「哪些要补货了」。",
    detail: "结合近期销速与当前库存，估算可售天数，标出断货/低库存 SKU，给补货决策打底。",
    params: "limit：取风险最高的前 N 个",
    features: [
      { title: "可售天数估算", description: "结合近期销速与当前库存，算出每个 SKU 还能卖几天。" },
      { title: "断货优先级", description: "标出断货/低库存 SKU，按紧急程度排序辅助补货决策。" },
    ],
  },
  {
    name: "ops_fulfillments_pending",
    label: "待发货",
    category: "库存与履约",
    badge: "社区精选",
    freq: "随时调用",
    description: "汇总待发货订单与超时风险，回答「有多少单没发、有没有快超时的」。",
    detail: "按考核口径列出待发货订单、临近取消线的超时风险，帮助盯紧发货 SLA。",
    params: "（无需参数）",
    features: [
      { title: "待发货盘点", description: "按考核口径汇总待发货订单数量，发货进度心里有底。" },
      { title: "超时风险预警", description: "标出临近取消线的订单，帮你盯紧发货 SLA 不踩线。" },
    ],
  },
];

export const SKILL_CATEGORIES = ["全部", "经营总览", "订单与销售", "商品", "库存与履约"];
