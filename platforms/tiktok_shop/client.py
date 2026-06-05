"""TikTok Shop API client."""

import json
import logging
import time
from typing import Optional

from core.base_client import BaseAPIClient

logger = logging.getLogger(__name__)

PLATFORM = "tiktok_shop"


class TikTokShopClient(BaseAPIClient):
    """TikTok Shop API客户端"""

    def __init__(
        self,
        app_key: Optional[str] = None,
        app_secret: Optional[str] = None,
        base_url: Optional[str] = None,
        *,
        auth_base_url: Optional[str] = None,
        country: str = "GLOBAL",
        shop_id: Optional[str] = None,
        seller_id: Optional[str] = None,
        account_id: Optional[str] = None,
        auto_load_token: bool = True,
    ):
        from core.config import settings
        app_key = app_key or settings.tiktok.app_key
        app_secret = app_secret or settings.tiktok.app_secret
        base_url = base_url or settings.tiktok.base_url
        auth_base_url = auth_base_url or settings.tiktok.auth_base_url
        super().__init__(
            app_key,
            app_secret,
            base_url,
            platform=PLATFORM,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
        )
        self.auth_base_url = auth_base_url.rstrip("/")
        self.shop_cipher: Optional[str] = None
        if auto_load_token:
            self.load_token()

    def load_token(self, platform: Optional[str] = None, **kwargs) -> bool:
        """从数据库加载Token，包含shop_cipher"""
        from core.db import SessionLocal
        from models.base_models import PlatformToken
        from services.scoping import build_scope_key
        from datetime import timezone

        scope_key = build_scope_key(
            platform=platform or self.platform,
            country=kwargs.get("country") or self.country,
            shop_id=kwargs.get("shop_id") if kwargs.get("shop_id") is not None else self.shop_id,
            seller_id=kwargs.get("seller_id") if kwargs.get("seller_id") is not None else self.seller_id,
            account_id=kwargs.get("account_id") if kwargs.get("account_id") is not None else self.account_id,
        )
        session = SessionLocal()
        try:
            record = session.query(PlatformToken).filter_by(scope_key=scope_key).first()
            if record and record.refresh_token:
                self.access_token = record.access_token
                self.refresh_token = record.refresh_token
                self.shop_cipher = record.shop_cipher
                self.token_expire_at = record.token_expire_at.replace(
                    tzinfo=timezone.utc
                ).timestamp() if record.token_expire_at else 0
                self.refresh_token_expire_at = record.refresh_token_expire_at.replace(
                    tzinfo=timezone.utc
                ).timestamp() if record.refresh_token_expire_at else 0
                return True
            return False
        finally:
            session.close()

    def _auth_get(self, path: str, params: dict, version: str = "202309") -> dict:
        """Call TikTok auth endpoints that do not use shop access tokens."""
        request_params = {"app_key": self.app_key, "app_secret": self.app_secret, "version": version, **params}
        request_params["sign"] = self._generate_sign(path, request_params)
        resp = self.session.get(
            f"{self.auth_base_url}{path}",
            params=request_params,
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()
        if result.get("code") not in (0, "0", None):
            raise Exception(f"TikTok auth API error: {result.get('message')}")
        return result

    def authenticate(self, auth_code: str) -> dict:
        """使用授权码获取access_token

        Args:
            auth_code: OAuth授权码

        Returns:
            Token响应数据
        """
        logger.info(f"[authenticate] 开始用授权码换取Token, auth_code={auth_code[:20]}...")
        result = self._auth_get(
            "/api/v2/token/get",
            params={
                "auth_code": auth_code,
                "grant_type": "authorized_code",
            },
        )
        self._apply_token_payload(result)
        logger.info(f"[authenticate] Token换取成功, shop_cipher={self.shop_cipher}")
        self.save_token(token_payload=result.get("data"))
        return result

    def save_token(self, platform: Optional[str] = None, token_payload: Optional[dict] = None):
        """保存token到数据库，包含shop_cipher"""
        from core.db import SessionLocal
        from models.base_models import PlatformToken
        from services.scoping import build_scope_key
        from datetime import datetime, timezone

        target_platform = platform or self.platform
        scope_key = build_scope_key(
            platform=target_platform,
            country=self.country,
            shop_id=self.shop_id,
            seller_id=self.seller_id,
            account_id=self.account_id,
        )
        session = SessionLocal()
        try:
            record = session.query(PlatformToken).filter_by(scope_key=scope_key).first()
            logger.info(f"[save_token] scope_key={scope_key}, 已存在记录={record is not None}")
            expire_dt = (
                datetime.fromtimestamp(self.token_expire_at, tz=timezone.utc)
                if self.token_expire_at
                else None
            )
            refresh_expire_dt = (
                datetime.fromtimestamp(self.refresh_token_expire_at, tz=timezone.utc)
                if self.refresh_token_expire_at
                else None
            )
            if record:
                record.access_token = self.access_token
                record.refresh_token = self.refresh_token
                record.token_expire_at = expire_dt
                record.refresh_token_expire_at = refresh_expire_dt
                record.shop_cipher = self.shop_cipher
                record.token_payload = token_payload
            else:
                record = PlatformToken(
                    platform=target_platform,
                    country=self.country,
                    shop_id=self.shop_id,
                    seller_id=self.seller_id,
                    account_id=self.account_id,
                    scope_key=scope_key,
                    access_token=self.access_token,
                    refresh_token=self.refresh_token,
                    token_expire_at=expire_dt,
                    refresh_token_expire_at=refresh_expire_dt,
                    shop_cipher=self.shop_cipher,
                    token_payload=token_payload,
                )
                session.add(record)
            session.commit()
            logger.info(f"[save_token] Token已保存到数据库, scope_key={scope_key}")
        except Exception as e:
            logger.error(f"[save_token] 保存Token失败: {e}", exc_info=True)
            session.rollback()
            raise
        finally:
            session.close()

    def refresh_access_token(self) -> dict:
        """刷新access_token

        Returns:
            新的Token响应数据
        """
        result = self._auth_get(
            "/api/v2/token/refresh",
            params={
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            },
        )
        self._apply_token_payload(result)
        self.save_token(token_payload=result.get("data"))
        return result

    def _apply_token_payload(self, result: dict):
        data = result.get("data", {})
        self.access_token = data["access_token"]
        self.refresh_token = data["refresh_token"]
        self.shop_cipher = data.get("shop_cipher")
        now = time.time()
        self.token_expire_at = _coerce_expiry(data, "access_token_expire_in", now)
        if not self.token_expire_at:
            self.token_expire_at = now + int(data.get("expires_in", 0))
        self.refresh_token_expire_at = _coerce_expiry(
            data, "refresh_token_expire_in", now
        )

    def request(
        self,
        method: str,
        path: str,
        params: dict = None,
        data: dict = None,
        max_retries: int = 2
    ) -> dict:
        """TikTok request with access token header."""
        self._ensure_token()

        params = params or {}
        headers = {"x-tts-access-token": self.access_token or ""}
        return super().request(
            method,
            path,
            params=params,
            data=data,
            max_retries=max_retries,
        ) if not headers else self._request_with_headers(
            method, path, params, data, headers, max_retries
        )

    def _request_with_headers(
        self,
        method: str,
        path: str,
        params: dict,
        data: dict | None,
        headers: dict,
        max_retries: int,
    ) -> dict:
        url = f"{self.base_url}{path}"
        params.update({
            "app_key": self.app_key,
            "timestamp": str(int(time.time())),
            "version": "202309",
        })
        body_str = json.dumps(data, separators=(",", ":")) if data else ""
        params["sign"] = self._generate_sign(path, params, body=body_str)

        for attempt in range(max_retries + 1):
            resp = self.session.request(
                method, url, params=params, json=data, headers=headers, timeout=30
            )
            if resp.status_code == 401 and attempt < max_retries:
                self.refresh_access_token()
                headers["x-tts-access-token"] = self.access_token or ""
                params["sign"] = self._generate_sign(path, params, body=body_str)
                continue
            resp.raise_for_status()
            result = resp.json()
            if result.get("code") not in (0, "0", None):
                raise Exception(f"API错误: {result.get('message')}")
            return result

        raise RuntimeError("unreachable")

    def get_inventory_page(
        self,
        warehouse_id: str = None,
        page_token: str = None,
        page_size: int = 100,
    ) -> dict:
        """获取库存列表

        Args:
            warehouse_id: 仓库ID（可选）

        Returns:
            API response data
        """
        params = {"page_size": page_size}
        if warehouse_id:
            params["warehouse_id"] = warehouse_id
        if page_token:
            params["page_token"] = page_token
        result = self.get("/api/inventory/get", params=params)
        return result.get("data", {})

    def iter_inventory(self, warehouse_id: str = None, page_size: int = 100):
        """Yield inventory pages until the API returns no next page token."""
        page_token = None
        while True:
            data = self.get_inventory_page(
                warehouse_id=warehouse_id,
                page_token=page_token,
                page_size=page_size,
            )
            yield data
            page_token = data.get("next_page_token")
            if not page_token:
                break

    def get_inventory(self, warehouse_id: str = None) -> list:
        items = []
        for page in self.iter_inventory(warehouse_id=warehouse_id):
            items.extend(page.get("inventory_list", []))
        return items


def _coerce_expiry(data: dict, key: str, now: float) -> float:
    value = data.get(key)
    if value is None:
        return 0
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return 0
    if numeric > 10_000_000_000:
        return numeric / 1000
    if numeric > 1_000_000_000:
        return float(numeric)
    return now + numeric
