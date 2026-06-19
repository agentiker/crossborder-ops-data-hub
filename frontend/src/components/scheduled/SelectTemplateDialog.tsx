import { CalendarClock } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { scheduleLabel, TEMPLATES, type ScheduledDraft } from "./templates-data";

interface Props {
  onClose: () => void;
  onPick: (draft: ScheduledDraft) => void;
}

export function SelectTemplateDialog({ onClose, onPick }: Props) {
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>从模板开始</DialogTitle>
          <DialogDescription>挑一个模板，会预填到创建表单，你可以再改。</DialogDescription>
        </DialogHeader>

        <div className="grid gap-2">
          {TEMPLATES.map((t) => (
            <button
              key={t.id}
              onClick={() => onPick(t.draft)}
              className="flex items-start gap-3 rounded-xl border border-border-shallow bg-card p-3 text-left transition-colors hover:border-border hover:bg-fill"
            >
              <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg bg-fill-deep">
                <CalendarClock className="size-4 text-foreground-secondary" />
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{t.title}</span>
                  <span className="text-xs text-foreground-tertiary">{scheduleLabel(t.draft)}</span>
                </div>
                <p className="mt-0.5 line-clamp-1 text-xs text-foreground-secondary">
                  {t.description}
                </p>
              </div>
            </button>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}
