import { useRef } from "react";
import { ArrowUp } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

// 命令栏输入（首页居中大号 / 对话底部 docked 同款）。
// 自管 textarea，提交后清空。三态阴影/圆角照搬 fork ChatInput（默认/hover/focus 渐深）。
export function Composer({
  onSend,
  streaming,
  autoFocus,
  size = "docked",
}: {
  onSend: (text: string) => void;
  streaming: boolean;
  autoFocus?: boolean;
  size?: "home" | "docked";
}) {
  const ref = useRef<HTMLTextAreaElement>(null);

  function submit() {
    const el = ref.current;
    if (!el) return;
    const text = el.value.trim();
    if (!text || streaming) return;
    el.value = "";
    el.style.height = "auto";
    onSend(text);
  }

  return (
    <div
      className={cn(
        "flex items-end gap-2 rounded-2xl border border-transparent bg-card transition-shadow duration-200",
        // 三态阴影（fork README 精确值，描边色用本仓库 token 的 rgba 直引）：
        // 默认细描边 → hover 描边加深 → focus 投影更明显。
        "shadow-[0_.25rem_1.25rem_hsl(0_0%_0%_/3.5%),0_0_0_.5px_var(--border-shallow)]",
        "hover:shadow-[0_.25rem_1.25rem_hsl(0_0%_0%_/3.5%),0_0_0_.5px_var(--border)]",
        "focus-within:shadow-[0_.25rem_1.25rem_hsl(0_0%_0%_/7.5%),0_0_0_.5px_var(--border-deep)]",
        size === "home" ? "px-3 py-2.5" : "px-3 py-2",
      )}
    >
      <textarea
        ref={ref}
        rows={1}
        autoFocus={autoFocus}
        placeholder="问我店铺经营数据，Enter 发送，Shift+Enter 换行"
        className={cn(
          "max-h-40 flex-1 resize-none bg-transparent px-1.5 py-1.5 text-sm leading-6 placeholder:text-foreground-tertiary focus-visible:outline-none",
          size === "home" && "text-base",
        )}
        onInput={(e) => {
          const t = e.currentTarget;
          t.style.height = "auto";
          t.style.height = Math.min(t.scrollHeight, 160) + "px";
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            submit();
          }
        }}
      />
      {size === "home" ? (
        <Button className="h-10 shrink-0 gap-1.5 rounded-xl px-4" disabled={streaming} onClick={submit}>
          发送
          <ArrowUp className="size-4" />
        </Button>
      ) : (
        <Button
          size="icon"
          className="h-9 w-9 shrink-0 rounded-xl"
          disabled={streaming}
          onClick={submit}
          aria-label="发送"
        >
          <ArrowUp className="size-4" />
        </Button>
      )}
    </div>
  );
}
