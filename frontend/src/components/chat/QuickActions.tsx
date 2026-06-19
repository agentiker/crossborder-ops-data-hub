import { useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Lightbulb } from "lucide-react";

// 首页建议 chips：横滚 + 左右渐变遮罩 + hover 箭头（照搬 fork QuickActions 的滚动交互）。
// 点击即发起对话（走真 send）。
export function QuickActions({
  presets,
  onPick,
}: {
  presets: string[];
  onPick: (text: string) => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [canLeft, setCanLeft] = useState(false);
  const [canRight, setCanRight] = useState(false);

  const check = () => {
    const el = scrollRef.current;
    if (!el) return;
    setCanLeft(el.scrollLeft > 5);
    setCanRight(el.scrollLeft < el.scrollWidth - el.clientWidth - 5);
  };

  useEffect(() => {
    check();
    const el = scrollRef.current;
    if (!el) return;
    el.addEventListener("scroll", check, { passive: true });
    window.addEventListener("resize", check);
    return () => {
      el.removeEventListener("scroll", check);
      window.removeEventListener("resize", check);
    };
  }, []);

  const scrollBy = (dir: "left" | "right") => {
    scrollRef.current?.scrollBy({ left: dir === "left" ? -200 : 200, behavior: "smooth" });
  };

  return (
    <div className="group relative">
      {canLeft && (
        <button
          onClick={() => scrollBy("left")}
          className="absolute left-0 top-1/2 z-10 flex size-8 -translate-y-1/2 items-center justify-center rounded-full border border-border bg-card text-foreground-secondary opacity-0 shadow-md transition-opacity hover:text-foreground group-hover:opacity-100"
          aria-label="向左滚动"
        >
          <ChevronLeft className="size-4" />
        </button>
      )}

      <div ref={scrollRef} className="flex items-center gap-2 overflow-x-auto py-1 scrollbar-hide">
        <Lightbulb className="size-3.5 shrink-0 text-foreground-tertiary" />
        {presets.map((p) => (
          <button
            key={p}
            onClick={() => onPick(p)}
            className="shrink-0 whitespace-nowrap rounded-full border border-border bg-card px-3.5 py-1.5 text-xs text-foreground-secondary transition-colors hover:border-foreground/30 hover:text-foreground"
          >
            {p}
          </button>
        ))}
      </div>

      {canRight && (
        <button
          onClick={() => scrollBy("right")}
          className="absolute right-0 top-1/2 z-10 flex size-8 -translate-y-1/2 items-center justify-center rounded-full border border-border bg-card text-foreground-secondary opacity-0 shadow-md transition-opacity hover:text-foreground group-hover:opacity-100"
          aria-label="向右滚动"
        >
          <ChevronRight className="size-4" />
        </button>
      )}

      {/* 渐变遮罩：暗示可继续横滚 */}
      {canLeft && (
        <div className="pointer-events-none absolute inset-y-0 left-0 w-10 bg-gradient-to-r from-background to-transparent" />
      )}
      {canRight && (
        <div className="pointer-events-none absolute inset-y-0 right-0 w-10 bg-gradient-to-l from-background to-transparent" />
      )}
    </div>
  );
}
