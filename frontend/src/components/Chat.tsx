import { useEffect, useRef } from "react";
import type { Message } from "../api";
import { Markdown } from "./Markdown";

const PRESETS = [
  "最近7天整体经营情况怎么样？",
  "今天的 GMV 和订单数是多少？",
  "近30天卖得最好的 10 个商品",
  "有哪些 SKU 快断货了？",
  "现在有多少待发货订单，有超时的吗？",
];

interface Props {
  messages: Message[];
  liveText: string;
  toolStatus: string | null;
  streaming: boolean;
  error: string | null;
  onSend: (text: string) => void;
  inputRef: React.RefObject<HTMLTextAreaElement>;
}

export function Chat({ messages, liveText, toolStatus, streaming, error, onSend, inputRef }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, liveText, toolStatus]);

  const isEmpty = messages.length === 0 && !streaming;

  function submit() {
    const el = inputRef.current;
    if (!el) return;
    const text = el.value.trim();
    if (!text || streaming) return;
    el.value = "";
    el.style.height = "auto";
    onSend(text);
  }

  return (
    <main className="chat">
      <div className="messages" ref={scrollRef}>
        {isEmpty && (
          <div className="welcome">
            <h2>问我店铺经营数据</h2>
            <p>试试这些：</p>
            <div className="presets">
              {PRESETS.map((p) => (
                <button key={p} className="preset" onClick={() => onSend(p)}>{p}</button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m, i) => (
          <div key={m.id ?? i} className={"msg " + m.role}>
            <div className="bubble">
              {m.role === "assistant" ? <Markdown text={m.content} /> : m.content}
            </div>
          </div>
        ))}

        {streaming && (
          <div className="msg assistant">
            <div className="bubble">
              {toolStatus && <div className="tool-pill">🔧 {toolStatus}</div>}
              {liveText ? <Markdown text={liveText} /> : (!toolStatus && <span className="typing">思考中…</span>)}
            </div>
          </div>
        )}

        {error && <div className="msg-error">⚠️ {error}</div>}
      </div>

      <div className="composer">
        <textarea
          ref={inputRef}
          placeholder="输入问题，Enter 发送，Shift+Enter 换行"
          rows={1}
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
        <button className="send-btn" disabled={streaming} onClick={submit}>
          {streaming ? "…" : "发送"}
        </button>
      </div>
    </main>
  );
}
