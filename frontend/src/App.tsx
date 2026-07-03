import { lazy } from "react";
import { Routes, Route } from "react-router-dom";
import { AppShell } from "@/components/shell/AppShell";
import { ChatPage } from "@/pages/ChatPage";
import { ImageViewerProvider } from "@/components/ImageViewer";
import { useToolbarRepaint } from "@/lib/useToolbarRepaint";

// 看板（含 echarts，~580KB）等按需懒加载：首页对话不为它们的体积买单。
const BoardPage = lazy(() => import("@/pages/BoardPage").then((m) => ({ default: m.BoardPage })));
const AdminPage = lazy(() => import("@/pages/AdminPage").then((m) => ({ default: m.AdminPage })));
const BizConfigPage = lazy(() =>
  import("@/pages/BizConfigPage").then((m) => ({ default: m.BizConfigPage })),
);
const SkillsPage = lazy(() => import("@/pages/SkillsPage").then((m) => ({ default: m.SkillsPage })));
const ScheduledPage = lazy(() =>
  import("@/pages/ScheduledPage").then((m) => ({ default: m.ScheduledPage })),
);

// 应用外壳 + 路由（plan/15）。所有页面挂在 AppShell 的 Outlet 下，共用 me/会话列表。
export function App() {
  useToolbarRepaint();
  return (
    <ImageViewerProvider>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<ChatPage />} />
          <Route path="c/:id" element={<ChatPage />} />
          <Route path="skills" element={<SkillsPage />} />
          <Route path="scheduled" element={<ScheduledPage />} />
          <Route path="board" element={<BoardPage />} />
          <Route path="admin" element={<AdminPage />} />
          <Route path="settings" element={<BizConfigPage />} />
          <Route path="*" element={<ChatPage />} />
        </Route>
      </Routes>
    </ImageViewerProvider>
  );
}
