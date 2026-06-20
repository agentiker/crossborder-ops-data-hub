import { Suspense, useCallback, useEffect, useState } from "react";
import { Menu } from "lucide-react";
import { Outlet, useOutletContext } from "react-router-dom";
import { api, type ConversationItem, type Me } from "@/api";
import { cn } from "@/lib/utils";
import { SidebarContent } from "./Sidebar";

export interface ShellContext {
  me: Me | null;
  conversations: ConversationItem[];
  refreshConversations: () => void;
}

// 页面通过这些 hook 拿外壳状态（AppShell 统一拉取）。仅在 Outlet 内的路由组件可用。
export function useMe(): Me | null {
  return useOutletContext<ShellContext>().me;
}
export function useShell(): ShellContext {
  return useOutletContext<ShellContext>();
}

// StoreClaw 式外壳：左固定 Sidebar（桌面）/ 抽屉（移动）+ 右内容区。
export function AppShell() {
  const [me, setMe] = useState<Me | null>(null);
  const [authError, setAuthError] = useState<string | null>(null);
  const [conversations, setConversations] = useState<ConversationItem[]>([]);
  const [mobileOpen, setMobileOpen] = useState(false);

  const refreshConversations = useCallback(() => {
    api.conversations().then((r) => setConversations(r.items)).catch(() => {});
  }, []);

  useEffect(() => {
    api.me().then(setMe).catch((e) => {
      // 401 由 api 层跳飞书登录（导航中），保持加载态；其余（403 待审批/未授权）展示服务端文案。
      const msg = e instanceof Error ? e.message : String(e);
      if (msg !== "unauthenticated") setAuthError(msg);
    });
    refreshConversations();
  }, [refreshConversations]);

  // 鉴权确认前只显示加载态/提示，不渲染受保护内容（避免 me=null 时先闪出老板首页）。
  if (me === null) {
    // 已登录但未获授权（待审批/未开通）：/api/me 返 403，展示服务端文案而非卡死在加载态。
    if (authError) {
      return (
        <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
          <div className="text-3xl">⏳</div>
          <p className="max-w-sm text-sm text-foreground-secondary">{authError}</p>
          <button
            onClick={() => window.location.reload()}
            className="rounded-full border border-border-shallow px-4 py-1.5 text-sm text-foreground-secondary hover:bg-fill"
          >
            刷新
          </button>
        </div>
      );
    }
    // 未登录时 api 层已触发跳飞书登录，此处保持加载态直到浏览器导航离开。
    return (
      <div className="flex h-full items-center justify-center text-sm text-foreground-tertiary">
        加载中…
      </div>
    );
  }

  return (
    <div className="grid h-full grid-cols-1 lg:grid-cols-[280px_minmax(0,1fr)]">
      {/* 桌面固定侧栏 */}
      <aside className="hidden border-r border-border-shallow bg-fill-shallow lg:flex lg:flex-col lg:overflow-hidden">
        <SidebarContent me={me} conversations={conversations} onRefresh={refreshConversations} />
      </aside>

      {/* 移动抽屉 */}
      {mobileOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/40 lg:hidden"
          onClick={() => setMobileOpen(false)}
        />
      )}
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-50 flex w-[280px] flex-col border-r border-border-shallow bg-background transition-transform duration-300 lg:hidden",
          mobileOpen ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <SidebarContent
          me={me}
          conversations={conversations}
          onRefresh={refreshConversations}
          onNavigate={() => setMobileOpen(false)}
        />
      </aside>

      <main className="flex min-h-0 flex-col overflow-hidden">
        {/* 移动顶栏 */}
        <div className="flex shrink-0 items-center gap-2 border-b border-border-shallow px-4 py-2.5 lg:hidden">
          <button
            onClick={() => setMobileOpen(true)}
            className="rounded-lg p-1.5 text-foreground-secondary hover:bg-fill"
            aria-label="菜单"
          >
            <Menu className="size-5" />
          </button>
          <span className="font-display font-semibold">数据中枢</span>
        </div>

        <div className="min-h-0 flex-1 overflow-hidden">
          <Suspense
            fallback={
              <div className="flex h-full items-center justify-center text-sm text-foreground-tertiary">
                加载中…
              </div>
            }
          >
            <Outlet context={{ me, conversations, refreshConversations } satisfies ShellContext} />
          </Suspense>
        </div>
      </main>
    </div>
  );
}
