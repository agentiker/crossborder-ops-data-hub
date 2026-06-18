import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

// 浅/深双主题（plan/15 UI 地基）。真相在 <html> 的 .dark class + localStorage。
// 首屏防闪烁由 index.html 的内联脚本先行设置 class，本 Provider 仅接管后续切换。
type Theme = "light" | "dark";

const STORAGE_KEY = "ops-theme";

interface ThemeCtx {
  theme: Theme;
  toggle: () => void;
}

const Ctx = createContext<ThemeCtx | null>(null);

function currentFromDom(): Theme {
  return document.documentElement.classList.contains("dark") ? "dark" : "light";
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(() => currentFromDom());

  useEffect(() => {
    const root = document.documentElement;
    root.classList.toggle("dark", theme === "dark");
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      /* 隐私模式下 localStorage 可能抛错，忽略 */
    }
  }, [theme]);

  const toggle = () => setTheme((t) => (t === "dark" ? "light" : "dark"));

  return <Ctx.Provider value={{ theme, toggle }}>{children}</Ctx.Provider>;
}

export function useTheme(): ThemeCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useTheme 必须在 ThemeProvider 内使用");
  return v;
}
