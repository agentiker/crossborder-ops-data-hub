"""Persistence helpers for raw API payloads and incremental cursors."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from models.base_models import RawAPIResponse, SyncCursor
from services.scoping import build_scope_key


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def record_raw_response(
    session,
    *,
    platform: str,
    resource: str,
    method: str,
    path: str,
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
    request_params: Optional[dict[str, Any]] = None,
    request_body: Optional[dict[str, Any]] = None,
    response_payload: Optional[dict[str, Any]] = None,
    http_status: Optional[int] = None,
    business_code: Optional[str] = None,
    error: Optional[str] = None,
) -> RawAPIResponse:
    """Persist a raw API response before transformation."""
    scope_key = build_scope_key(
        platform=platform,
        country=country,
        shop_id=shop_id,
        seller_id=seller_id,
        account_id=account_id,
        resource=resource,
    )
    record = RawAPIResponse(
        platform=platform,
        country=country,
        shop_id=shop_id,
        seller_id=seller_id,
        account_id=account_id,
        scope_key=scope_key,
        resource=resource,
        method=method.upper(),
        path=path,
        request_params=request_params,
        request_body=request_body,
        response_payload=response_payload,
        http_status=http_status,
        business_code=str(business_code) if business_code is not None else None,
        error=error,
        fetched_at=utcnow(),
    )
    session.add(record)
    session.flush()
    return record


def get_cursor(
    session,
    *,
    platform: str,
    resource: str,
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
) -> Optional[SyncCursor]:
    scope_key = build_scope_key(
        platform=platform,
        country=country,
        shop_id=shop_id,
        seller_id=seller_id,
        account_id=account_id,
        resource=resource,
    )
    return session.query(SyncCursor).filter_by(scope_key=scope_key).first()


def upsert_cursor(
    session,
    *,
    platform: str,
    resource: str,
    country: str = "GLOBAL",
    shop_id: Optional[str] = None,
    seller_id: Optional[str] = None,
    account_id: Optional[str] = None,
    cursor: Optional[str] = None,
    window_start: Optional[datetime] = None,
    window_end: Optional[datetime] = None,
    extra: Optional[dict[str, Any]] = None,
) -> SyncCursor:
    """Create or update the sync cursor for a scoped resource."""
    scope_key = build_scope_key(
        platform=platform,
        country=country,
        shop_id=shop_id,
        seller_id=seller_id,
        account_id=account_id,
        resource=resource,
    )
    record = session.query(SyncCursor).filter_by(scope_key=scope_key).first()
    if record is None:
        record = SyncCursor(
            platform=platform,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
            resource=resource,
            scope_key=scope_key,
        )
        session.add(record)

    record.cursor = cursor
    record.window_start = window_start
    record.window_end = window_end
    record.last_synced_at = utcnow()
    record.extra = extra
    session.flush()
    return record
