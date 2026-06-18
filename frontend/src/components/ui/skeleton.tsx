import type { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

// 加载占位骨架。reduced-motion 下由 tailwindcss-animate 的 pulse 自动降级。
export function Skeleton({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("animate-pulse rounded-md bg-muted", className)} {...props} />;
}
