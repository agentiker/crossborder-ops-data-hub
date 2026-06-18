import { Suspense, useEffect, useState } from "react";
import { Outlet, useOutletContext } from "react-router-dom";
import { api, type Me } from "@/api";
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

  useEffect(() => {
    // 401 由 api 层跳飞书登录；这里只管成功态。
    api.me().then(setMe).catch(() => {});
  }, []);

  // 导航上移到顶部：外壳只剩 TopBar + 内容区；左列由对话页自带会话列表承载。
  return (
    <div className="flex h-full flex-col">
      <TopBar me={me} />
      <main className="min-h-0 flex-1 overflow-hidden">
        <Suspense
          fallback={
            <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
              加载中…
            </div>
          }
        >
          <Outlet context={{ me } satisfies ShellContext} />
        </Suspense>
      </main>
    </div>
  );
}
