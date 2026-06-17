import { useEffect, useRef, useState } from "react";
import { api, sendChat, type ConversationItem, type Me, type Message } from "./api";
import { Sidebar } from "./components/Sidebar";
import { Chat } from "./components/Chat";

const TOOL_LABELS: Record<string, string> = {
  ops_overview: "经营概览",
  ops_orders_summary: "订单汇总",
  ops_orders_trend: "订单趋势",
  ops_top_skus: "爆款榜",
  ops_low_stock: "断货风险",
  ops_fulfillments_pending: "待发货",
};

export function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [conversations, setConversations] = useState<ConversationItem[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [liveText, setLiveText] = useState("");
  const [toolStatus, setToolStatus] = useState<string | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // 首屏：拿身份（401 会在 api 层跳登录）+ 会话列表
  useEffect(() => {
    api.me().then(setMe).catch(() => {});
    refreshConversations();
  }, []);

  function refreshConversations() {
    api.conversations().then((r) => setConversations(r.items)).catch(() => {});
  }

  async function openConversation(id: number) {
    if (streaming) return;
    setActiveId(id);
    setError(null);
    setLiveText("");
    try {
      const detail = await api.conversation(id);
      setMessages(detail.messages);
    } catch {
      setMessages([]);
    }
  }

  function newConversation() {
    if (streaming) return;
    setActiveId(null);
    setMessages([]);
    setLiveText("");
    setError(null);
    inputRef.current?.focus();
  }

  async function deleteConversation(id: number) {
    await api.remove(id);
    if (id === activeId) newConversation();
    refreshConversations();
  }

  async function send(text: string) {
    if (streaming) return;
    setError(null);
    setMessages((m) => [...m, { role: "user", content: text }]);
    setStreaming(true);
    setLiveText("");
    setToolStatus(null);

    let convId = activeId;
    let acc = "";
    try {
      for await (const ev of sendChat(text, convId)) {
        if (ev.type === "meta") {
          convId = ev.conversation_id;
          if (activeId === null) setActiveId(convId);
        } else if (ev.type === "delta") {
          acc += ev.text;
          setLiveText(acc);
          setToolStatus(null);
        } else if (ev.type === "tool") {
          const label = TOOL_LABELS[ev.name] || ev.name;
          setToolStatus(ev.status === "running" ? `正在查询：${label}…` : null);
        } else if (ev.type === "error") {
          setError(ev.message);
        } else if (ev.type === "done") {
          convId = ev.conversation_id;
        }
      }
    } catch (e) {
      setError(String(e));
    }

    // 收尾：把流式文本固化为一条 assistant 消息
    setMessages((m) => [...m, { role: "assistant", content: acc }]);
    setLiveText("");
    setToolStatus(null);
    setStreaming(false);
    refreshConversations();
  }

  return (
    <div className="layout">
      <Sidebar
        me={me}
        conversations={conversations}
        activeId={activeId}
        onSelect={openConversation}
        onNew={newConversation}
        onDelete={deleteConversation}
      />
      <Chat
        messages={messages}
        liveText={liveText}
        toolStatus={toolStatus}
        streaming={streaming}
        error={error}
        onSend={send}
        inputRef={inputRef}
      />
    </div>
  );
}
