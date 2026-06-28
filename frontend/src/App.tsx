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

// iOS 26 Safari 底部工具栏只在「整页加载 / 真实滚动合成」时采样底部 fixed 诱饵；SPA 软导航
// （侧栏点页）后不重新采样 → 沿用切页前的工具栏色或回落灰（现象：直接访问/刷新正常、侧栏切入发灰）。
// 修：路由变化后做一次极小的程序化滚动（下移 1px 再回顶），逼 Safari 重新合成工具栏后的像素并
// 重采样诱饵。延时 360ms 等移动侧栏滑出动画（300ms）结束，避免抽屉还在屏内时被采样污染。
// 视觉上 1px 抖动不可察。比重插 DOM 可靠（实测重插无效）。
function useToolbarTintRepaint() {
  const { pathname } = useLocation();
  useEffect(() => {
    const t = setTimeout(() => {
      const y = window.scrollY;
      window.scrollTo(0, y + 1);
      requestAnimationFrame(() => window.scrollTo(0, y));
    }, 360);
    return () => clearTimeout(t);
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
