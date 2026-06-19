import { CalendarClock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { scheduleLabel, type Template } from "./templates-data";

interface Props {
  template: Template;
  onUse: () => void;
}

// 模板卡（照 forkStoreClaw TemplateCard 风，去掉外链装饰图，用图标 + 纯色块）。
export function TemplateCard({ template, onUse }: Props) {
  return (
    <div className="flex min-w-[260px] flex-1 flex-col gap-4 overflow-hidden rounded-2xl border border-border-shallow bg-card p-5 shadow-sm transition-shadow hover:shadow-md">
      <div className="flex items-center gap-2 text-xs font-medium text-foreground-tertiary">
        <CalendarClock className="size-3.5" />
        {scheduleLabel(template.draft)}
      </div>
      <div className="flex-1">
        <h4 className="mb-1 text-base font-semibold text-foreground">{template.title}</h4>
        <p className="line-clamp-2 text-sm text-foreground-secondary">{template.description}</p>
      </div>
      <Button size="sm" className="self-start" onClick={onUse}>
        用这个
      </Button>
    </div>
  );
}
