import { useRef, useState, useEffect } from "react";
import {
  Sparkles,
  Search,
  Package,
  PenTool,
  Store,
  BarChart3,
  ChevronLeft,
  ChevronRight,
} from "lucide-react";

// 照搬 forkStoreClaw/src/components/Chat/QuickActions.tsx：
// max-w-[820px] 横滚区、hover 浮现左右箭头（bg-white shadow-md）、两侧渐隐遮罩 1:1；
// 仅 ①数据接线：chips 换成中文建议问题、点击直接发起对话（不再是「分类筛选」，故无常驻 active 态）。
const ICONS = [Sparkles, Search, Package, PenTool, Store, BarChart3];

interface QuickActionsProps {
  presets: string[];
  onPick?: (label: string) => void;
}

export function QuickActions({ presets, onPick }: QuickActionsProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [canScrollLeft, setCanScrollLeft] = useState(false);
  const [canScrollRight, setCanScrollRight] = useState(false);

  const checkScroll = () => {
    if (scrollRef.current) {
      const { scrollLeft, scrollWidth, clientWidth } = scrollRef.current;
      setCanScrollLeft(scrollLeft > 5);
      setCanScrollRight(scrollLeft < scrollWidth - clientWidth - 5);
    }
  };

  useEffect(() => {
    checkScroll();
    const el = scrollRef.current;
    if (el) {
      el.addEventListener("scroll", checkScroll, { passive: true });
      window.addEventListener("resize", checkScroll);
      return () => {
        el.removeEventListener("scroll", checkScroll);
        window.removeEventListener("resize", checkScroll);
      };
    }
  }, []);

  const scroll = (direction: "left" | "right") => {
    if (scrollRef.current) {
      const amount = direction === "left" ? -200 : 200;
      scrollRef.current.scrollBy({ left: amount, behavior: "smooth" });
    }
  };

  return (
    <div className="relative mx-auto w-full max-w-[820px]">
      <div className="relative group">
        {/* Left arrow */}
        {canScrollLeft && (
          <button
            onClick={() => scroll("left")}
            className="absolute left-0 top-1/2 -translate-y-1/2 z-10 w-8 h-8 flex items-center justify-center rounded-full bg-white shadow-md border border-border text-foreground-secondary hover:text-foreground transition-opacity opacity-0 group-hover:opacity-100"
          >
            <ChevronLeft size={16} />
          </button>
        )}

        {/* Scrollable container */}
        <div ref={scrollRef} className="flex gap-2 overflow-x-auto py-2 px-1 scrollbar-hide">
          {presets.map((label, i) => {
            const Icon = ICONS[i % ICONS.length];
            return (
              <button
                key={label}
                onClick={() => onPick?.(label)}
                className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border px-3.5 py-2 text-sm font-medium transition-colors duration-200 border-border text-foreground hover:border-border-deep"
              >
                <span className="-mx-0.5 inline-flex size-4 items-center justify-center [&_svg]:size-full [&_svg]:shrink-0">
                  <Icon size={16} />
                </span>
                {label}
              </button>
            );
          })}
        </div>

        {/* Right arrow */}
        {canScrollRight && (
          <button
            onClick={() => scroll("right")}
            className="absolute right-0 top-1/2 -translate-y-1/2 z-10 w-8 h-8 flex items-center justify-center rounded-full bg-white shadow-md border border-border text-foreground-secondary hover:text-foreground transition-opacity opacity-0 group-hover:opacity-100"
          >
            <ChevronRight size={16} />
          </button>
        )}

        {/* Fade edges */}
        {canScrollLeft && (
          <div className="absolute left-0 top-0 bottom-0 w-12 bg-gradient-to-r from-background-solid to-transparent pointer-events-none" />
        )}
        {canScrollRight && (
          <div className="absolute right-0 top-0 bottom-0 w-12 bg-gradient-to-l from-background-solid to-transparent pointer-events-none" />
        )}
      </div>
    </div>
  );
}
