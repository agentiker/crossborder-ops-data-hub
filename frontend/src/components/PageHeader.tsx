import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

// 页头：标题 + eyebrow 标签。eyebrow 编码真实信息（范围/时段/数据新鲜度），
// 不做装饰——看板/管理/对话页共用同一结构语言。
interface Props {
  title: string;
  scope?: string;
  period?: string;
  updatedAt?: string;
  actions?: ReactNode;
  className?: string;
}

function Eyebrow({ k, v }: { k: string; v: string }) {
  return (
    <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
      <span className="text-muted-foreground/70">{k}</span>
      <span className="font-medium text-foreground/80">{v}</span>
    </span>
  );
}

export function PageHeader({ title, scope, period, updatedAt, actions, className }: Props) {
  return (
    <div className={cn("flex flex-wrap items-center justify-between gap-3", className)}>
      <div className="min-w-0">
        <h1 className="font-display text-xl font-semibold tracking-tight">{title}</h1>
        {(scope || period || updatedAt) && (
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1">
            {scope && <Eyebrow k="范围" v={scope} />}
            {period && <Eyebrow k="时段" v={period} />}
            {updatedAt && <Eyebrow k="更新" v={updatedAt} />}
          </div>
        )}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}
