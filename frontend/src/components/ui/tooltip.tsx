import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * 轻量自建 tooltip（不引 radix）。hover + 点击 toggle 双触发——桌面端悬停即显,
 * 移动端老板用手机也能点开。点击外部或再次点击关闭。
 *
 * 用法：<InfoTooltip content="说明文案"><Info className="..." /></InfoTooltip>
 * children 作为触发元素（图标/文字均可）;content 为气泡内容。
 */
export function InfoTooltip({
  content,
  children,
  className,
  side = "top",
  align = "center",
}: {
  content: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  side?: "top" | "bottom";
  // 水平对齐：center 居中（默认）；start 气泡左缘贴触发点向右展开（用于靠左、左侧被侧栏/容器裁剪处）；end 反之。
  align?: "center" | "start" | "end";
}) {
  const [open, setOpen] = React.useState(false);
  const ref = React.useRef<HTMLSpanElement>(null);

  // 点击页面其他位置关闭（移动端点开后的收起路径）
  React.useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("click", onDocClick);
    return () => document.removeEventListener("click", onDocClick);
  }, [open]);

  return (
    <span
      ref={ref}
      className={cn("relative inline-flex items-center", className)}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        aria-label="说明"
        // -m-1 p-1：放大触控热区（移动端老板手指点得到）而不撑动行内排版。
        className="-m-1 inline-flex items-center p-1 text-foreground-secondary transition-colors hover:text-foreground focus:outline-none"
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
      >
        {children}
      </button>
      {open && (
        <span
          role="tooltip"
          className={cn(
            "absolute z-50 w-56 rounded-lg border border-border-shallow bg-background px-3 py-2 text-xs leading-relaxed text-foreground shadow-lg",
            side === "top" ? "bottom-full mb-2" : "top-full mt-2",
            align === "center" && "left-1/2 -translate-x-1/2",
            align === "start" && "left-0",
            align === "end" && "right-0",
          )}
        >
          {content}
        </span>
      )}
    </span>
  );
}
