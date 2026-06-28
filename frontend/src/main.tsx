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


