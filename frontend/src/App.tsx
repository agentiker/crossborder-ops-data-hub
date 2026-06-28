import { lazy, useEffect, useRef } from "react";
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

// iOS 26 Safari 底部工具栏在 SPA 软导航（侧栏点页）后不重新采样 fixed 诱饵 → 沿用旧色/回退灰。
// 配合 CSS 顶部的 scroll runway（见 index.css @supports 块）：每次路由变化后 scrollTo 到 runway
// 偏移量（默认 62px，等于 CSS --safari-runway），让文档进入「非零滚动并合成」状态，逼 Safari
// 重新 composite 工具栏区域的像素并采样诱饵。scrollTo 后不滚回 0——回到 0 会再次触发回退色。
// 视觉副作用：切页后内容下移约一个工具栏高度（sticky 顶栏仍吸附顶部），用户上滑可看回顶。
// 非 iOS Safari 无 runway（CSS 未生效），scrollTo 到 0 = 无操作，零影响。
function useScrollRunwayRepaint() {
  const { pathname } = useLocation();
  const firstRef = useRef(true);
  useEffect(() => {
    // 首次挂载跳过：整页加载时 Safari 本就会采样诱饵（诱饵对首屏有效），无需下移。
    if (firstRef.current) {
      firstRef.current = false;
      return;
    }
    const runway = parseFloat(
      getComputedStyle(document.documentElement).getPropertyValue("--safari-runway"),
    ) || 0;
    if (runway > 0) {
      // 延时等懒加载页面挂载 + 移动侧栏滑出动画(300ms)结束，避免抽屉还在屏内污染采样。
      const t = setTimeout(() => window.scrollTo({ top: runway, left: 0, behavior: "instant" }), 360);
      return () => clearTimeout(t);
    }
  }, [pathname]);
}

// 应用外壳 + 路由（plan/15）。所有页面挂在 AppShell 的 Outlet 下，共用 me/会话列表。
export function App() {
  useScrollRunwayRepaint();
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
