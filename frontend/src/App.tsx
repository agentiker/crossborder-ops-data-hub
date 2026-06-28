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

// iOS 26 Safari 底部工具栏只在「整页加载/导航」时采样底部 fixed 诱饵元素；SPA 软导航（侧栏点页）
// 不重新采样 → 沿用切页前的工具栏色或回落灰（现象：直接访问/刷新正常、侧栏切入发灰）。
// 修：每次路由变化把诱饵 #ios-toolbar-tint 短暂移出再放回 DOM，逼 Safari 在下一帧重渲染时重采样。
function useToolbarTintRepaint() {
  const { pathname } = useLocation();
  useEffect(() => {
    const el = document.getElementById("ios-toolbar-tint");
    if (!el) return;
    // 重新插入 DOM = 一次真实的元素增删，比改样式更能触发 Safari 工具栏重采样。
    const parent = el.parentNode;
    const next = el.nextSibling;
    el.remove();
    const id = requestAnimationFrame(() => parent?.insertBefore(el, next));
    return () => cancelAnimationFrame(id);
  }, [pathname]);
}

// 应用外壳 + 路由（plan/15）。所有页面挂在 AppShell 的 Outlet 下，共用 me/会话列表。
export function App() {
  useToolbarTintRepaint();
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
