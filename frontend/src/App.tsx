import { Routes, Route } from "react-router-dom";
import { AppShell } from "@/components/shell/AppShell";
import { ChatPage } from "@/pages/ChatPage";
import { BoardPage } from "@/pages/BoardPage";
import { AdminPage } from "@/pages/AdminPage";

// 应用外壳 + 路由（plan/15 UI 地基）。所有页面挂在 AppShell 的 Outlet 下，共用 me。
export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<ChatPage />} />
        <Route path="board" element={<BoardPage />} />
        <Route path="admin" element={<AdminPage />} />
        <Route path="*" element={<ChatPage />} />
      </Route>
    </Routes>
  );
}
