import { lazy, useEffect } from "react";
import { Routes, Route, useLocation } from "react-router-dom";
import { AppShell } from "@/components/shell/AppShell";
import { ChatPage } from "@/pages/ChatPage";

// 看板（含 echarts，~580KB）等按需懒加载：首页对话不为它们的体积买单。
const BoardPage = lazy(() => import("@/pages/BoardPage").then((m) => ({ default: m.BoardPage })));
const AdminPage = lazy(() => import("@/pages/AdminPage").then((m) => ({ default: m.AdminPage })));
const SkillsPage = lazy(() => import("@/pages/SkillsPage").then((m) => ({ default: m.SkillsPage })));
const ScheduledPage = lazy(() =>
  import("@/pages/ScheduledPage").then((m) => ({ default: m.ScheduledPage })),
);

// iOS Safari 对 SPA 既不在首屏、也不在路由切换后自动重读 theme-color（系统栏回落黑灰）。
// 每次路由变化「改一下再改回」theme-color，强制 Safari 重新采样，保证每页系统栏都贴页面色。
function useThemeColorRepaint() {
  const { pathname } = useLocation();
  useEffect(() => {
    const meta = document.querySelector('meta[name="theme-color"]');
    if (!meta) return;
    const v = meta.getAttribute("content") || "#f7f7f3";
    meta.setAttribute("content", "#f7f7f2"); // 临时差一点的值，触发真实变更
    const id = requestAnimationFrame(() => meta.setAttribute("content", v));
    return () => cancelAnimationFrame(id);
  }, [pathname]);
}

// 应用外壳 + 路由（plan/15）。所有页面挂在 AppShell 的 Outlet 下，共用 me/会话列表。
export function App() {
  useThemeColorRepaint();
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<ChatPage />} />
        <Route path="c/:id" element={<ChatPage />} />
        <Route path="skills" element={<SkillsPage />} />
        <Route path="scheduled" element={<ScheduledPage />} />
        <Route path="board" element={<BoardPage />} />
        <Route path="admin" element={<AdminPage />} />
        <Route path="*" element={<ChatPage />} />
      </Route>
    </Routes>
  );
}
