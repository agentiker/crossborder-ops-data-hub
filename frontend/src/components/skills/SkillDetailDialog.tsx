import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { ToolSkill } from "./skills-data";

interface Props {
  skill: ToolSkill;
  enabled: boolean;
  onToggle: (enabled: boolean) => void;
  onClose: () => void;
}

export function SkillDetailDialog({ skill, enabled, onToggle, onClose }: Props) {
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{skill.label}</DialogTitle>
          <DialogDescription className="font-mono">{skill.name}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4 text-sm">
          <p className="text-foreground-secondary">{skill.detail}</p>

          <div>
            <div className="mb-1 text-xs font-medium uppercase tracking-wide text-foreground-tertiary">
              可调参数
            </div>
            <p className="text-foreground-secondary">{skill.params}</p>
          </div>

          <div className="flex items-center justify-between rounded-lg border border-border-shallow bg-fill-shallow px-3 py-2.5">
            <div>
              <div className="font-medium">{enabled ? "已启用" : "已停用"}</div>
              <div className="text-xs text-foreground-tertiary">
                启用后，对话 AI 可调用此技能取数
              </div>
            </div>
            <button
              onClick={() => onToggle(!enabled)}
              role="switch"
              aria-checked={enabled}
              className={cn(
                "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors",
                enabled ? "bg-primary" : "border border-border bg-fill-deep",
              )}
            >
              <span
                className={cn(
                  "block size-5 rounded-full bg-white shadow-md transition-transform",
                  enabled ? "translate-x-[22px]" : "translate-x-0.5",
                )}
              />
            </button>
          </div>
        </div>

        <div className="mt-2 flex justify-end">
          <Button variant="outline" onClick={onClose}>
            关闭
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
