"""Internal access control for the skill-facing data API."""

import secrets

from fastapi import Header, HTTPException

from core.config import settings


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
