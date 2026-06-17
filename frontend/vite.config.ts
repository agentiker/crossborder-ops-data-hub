import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 同源托管：构建产物挂在 FastAPI 的 /app 下，故 base=/app/。
// 开发时 dev server 把 /api、/board 代理到本地 FastAPI（8000），免 CORS。
export default defineConfig({
  base: "/app/",
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/board": "http://127.0.0.1:8000",
    },
  },
});
