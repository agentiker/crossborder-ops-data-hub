import { useState } from "react";
import { Copy, Check, ChevronDown, ChevronRight, FileCode, Zap, Terminal, Database, Upload, Send } from "lucide-react";
import { Markdown } from "@/components/Markdown";

// 照搬 forkStoreClaw/src/components/Chat/ChatMessage.tsx：
// 用户气泡（三圆角 + hover 时间戳/复制）、助手裸文、工作耗时折叠区（左竖线 + 分类图标 +
// 前 3 步 + Show more + 完成绿勾）、流式脉冲光标——版式/类名 1:1。
// 三处按本项目落差替换：
//   ①渲染：助手正文走自家 marked 版 <Markdown/>，不用 fork 的正则 formatMarkdown。
//   ①数据：thinkingSteps 来自真实 SSE（ChatPage 适配），不再注入 fork 的 mock defaultSteps；
//          我方步骤无「thinking 首行」语义，故不 slice(1)、不渲染 fork 的斜体分析行（不造假）。
//   交互：新增 defaultThinkingOpen（流式中默认展开过程），其余照搬。
export interface ThinkingStep {
  type: "thinking" | "skill" | "api" | "command" | "file" | "action";
  label: string;
  detail?: string;
  status?: "running" | "done" | "error";
}

interface ChatMessageProps {
  role: "user" | "assistant";
  content: string;
  timestamp?: string;
  workingTime?: string;
  thinkingSteps?: ThinkingStep[];
  files?: { name: string; type: string }[];
  isStreaming?: boolean;
  defaultThinkingOpen?: boolean;
}

const stepIcons: Record<string, React.ReactNode> = {
  thinking: <Zap className="h-3.5 w-3.5" />,
  skill: <Zap className="h-3.5 w-3.5 text-blue-500" />,
  api: <Database className="h-3.5 w-3.5 text-green-500" />,
  command: <Terminal className="h-3.5 w-3.5 text-purple-500" />,
  file: <Upload className="h-3.5 w-3.5 text-orange-500" />,
  action: <Send className="h-3.5 w-3.5 text-teal-500" />,
};

export function ChatMessage({
  role,
  content,
  timestamp,
  workingTime,
  thinkingSteps,
  files,
  isStreaming = false,
  defaultThinkingOpen = false,
}: ChatMessageProps) {
  const [copied, setCopied] = useState(false);
  const [showThinking, setShowThinking] = useState(defaultThinkingOpen);
  const [showMore, setShowMore] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  if (role === "user") {
    return (
      <div className="flex justify-end" style={{ contain: "paint" }}>
        <div className="group/user-turn flex w-full flex-col items-end gap-1 px-2 max-w-[90%]">
          <div className="max-w-full rounded-tl-2xl rounded-tr-2xl rounded-bl-2xl px-3.5 py-2 text-[16px] min-w-14 leading-7 text-foreground bg-white border border-muted whitespace-pre-wrap break-words">
            {content}
          </div>
          <div className="flex items-center gap-1.5 text-[#6A7280] opacity-0 transition-opacity group-hover/user-turn:opacity-100">
            {timestamp && (
              <span className="text-xs" title={timestamp}>{timestamp}</span>
            )}
            <button
              onClick={handleCopy}
              className="inline-flex items-center rounded-full p-1 transition-colors hover:bg-white/70 hover:text-[#0A0F1A]"
            >
              {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
            </button>
          </div>
        </div>
      </div>
    );
  }

  const steps = thinkingSteps || [];
  const visibleSteps = showMore ? steps : steps.slice(0, 3);

  return (
    <div className="space-y-1 group" style={{ contain: "paint", opacity: 1, transform: "none" }}>
      <div className="group/assistant-content min-w-0 px-2 space-y-2 text-[15px] leading-7 text-[#0A0F1A]">
        {/* Working time indicator */}
        {workingTime && (
          <div className="space-y-2">
            <div className="flex items-center gap-2 group/working-header -mb-3">
              <button
                type="button"
                onClick={() => setShowThinking(!showThinking)}
                className="flex items-center gap-1.5 py-1 chat-thinking-section text-foreground-secondary transition-colors hover:text-foreground cursor-pointer"
              >
                <span>{workingTime}</span>
                <ChevronDown
                  className={`h-3.5 w-3.5 opacity-50 transition-transform duration-200 ${showThinking ? "rotate-180" : ""}`}
                />
              </button>
            </div>

            {/* Expanded thinking steps */}
            {showThinking && steps.length > 0 && (
              <div className="ml-2 pl-3 border-l-2 border-border-shallow space-y-1.5 animate-fade-up">
                {/* Steps */}
                {visibleSteps.map((step, index) => (
                  <div key={index} className="flex items-start gap-2 group/step">
                    <div className="flex items-center gap-2 flex-1 min-w-0">
                      <span className="flex-shrink-0 mt-0.5">
                        {stepIcons[step.type] || <Terminal className="h-3.5 w-3.5" />}
                      </span>
                      <div className="flex-1 min-w-0">
                        <div className="text-sm font-medium text-foreground truncate">
                          {step.label}
                        </div>
                        {step.detail && (
                          <div className="text-xs text-foreground-tertiary truncate">
                            {step.detail}
                          </div>
                        )}
                      </div>
                    </div>
                    {step.status === "done" && (
                      <Check className="h-3.5 w-3.5 text-green-500 flex-shrink-0" />
                    )}
                  </div>
                ))}

                {/* Show more button */}
                {steps.length > 3 && !showMore && (
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      setShowMore(true);
                    }}
                    className="flex items-center gap-1 text-xs text-foreground-secondary hover:text-foreground transition-colors mt-1"
                  >
                    <ChevronRight className="h-3 w-3" />
                    展开全部
                  </button>
                )}
              </div>
            )}
          </div>
        )}

        {/* Content */}
        <div className="max-w-[768px] !mt-3 !mb-3">
          <div className="text-foreground">
            <Markdown text={content} />
          </div>
          {isStreaming && content.length > 0 && (
            <span className="inline-block w-2 h-5 bg-foreground animate-pulse ml-0.5" />
          )}
        </div>

        {/* File attachments */}
        {files && files.length > 0 && (
          <div className="max-w-[768px] space-y-2 !mt-3 !mb-3">
            {files.map((file, index) => (
              <span key={index} className="group block">
                <div className="flex items-center gap-3 rounded-2xl border border-border bg-card px-5 py-5 cursor-pointer transition-shadow duration-150 hover:shadow-md">
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-teal-50">
                    <FileCode className="shrink-0 h-6 w-6 text-teal-600" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-base font-medium text-foreground">{file.name}</div>
                  </div>
                  <button className="inline-flex items-center gap-1 justify-center whitespace-nowrap rounded-lg text-foreground font-bold h-8 px-3 text-sm hover:opacity-90 transition-opacity">
                    查看
                  </button>
                </div>
              </span>
            ))}
          </div>
        )}

        {/* Action buttons (visible on hover) */}
        {!isStreaming && content.length > 0 && (
          <div className="px-2 opacity-0 group-hover:opacity-100 transition-opacity">
            <div className="flex items-center gap-1">
              <button
                onClick={handleCopy}
                className="inline-flex items-center rounded-full p-1.5 transition-colors hover:bg-fill-default text-foreground-secondary hover:text-foreground"
              >
                {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
