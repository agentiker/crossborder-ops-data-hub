import { useState } from "react";
import { ArrowUp } from "lucide-react";
import { Composer } from "./Composer";
import { QuickActions } from "./QuickActions";

// 按本机时刻给时段名 + 氛围标签 + 问候 + emoji（StoreClaw 式时段徽标 ● 两段式）。
function timeOfDay(): { period: string; tag: string; greeting: string; emoji: string } {
  const h = new Date().getHours();
  if (h < 5) return { period: "夜深", tag: "夜猫子档", greeting: "夜深了，看看今天的生意", emoji: "🌙" };
  if (h < 8) return { period: "清晨", tag: "醒得早", greeting: "早，先看看昨天的收成", emoji: "🌅" };
  if (h < 11) return { period: "上午", tag: "开工时段", greeting: "上午好，今天想看哪块数据", emoji: "☀️" };
  if (h < 13) return { period: "午后", tag: "午间小憩", greeting: "午间好，店铺跑得怎么样", emoji: "🍵" };
  if (h < 18) return { period: "下午", tag: "下午场", greeting: "下午好，盯一眼经营节奏", emoji: "📊" };
  if (h < 23) return { period: "傍晚", tag: "收工盘点", greeting: "傍晚好，盘点今天这一单单", emoji: "🌆" };
  return { period: "夜深", tag: "夜猫子档", greeting: "夜深了，看看今天的生意", emoji: "🌙" };
}

// 首页命令栏 launcher：时段徽标 + 问候 + 输入框 + 横滚 chips + 两张快捷卡。
// 入场动画照搬 fork：badge 0.3s / 标题 0s / emoji 0.2s / 副标题 0.4s 依次 fade-up。
export function WelcomeScreen({
  scopeLabel,
  presets,
  quickCards,
  onSend,
  streaming,
}: {
  scopeLabel?: string;
  presets: string[];
  quickCards: { title: string; desc: string; q: string }[];
  onSend: (text: string) => void;
  streaming: boolean;
}) {
  const [tod] = useState(timeOfDay);
  // 当前 Me 没有姓名，用 scope_label 当称呼；缺省就只问候不带名字。
  const who = scopeLabel?.trim();

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-2xl px-6 pb-16 pt-[14vh]">
        <span
          className="inline-flex animate-fade-up items-center gap-2 rounded-full border border-border-shallow bg-card px-3 py-1 text-xs font-medium tracking-[0.02em] text-foreground-secondary"
          style={{ animationDelay: "0.3s" }}
        >
          <span className="relative flex size-2">
            <span className="inline-flex size-full rounded-full bg-positive animate-pulse-dot" />
          </span>
          {tod.period} · {tod.tag}
        </span>

        <h1 className="mt-5 animate-fade-up text-4xl font-bold leading-[1.15] tracking-tight sm:text-[2.75rem]">
          {tod.greeting}
          {who ? <span className="text-foreground-secondary">，{who}</span> : null}
          <span
            className="ml-2 inline-block origin-bottom animate-fade-up animate-wiggle"
            style={{ animationDelay: "0.2s" }}
          >
            {tod.emoji}
          </span>
        </h1>

        <p
          className="mt-2 animate-fade-up text-sm text-foreground-secondary"
          style={{ animationDelay: "0.4s" }}
        >
          问我店铺的 GMV、订单、爆款、库存与待发货——按你的权限范围答。
        </p>

        <div className="mt-6">
          <Composer onSend={onSend} streaming={streaming} autoFocus size="home" />
        </div>

        <div className="mt-4">
          <QuickActions presets={presets} onPick={onSend} />
        </div>

        <div className="mt-7 grid grid-cols-1 gap-3 sm:grid-cols-2">
          {quickCards.map((c) => (
            <button
              key={c.title}
              onClick={() => onSend(c.q)}
              className="group rounded-2xl border border-border-shallow bg-card p-4 text-left shadow-sm transition-all hover:-translate-y-0.5 hover:border-border hover:shadow-md"
            >
              <div className="flex items-center justify-between">
                <span className="text-base font-semibold">{c.title}</span>
                <ArrowUp className="size-4 rotate-45 text-foreground-tertiary transition-transform group-hover:-translate-y-0.5 group-hover:translate-x-0.5" />
              </div>
              <p className="mt-1 text-xs text-foreground-secondary">{c.desc}</p>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
