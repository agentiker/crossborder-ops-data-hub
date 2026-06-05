"""FastAPI application entry point."""

from fastapi import Depends, FastAPI
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
