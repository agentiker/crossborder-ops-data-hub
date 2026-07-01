import * as React from "react";
import { createPortal } from "react-dom";
import { cn } from "@/lib/utils";

/**
 * 轻量自建 tooltip（不引 radix）。hover + 点击 toggle 双触发——桌面端悬停即显,
 * 移动端老板用手机也能点开。点击外部或再次点击关闭。
 *
 * ⚠️ 气泡经 **portal 挂到 document.body + position:fixed**，并按触发点实测位置 + 视口边界
 * 做**翻转/夹紧**（见 placeBubble）：彻底规避 `absolute` 在 overflow 滚动容器 / 卡片网格里
 * 被裁切、靠边溢出被挡的问题（看板是滚动容器 + 移动端一等公民，遮挡是结构性的）。
 * 气泡 `pointer-events-none`：纯文本说明无需交互，鼠标移开触发点即关，无 hover-gap 闪烁。
 *
 * 用法：<InfoTooltip content="说明文案"><Info className="..." /></InfoTooltip>
 * children 作为触发元素（图标/文字均可）;content 为气泡内容。side/align 仅作**初始偏好**，
 * 空间不足时自动翻转/夹紧到视口内。信息量大的说明请改用点击弹窗（空间更足）。
 */
export function InfoTooltip({
  content,
  children,
  className,
  side = "top",
  align = "center",
  triggerClassName,
}: {
  content: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  side?: "top" | "bottom";
  // 水平对齐初始偏好：center 居中（默认）/ start 左缘贴触发点 / end 右缘贴触发点；越界自动夹紧。
  align?: "center" | "start" | "end";
  // 触发元素（button）的样式覆盖：默认是图标灰字热区；文字触发（如截断商品名）可传入正常文字样式。
  triggerClassName?: string;
}) {
  const [open, setOpen] = React.useState(false);
  const [pos, setPos] = React.useState<{ top: number; left: number } | null>(null);
  const triggerRef = React.useRef<HTMLSpanElement>(null);
  const bubbleRef = React.useRef<HTMLDivElement>(null);

  const M = 8; // 视口边距 + 触发点与气泡间距

  const placeBubble = React.useCallback(() => {
    const el = triggerRef.current;
    const bub = bubbleRef.current;
    if (!el || !bub) return;
    const r = el.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const bw = bub.offsetWidth;
    const bh = bub.offsetHeight;

    // 垂直：按 side 偏好，空间不足则翻转到另一侧
    let placement = side;
    if (side === "top" && r.top < bh + M * 2) placement = "bottom";
    else if (side === "bottom" && vh - r.bottom < bh + M * 2) placement = "top";
    const top = placement === "top" ? r.top - bh - M : r.bottom + M;

    // 水平：按 align 取锚点，再夹紧到视口内（留 M 边距）
    let left: number;
    if (align === "start") left = r.left;
    else if (align === "end") left = r.right - bw;
    else left = r.left + r.width / 2 - bw / 2;
    left = Math.max(M, Math.min(left, vw - bw - M));

    setPos({ top, left });
  }, [side, align]);

  // 打开后实测气泡尺寸再定位（首帧渲染在屏外，paint 前同步归位，无跳动）
  React.useLayoutEffect(() => {
    if (open) placeBubble();
    else setPos(null);
  }, [open, placeBubble]);

  React.useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (triggerRef.current && !triggerRef.current.contains(e.target as Node)) setOpen(false);
    };
    // 任意容器滚动（capture 捕获内部 overflow 滚动）/ 视口变化 → 重新定位
    const reposition = () => placeBubble();
    document.addEventListener("click", onDocClick);
    window.addEventListener("resize", reposition);
    window.addEventListener("scroll", reposition, true);
    return () => {
      document.removeEventListener("click", onDocClick);
      window.removeEventListener("resize", reposition);
      window.removeEventListener("scroll", reposition, true);
    };
  }, [open, placeBubble]);

  return (
    <span
      ref={triggerRef}
      className={cn("relative inline-flex items-center", className)}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        aria-label="说明"
        // -m-1 p-1：放大触控热区（移动端老板手指点得到）而不撑动行内排版。
        className={cn(
          "-m-1 inline-flex items-center p-1 text-foreground-secondary transition-colors hover:text-foreground focus:outline-none",
          triggerClassName,
        )}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
      >
        {children}
      </button>
      {open &&
        createPortal(
          <span
            ref={bubbleRef}
            role="tooltip"
            style={{ top: pos?.top ?? -9999, left: pos?.left ?? -9999 }}
            className="pointer-events-none fixed z-[70] w-56 max-w-[calc(100vw-1rem)] rounded-lg border border-border-shallow bg-background px-3 py-2 text-xs leading-relaxed text-foreground shadow-lg"
          >
            {content}
          </span>,
          document.body,
        )}
    </span>
  );
}
