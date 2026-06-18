import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 同源托管：构建产物挂在 FastAPI 的 /app 下，故 base=/app/。
// 开发时 dev server 把 /api、/board 代理到本地 FastAPI（默认 8000）；
// 可用 VITE_API_PROXY 覆盖代理目标，避开本机其它占用 8000 的项目。
const apiTarget = process.env.VITE_API_PROXY ?? "http://127.0.0.1:8000";

export default defineConfig({
  base: "/app/",
  plugins: [react()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": apiTarget,
      "/board": apiTarget,
    },
  },
});
