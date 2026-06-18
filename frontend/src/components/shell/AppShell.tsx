import { useEffect, useState } from "react";
import { Outlet, useOutletContext } from "react-router-dom";
import { api, type Me } from "@/api";
import { SideNav } from "./SideNav";
import { TopBar } from "./TopBar";

export interface ShellContext {
  me: Me | null;
}

// 页面通过此 hook 拿登录身份（AppShell 统一拉取，避免每页重复请求）。
export function useMe(): Me | null {
  return useOutletContext<ShellContext>().me;
}

export function AppShell() {
  const [me, setMe] = useState<Me | null>(null);
  const [drawer, setDrawer] = useState(false);

  useEffect(() => {
    // 401 由 api 层跳飞书登录；这里只管成功态。
    api.me().then(setMe).catch(() => {});
  }, []);

  return (
    <div className="flex h-full">
      <aside className="hidden shrink-0 md:block">
        <SideNav me={me} />
      </aside>

      {drawer && (
        <div className="fixed inset-0 z-40 md:hidden">
          <div className="absolute inset-0 bg-black/40" onClick={() => setDrawer(false)} />
          <div className="absolute left-0 top-0 h-full animate-fade-in">
            <SideNav me={me} onNavigate={() => setDrawer(false)} />
          </div>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar me={me} onMenu={() => setDrawer(true)} />
        <main className="min-h-0 flex-1 overflow-hidden">
          <Outlet context={{ me } satisfies ShellContext} />
        </main>
      </div>
    </div>
  );
}
