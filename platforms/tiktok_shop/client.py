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
        # TikTok 域名国内直连可达，且 IP 白名单需稳定出口。强制直连，
        # 不走本机代理（规则代理会按域名分流到不同出口，导致白名单 IP 对不上）。
        self.session.trust_env = False
        self.session.proxies = {"http": None, "https": None}
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
        """使用授权码获取access_token，并按已授权店铺落库（含 shop_cipher）。

        关键：/api/v2/token/get **不返回** shop_cipher / shop_id（实测仅返回 token、
        granted_scopes、open_id、seller_name、seller_base_region、user_type）。shop_cipher
        必须用 GET /authorization/202309/shops 单独取。因此换完 token 后调 get-authorized-shops，
        按返回的 region/shop_id/cipher 给每个店各落一行；不再依赖 token/get 返回 shop 信息，
        也不再退化成 country=GLOBAL|shop=_ 无 cipher 的占位行。

        Args:
            auth_code: OAuth授权码

        Returns:
            Token响应数据（/api/v2/token/get 原始返回）
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
        payload = result.get("data") or {}
        logger.info(f"[authenticate] Token换取成功, granted_scopes={payload.get('granted_scopes')}")

        shops = self.get_authorized_shops()
        if shops:
            for sh in shops:
                self.country = sh.get("region") or self.country
                self.shop_id = str(sh["id"]) if sh.get("id") is not None else self.shop_id
                self.shop_cipher = sh.get("cipher")
                self.save_token(token_payload=payload)
            logger.info(
                f"[authenticate] 已为 {len(shops)} 个授权店铺保存 token（含 cipher）: "
                f"{[str(s.get('id')) for s in shops]}"
            )
        else:
            # 兜底：get-authorized-shops 未返回店铺（异常/无授权店）。按 seller_base_region 存，
            # 仍无 cipher——记 warning，提示需排查（否则 finance/order 等接口会缺 cipher）。
            self.country = payload.get("seller_base_region") or self.country
            logger.warning(
                "[authenticate] get-authorized-shops 未返回店铺，按 seller_base_region=%s 兜底存"
                "（无 shop_cipher，后续需 cipher 的接口会失败，请排查授权）",
                self.country,
            )
            self.save_token(token_payload=payload)
        return result

    def get_authorized_shops(self) -> list:
        """GET /authorization/202309/shops —— 取该 token 下已授权店铺（含 shop_cipher）。

        token/get 不返回 shop_cipher/shop_id，靠本接口补全（header 带 access_token，无需 cipher）。
        返回 [{id, region, name, cipher, ...}]；调用失败或无店返回 []。
        """
        try:
            result = self.request("GET", "/authorization/202309/shops")
        except Exception as exc:
            logger.warning(f"[get_authorized_shops] 调用失败: {exc}")
            return []
        return (result.get("data") or {}).get("shops") or []

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
                record.token_expire_at = expire_dt
                # 仅在本次确实拿到 refresh_token 时才覆盖：/api/v2/token/refresh 的响应
                # **可能返回空/None 的 refresh_token**（同 shop_cipher 不返回的坑）。若无条件
                # 写回会把 DB 里有效的 refresh_token 抹成 NULL → 刷新任务因 `refresh_token
                # IS NOT NULL` 永久排除该行（静默"找到 0 个"）、access_token 到期后同步无法
                # 自救只能人工重新授权（2026-06-21 实际烧过数天）。空值保留旧 refresh_token
                # 及其有效期，二者一起守（给了新 token 才一起更）。
                if self.refresh_token:
                    record.refresh_token = self.refresh_token
                    record.refresh_token_expire_at = refresh_expire_dt
                # 仅在本次确实拿到 shop_cipher 时才覆盖：token 刷新接口
                # (/api/v2/token/refresh) 的响应不含 shop_cipher，若无条件写回会把
                # DB 里的旧 cipher 抹成 None，导致后续 orders/products search 报
                # 400 106013 Missing shop_cipher。空值时保留旧 cipher。
                if self.shop_cipher:
                    record.shop_cipher = self.shop_cipher
                record.token_payload = token_payload
                # token 响应中可能含 shop_id/seller_id，顺手回填
                if self.shop_id and not record.shop_id:
                    record.shop_id = self.shop_id
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
        # TikTok token 响应含 seller 对象（shop_id / seller_id），
        # 但 schema 版本间字段名不一致，优先取 data 顶层，再从 seller 里兜底。
        if not self.shop_id:
            self.shop_id = (
                data.get("shop_id")
                or data.get("shopid")
                or (data.get("seller") or {}).get("shop_id")
                or (data.get("seller") or {}).get("shopid")
            )
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
        max_retries: int = 2,
        version: str = "202309",
    ) -> dict:
        """TikTok request with access token header.

        version: 接口版本号（公共参数 version + 参与签名）。多数接口为 202309；
        finance 部分端点须用 202501/202507（如 statement_transactions），故可覆盖。
        """
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
            method, path, params, data, headers, max_retries, version
        )

    def _request_with_headers(
        self,
        method: str,
        path: str,
        params: dict,
        data: dict | None,
        headers: dict,
        max_retries: int,
        version: str = "202309",
    ) -> dict:
        url = f"{self.base_url}{path}"
        params.update({
            "app_key": self.app_key,
            "timestamp": str(int(time.time())),
            "version": version,
        })
        # data 为 {} 时也需序列化成 "{}" 并带 Content-Type（POST 空 body 合法，
        # 否则 TikTok 对缺失 Content-Type 的 POST 返回 415）；GET 时 data=None 不带 body。
        body_str = json.dumps(data, separators=(",", ":")) if data is not None else ""
        params["sign"] = self._generate_sign(path, params, body=body_str)
        if body_str:
            headers = {**headers, "Content-Type": "application/json"}

        for attempt in range(max_retries + 1):
            resp = self.session.request(
                method,
                url,
                params=params,
                data=body_str.encode("utf-8") if body_str else None,
                headers=headers,
                timeout=30,
            )
            if resp.status_code == 401 and attempt < max_retries:
                self.refresh_access_token()
                headers["x-tts-access-token"] = self.access_token or ""
                params["sign"] = self._generate_sign(path, params, body=body_str)
                continue
            if resp.status_code >= 400:
                logger.error(
                    "TikTok %s %s -> %s: %s",
                    method, path, resp.status_code, resp.text,
                )
            resp.raise_for_status()
            result = resp.json()
            if result.get("code") not in (0, "0", None):
                raise Exception(f"API错误: {result.get('message')}")
            return result

        raise RuntimeError("unreachable")

    # ── 商品（product/202309）──────────────────────────────────────────────

    def iter_products(self, page_size: int = 100, status: str | None = "ACTIVATE"):
        """翻页拉取商品列表（POST /product/202309/products/search）。

        每页 yield data 段（含 products[]、next_page_token）。默认 status="ACTIVATE"
        只拉在售商品（草稿/下架/冻结不返回，避免污染经营分析）；传 status=None 则发空
        body 枚举全店（含全部状态）。status 在请求体里过滤，page_size / page_token /
        shop_cipher 放 query 参与签名。
        """
        body: dict = {"status": status} if status else {}
        page_token = None
        while True:
            params: dict = {"page_size": page_size}
            if self.shop_cipher:
                params["shop_cipher"] = self.shop_cipher
            if page_token:
                params["page_token"] = page_token
            result = self.request(
                "POST", "/product/202309/products/search", params=params, data=body
            )
            data = result.get("data", {})
            yield data
            page_token = data.get("next_page_token")
            if not page_token:
                break

    def list_products(
        self, page_size: int = 100, status: str | None = "ACTIVATE"
    ) -> list[dict]:
        """枚举全店商品，返回 products[] 合并列表（每项含 id、title、skus）。

        默认只返回 ACTIVATE 在售商品；传 status=None 拉全量（含草稿/下架）。
        """
        products: list[dict] = []
        for data in self.iter_products(page_size=page_size, status=status):
            products.extend(data.get("products", []))
        return products

    def get_product(self, product_id: str) -> dict:
        """拉取单个商品详情（GET /product/202309/products/{product_id}），返回 data 段。

        价格走详情而非 products/search：详情的 skus[].price.sale_price 是"商品页含税
        展示价（折扣前）"、对所有卖家可用；products/search 的 sale_price 仅对中国跨境
        卖家返回，本类卖家只回 tax_exclusive_price（税前价）。GET 无 body。
        """
        params: dict = {}
        if self.shop_cipher:
            params["shop_cipher"] = self.shop_cipher
        result = self.request(
            "GET", f"/product/202309/products/{product_id}", params=params, data=None
        )
        return result.get("data", {})

    # ── 库存（product/202309）──────────────────────────────────────────────

    def search_inventory_batch(self, product_ids: list[str]) -> list[dict]:
        """单批查询库存（≤100 个 product_id），返回该批 inventory[]。"""
        result = self.request(
            "POST",
            "/product/202309/inventory/search",
            params={"shop_cipher": self.shop_cipher} if self.shop_cipher else {},
            data={"product_ids": product_ids},
        )
        return result.get("data", {}).get("inventory", [])

    def search_inventory(self, product_ids: list[str], batch_size: int = 100) -> list[dict]:
        """按 product_id 查询库存（POST /product/202309/inventory/search，无翻页）。

        接口单次最多 100 个 product_id，内部自动按 batch_size 切批并合并 inventory[]。
        """
        inventory: list[dict] = []
        for start in range(0, len(product_ids), batch_size):
            batch = product_ids[start:start + batch_size]
            if not batch:
                continue
            inventory.extend(self.search_inventory_batch(batch))
        return inventory

    # ── 订单（order/202309）────────────────────────────────────────────────

    def search_orders_page(
        self,
        *,
        create_time_ge: Optional[int] = None,
        create_time_lt: Optional[int] = None,
        update_time_ge: Optional[int] = None,
        update_time_lt: Optional[int] = None,
        order_status: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = 50,
        sort_field: str = "create_time",
        sort_order: str = "ASC",
    ) -> dict:
        """调用 POST /order/202309/orders/search，返回 data 段。

        时间窗口等过滤条件放在请求体；分页与 shop_cipher 放在 query（参与签名）。
        """
        params: dict = {
            "page_size": page_size,
            "sort_field": sort_field,
            "sort_order": sort_order,
        }
        if self.shop_cipher:
            params["shop_cipher"] = self.shop_cipher
        if page_token:
            params["page_token"] = page_token

        body: dict = {}
        if create_time_ge is not None:
            body["create_time_ge"] = create_time_ge
        if create_time_lt is not None:
            body["create_time_lt"] = create_time_lt
        if update_time_ge is not None:
            body["update_time_ge"] = update_time_ge
        if update_time_lt is not None:
            body["update_time_lt"] = update_time_lt
        if order_status:
            body["order_status"] = order_status

        result = self.request(
            "POST", "/order/202309/orders/search", params=params, data=body
        )
        return result.get("data", {})

    def iter_orders(
        self,
        *,
        create_time_ge: Optional[int] = None,
        create_time_lt: Optional[int] = None,
        update_time_ge: Optional[int] = None,
        update_time_lt: Optional[int] = None,
        order_status: Optional[str] = None,
        page_size: int = 50,
        sort_field: str = "create_time",
        sort_order: str = "ASC",
    ):
        """Yield order-search pages until the API returns no next page token."""
        page_token = None
        while True:
            data = self.search_orders_page(
                create_time_ge=create_time_ge,
                create_time_lt=create_time_lt,
                update_time_ge=update_time_ge,
                update_time_lt=update_time_lt,
                order_status=order_status,
                page_token=page_token,
                page_size=page_size,
                sort_field=sort_field,
                sort_order=sort_order,
            )
            yield data
            page_token = data.get("next_page_token")
            if not page_token:
                break

    # ── 结算/财务（finance）────────────────────────────────────────────────

    def iter_statements(
        self,
        *,
        statement_time_ge: Optional[int] = None,
        statement_time_lt: Optional[int] = None,
        payment_status: Optional[str] = None,
        page_size: int = 50,
        sort_field: str = "statement_time",
        sort_order: str = "DESC",
    ):
        """翻页拉取结算单列表（GET /finance/202309/statements）。

        每页 yield data 段（含 statements[]、next_page_token）。每个 statement 含
        id / statement_time / currency 等；id 用于下一步取交易明细。时间窗 statement_time_ge/lt
        与分页、shop_cipher 均放 query（参与签名）。仅有 2023-07-01 之后数据。
        """
        page_token = None
        while True:
            params: dict = {
                "page_size": page_size,
                "sort_field": sort_field,
                "sort_order": sort_order,
            }
            if self.shop_cipher:
                params["shop_cipher"] = self.shop_cipher
            if statement_time_ge is not None:
                params["statement_time_ge"] = statement_time_ge
            if statement_time_lt is not None:
                params["statement_time_lt"] = statement_time_lt
            if payment_status:
                params["payment_status"] = payment_status
            if page_token:
                params["page_token"] = page_token
            result = self.request(
                "GET", "/finance/202309/statements", params=params, data=None,
                version="202309",
            )
            data = result.get("data", {})
            yield data
            page_token = data.get("next_page_token")
            if not page_token:
                break

    def iter_statement_transactions(
        self,
        statement_id: str,
        *,
        page_size: int = 50,
        sort_field: str = "order_create_time",
        sort_order: str = "ASC",
    ):
        """翻页拉取某结算单下的交易明细（GET /finance/202501/statements/{id}/statement_transactions）。

        必须用 202501 版本：只有该版本的 transactions[].fee_tax_breakdown.fee 同时含三项广告费
        （gmv_max_ad_fee_amount / tap_shop_ads_commission / affiliate_ads_commission_amount）；
        202309/202507 缺前两项。每笔交易含 order_create_time（用于按业务日归集）、order_id。
        sort_field 必填（仅支持 order_create_time）。GET 无 body，分页/shop_cipher 放 query。
        """
        page_token = None
        while True:
            params: dict = {
                "page_size": page_size,
                "sort_field": sort_field,
                "sort_order": sort_order,
            }
            if self.shop_cipher:
                params["shop_cipher"] = self.shop_cipher
            if page_token:
                params["page_token"] = page_token
            result = self.request(
                "GET",
                f"/finance/202501/statements/{statement_id}/statement_transactions",
                params=params, data=None, version="202501",
            )
            data = result.get("data", {})
            yield data
            page_token = data.get("next_page_token")
            if not page_token:
                break


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
