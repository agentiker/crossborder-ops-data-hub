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
    未注入头 → 回落 DEFAULT_ACCOUNT（主租户），旧 openclaw / 内部调用零行为变更。
    """
    account_id = x_account_id or DEFAULT_ACCOUNT
    set_current_account(account_id)
    return account_id


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
