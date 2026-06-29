import { CalendarClock, X } from "lucide-react";
import { scheduleLabel, TEMPLATES, type ScheduledDraft } from "./templates-data";

interface Props {
  onClose: () => void;
  onPick: (draft: ScheduledDraft) => void;
}

// 选模板弹窗（照 forkStoreClaw SelectTemplateDialog 风：手写 overlay + fade-up + 模板网格）。
export function SelectTemplateDialog({ onClose, onPick }: Props) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />

      <div className="relative flex max-h-[90vh] w-[713px] max-w-[90vw] animate-fade-up flex-col rounded-2xl bg-card shadow-lg">
        {/* Header */}
        <div className="flex min-h-[72px] items-center justify-between px-6 py-3">
          <div className="text-lg font-semibold leading-6 text-foreground">选择模板</div>
          <button
            onClick={onClose}
            aria-label="关闭"
            className="rounded-lg p-1 transition-colors hover:bg-fill"
          >
            <X size={20} className="text-foreground-secondary" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-6 pb-6">
          <p className="mb-4 text-sm text-foreground-secondary">
            挑一个模板，会预填到创建表单，你可以再改。
          </p>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {TEMPLATES.map((t) => (
              <div
                key={t.id}
                className="flex cursor-pointer flex-col rounded-xl border border-border bg-card p-4 transition-shadow hover:shadow-md"
                onClick={() => {
                  onPick(t.draft);
                  onClose();
                }}
              >
                <div className="mb-2 inline-flex items-center gap-1.5 text-xs font-medium text-foreground-tertiary">
                  <CalendarClock className="size-3.5" />
                  {scheduleLabel(t.draft)}
                </div>
                <h4 className="mb-1 text-sm font-semibold text-foreground">{t.title}</h4>
                <p className="mb-3 line-clamp-2 flex-1 text-xs text-foreground-secondary">
                  {t.description}
                </p>
                <div className="flex items-center justify-end">
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onPick(t.draft);
                      onClose();
                    }}
                    className="rounded-lg bg-primary px-3 py-1 text-xs font-medium text-primary-foreground transition-opacity hover:opacity-90"
                  >
                    使用
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
