"""Persistence helpers for raw API responses and incremental cursors."""
from __future__ import annotations

from models.base_models import RawAPIResponse, SyncCursor
from services.scoping import build_scope_key


def save_raw_api_response(
    session,
    *,
    platform: str,
    shop_id: str | None,
    endpoint: str | None = None,
    request_key: str,
    payload: dict | list,
    status_code: int = 200,
    resource: str = "unknown",
    country: str = "GLOBAL",
    seller_id: str | None = None,
    account_id: str | None = None,
    method: str = "GET",
    request_params: dict | None = None,
    request_body: dict | None = None,
) -> RawAPIResponse:
    """Insert or update a raw API response for an idempotent request window."""
    path = endpoint or request_key
    scope_key = build_scope_key(
        platform=platform,
        country=country,
        shop_id=shop_id,
        seller_id=seller_id,
        account_id=account_id,
        resource=f"raw:{resource}:{path}:{request_key}",
    )
    record = (
        session.query(RawAPIResponse)
        .filter_by(scope_key=scope_key)
        .first()
    )
    if record:
        record.response_payload = payload
        record.http_status = status_code
        record.request_params = request_params
        record.request_body = request_body
    else:
        record = RawAPIResponse(
            platform=platform,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
            scope_key=scope_key,
            resource=resource,
            method=method,
            path=path,
            request_params=request_params,
            request_body=request_body,
            response_payload=payload,
            http_status=status_code,
        )
        session.add(record)
    session.flush()
    return record


def save_sync_cursor(
    session,
    *,
    platform: str,
    shop_id: str | None,
    resource: str,
    cursor_value: str,
    country: str = "GLOBAL",
    seller_id: str | None = None,
    account_id: str | None = None,
) -> SyncCursor:
    """Insert or update a sync cursor for one platform/shop/resource."""
    scope_key = build_scope_key(
        platform=platform,
        country=country,
        shop_id=shop_id,
        seller_id=seller_id,
        account_id=account_id,
        resource=resource,
    )
    record = (
        session.query(SyncCursor)
        .filter_by(scope_key=scope_key)
        .first()
    )
    if record:
        record.cursor = cursor_value
    else:
        record = SyncCursor(
            platform=platform,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
            resource=resource,
            scope_key=scope_key,
            cursor=cursor_value,
        )
        session.add(record)
    session.flush()
    return record


def load_sync_cursor(
    session,
    *,
    platform: str,
    shop_id: str | None,
    resource: str,
    country: str = "GLOBAL",
    seller_id: str | None = None,
    account_id: str | None = None,
) -> SyncCursor | None:
    """Load a sync cursor for one platform/shop/resource."""
    scope_key = build_scope_key(
        platform=platform,
        country=country,
        shop_id=shop_id,
        seller_id=seller_id,
        account_id=account_id,
        resource=resource,
    )
    return (
        session.query(SyncCursor)
        .filter_by(scope_key=scope_key)
        .first()
    )
