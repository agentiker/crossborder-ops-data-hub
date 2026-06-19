import { useState } from "react";
import { Check, Copy } from "lucide-react";
import type { ThinkingStep } from "@/api";
import { Markdown } from "@/components/Markdown";
import { ThinkingSteps } from "./ThinkingSteps";

// 单条消息。用户=右对齐气泡；助手=无气泡裸文 + 可折叠工具轨迹（StoreClaw 风）。
// hover 时露出时间戳 + 复制按钮（fork ChatMessage 同款交互）。
// 流式进行时 isStreaming=true，给助手末尾接脉冲光标。
export function Bubble({
  role,
  content,
  steps,
  ts,
  workedMs,
  isStreaming,
}: {
  role: string;
  content: string;
  steps?: ThinkingStep[];
  ts?: string;
  workedMs?: number;
  isStreaming?: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const isUser = role === "user";

  function copy() {
    navigator.clipboard?.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  if (isUser) {
    return (
      <div className="mb-8 flex justify-end">
        <div className="group/u flex max-w-[85%] flex-col items-end gap-1">
          <div className="whitespace-pre-wrap break-words rounded-2xl rounded-br-none border border-border-shallow bg-card px-4 py-2.5 text-sm leading-6 shadow-sm">
            {content}
          </div>
          <div className="flex items-center gap-1.5 text-foreground-tertiary opacity-0 transition-opacity group-hover/u:opacity-100">
            {ts && <span className="text-xs">{ts}</span>}
            <button
              onClick={copy}
              className="inline-flex items-center rounded-full p-1 transition-colors hover:bg-fill hover:text-foreground"
              aria-label="复制"
            >
              {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="group/a mb-8">
      {steps && steps.length > 0 && (
        <ThinkingSteps steps={steps} live={isStreaming} workedMs={workedMs} />
      )}
      <div className="text-sm leading-7">
        <Markdown text={content} />
        {isStreaming && content.length > 0 && (
          <span className="ml-0.5 inline-block h-4 w-1.5 translate-y-0.5 animate-pulse bg-foreground align-middle" />
        )}
      </div>
      {!isStreaming && content.length > 0 && (
        <div className="mt-1 flex items-center gap-1.5 text-foreground-tertiary opacity-0 transition-opacity group-hover/a:opacity-100">
          {ts && <span className="text-xs">{ts}</span>}
          <button
            onClick={copy}
            className="inline-flex items-center rounded-full p-1 transition-colors hover:bg-fill hover:text-foreground"
            aria-label="复制"
          >
            {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
          </button>
        </div>
      )}
    </div>
  );
}
