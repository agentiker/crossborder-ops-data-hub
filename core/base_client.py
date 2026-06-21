"""Base API client with account-scoped token management and retries."""

import hmac
import hashlib
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Optional
import requests

from services.scoping import build_scope_key


class BaseAPIClient(ABC):
    """统一API客户端基类"""

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        base_url: str,
        *,
        platform: str,
        country: str = "GLOBAL",
        shop_id: Optional[str] = None,
        seller_id: Optional[str] = None,
        account_id: Optional[str] = None,
    ):
        self.app_key = app_key
        self.app_secret = app_secret
        self.base_url = base_url
        self.platform = platform
        self.country = country
        self.shop_id = shop_id
        self.seller_id = seller_id
        self.account_id = account_id
        self.scope_key = build_scope_key(
            platform=platform,
            country=country,
            shop_id=shop_id,
            seller_id=seller_id,
            account_id=account_id,
        )
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.token_expire_at: float = 0
        self.refresh_token_expire_at: float = 0
        self.session = requests.Session()

    @abstractmethod
    def authenticate(self, **kwargs) -> dict:
        """平台特有鉴权逻辑，子类必须实现"""
        pass

    @abstractmethod
    def refresh_access_token(self) -> dict:
        """刷新Token逻辑，子类必须实现"""
        pass

    def _generate_sign(self, path: str, params: dict, body: str = "") -> str:
        """生成HMAC-SHA256签名

        签名算法: app_secret + path + sorted_params + body + app_secret
        排除 sign 和 access_token 参数，path 保留前导 /
        """
        excluded = {"sign", "access_token"}
        sign_params = {
            k: v for k, v in params.items()
            if k not in excluded and v is not None
        }
        sorted_params = "".join(f"{k}{v}" for k, v in sorted(sign_params.items()))
        sign_str = f"{self.app_secret}{path}{sorted_params}{body}{self.app_secret}"
        return hmac.new(
            self.app_secret.encode(),
            sign_str.encode(),
            hashlib.sha256
        ).hexdigest()

    def _is_token_expired(self) -> bool:
        """检查Token是否过期（预留5分钟缓冲）"""
        return time.time() >= (self.token_expire_at - 300)

    def _ensure_token(self):
        """确保Token有效，过期则自动刷新"""
        if self._is_token_expired():
            if self.refresh_token:
                self.refresh_access_token()
            else:
                raise ValueError("Token已过期且无refresh_token，请重新授权")

    def load_token(
        self,
        platform: Optional[str] = None,
        *,
        country: Optional[str] = None,
        shop_id: Optional[str] = None,
        seller_id: Optional[str] = None,
        account_id: Optional[str] = None,
    ) -> bool:
        """从数据库加载Token

        Args:
            platform: 平台标识，如 "tiktok_shop"

        Returns:
            是否成功加载到Token
        """
        from core.db import SessionLocal
        from models.base_models import PlatformToken

        scope_key = build_scope_key(
            platform=platform or self.platform,
            country=country or self.country,
            shop_id=shop_id if shop_id is not None else self.shop_id,
            seller_id=seller_id if seller_id is not None else self.seller_id,
            account_id=account_id if account_id is not None else self.account_id,
        )
        session = SessionLocal()
        try:
            record = session.query(PlatformToken).filter_by(scope_key=scope_key).first()
            if record and record.refresh_token:
                self.access_token = record.access_token
                self.refresh_token = record.refresh_token
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

    def save_token(self, platform: Optional[str] = None, token_payload: Optional[dict] = None):
        """将当前Token保存到数据库

        Args:
            platform: 平台标识，如 "tiktok_shop"
        """
        from core.db import SessionLocal
        from models.base_models import PlatformToken

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
                record.token_expire_at = expire_dt
                # 空 refresh_token 不抹库：刷新响应可能不返回 refresh_token，无条件写回会把
                # DB 旧值抹成 NULL，使刷新任务永久排除该行（详注见 TikTokShopClient.save_token）。
                if self.refresh_token:
                    record.refresh_token = self.refresh_token
                    record.refresh_token_expire_at = refresh_expire_dt
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
                    token_payload=token_payload,
                )
                session.add(record)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def request(
        self,
        method: str,
        path: str,
        params: dict = None,
        data: dict = None,
        max_retries: int = 2
    ) -> dict:
        """统一请求方法，内置签名和重试

        Args:
            method: HTTP方法 (GET/POST)
            path: API路径
            params: 查询参数
            data: 请求体数据
            max_retries: 最大重试次数

        Returns:
            API响应数据
        """
        self._ensure_token()

        url = f"{self.base_url}{path}"
        params = params or {}

        # 添加公共参数
        params.update({
            "app_key": self.app_key,
            "timestamp": str(int(time.time())),
            "access_token": self.access_token or "",
        })
        params["sign"] = self._generate_sign(path, params)

        for attempt in range(max_retries + 1):
            try:
                resp = self.session.request(
                    method, url, params=params, json=data, timeout=30
                )

                # 401自动刷新Token重试
                if resp.status_code == 401 and attempt < max_retries:
                    self.refresh_access_token()
                    params["access_token"] = self.access_token
                    params["sign"] = self._generate_sign(path, params)
                    continue

                resp.raise_for_status()
                result = resp.json()

                # 检查业务错误码（兼容 int 0、字符串 "0"、缺省 None）
                if result.get("code") not in (0, "0", None):
                    raise Exception(f"API错误: {result.get('message')}")

                return result

            except requests.RequestException as e:
                if attempt == max_retries:
                    raise
                time.sleep(2 ** attempt)  # 指数退避

    def get(self, path: str, params: dict = None) -> dict:
        """GET请求"""
        return self.request("GET", path, params=params)

    def post(self, path: str, data: dict = None, params: dict = None) -> dict:
        """POST请求"""
        return self.request("POST", path, params=params, data=data)
