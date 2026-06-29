// 看板「问 AI」抽屉的持久会话 store（module 级单例）。
//
// 为什么需要它：抽屉原本把对话状态放组件内 state，关闭即 unmount → 状态销毁、流式 abort。
// 用户诉求：① 同一张卡片关掉再开，问答还在；② 回答生成慢时关掉，后台继续跑完，回来能看到完整结果。
// 解法：把「每个问题对应的会话」提到组件外的单例里，抽屉只是它的订阅式视图。抽屉 unmount 不影响
// 流式继续写入 store；重开同一 question 命中已存在的会话，直接展示（含进行中的流式）。
//
// 复用现有基础设施：sendChat（SSE 流式）、Message/ThinkingStep 类型，不重造数据层。

import { useSyncExternalStore } from "react";
import { sendChat, type Message, type ThinkingStep } from "@/api";

// ops_* 工具中文名（与 ChatPage/抽屉一致）；SSE 的 tool 事件 label 可能为空或非中文，前端兜底映射。
const TOOL_LABELS: Record<string, string> = {
  ops_overview: "经营概览",
  ops_orders_summary: "订单汇总",
  ops_orders_trend: "订单趋势",
  ops_top_skus: "爆款榜",
  ops_low_stock: "断货风险",
  ops_fulfillments_pending: "待发货",
  ops_business_rules: "业务规则",
  ops_report: "经营报告",
};

// 一个问题对应的会话快照（抽屉渲染所需的全部状态）。
export interface AskSession {
  question: string; // 作为 key：同一卡片的同一疑问复用同一会话
  messages: Message[]; // 已落定的多轮消息
  liveText: string; // 当前流式回合的增量正文
  liveSteps: ThinkingStep[]; // 当前流式回合的工具步骤
  streaming: boolean; // 是否正在生成
  error: string | null;
  convId: number | null; // 后端会话 id（续传追问用）
  abort: AbortController | null; // 仅「换问题/显式重置」时才中断；关闭抽屉不中断
}

type Listener = () => void;

const sessions = new Map<string, AskSession>();
const listeners = new Set<Listener>();

function emit() {
  for (const l of listeners) l();
}

function subscribe(l: Listener) {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}

function blankSession(question: string): AskSession {
  return {
    question,
    messages: [],
    liveText: "",
    liveSteps: [],
    streaming: false,
    error: null,
    convId: null,
    abort: null,
  };
}

function patch(question: string, p: Partial<AskSession>) {
  const cur = sessions.get(question);
  if (!cur) return;
  sessions.set(question, { ...cur, ...p });
  emit();
}

// 发一轮（首问或追问）：复用 sendChat，续传 convId；流式增量直接写进 store。
async function runTurn(question: string, text: string) {
  const s = sessions.get(question);
  if (!s || s.streaming || !text.trim()) return;

  const ctrl = new AbortController();
  patch(question, {
    messages: [...s.messages, { role: "user", content: text }],
    streaming: true,
    liveText: "",
    liveSteps: [],
    error: null,
    abort: ctrl,
  });

  let acc = "";
  let steps: ThinkingStep[] = [];
  try {
    for await (const ev of sendChat(text, sessions.get(question)?.convId ?? null, ctrl.signal)) {
      if (ev.type === "meta") {
        patch(question, { convId: ev.conversation_id });
      } else if (ev.type === "delta") {
        acc += ev.text;
        patch(question, { liveText: acc });
      } else if (ev.type === "tool") {
        const label = TOOL_LABELS[ev.name] || ev.name;
        if (ev.status === "running") {
          if (!steps.some((x) => x.name === ev.name && !x.done)) {
            steps = [...steps, { name: ev.name, label, done: false }];
          }
        } else {
          let marked = false;
          steps = steps.map((x) =>
            !marked && x.name === ev.name && !x.done ? ((marked = true), { ...x, done: true }) : x,
          );
        }
        patch(question, { liveSteps: steps });
      } else if (ev.type === "error") {
        patch(question, { error: ev.message });
      }
    }
  } catch (e) {
    if (!ctrl.signal.aborted) patch(question, { error: String(e) });
  }

  if (ctrl.signal.aborted) return; // 被显式中断（换问题/重置）→ 不落定
  steps = steps.map((x) => ({ ...x, done: true }));
  const cur = sessions.get(question);
  if (!cur) return;
  sessions.set(question, {
    ...cur,
    messages: [...cur.messages, { role: "assistant", content: acc, steps: steps.length ? steps : undefined }],
    liveText: "",
    liveSteps: [],
    streaming: false,
    abort: null,
  });
  emit();
}

// ── 对外 API ──

// 打开某问题的会话：已存在则复用（关键——关掉再开问答还在）；不存在才建并自动发首问。
export function openAskSession(question: string) {
  if (!sessions.has(question)) {
    sessions.set(question, blankSession(question));
    void runTurn(question, question); // 首问 = 卡片带来的疑问
  }
}

// 追问（抽屉底部输入框）。
export function askFollowUp(question: string, text: string) {
  void runTurn(question, text);
}

// 显式清空某问题的会话（如需「重新问」时调用）——会中断进行中的流式。
export function resetAskSession(question: string) {
  const s = sessions.get(question);
  s?.abort?.abort();
  sessions.delete(question);
  emit();
}

// 订阅某问题的会话快照。抽屉用它渲染；关闭抽屉只是取消订阅，store 与流式不受影响。
export function useAskSession(question: string): AskSession {
  return useSyncExternalStore(
    subscribe,
    () => sessions.get(question) ?? FALLBACK,
    () => sessions.get(question) ?? FALLBACK,
  );
}

// 稳定的空快照引用（避免 getSnapshot 每次返回新对象触发无限重渲染）。
const FALLBACK: AskSession = Object.freeze(blankSession(""));
