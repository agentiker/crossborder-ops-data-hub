import { useEffect, useRef, useState } from "react";
import { Clock, MoreHorizontal, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { scheduleLabel, type ScheduledTaskItem } from "./templates-data";

interface Props {
  task: ScheduledTaskItem;
  onToggle: () => void;
  onDelete: () => void;
}

// 任务卡（照 forkStoreClaw TaskCard 风）：状态徽章 + 频率 + iOS 开关 + 菜单删除，暂停态降透明度。
export function TaskCard({ task, onToggle, onDelete }: Props) {
  const [showMenu, setShowMenu] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setShowMenu(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  return (
    <div
      className={cn(
        "relative flex w-full items-center gap-4 rounded-xl border border-border-shallow bg-card px-5 py-5 text-card-foreground transition-shadow hover:border-border hover:shadow lg:w-[calc((100%-0.75rem)/2)]",
        !task.enabled && "opacity-60",
      )}
    >
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <div className="flex items-center gap-2">
          <h3 className="min-w-0 truncate text-base font-medium text-foreground">{task.name}</h3>
          <span
            className={cn(
              "text-nowrap rounded-md px-1.5 py-0.5 text-xs leading-4",
              task.enabled
                ? "bg-positive/15 text-positive"
                : "bg-fill text-foreground-secondary",
            )}
          >
            {task.enabled ? "运行中" : "已暂停"}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-x-6 gap-y-1">
          <span className="flex items-center gap-1.5 text-sm text-foreground-tertiary">
            <Clock className="size-3 shrink-0" />
            {scheduleLabel(task)}
          </span>
          {task.description && (
            <span className="min-w-0 truncate text-sm text-foreground-tertiary">
              {task.description}
            </span>
          )}
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-6">
        <button
          type="button"
          role="switch"
          aria-checked={task.enabled}
          aria-label={task.enabled ? "停用" : "启用"}
          onClick={onToggle}
          className={cn(
            "relative inline-flex h-[26px] w-[46px] shrink-0 cursor-pointer items-center rounded-full transition-all duration-300 ease-in-out",
            task.enabled ? "bg-primary" : "border border-border bg-fill-deep",
          )}
        >
          <span
            className={cn(
              "pointer-events-none block size-[22px] rounded-full bg-white shadow-md ring-0 transition-transform duration-300 ease-in-out",
              task.enabled ? "translate-x-[22px]" : "translate-x-[2px]",
            )}
          />
        </button>

        <div className="relative" ref={menuRef}>
          <button
            onClick={() => setShowMenu((s) => !s)}
            aria-label="更多"
            className="rounded-lg p-1 text-foreground-tertiary transition-colors hover:bg-fill hover:text-foreground"
          >
            <MoreHorizontal size={16} />
          </button>

          {showMenu && (
            <div className="absolute right-0 top-full z-20 mt-1 w-32 rounded-xl border border-border bg-card py-1 shadow-lg">
              <button
                onClick={() => {
                  setShowMenu(false);
                  onDelete();
                }}
                className="flex w-full items-center gap-2 px-3 py-2 text-sm text-destructive transition-colors hover:bg-fill"
              >
                <Trash2 size={14} />
                删除
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
