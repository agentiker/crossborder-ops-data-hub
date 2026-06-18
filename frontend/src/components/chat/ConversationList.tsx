import { Plus, Trash2 } from "lucide-react";
import type { ConversationItem } from "@/api";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface Props {
  conversations: ConversationItem[];
  activeId: number | null;
  onSelect: (id: number) => void;
  onNew: () => void;
  onDelete: (id: number) => void;
}

export function ConversationList({ conversations, activeId, onSelect, onNew, onDelete }: Props) {
  return (
    <div className="hidden w-64 shrink-0 flex-col border-r bg-card/40 md:flex">
      <div className="p-3">
        <Button variant="outline" className="w-full justify-start gap-2" onClick={onNew}>
          <Plus className="size-4" />
          新会话
        </Button>
      </div>

      <nav className="flex-1 space-y-0.5 overflow-y-auto px-2 pb-3">
        {conversations.length === 0 && (
          <div className="px-3 py-2 text-xs text-muted-foreground">还没有会话</div>
        )}
        {conversations.map((c) => (
          <div
            key={c.id}
            onClick={() => onSelect(c.id)}
            className={cn(
              "group flex cursor-pointer items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors",
              c.id === activeId ? "bg-accent text-accent-foreground" : "hover:bg-accent/60",
            )}
          >
            <span className="flex-1 truncate">{c.title || "新会话"}</span>
            <button
              className="shrink-0 text-muted-foreground opacity-0 transition-opacity hover:text-destructive group-hover:opacity-100"
              title="删除"
              onClick={(e) => {
                e.stopPropagation();
                if (confirm("删除该会话？")) onDelete(c.id);
              }}
            >
              <Trash2 className="size-3.5" />
            </button>
          </div>
        ))}
      </nav>
    </div>
  );
}
