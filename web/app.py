"""FastAPI application entry point."""

from fastapi import Depends, FastAPI
from fastapi_mcp import FastApiMCP

from web.routes.auth import router as auth_router
from web.routes.data import router as data_router
from web.security import require_internal_token

app = FastAPI(
    title="Crossborder Ops Data Hub",
    description="跨境电商运营数据中台 API",
    version="0.1.0",
)

app.include_router(auth_router, prefix="/auth", tags=["认证"])
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


# ── MCP 服务（方案 C：同进程暴露只读数据工具给 openclaw） ──────────────────────
# fastapi-mcp 用 ASGITransport 进程内调用底层路由（无额外 HTTP 跳），并复用
# /api/data 的 require_internal_token 依赖。openclaw 在 MCP 请求头携带
# X-Internal-Token，经 headers 白名单转发到底层依赖完成鉴权。
# include_operations 白名单仅暴露 8 个 ops_* live 工具；profit/alerts（503）不暴露。
# scope binding 只暴露写工具 ops_set_scope_binding；读由数据端点服务端自动注入，不再单列 GET 工具。
mcp = FastApiMCP(
    app,
    name="Data Hub",
    include_operations=[
        "ops_overview",
        "ops_inventory",
        "ops_products",
        "ops_orders_summary",
        "ops_orders_trend",
        "ops_top_skus",
        "ops_scopes",
        "ops_set_scope_binding",
    ],
    headers=["x-internal-token"],
)
mcp.mount_http()  # streamable-http，挂在 /mcp
