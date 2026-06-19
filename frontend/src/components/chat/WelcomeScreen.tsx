import { useState, useEffect } from "react";

// 照搬 forkStoreClaw/src/components/Chat/WelcomeScreen.tsx：
// 版式/动画/入场延迟序列（badge 0.3s · emoji 0.2s · 副标题 0.4s）1:1；
// 仅 ②文案中文化（问候/时段/副标题），称呼由调用方按权限范围传入。
interface WelcomeScreenProps {
  userName?: string;
}

function getGreeting(): { text: string; emoji: string; period: string; subtitle: string } {
  const hour = new Date().getHours();

  if (hour >= 6 && hour < 12) {
    return { text: "早上好", emoji: "☀️", period: "上午 · 元气满满", subtitle: "新的一天，先看看店铺整体表现吧。" };
  } else if (hour >= 12 && hour < 18) {
    return { text: "下午好", emoji: "🌤️", period: "下午 · 专注时段", subtitle: "想盯哪块数据？订单、爆款还是库存。" };
  } else if (hour >= 18 && hour < 22) {
    return { text: "晚上好", emoji: "🌙", period: "夜间 · 收尾复盘", subtitle: "复盘一下今天的经营，明天更从容。" };
  } else {
    return { text: "又熬夜了", emoji: "(._.)", period: "深夜 · 夜猫子", subtitle: "夜深了，有什么想查的尽管问。" };
  }
}

export function WelcomeScreen({ userName = "老板" }: WelcomeScreenProps) {
  const [greeting, setGreeting] = useState(getGreeting());

  useEffect(() => {
    const timer = setInterval(() => {
      setGreeting(getGreeting());
    }, 60000); // 每分钟刷新一次时段

    return () => clearInterval(timer);
  }, []);

  return (
    <div className="text-center pb-5">
      {/* Status badge */}
      <div
        className="inline-flex items-center gap-1.5 bg-fill-default rounded-full px-3 py-1 text-xs text-foreground-tertiary mb-3.5 tracking-[0.02em] animate-fade-up"
        style={{ animationDelay: "0.3s" }}
      >
        <span className="size-1.5 rounded-full bg-green-500 inline-block animate-pulse-slow"></span>
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
          {greeting.emoji}
        </span>
      </div>

      {/* Subtitle */}
      <div className="overflow-hidden relative px-3 animate-fade-up" style={{ animationDelay: "0.4s" }}>
        <p className="text-[14px] sm:text-[15px] md:text-[17px] text-foreground-secondary leading-6 sm:leading-6 md:leading-7 transition-[opacity,transform] duration-300 opacity-100 translate-y-0">
          <span className="relative inline-block max-w-full break-words">
            <span className="invisible inline-flex">{greeting.subtitle}</span>
            <span className="absolute left-0 top-0 inline-flex items-baseline flex-wrap">
              {greeting.subtitle}
            </span>
          </span>
        </p>
      </div>
    </div>
  );
}
