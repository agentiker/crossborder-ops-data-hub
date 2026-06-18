"""FastAPI application entry point."""

import os

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi_mcp import FastApiMCP

from web.routes.admin import router as admin_router
from web.routes.auth import router as auth_router
from web.routes.auth_feishu import router as auth_feishu_router
from web.routes.board import router as board_router
from web.routes.chat import router as chat_router
from web.routes.dashboard import router as dashboard_router
from web.routes.data import router as data_router
from web.security import require_internal_token
from web.web_security import register_web_auth_handlers

app = FastAPI(
    title="Crossborder Ops Data Hub",
    description="跨境电商运营数据中台 API",
    version="0.1.0",
)

app.include_router(auth_router, prefix="/auth", tags=["认证"])
# 看板 demo（plan/13）：仅本机预览，不带 internal token（靠 127.0.0.1 绑定保护），不纳入 OpenAPI/MCP
app.include_router(dashboard_router, tags=["看板"])
# 独立运营看板（plan/14）：飞书 OAuth 登录态 + user_authz 权限闸；公网经 cloudflared 只放行 /board*。
# 不带 internal token（鉴权靠登录 cookie），include_in_schema=False 故不入 OpenAPI/MCP。
app.include_router(auth_feishu_router, prefix="/board/auth/feishu", tags=["看板登录"])
app.include_router(board_router, tags=["运营看板"])
# Web 对话端（plan/15 Phase A）：自建 agent + 会话 API（/api/chat、/api/conversations、/api/me）。
# 鉴权用飞书 OAuth 登录 cookie（require_web_user_api，未登录返 401），不带 internal token，
# include_in_schema=False 故不入 OpenAPI/MCP（避免被 openclaw 当工具调用）。
app.include_router(chat_router, tags=["Web对话"])
# 角色权限可配置 admin API（plan/15 Phase C，boss-only）：管理 user_roles 真相源。
# 鉴权用飞书 OAuth 登录 cookie + boss 守卫（require_boss），不带 internal token，
# include_in_schema=False 故不入 OpenAPI/MCP（避免被 openclaw 当工具调用）。
app.include_router(admin_router)
register_web_auth_handlers(app)  # 装 WebAuthRedirect→302 / WebAuthForbidden→403 页
app.include_router(
    data_router,
    prefix="/api/data",
    tags=["数据查询"],
    dependencies=[Depends(require_internal_token)],
)


@app.get("/")
async def root():
    return {"message": "Crossborder Ops Data Hub API"}


@app.get("/health")
async def health():
    return {"status": "ok"}


# ── Web 对话端 SPA 同源托管（plan/15 Phase A）──────────────────────────────────
# 构建产物 frontend/dist 挂在 /app 下（html=True → /app 直出 index.html，SPA 资源在
# /app/assets/*，与 vite base=/app/ 对齐）。鉴权由 SPA 调 /api/me 触发（401 跳飞书登录），
# 故静态资源本身无需鉴权。dist 不存在（未构建）时跳过挂载，不影响后端启动。
_SPA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend", "dist")
if os.path.isdir(_SPA_DIR):
    app.mount("/app", StaticFiles(directory=_SPA_DIR, html=True), name="spa")


# ── MCP 服务（方案 C：同进程暴露只读数据工具给 openclaw） ──────────────────────
# fastapi-mcp 用 ASGITransport 进程内调用底层路由（无额外 HTTP 跳），并复用
# /api/data 的 require_internal_token 依赖。openclaw 在 MCP 请求头携带
# X-Internal-Token，经 headers 白名单转发到底层依赖完成鉴权。
# include_operations 白名单仅暴露 ops_* live 工具；profit/alerts（503）不暴露。
# scope binding 只暴露写工具 ops_set_scope_binding；读由数据端点服务端自动注入，不再单列 GET 工具。
mcp = FastApiMCP(
    app,
    name="Data Hub",
    include_operations=[
        "ops_overview",
        "ops_inventory",
        "ops_low_stock",
        "ops_products",
        "ops_orders_summary",
        "ops_orders_trend",
        "ops_top_skus",
        "ops_fulfillments_pending",
        "ops_scopes",
        "ops_set_scope_binding",
        "ops_dashboard_link",
    ],
    headers=["x-internal-token"],
)
mcp.mount_http()  # streamable-http，挂在 /mcp
