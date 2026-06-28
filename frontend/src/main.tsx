import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import "./index.css";

// base=/app/（vite）⇒ 路由 basename=/app，客户端路径 /board 实际 URL 为 /app/board。
// 单浅色主题（贴 StoreClaw），无主题 Provider。
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter basename="/app">
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);

// iOS Safari 首屏 quirk：SPA 首次渲染时底部/顶部系统栏不读 theme-color（呈默认黑灰），
// 要到一次重绘后才采样（之前开关弹窗切 body.overflow 恰好触发→那之后才变对）。
// 这里挂载后主动「改一下再改回」theme-color，制造一次真实属性变更强制 Safari 重新采样。
// 设成同值 Safari 可能忽略，故先置临时值、下一帧再还原。
requestAnimationFrame(() => {
  const meta = document.querySelector('meta[name="theme-color"]');
  if (!meta) return;
  const v = meta.getAttribute("content") || "#f7f7f3";
  meta.setAttribute("content", "#f7f7f2"); // 临时差一点的值，触发变更
  requestAnimationFrame(() => meta.setAttribute("content", v)); // 下一帧还原
});

