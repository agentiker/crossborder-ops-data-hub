import { useState, useRef, useEffect } from "react";
import { Send, Plus, FileText, Zap } from "lucide-react";

// 照搬 forkStoreClaw/src/components/Chat/ChatInput.tsx：
// 三态阴影（默认 / hover / focus-within）、自增高 textarea、+ 菜单、发送按钮态 1:1；
// 仅 ①数据接线：onSend 接真实 send()，新增 disabled（流式时禁用提交）；②placeholder 中文化。
interface ChatInputProps {
  onSend?: (message: string) => void;
  placeholder?: string;
  disabled?: boolean;
}

export function ChatInput({ onSend, placeholder = "问我店铺的经营数据……", disabled = false }: ChatInputProps) {
  const [message, setMessage] = useState("");
  const [showMenu, setShowMenu] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 300)}px`;
    }
  }, [message]);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setShowMenu(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const handleSubmit = () => {
    if (disabled) return;
    if (message.trim()) {
      onSend?.(message);
      setMessage("");
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const canSend = message.trim().length > 0 && !disabled;

  return (
    <div className="relative mx-auto w-full max-w-[820px] pb-0">
      <div className="relative bg-white rounded-2xl transition-all duration-200 border border-transparent shadow-[0_.25rem_1.25rem_hsl(0_0%_0%_/3.5%),0_0_0_.5px_hsla(30_3.3%_11.8%_/.15)] hover:shadow-[0_.25rem_1.25rem_hsl(0_0%_0%_/3.5%),0_0_0_.5px_hsla(30_3.3%_11.8%_/.3)] focus-within:shadow-[0_0.25rem_1.25rem_hsl(0_0%_0%_/7.5%),0_0_0_0.5px_hsla(30_3.3%_11.8%_/0.3)] hover:focus-within:shadow-[0_0.25rem_1.25rem_hsl(0_0%_0%_/7.5%),0_0_0_0.5px_hsla(30_3.3%_11.8%_/0.3)]">
        <div className="flex w-full flex-col p-4">
          <textarea
            ref={textareaRef}
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={placeholder}
            rows={2}
            className="w-full resize-none border-0 bg-transparent p-0 text-foreground placeholder:text-foreground-tertiary focus:outline-none focus:ring-0 text-[15px] leading-[22px] min-h-[44px]"
          />

          <div className="flex items-center justify-between mt-3">
            <div className="relative" ref={menuRef}>
              <button
                type="button"
                onClick={() => setShowMenu(!showMenu)}
                className="w-9 h-9 rounded-full border-2 border-border flex items-center justify-center text-foreground-secondary hover:text-foreground hover:border-foreground transition-colors"
              >
                <Plus size={18} />
              </button>

              {showMenu && (
                <div className="absolute top-full left-0 mt-2 w-48 bg-white rounded-xl shadow-lg border border-border py-1 z-50">
                  <button
                    onClick={() => {
                      setShowMenu(false);
                      console.log("File upload clicked");
                    }}
                    className="flex items-center gap-3 w-full px-3 py-2.5 text-sm text-foreground hover:bg-fill-default transition-colors"
                  >
                    <FileText size={18} className="text-foreground-secondary" />
                    <span>附件</span>
                  </button>
                  <button
                    onClick={() => {
                      setShowMenu(false);
                      console.log("Skill clicked");
                    }}
                    className="flex items-center gap-3 w-full px-3 py-2.5 text-sm text-foreground hover:bg-fill-default transition-colors"
                  >
                    <Zap size={18} className="text-foreground-secondary" />
                    <span>技能</span>
                  </button>
                </div>
              )}
            </div>

            <button
              onClick={handleSubmit}
              disabled={!canSend}
              className={`
                p-2.5 rounded-xl transition-all duration-200
                ${canSend
                  ? "bg-foreground text-white hover:opacity-90"
                  : "bg-fill-default text-foreground-tertiary cursor-not-allowed"
                }
              `}
            >
              <Send size={20} />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
