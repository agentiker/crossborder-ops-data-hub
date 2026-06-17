import type { ConversationItem, Me } from "../api";

interface Props {
  me: Me | null;
  conversations: ConversationItem[];
  activeId: number | null;
  onSelect: (id: number) => void;
  onNew: () => void;
  onDelete: (id: number) => void;
}

export function Sidebar({ me, conversations, activeId, onSelect, onNew, onDelete }: Props) {
  return (
    <aside className="sidebar">
      <div className="sidebar-head">
        <span className="brand">运营助手</span>
        <button className="new-btn" onClick={onNew}>＋ 新会话</button>
      </div>

      <nav className="conv-list">
        {conversations.length === 0 && <div className="empty-hint">还没有会话</div>}
        {conversations.map((c) => (
          <div
            key={c.id}
            className={"conv-item" + (c.id === activeId ? " active" : "")}
            onClick={() => onSelect(c.id)}
          >
            <span className="conv-title">{c.title || "新会话"}</span>
            <button
              className="del-btn"
              title="删除"
              onClick={(e) => {
                e.stopPropagation();
                if (confirm("删除该会话？")) onDelete(c.id);
              }}
            >
              ×
            </button>
          </div>
        ))}
      </nav>

      <div className="sidebar-foot">
        {me && (
          <>
            <div className="user-role">{me.is_boss ? "老板" : "运营"} · {me.role}</div>
            <div className="user-scope" title={me.scope_label}>范围：{me.scope_label}</div>
          </>
        )}
        <a className="logout" href="/board/auth/feishu/logout">退出登录</a>
      </div>
    </aside>
  );
}
