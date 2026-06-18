import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import { ThemeProvider } from "./theme";
import "./index.css";

// base=/app/（vite）⇒ 路由 basename=/app，客户端路径 /board 实际 URL 为 /app/board。
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ThemeProvider>
      <BrowserRouter basename="/app">
        <App />
      </BrowserRouter>
    </ThemeProvider>
  </React.StrictMode>,
);
