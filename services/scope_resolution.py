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
from services.shop_directory import get_shop_names


class ScopeError(ValueError):
    """范围解析/校验失败（API 层应转 400）。"""


# 永不命中的店铺哨兵：当某租户「全部范围」展开后可见店铺为空时用它占位，
# 让下游 shop_id IN (...) 过滤命中空集（fail-closed），绝不退化为"不过滤=查全库"。
NO_SHOP_SENTINEL = "__no_shop__"


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
    """把某租户名下一个命名 scope（或 `shop:`/`platform:`/`country:` 伪 scope）展开为 shop_ids 集合。

    伪 scope（不落 business_scopes 表、按租户可见店集校验，fail-closed）：
      · `shop:<shop_id>`   → 单店
      · `platform:<p>`     → 本租户该平台的所有可见店
      · `country:<c>`      → 本租户该区域的所有可见店
    这里是范围解析唯一收口点，支持它们之后 operator 授权(allowed_scope_key)/告警订阅
    (alert_recipients.scope_key)/管理页校验/默认范围绑定全部自动获得「按店/平台/区域指定」能力。
    """
    if scope_key.startswith("shop:"):
        sid = scope_key[len("shop:"):].strip()
        if not sid or sid not in tenant_visible_shop_ids(account_id):
            raise ScopeError(f"店铺不在本租户可见范围内：{sid or '(空)'}")
        return ScopeFilters(
            platform=None,
            country=None,
            shop_ids=[sid],
            scope_key=scope_key,
            display_text=_display_text(platform=None, country=None, shop_ids=[sid]),
        )
    # `platform:<p>` / `country:<c>` 分组伪 scope（与 shop: 同语义，不落 business_scopes 表）：
    # 展开为本租户可见店中该平台/区域的店集。空集 → fail-closed。供默认范围切换的自动分组 +
    # 未来 operator 授权/告警订阅按平台或区域指定，全走此收口点。
    if scope_key.startswith("platform:"):
        p = scope_key[len("platform:"):].strip()
        dims = tenant_shop_dimensions(account_id)
        sids = sorted(s for s, d in dims.items() if d.get("platform") == p)
        if not sids:
            raise ScopeError(f"本租户在平台「{p or '(空)'}」下无可见店铺")
        return ScopeFilters(
            platform=p, country=None, shop_ids=sids, scope_key=scope_key,
            display_text=_display_text(platform=p, country=None, shop_ids=sids),
        )
    if scope_key.startswith("country:"):
        c = scope_key[len("country:"):].strip()
        dims = tenant_shop_dimensions(account_id)
        sids = sorted(s for s, d in dims.items() if d.get("country") == c)
        if not sids:
            raise ScopeError(f"本租户在区域「{c or '(空)'}」下无可见店铺")
        return ScopeFilters(
            platform=None, country=c, shop_ids=sids, scope_key=scope_key,
            display_text=_display_text(platform=None, country=c, shop_ids=sids),
        )
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


def tenant_shop_dimensions(account_id: str = DEFAULT_ACCOUNT) -> dict[str, dict]:
    """本租户可见店 → {"platform", "country"}（取自 platform_tokens）。

    用于默认范围选项的平台/区域自动分组与单店后缀。店铺的平台/区域是其固有属性，
    按 shop_id 直接查（不按 account 过滤，借用的 scope 店也能取到维度）。无 token 行的店
    （极少数只挂在 scope 里的）不出现在此映射里——分组时被忽略、不影响正确性。
    """
    visible = tenant_visible_shop_ids(account_id)
    if not visible:
        return {}
    session = SessionLocal()
    try:
        out: dict[str, dict] = {}
        for sid, platform, country in (
            session.query(PlatformToken.shop_id, PlatformToken.platform, PlatformToken.country)
            .filter(PlatformToken.shop_id.in_(visible))
            .all()
        ):
            if sid and str(sid) not in out:
                out[str(sid)] = {"platform": platform, "country": country}
        return out
    finally:
        session.close()


def list_scope_options(account_id: str = DEFAULT_ACCOUNT) -> list[dict]:
    """默认范围切换 / 范围列举的规范选项（boss 视角，含平台/区域自动分组 + 逐店）。

    返回 [{key, label, description, scope_type}]，key 即可直接绑定的 scope_key：
      · 全部店铺（key=""）恒首项 = 本租户可见店并集（单平台/区域时 description 带「平台 区域」）。
      · 平台组（key=platform:<p>）：本租户跨 ≥2 平台才出，每平台一项。
      · 区域组（key=country:<c>）：跨 ≥2 区域才出，每区域一项。
      · 单店（key=shop:<id>，label=店名）：跨多平台或多区域时后缀「· 平台/区域」，单一时只显店名。
      · 真子集命名 scope（店数 < 全部）才追加；恰好=全部的（如 tts-id-all）跳过，避免与首项重复。
    **不含「全量」**——它与「全部店铺」同义、纯冗余（历史上由 LLM 脑补加出）。
    """
    all_shops = sorted(tenant_visible_shop_ids(account_id))
    dims = tenant_shop_dimensions(account_id)
    names = get_shop_names(account_id)
    platforms = sorted({d["platform"] for d in dims.values() if d.get("platform")})
    countries = sorted({d["country"] for d in dims.values() if d.get("country")})
    multi_dim = len(platforms) > 1 or len(countries) > 1

    def _pl(p: Optional[str]) -> Optional[str]:
        return _PLATFORM_DISPLAY.get(p, p) if p else None

    def _cl(c: Optional[str]) -> Optional[str]:
        return _COUNTRY_DISPLAY.get(c, c) if c else None

    opts: list[dict] = []
    # 全部店铺（恒首项）
    head = " ".join(x for x in [
        _pl(platforms[0]) if len(platforms) == 1 else None,
        _cl(countries[0]) if len(countries) == 1 else None,
    ] if x)
    all_desc = (f"{head}，" if head else "") + f"{len(all_shops)} 个店铺"
    opts.append({"key": "", "label": "全部店铺", "description": all_desc, "scope_type": "all"})

    # 平台组 / 区域组（跨多个才出）
    if len(platforms) > 1:
        for p in platforms:
            n = sum(1 for d in dims.values() if d.get("platform") == p)
            opts.append({"key": f"platform:{p}", "label": f"全部 {_pl(p)}",
                         "description": f"{n} 个店铺", "scope_type": "platform"})
    if len(countries) > 1:
        for c in countries:
            n = sum(1 for d in dims.values() if d.get("country") == c)
            opts.append({"key": f"country:{c}", "label": f"全部 {_cl(c)}",
                         "description": f"{n} 个店铺", "scope_type": "country"})

    # 逐店
    for sid in all_shops:
        label = names.get(sid, sid)
        if multi_dim:
            d = dims.get(sid, {})
            tag = "/".join(x for x in [_pl(d.get("platform")), _cl(d.get("country"))] if x)
            if tag:
                label = f"{label} · {tag}"
        opts.append({"key": f"shop:{sid}", "label": label,
                     "description": None, "scope_type": "shop"})

    # 真子集命名 scope
    all_set = set(all_shops)
    for s in list_scopes(account_id):
        sset = set(s["shop_ids"] or [])
        if sset and sset < all_set:
            opts.append({"key": s["scope_key"], "label": s["scope_name"],
                         "description": f"{len(sset)} 个店铺", "scope_type": "named"})
    return opts


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

    # 无 scope_key + 有显式店：校验都在本租户可见集内，按显式店过滤。
    if explicit_shops:
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

    # 无 scope_key + 无显式店 = 本租户「全部范围」：收口为本租户可见店并集，
    # **绝不退化为 shop_ids=[]（=不过滤=查全库）**。空集 → 哨兵，fail-closed。
    visible = sorted(tenant_visible_shop_ids(account_id))
    if not visible:
        return ScopeFilters(
            platform=platform, country=country, shop_ids=[NO_SHOP_SENTINEL],
            scope_key=None, display_text="（暂无可见店铺）",
        )
    return ScopeFilters(
        platform=platform,
        country=country,
        shop_ids=visible,
        scope_key=None,
        # platform/country 仍透传过滤；display 在有维度词时显示维度，否则「全部店铺」。
        display_text=(
            _display_text(platform=platform, country=country, shop_ids=[])
            if (platform or country)
            else "全部店铺"
        ),
    )
