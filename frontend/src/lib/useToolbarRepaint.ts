import { useEffect } from "react";
import { useLocation } from "react-router-dom";

// iOS 26 Safari 底部工具栏「软导航后发灰」第 7 版尝试 —— 合成层翻转法。
//
// 背景：iOS 26 起 Safari 底部工具栏靠采样视口底边的 fixed 诱饵(#ios-toolbar-tint)上色。
// 整页加载会采样(同色 ✅)，但 SPA 软导航(侧栏点页)后不重采样 → 工具栏沿用旧色/回落灰。
// 已试失败：静态 theme-color / JS 重写 theme-color / 重插诱饵 DOM / 路由后微滚动 / scroll runway。
//
// 新角度(本版)：上述 hack 都没动过诱饵自身的「合成层」。这里在路由切换后强制诱饵脱离再
// 重建合成层 —— 翻转 transform(translateZ + 微 scale) 与 opacity，并在下一帧还原。合成层
// 重建会让 Safari 重新 composite 视口底边像素，从而(寄望)触发对诱饵色的重采样。
// 仅 iOS Safari 有诱饵(CSS @supports -webkit-touch-callout)，其余平台元素不存在 → 纯 no-op。
export function useToolbarRepaint() {
  const { pathname } = useLocation();
  useEffect(() => {
    const tint = document.getElementById("ios-toolbar-tint");
    if (!tint) return;
    // 延时等懒加载页面挂载 + 移动抽屉滑出动画(300ms)结束，避免抽屉还在屏内污染采样。
    const t = window.setTimeout(() => {
      // 翻转：建立独立合成层并微动，逼 Safari 重新 composite 底边。
      tint.style.willChange = "transform, opacity";
      tint.style.transform = "translateZ(0) scaleY(1.04)";
      tint.style.opacity = "0.99";
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          // 还原：合成层重建完成，恢复诱饵原态(CSS 默认值)。
          tint.style.transform = "";
          tint.style.opacity = "";
          tint.style.willChange = "";
        });
      });
    }, 360);
    return () => window.clearTimeout(t);
  }, [pathname]);
}
