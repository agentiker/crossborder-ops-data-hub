"""TikTok for Business (Marketing API) 客户端 —— 仅用于拉 GMV Max 广告花费。

为什么不继承 core.base_client.BaseAPIClient
--------------------------------------------------
BaseAPIClient 是 TikTok **Shop** 专用：请求要拼 Shop 特有签名
（app_secret+path+sorted_params+body+app_secret 的 HMAC），token 走 query 参数。
Marketing API 完全不同：token 放 **`Access-Token` 请求头**、**无** sign 拼接、
授权主体是 **advertiser_id**（广告账户）而非 shop_id。强行继承会把 Shop 的签名逻辑
错误地套上来，故独立实现。token 持久化仍复用 services.token_store（platform_tokens 表通用）。

坐实来源（官方 Python SDK + portal 文档正文，2026-07-09）
--------------------------------------------------
- 换 token:   POST /open_api/v1.3/oauth2/access_token/  body(JSON)={app_id,auth_code,secret}，无 grant_type
- 列广告主:   GET  /open_api/v1.3/oauth2/advertiser/get/  ?app_id&secret&access_token
- 列店铺:     GET  /open_api/v1.3/gmv_max/store/list/     ?advertiser_id  (Access-Token 头)
- 拉报表:     GET  /open_api/v1.3/gmv_max/report/get/     (Access-Token 头)
              花费字段 = data.list[].metrics.cost（字符串），另有 net_cost/gross_revenue/orders/roi/currency
              约束: store_ids 单次最多 1 个；含 stat_time_day 时窗口 ≤ 30 天；日期基于广告账户时区
响应信封统一 {code, message, request_id, data}，code==0 为成功。
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

from core.config import settings

logger = logging.getLogger(__name__)

PLATFORM = "tiktok_business"

# GMV Max 报表默认拉取的 metrics（product/live campaign 通用子集，见 portal 支持表）。
# cost = 我们的核心目标（GMV Max 花费）；其余顺带取，够算官方口径 ROAS。
DEFAULT_METRICS = ["cost", "net_cost", "gross_revenue", "orders", "roi", "cost_per_order"]
# 默认维度：按天。注意含 stat_time_day 时 start~end 窗口官方限 ≤30 天（调用方负责分片）。
DEFAULT_DIMENSIONS = ["stat_time_day"]


class TikTokBusinessError(Exception):
    """Marketing API 业务错误（code != 0）。携带 code 便于上层区分限流/鉴权。"""

    def __init__(self, code, message, request_id: str = ""):
        self.code = code
        self.message = message
        self.request_id = request_id
        super().__init__(f"TikTok Marketing API error code={code}: {message}")


class TikTokBusinessClient:
    """TikTok Marketing API 客户端（GMV Max 花费）。

    典型用法：
        client = TikTokBusinessClient(account_id="ecom-app")
        client.authenticate(auth_code)          # OAuth 回调里换 token 存库
        advertisers = client.get_advertisers()  # 拿授权的 advertiser_id
        stores = client.get_gmv_max_stores(advertiser_id)
        rows = client.get_gmv_max_report(advertiser_id, store_id, "2025-07-01", "2025-07-07")
    """

    def __init__(
        self,
        *,
        account_id: Optional[str] = None,
        app_id: Optional[str] = None,
        app_secret: Optional[str] = None,
        base_url: Optional[str] = None,
        access_token: Optional[str] = None,
        advertiser_id: Optional[str] = None,
        timeout: int = 30,
    ):
        from core.tenancy import DEFAULT_ACCOUNT

        self.account_id = account_id or DEFAULT_ACCOUNT
        cred = settings.tiktok_business.credential(self.account_id)
        self.app_id = app_id or cred.app_id
        self.app_secret = app_secret or cred.app_secret
        self.base_url = (base_url or settings.tiktok_business.base_url).rstrip("/")
        self.access_token = access_token
        self.advertiser_id = advertiser_id
        self.timeout = timeout
        self.session = requests.Session()

    # ── 底层请求 ──────────────────────────────────────────────────────────
    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
        access_token: Optional[str] = None,
        send_token_header: bool = True,
    ) -> dict:
        """发一次 Marketing API 请求并解包 data 段。

        token 走 Access-Token 头；code!=0 抛 TikTokBusinessError。数组类查询参数已由
        调用方序列化成 JSON 字符串（TikTok 报表接口收 `["x"]` 形式，见文档 curl 示例）。
        oauth2/advertiser/get 例外——token 只走 query 参数，用 send_token_header=False 抑制头。
        """
        url = f"{self.base_url}{path}"
        headers = {"Content-Type": "application/json"}
        if send_token_header:
            tok = access_token or self.access_token
            if tok:
                headers["Access-Token"] = tok

        resp = self.session.request(
            method, url, params=params, json=json_body, headers=headers, timeout=self.timeout
        )
        resp.raise_for_status()
        result = resp.json()
        code = result.get("code")
        if code not in (0, "0"):
            raise TikTokBusinessError(code, result.get("message"), result.get("request_id", ""))
        return result.get("data") or {}

    # ── OAuth ────────────────────────────────────────────────────────────
    def build_authorize_url(self, redirect_uri: str, state: str = "") -> str:
        """构造广告主授权链接（客户点开授权后回调带 auth_code）。"""
        from urllib.parse import urlencode

        q = {"app_id": self.app_id, "redirect_uri": redirect_uri}
        if state:
            q["state"] = state
        return f"{self.base_url}/portal/auth?{urlencode(q)}"

    def authenticate(self, auth_code: str, *, persist: bool = True) -> dict:
        """用授权码换 access_token，成功后（默认）持久化到 platform_tokens。

        Marketing API 的 advertiser access_token 不过期（除非 revoke），故不做刷新逻辑。
        响应 data 含 access_token / advertiser_ids[] / scope[]。返回原始 data 段。
        """
        logger.info("[tiktok_business.authenticate] 换 token, auth_code=%s...", auth_code[:12])
        data = self._request(
            "POST",
            "/open_api/v1.3/oauth2/access_token/",
            json_body={
                "app_id": self.app_id,
                "auth_code": auth_code,
                "secret": self.app_secret,
            },
        )
        self.access_token = data.get("access_token")
        advertiser_ids = data.get("advertiser_ids") or data.get("advertiser_id") or []
        if isinstance(advertiser_ids, str):
            advertiser_ids = [advertiser_ids]
        logger.info(
            "[tiktok_business.authenticate] 成功, scope=%s, advertiser_ids=%s",
            data.get("scope"), advertiser_ids,
        )
        if persist and self.access_token:
            # 每个授权的 advertiser 各存一行 token（同一 access_token，advertiser_id 落 seller_id 槽）。
            for adv in advertiser_ids or [None]:
                self.save_token(advertiser_id=str(adv) if adv is not None else None, payload=data)
        return data

    def save_token(self, *, advertiser_id: Optional[str] = None, payload: Optional[dict] = None):
        """持久化 access_token（复用 token_store，platform=tiktok_business）。

        advertiser_id 落进 scope 的 seller_id 槽（Marketing API 的账户级主键）。token 不过期，
        token_expire_at 设一个远期哨兵（1 年后）以复用现有 not-null 语义，实际不据此刷新。
        """
        from core.db import SessionLocal
        from services.token_store import TokenScope, save_token

        adv = advertiser_id or self.advertiser_id
        scope = TokenScope(
            platform=PLATFORM,
            country="GLOBAL",
            seller_id=str(adv) if adv is not None else None,
            account_id=self.account_id,
        )
        far_future = datetime.now(timezone.utc) + timedelta(days=365)
        session = SessionLocal()
        try:
            save_token(
                session,
                scope=scope,
                access_token=self.access_token or "",
                refresh_token="",  # Marketing advertiser token 无需刷新
                token_expire_at=far_future,
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def load_token(self, *, advertiser_id: Optional[str] = None) -> bool:
        """从库加载 access_token（按 account_id + advertiser_id 定位）。成功则回填 self。"""
        from core.db import SessionLocal
        from services.token_store import TokenScope, load_token

        adv = advertiser_id or self.advertiser_id
        scope = TokenScope(
            platform=PLATFORM,
            country="GLOBAL",
            seller_id=str(adv) if adv is not None else None,
            account_id=self.account_id,
        )
        session = SessionLocal()
        try:
            record = load_token(session, scope=scope)
            if record and record.access_token:
                self.access_token = record.access_token
                if adv is not None:
                    self.advertiser_id = str(adv)
                return True
            return False
        finally:
            session.close()

    # ── 数据接口 ──────────────────────────────────────────────────────────
    def get_advertisers(self) -> list:
        """列出授权给本 app 的广告主（advertiser_id + 名称）。

        GET /oauth2/advertiser/get/ 用 app_id+secret+access_token 作 query 参数
        （此接口不走 Access-Token 头，与其它不同——照 SDK 签名）。
        """
        data = self._request(
            "GET",
            "/open_api/v1.3/oauth2/advertiser/get/",
            params={
                "app_id": self.app_id,
                "secret": self.app_secret,
                "access_token": self.access_token,
            },
            send_token_header=False,  # 此接口凭 query 里的 access_token，不塞头
        )
        return data.get("list") or []

    def get_gmv_max_stores(self, advertiser_id: str) -> list:
        """列出该广告主下可用于 GMV Max 的店铺。

        用于拿 store_ids 喂报表接口；应筛 is_gmv_max_available==true 的店。
        """
        data = self._request(
            "GET",
            "/open_api/v1.3/gmv_max/store/list/",
            params={"advertiser_id": advertiser_id},
        )
        return data.get("list") or data.get("stores") or []

    def get_gmv_max_report(
        self,
        advertiser_id: str,
        store_id: str,
        start_date: str,
        end_date: str,
        *,
        metrics: Optional[list] = None,
        dimensions: Optional[list] = None,
        page: int = 1,
        page_size: int = 1000,
    ) -> dict:
        """拉某店某窗口的 GMV Max 报表（原始 data 段：含 list[] 与 page_info）。

        约束（官方）：store_ids 单次最多 1 个（故本方法一次一店）；含 stat_time_day 时
        start~end 窗口 ≤ 30 天；日期基于广告账户时区。数组参数按文档 curl 示例序列化成
        JSON 字符串（`["x"]`）。花费在 list[].metrics.cost（字符串）。
        """
        metrics = metrics or DEFAULT_METRICS
        dimensions = dimensions or DEFAULT_DIMENSIONS
        params = {
            "advertiser_id": advertiser_id,
            "store_ids": json.dumps([str(store_id)]),
            "dimensions": json.dumps(dimensions),
            "metrics": json.dumps(metrics),
            "start_date": start_date,
            "end_date": end_date,
            "page": page,
            "page_size": page_size,
        }
        return self._request(
            "GET", "/open_api/v1.3/gmv_max/report/get/", params=params
        )
