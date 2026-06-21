"""Internal access control for the skill-facing data API."""

import secrets

from fastapi import Header, HTTPException

from core.config import settings
from core.tenancy import DEFAULT_ACCOUNT, set_current_account


async def bind_account_context(
    x_account_id: str = Header(
        default="", alias="X-Account-Id", include_in_schema=False
    ),
) -> str:
    """把 openclaw 注入的 X-Account-Id 头写进请求级 contextvar（多租户）。

    挂在 /api/data 路由级依赖，每个数据/MCP 请求开头跑一次，下游 `_resolve_scope`、
    `scope_binding`、链接签发据此隔离租户。**必须是 async**：FastAPI 把 sync 依赖丢
    threadpool，其 contextvar.set 不回传父 context，async 依赖才与端点同 context。

    **只在头存在时才设 contextvar**：openclaw 这版不注入该头，则保持"未设定"，由下游
    `resolve_dialog_account` 按 open_id 反查 user_roles 定租户（见 plan/09 Phase 4 收尾）。
    """
    if x_account_id:
        set_current_account(x_account_id)
        return x_account_id
    return DEFAULT_ACCOUNT


async def require_internal_token(
    x_internal_token: str = Header(
        default="", alias="X-Internal-Token", include_in_schema=False
    ),
) -> None:
    """Reject /api/data calls that lack a valid internal token.

    The data API is meant to be reached only by local openclaw skills over
    127.0.0.1. We still require a shared token so other local processes cannot
    read business data by hitting the port directly.
    """
    expected = settings.api.internal_token
    if not expected:
        # Fail closed: refuse to serve data until a token is configured.
        raise HTTPException(status_code=503, detail="API__INTERNAL_TOKEN 未配置")
    if not secrets.compare_digest(x_internal_token, expected):
        raise HTTPException(status_code=401, detail="无效的内部令牌")
