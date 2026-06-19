// Scheduled 页的数据类型与模板。Phase 1 任务暂存前端 state；
// Phase 3 接 /api/scheduled CRUD，到点由 systemd 扫表执行器跑 agent 取数并推送。

export type Freq = "daily" | "weekly";

export interface ScheduledDraft {
  name: string;
  description: string;
  prompt: string;
  freq: Freq;
  time: string; // HH:MM
  weekday: number; // 0=周日 … 6=周六（freq=weekly 时用）
}

export interface ScheduledTaskItem extends ScheduledDraft {
  id: string;
  enabled: boolean;
}

export interface Template {
  id: string;
  title: string;
  description: string;
  draft: ScheduledDraft;
}

export const WEEKDAYS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

export function scheduleLabel(t: { freq: Freq; time: string; weekday: number }): string {
  return t.freq === "daily" ? `每天 ${t.time}` : `每${WEEKDAYS[t.weekday]} ${t.time}`;
}

export const TEMPLATES: Template[] = [
  {
    id: "morning-brief",
    title: "晨间经营简报",
    description: "每天开工前，先把昨天的 GMV、订单、待发货收成发到你手上。",
    draft: {
      name: "晨间经营简报",
      description: "每天 09:00 推送昨日经营概览",
      prompt: "汇总昨天的 GMV、订单数、客单价和待发货情况，用简报口吻给我，重点标出异常。",
      freq: "daily",
      time: "09:00",
      weekday: 1,
    },
  },
  {
    id: "weekly-top",
    title: "周度爆款盘点",
    description: "每周一回顾上周卖得最好的商品，方便排补货和营销。",
    draft: {
      name: "周度爆款盘点",
      description: "每周一 09:00 推送上周爆款榜",
      prompt: "列出上周销量最好的 10 个商品，并简要点评趋势和补货建议。",
      freq: "weekly",
      time: "09:00",
      weekday: 1,
    },
  },
  {
    id: "stock-alert",
    title: "库存补货提醒",
    description: "每天盯一眼快断货的 SKU，别等卖断了才发现。",
    draft: {
      name: "库存补货提醒",
      description: "每天 10:00 推送断货风险 SKU",
      prompt: "找出可售天数过低或已断货的 SKU，按紧急程度排序，给补货优先级建议。",
      freq: "daily",
      time: "10:00",
      weekday: 1,
    },
  },
];
