import { useState, useEffect } from "react";

// 照搬 forkStoreClaw/src/components/Chat/WelcomeScreen.tsx：
// 版式/动画/入场延迟序列（badge 0.3s · emoji 0.2s · 副标题 0.4s）1:1；
// 仅 ②文案中文化（问候/时段/副标题），称呼由调用方按权限范围传入。
interface WelcomeScreenProps {
  userName?: string;
}

function getGreeting(): { text: string; emojis: string[]; period: string; subtitles: string[] } {
  const hour = new Date().getHours();

  if (hour >= 6 && hour < 12) {
    return {
      text: "早上好",
      emojis: ["☀️", "🌅", "☕", "🐤", "(๑•̀ㅂ•́)و"],
      period: "上午 · 元气满满",
      subtitles: [
        "清晨最安静，好点子往往这时候冒出来。",
        "先泡杯咖啡，再看看昨天的数字。",
        "新的一天，从一眼全局开始。",
        "趁人还没多，先把今天的方向理顺。",
        "早起的人，总能先看到机会。",
      ],
    };
  } else if (hour >= 12 && hour < 18) {
    return {
      text: "下午好",
      emojis: ["🌤️", "🍵", "💪", "✨", "(๑˃̵ᴗ˂̵)"],
      period: "下午 · 专注时段",
      subtitles: [
        "没人盯着的时候，正好试点新想法。",
        "下午茶时间，顺手翻翻爆款榜。",
        "专注这两小时，胜过忙乱一整天。",
        "想盯哪块数据？订单、爆款还是库存。",
        "灵感来了别放过，随时问我。",
      ],
    };
  } else if (hour >= 18 && hour < 22) {
    return {
      text: "晚上好",
      emojis: ["🌙", "🌆", "🌃", "⭐", "(◍•ᴗ•◍)"],
      period: "夜间 · 收尾复盘",
      subtitles: [
        "今晚安排好，明早醒来队列清清爽爽。",
        "睡前复盘一下，明天更从容。",
        "把今天收个尾，别把问题带到明天。",
        "夜色正好，慢慢看数据。",
        "起步晚一点也没关系，现在开始不算迟。",
      ],
    };
  } else {
    return {
      text: "又熬夜了",
      emojis: ["(._.)", "🌚", "🦉", "☕", "(=_=)"],
      period: "深夜 · 夜猫子",
      subtitles: [
        "还在熬夜？那就让这段时间熬得值一点。",
        "夜深了，安静的时候脑子最清楚。",
        "别硬撑，查完这条早点休息。",
        "夜猫子模式已开启，有什么尽管问。",
        "这个点还在忙，辛苦了。",
      ],
    };
  }
}

// 从时段 emoji 组里随机挑一个（每次进入欢迎页/切时段时变一次）。
function pickEmoji(emojis: string[]): string {
  return emojis[Math.floor(Math.random() * emojis.length)];
}

// 随机起点下标（进页/切时段时挑一句起头，之后顺序轮播）。
function randomIndex(len: number): number {
  return Math.floor(Math.random() * len);
}

export function WelcomeScreen({ userName = "老板" }: WelcomeScreenProps) {
  const [greeting, setGreeting] = useState(getGreeting());
  const [emoji, setEmoji] = useState(() => pickEmoji(greeting.emojis));
  // subIdx = 当前副标题下标（时段内顺序轮播）；typed = 已显示字数（打字机）；
  // fading = 打完停留后进入淡出，淡出结束切下一句。
  const [subIdx, setSubIdx] = useState(() => randomIndex(greeting.subtitles.length));
  const [typed, setTyped] = useState(0);
  const [fading, setFading] = useState(false);

  const subtitle = greeting.subtitles[subIdx] ?? "";

  // 每分钟检测时段，只有真正跨时段（text 变）才重置 emoji/副标题/打字机。
  useEffect(() => {
    const timer = setInterval(() => {
      setGreeting((prev) => {
        const next = getGreeting();
        if (next.text !== prev.text) {
          setEmoji(pickEmoji(next.emojis));
          setSubIdx(randomIndex(next.subtitles.length));
          setTyped(0);
          setFading(false);
        }
        return next;
      });
    }, 60000);
    return () => clearInterval(timer);
  }, []);

  // 打字机：逐字显示当前副标题。
  useEffect(() => {
    if (typed >= subtitle.length) return;
    // 首字延到 0.5s（让入场动画先落定），其余每字 55ms。
    const delay = typed === 0 ? 500 : 55;
    const t = setTimeout(() => setTyped((n) => n + 1), delay);
    return () => clearTimeout(t);
  }, [typed, subtitle]);

  // 轮播：打完 → 停留 3.2s → 淡出 0.4s → 切下一句 → 从头再打。
  useEffect(() => {
    if (subtitle.length === 0 || typed < subtitle.length) return;
    const hold = setTimeout(() => setFading(true), 3200);
    return () => clearTimeout(hold);
  }, [typed, subtitle]);

  useEffect(() => {
    if (!fading) return;
    const t = setTimeout(() => {
      setSubIdx((i) => (i + 1) % greeting.subtitles.length);
      setTyped(0);
      setFading(false);
    }, 400); // 与下方 transition duration-300~400 对齐
    return () => clearTimeout(t);
  }, [fading, greeting.subtitles.length]);

  const shownSubtitle = subtitle.slice(0, typed);
  const typing = typed < subtitle.length;

  // invisible 占位取时段内最长一句，保证逐字/换句时高度稳定不跳。
  const placeholder = greeting.subtitles.reduce((a, b) => (b.length > a.length ? b : a), "");

  return (
    <div className="text-center pb-5">
      {/* Status badge */}
      <div
        className="inline-flex items-center gap-1.5 bg-fill-default rounded-full px-3 py-1 text-xs text-foreground-tertiary mb-3.5 tracking-[0.02em] animate-fade-up"
        style={{ animationDelay: "0.3s" }}
      >
        <span className="size-1.5 rounded-full bg-positive inline-block animate-pulse-slow"></span>
        {greeting.period}
      </div>

      {/* Greeting */}
      <div className="flex flex-wrap items-center justify-center gap-x-2 gap-y-1 mb-2.5 px-3">
        <h1 className="text-[22px] sm:text-[26px] md:text-[32px] leading-tight font-bold text-foreground tracking-[-0.01em] animate-fade-up break-words">
          {greeting.text}，{userName}
        </h1>
        <span
          role="img"
          aria-hidden="true"
          className="text-[20px] sm:text-[22px] md:text-[26px] inline-block origin-bottom animate-fade-up animate-wiggle cursor-default select-none"
          style={{ animationDelay: "0.2s" }}
        >
          {emoji}
        </span>
      </div>

      {/* Subtitle（打字机逐字显示 + 轮播；invisible 占位取最长句，防止换句抖动换行）*/}
      <div className="overflow-hidden relative px-3 animate-fade-up" style={{ animationDelay: "0.4s" }}>
        <p className="text-[14px] sm:text-[15px] md:text-[17px] text-foreground-secondary leading-6 sm:leading-6 md:leading-7">
          <span className="relative inline-block max-w-full break-words">
            <span className="invisible inline-flex" aria-hidden="true">{placeholder}</span>
            <span
              className={`absolute left-0 top-0 inline-flex items-baseline flex-wrap transition-opacity duration-300 ${
                fading ? "opacity-0" : "opacity-100"
              }`}
            >
              {shownSubtitle}
              {/* 光标：打字中闪烁，打完消失 */}
              {typing && (
                <span className="ml-0.5 inline-block w-px self-center h-[1em] bg-foreground-secondary animate-pulse" />
              )}
            </span>
          </span>
        </p>
      </div>
    </div>
  );
}
