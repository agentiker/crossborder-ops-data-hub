import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { X } from "lucide-react";

// 全局单图灯箱：任意商品图点击后全屏看高清大图（点遮罩/X/Esc 关闭，不离开当前页）。
// 单实例挂在 SPA 根，各处用 useImageViewer().open(src, alt) 触发。
// 动画走 animate-fade-in（index.css 的 prefers-reduced-motion 全局降级已覆盖，无需额外处理）。

interface ImageViewerCtx {
  open: (src: string | undefined, alt?: string) => void;
}

const Ctx = createContext<ImageViewerCtx>({ open: () => {} });

export function useImageViewer() {
  return useContext(Ctx);
}

export function ImageViewerProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<{ src: string; alt: string } | null>(null);

  const open = useCallback((src: string | undefined, alt = "商品大图") => {
    if (!src) return; // 无图不开
    setState({ src, alt });
  }, []);

  const close = useCallback(() => setState(null), []);

  // Esc 关闭
  useEffect(() => {
    if (!state) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [state, close]);

  return (
    <Ctx.Provider value={{ open }}>
      {children}
      {state && (
        <div
          className="fixed inset-0 z-[60] flex animate-fade-in items-center justify-center bg-black/80 p-6"
          onClick={close}
          role="dialog"
          aria-modal="true"
          aria-label={state.alt}
        >
          <img
            src={state.src}
            alt={state.alt}
            className="max-h-[86vh] max-w-[92vw] rounded-2xl object-contain shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          />
          <button
            type="button"
            aria-label="关闭大图"
            onClick={close}
            className="absolute right-4 top-4 rounded-full bg-white/15 p-2 text-white backdrop-blur transition-colors hover:bg-white/25 [@media(pointer:coarse)]:p-3"
          >
            <X className="size-5" />
          </button>
        </div>
      )}
    </Ctx.Provider>
  );
}
