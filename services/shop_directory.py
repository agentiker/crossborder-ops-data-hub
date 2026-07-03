"""店铺 shop_id → 可读店名映射（单一数据源：platform_tokens.token_payload.seller_name）。

店名来自 TikTok 授权时 /api/v2/token/get 返回的 seller_name（如 "SasaQueen.id"），
授权流程已存进 platform_tokens.token_payload（JSON 列，明文，非加密字段）。本模块提供
「按 shop_id 批量查店名」的统一读取层，供待发货/看板等处把裸 shop_id 富化成店名。

多租户：只在当前 account_id（core.tenancy contextvar）名下的 token 里查——不同租户
不互相看到店名。进程内轻缓存（按 account_id），token 变动频率极低，缓存 TTL 靠进程生命周期
+ 手动 clear（授权/重授权后可调 clear_shop_name_cache）。查库异常吞掉返回空映射（fail-safe，
绝不让店名富化中断主数据）。
"""
from __future__ import annotations

import logging
from typing import Optional

from core.db import SessionLocal
from core.tenancy import current_account_or_none
from models.base_models import PlatformToken

logger = logging.getLogger(__name__)

# 进程内缓存：account_id -> {shop_id: shop_name}
_cache: dict[str, dict[str, str]] = {}


def clear_shop_name_cache() -> None:
    """授权/重授权后失效缓存（新店名生效）。"""
    _cache.clear()


def _load(account_id: str) -> dict[str, str]:
    """从 platform_tokens 读该租户 shop_id → seller_name 映射。"""
    session = SessionLocal()
    try:
        mapping: dict[str, str] = {}
        rows = (
            session.query(PlatformToken)
            .filter(PlatformToken.account_id == account_id)
            .all()
        )
        for t in rows:
            if not t.shop_id:
                continue
            payload = t.token_payload or {}
            name = payload.get("seller_name")
            if name:
                mapping[str(t.shop_id)] = str(name)
        return mapping
    except Exception:  # noqa: BLE001 — fail-safe：查不到店名不影响主数据
        logger.warning("加载店名映射失败", exc_info=True)
        return {}
    finally:
        session.close()


def get_shop_names(account_id: Optional[str] = None) -> dict[str, str]:
    """返回当前（或指定）租户的 shop_id → 店名映射（带进程缓存）。"""
    acct = account_id or current_account_or_none() or ""
    if acct not in _cache:
        _cache[acct] = _load(acct)
    return _cache[acct]


def shop_label(shop_id: Optional[str], account_id: Optional[str] = None) -> Optional[str]:
    """单个 shop_id → 店名；查不到回落 None（调用方自行决定回落裸 id）。"""
    if not shop_id:
        return None
    return get_shop_names(account_id).get(str(shop_id))
