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

from sqlalchemy import or_

from core.db import SessionLocal
from core.tenancy import DEFAULT_ACCOUNT
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


def list_scopes(account_id: str = DEFAULT_ACCOUNT) -> list[dict]:
    """列出某租户启用的 scope（运维/调试/下拉用）。

    多租户：只返回 `account_id` 名下的 scope——gtl boss 看不到 ecom-app 的范围。
    """
    session = SessionLocal()
    try:
        rows = (
            session.query(BusinessScope)
            .filter(
                BusinessScope.is_active.is_(True),
                BusinessScope.account_id == account_id,
            )
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


def get_scope(scope_key: str, account_id: str = DEFAULT_ACCOUNT) -> Optional[BusinessScope]:
    session = SessionLocal()
    try:
        return (
            session.query(BusinessScope)
            .filter(
                BusinessScope.scope_key == scope_key,
                BusinessScope.account_id == account_id,
            )
            .first()
        )
    finally:
        session.close()


def expand_scope(scope_key: str, account_id: str = DEFAULT_ACCOUNT) -> ScopeFilters:
    """把某租户名下一个命名 scope 展开为 shop_ids 集合。"""
    scope = get_scope(scope_key, account_id=account_id)
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


def tenant_visible_shop_ids(account_id: str = DEFAULT_ACCOUNT) -> set[str]:
    """某租户可见的全部店铺 = 自有 token 店 ∪ 自有 scope 的店铺并集。

    - 自有 token 店：`platform_tokens.account_id == account_id`（店铺归属/接入）；
    - 自有 scope 店：该租户名下所有 active business_scopes 的 shop_ids 并集（显式授权，
      可把别租户拥有的店"借"给本租户做测试/共享，如把 ecom 的店授权给 gtl）。

    用作 boss 全量范围的上限与显式店铺的越权校验集。空集 = 该租户暂无任何可见店
    （上层据此 fail-closed，绝不退化为"无过滤=查全库"）。
    """
    session = SessionLocal()
    try:
        # 店铺归属过滤：account_id 匹配的 token。**向后兼容**：未打标的旧 token
        # （account_id IS NULL，迁移回填前的存量）视为归属创始租户 DEFAULT_ACCOUNT，
        # 镜像 Phase 1 cookie/token「无 account → DEFAULT」回落，使正确性不依赖回填。
        if account_id == DEFAULT_ACCOUNT:
            owner_cond = or_(
                PlatformToken.account_id == account_id,
                PlatformToken.account_id.is_(None),
            )
        else:
            owner_cond = PlatformToken.account_id == account_id
        owned = {
            r[0]
            for r in session.query(PlatformToken.shop_id)
            .filter(PlatformToken.shop_id.isnot(None), owner_cond)
            .all()
            if r[0]
        }
        scoped: set[str] = set()
        for r in (
            session.query(BusinessScope.shop_ids)
            .filter(
                BusinessScope.is_active.is_(True),
                BusinessScope.account_id == account_id,
            )
            .all()
        ):
            for s in (r[0] or []):
                if s:
                    scoped.add(s)
        return owned | scoped
    finally:
        session.close()


def _validate_shops_authorized(shop_ids: list[str], account_id: str = DEFAULT_ACCOUNT) -> None:
    """拒绝任何不在本租户可见集中的店（防止瞎配 / 跨租户越权）。"""
    if not shop_ids:
        return
    known = tenant_visible_shop_ids(account_id)
    unknown = [s for s in shop_ids if s not in known]
    if unknown:
        raise ScopeError(
            f"以下店铺不在本租户可见范围内：{', '.join(unknown)}"
        )


def resolve_filters(
    *,
    scope_key: Optional[str] = None,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    shop_id: Optional[str] = None,
    shop_ids: Optional[list[str]] = None,
    account_id: str = DEFAULT_ACCOUNT,
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
        base = expand_scope(scope_key, account_id=account_id)
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

    # 无 scope_key：兼容旧的显式过滤，但校验店铺合法性（按本租户可见集）
    _validate_shops_authorized(explicit_shops, account_id=account_id)
    return ScopeFilters(
        platform=platform,
        country=country,
        shop_ids=explicit_shops,
        scope_key=None,
        display_text=_display_text(
            platform=platform, country=country, shop_ids=explicit_shops
        ),
    )
