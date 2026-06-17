"""统一权限闸：单租户内分角色的硬权限上限（plan/14 方案 B 的唯一真相）。

读 `user_roles` 表，把"谁(open_id)能看什么范围"沉到数据层，覆盖三处：
- 看板网站 /board（飞书 OAuth 登录态）；
- 对话侧 web/routes/data.py::_resolve_scope（所有 ops_* MCP 工具）；
- 主动推送（按收件人 open_id 的 allowed_scope 裁内容）。

角色语义：
- boss：看全部，不夹范围（请求什么解析什么；不传则全范围）。
- operator：被钉死在 allowed_scope_key 且**不可越界**——任何请求都先夹进 allowed_scope，
  越界天然被拒。越界判断不在这里重写，全靠 `scope_resolution.resolve_filters` 的
  「scope+显式 shop 取交集、越界抛 ScopeError」语义（scope_resolution.py:166-189）。

未登记/停用 open_id = fail closed：`get_user_permission` 返回 None、
`assert_authorized` 抛 `AuthzError`（上层转 403 / 拒答文案）。对话侧是否真的拒，
由灰度开关 `settings.feishu_oauth.enforce_dialog_authz` 在 Phase 5 控制（防自锁）。

本服务**不接收任何自然语言**。单租户阶段不引入 tenant_id（见 plan/09）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from core.db import SessionLocal
from models.base_models import UserRole
from services.scope_resolution import ScopeError, ScopeFilters, expand_scope, resolve_filters

ROLE_BOSS = "boss"
ROLE_OPERATOR = "operator"
_VALID_ROLES = (ROLE_BOSS, ROLE_OPERATOR)


class AuthzError(ValueError):
    """权限校验失败：未登记/停用 open_id，或 operator 配置缺 allowed_scope_key。

    与 `ScopeError`（越界，由 resolve_filters 抛）区分：二者在 API 层都应转 403，
    但语义不同（无权限 vs 越界），便于上层给不同文案。
    """


@dataclass
class UserPermission:
    """一个 open_id 的权限快照（来自 user_roles 一行）。"""
    open_id: str
    role: str  # boss / operator
    allowed_scope_key: Optional[str]  # operator 的硬上限；boss 恒为 None 语义忽略
    channel: str
    account_id: str
    note: Optional[str] = None

    @property
    def is_boss(self) -> bool:
        return self.role == ROLE_BOSS


def get_user_permission(
    open_id: str,
    *,
    channel: str = "feishu",
    account_id: str = "ecom-app",
) -> Optional[UserPermission]:
    """读 user_roles 拿权限快照。无行 / 已停用 / open_id 为空 → None（fail closed）。"""
    if not open_id:
        return None
    session = SessionLocal()
    try:
        row = (
            session.query(UserRole)
            .filter(
                UserRole.channel == channel,
                UserRole.account_id == account_id,
                UserRole.open_id == open_id,
            )
            .first()
        )
        if row is None or not row.is_active:
            return None
        return UserPermission(
            open_id=row.open_id,
            role=row.role,
            allowed_scope_key=row.allowed_scope_key,
            channel=row.channel,
            account_id=row.account_id,
            note=row.note,
        )
    finally:
        session.close()


def resolve_authorized_scope(
    perm: UserPermission,
    *,
    requested_scope_key: Optional[str] = None,
    requested_shop_ids: Optional[list[str]] = None,
) -> ScopeFilters:
    """把"用户请求的范围"夹进"该用户被授权的范围"，返回最终过滤条件。

    - boss：无上限。请求什么解析什么（resolve_filters 仍校验显式店铺已授权）；
      都不传 → 全范围。
    - operator：硬上限 = allowed_scope_key。把请求的 scope_key 展开成 shop_ids、与
      请求的 shop_ids 合并，作为"显式店铺"交给 resolve_filters(scope_key=allowed)——
      落在 allowed 内则收窄、越界则抛 ScopeError。operator 未配 allowed_scope_key
      （None）→ AuthzError（配置错，绝不放行全量）。
    """
    requested_scope_key = requested_scope_key or None
    requested_shop_ids = list(requested_shop_ids or [])

    if perm.is_boss:
        # 无上限：直接按请求解析（不传即全范围）。
        return resolve_filters(
            scope_key=requested_scope_key,
            shop_ids=requested_shop_ids or None,
        )

    # operator：必须有硬上限，否则视为配置错（不允许 operator 看全量）。
    if not perm.allowed_scope_key:
        raise AuthzError(
            f"operator（open_id={perm.open_id}）未配置 allowed_scope_key，拒绝授权"
        )

    # 把请求的 scope_key 展开为具体店铺，并入显式 shop 集合，统一交给 resolve_filters
    # 与 allowed 取交集；任何越界（不在 allowed 范围内）由 resolve_filters 抛 ScopeError。
    explicit_shops = list(requested_shop_ids)
    if requested_scope_key:
        explicit_shops.extend(expand_scope(requested_scope_key).shop_ids)
    explicit_shops = list(dict.fromkeys(s for s in explicit_shops if s))  # 去重保序

    return resolve_filters(
        scope_key=perm.allowed_scope_key,
        shop_ids=explicit_shops or None,
    )


def assert_authorized(
    open_id: str,
    *,
    channel: str = "feishu",
    account_id: str = "ecom-app",
    requested_scope_key: Optional[str] = None,
    requested_shop_ids: Optional[list[str]] = None,
) -> ScopeFilters:
    """对话侧/推送侧便捷封装：取权限→夹紧→返回 ScopeFilters，否则抛错。

    未登记/停用 open_id → AuthzError（fail closed）；越界 → ScopeError。
    上层（Phase 5 的 _resolve_scope / 推送）据此转 403 或拒答文案。
    """
    perm = get_user_permission(open_id, channel=channel, account_id=account_id)
    if perm is None:
        raise AuthzError(f"open_id={open_id} 未登记或已停用，无数据权限")
    return resolve_authorized_scope(
        perm,
        requested_scope_key=requested_scope_key,
        requested_shop_ids=requested_shop_ids,
    )
