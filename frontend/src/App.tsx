import { lazy } from "react";
import { Routes, Route } from "react-router-dom";
import { AppShell } from "@/components/shell/AppShell";
import { ChatPage } from "@/pages/ChatPage";

// 看板（含 echarts，~580KB）与管理页按需懒加载：首页对话不为它们的体积买单。
const BoardPage = lazy(() => import("@/pages/BoardPage").then((m) => ({ default: m.BoardPage })));
const AdminPage = lazy(() => import("@/pages/AdminPage").then((m) => ({ default: m.AdminPage })));

// 应用外壳 + 路由（plan/15）。所有页面挂在 AppShell 的 Outlet 下，共用 me。
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
