"""Deterministic business-scope resolution (命名店铺集合 → 查询过滤条件).

本服务**不接收任何自然语言**，只做结构化展开与校验：
- `expand_scope`：把一个命名 scope 展开成具体的 shop_ids 集合。
- `resolve_filters`：把 scope_key + 可选显式 platform/country/shop_id(s) 合并成最终过滤条件，
  采用「收窄取交集」语义——指定的店必须落在 scope 范围内，越界即拒，绝不放行范围外的店。

单客户、单租户阶段只切 平台/国家/店铺 三维，不引入 tenant_id（见 plan/09）。
合法店铺以现有 `platform_tokens`（每店一行的授权登记）为准。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from core.db import SessionLocal
from models.base_models import BusinessScope, PlatformToken


class ScopeError(ValueError):
    """范围解析/校验失败（API 层应转 400）。"""


# 展示名小词表（仅用于 display_text，不参与任何过滤逻辑）
_PLATFORM_DISPLAY = {
    "tiktok_shop": "TikTok Shop",
    "shopee": "Shopee",
}
_COUNTRY_DISPLAY = {
    "ID": "印尼",
    "MY": "马来",
    "TH": "泰国",
    "VN": "越南",
    "PH": "菲律宾",
    "GB": "英国",
    "US": "美国",
}


@dataclass
class ScopeFilters:
    platform: Optional[str]
    country: Optional[str]
    shop_ids: list[str]  # 展开后的具体店铺集合（可能为空 = 不按店过滤）
    scope_key: Optional[str]
    display_text: str  # "TikTok Shop / 印尼 / 3 个店铺"


def _display_text(
    *, platform: Optional[str], country: Optional[str], shop_ids: list[str]
) -> str:
    parts: list[str] = []
    if platform:
        parts.append(_PLATFORM_DISPLAY.get(platform, platform))
    if country:
        parts.append(_COUNTRY_DISPLAY.get(country, country))
    if shop_ids:
        parts.append(f"{len(shop_ids)} 个店铺")
    return " / ".join(parts) if parts else "全部范围"


def list_scopes() -> list[dict]:
    """列出所有启用的 scope（运维/调试用）。"""
    session = SessionLocal()
    try:
        rows = (
            session.query(BusinessScope)
            .filter(BusinessScope.is_active.is_(True))
            .order_by(BusinessScope.scope_key)
            .all()
        )
        return [
            {
                "scope_key": r.scope_key,
                "scope_name": r.scope_name,
                "scope_type": r.scope_type,
                "platform": r.platform,
                "country": r.country,
                "shop_ids": list(r.shop_ids or []),
            }
            for r in rows
        ]
    finally:
        session.close()


def get_scope(scope_key: str) -> Optional[BusinessScope]:
    session = SessionLocal()
    try:
        return (
            session.query(BusinessScope)
            .filter(BusinessScope.scope_key == scope_key)
            .first()
        )
    finally:
        session.close()


def expand_scope(scope_key: str) -> ScopeFilters:
    """把一个命名 scope 展开为 shop_ids 集合。"""
    scope = get_scope(scope_key)
    if scope is None or not scope.is_active:
        raise ScopeError(f"未知或已停用的 scope：{scope_key}")
    shop_ids = list(scope.shop_ids or [])
    return ScopeFilters(
        platform=scope.platform,
        country=scope.country,
        shop_ids=shop_ids,
        scope_key=scope.scope_key,
        display_text=_display_text(
            platform=scope.platform, country=scope.country, shop_ids=shop_ids
        ),
    )


def _known_shop_ids() -> set[str]:
    """已授权的合法店铺集合（来自 platform_tokens，每店一行）。"""
    session = SessionLocal()
    try:
        rows = (
            session.query(PlatformToken.shop_id)
            .filter(PlatformToken.shop_id.isnot(None))
            .all()
        )
        return {r[0] for r in rows if r[0]}
    finally:
        session.close()


def _validate_shops_authorized(shop_ids: list[str]) -> None:
    """拒绝任何不在 platform_tokens 中的店（防止瞎配 / 越权）。"""
    if not shop_ids:
        return
    known = _known_shop_ids()
    unknown = [s for s in shop_ids if s not in known]
    if unknown:
        raise ScopeError(
            f"以下店铺未在已授权列表(platform_tokens)中：{', '.join(unknown)}"
        )


def resolve_filters(
    *,
    scope_key: Optional[str] = None,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    shop_id: Optional[str] = None,
    shop_ids: Optional[list[str]] = None,
) -> ScopeFilters:
    """把 scope + 显式条件合并为最终过滤条件（收窄取交集语义）。

    - 只传 scope_key：展开为该 scope 的 shop_ids。
    - scope_key + 显式 shop_id/shop_ids：取交集（在 scope 范围内收窄到指定店）；
      指定的店不在 scope 内 → 抛 ScopeError，绝不放行范围外的店。
    - 只传显式 platform/country/shop_id(s)、无 scope_key：保持现有行为（兼容旧调用），
      但仍校验 shop_ids 必须已授权。
    """
    # 归一化显式 shop 集合
    explicit_shops: list[str] = []
    if shop_id:
        explicit_shops.append(shop_id)
    if shop_ids:
        explicit_shops.extend(shop_ids)
    explicit_shops = list(dict.fromkeys(s for s in explicit_shops if s))  # 去重保序

    if scope_key:
        base = expand_scope(scope_key)
        if explicit_shops:
            # 收窄：指定店必须落在 scope 内
            out_of_scope = [s for s in explicit_shops if s not in base.shop_ids]
            if out_of_scope:
                raise ScopeError(
                    f"指定店铺不在 scope「{scope_key}」范围内："
                    f"{', '.join(out_of_scope)}"
                )
            final_shops = [s for s in base.shop_ids if s in set(explicit_shops)]
        else:
            final_shops = list(base.shop_ids)
        return ScopeFilters(
            platform=platform or base.platform,
            country=country or base.country,
            shop_ids=final_shops,
            scope_key=base.scope_key,
            display_text=_display_text(
                platform=platform or base.platform,
                country=country or base.country,
                shop_ids=final_shops,
            ),
        )

    # 无 scope_key：兼容旧的显式过滤，但校验店铺合法性
    _validate_shops_authorized(explicit_shops)
    return ScopeFilters(
        platform=platform,
        country=country,
        shop_ids=explicit_shops,
        scope_key=None,
        display_text=_display_text(
            platform=platform, country=country, shop_ids=explicit_shops
        ),
    )
