"""Session-injected token storage routines for multi-shop credentials."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from services.scoping import build_scope_key


@dataclass(frozen=True)
class TokenScope:
    """Identifies the account scope a platform token belongs to."""

    platform: str
    country: str | None = None
    seller_id: str | None = None
    shop_id: str | None = None
    account_id: str | None = None


def build_token_key(scope: TokenScope) -> str:
    """Build the same account scope key used by API clients and ORM rows."""
    return build_scope_key(
        platform=scope.platform,
        country=scope.country or "GLOBAL",
        shop_id=scope.shop_id,
        seller_id=scope.seller_id,
        account_id=scope.account_id,
    )


def save_token(
    session,
    *,
    scope: TokenScope,
    access_token: str,
    refresh_token: str,
    token_expire_at: datetime,
):
    """Insert or update a token using the multi-shop token key."""
    from models.base_models import PlatformToken

    token_key = build_token_key(scope)
    expires_at = _ensure_utc_naive(token_expire_at)
    record = session.query(PlatformToken).filter_by(scope_key=token_key).first()
    if record:
        record.access_token = access_token
        record.refresh_token = refresh_token
        record.token_expire_at = expires_at
    else:
        record = PlatformToken(
            platform=scope.platform,
            country=scope.country or "GLOBAL",
            shop_id=scope.shop_id,
            seller_id=scope.seller_id,
            account_id=scope.account_id,
            scope_key=token_key,
            access_token=access_token,
            refresh_token=refresh_token,
            token_expire_at=expires_at,
        )
        session.add(record)
    session.flush()
    return record


def load_token(session, *, scope: TokenScope):
    """Load a token by multi-shop token key."""
    from models.base_models import PlatformToken

    return session.query(PlatformToken).filter_by(scope_key=build_token_key(scope)).first()


def _ensure_utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)
