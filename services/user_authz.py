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
由灰度开关 `settings.feishu_oauth.enforce_dialog_authz` 控制（防自锁）——对话路径的登记闸
在 `web/routes/data.py::_assert_dialog_registered` 读该开关（Phase 7 接通）。

本服务**不接收任何自然语言**。单租户阶段不引入 tenant_id（见 plan/09）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from core.db import SessionLocal
from core.tenancy import account_is_set, current_account, set_current_account
from models.base_models import UserRole
from services.scope_resolution import (
    ScopeError,
    ScopeFilters,
    expand_scope,
    resolve_filters,
)

ROLE_BOSS = "boss"
ROLE_OPERATOR = "operator"
ROLE_PENDING = "pending"  # 自助申请待审批的哨兵角色：恒 is_active=False，永不被当有效角色读
_VALID_ROLES = (ROLE_BOSS, ROLE_OPERATOR)


def account_for_open_id(open_id: str, *, channel: str = "feishu") -> Optional[str]:
    """按 open_id 反查它登记在哪个租户（user_roles.account_id）。

    飞书 open_id 跨租户天然不重复，故一个 open_id 至多命中一行。未登记/查库异常 → None
    （上层回落 DEFAULT，绝不因此报错阻断数据查询）。
    """
    if not open_id:
        return None
    try:
        session = SessionLocal()
        try:
            row = (
                session.query(UserRole)
                .filter(UserRole.channel == channel, UserRole.open_id == open_id)
                .first()
            )
            return row.account_id if row else None
        finally:
            session.close()
    except Exception:  # 查库异常绝不阻断主数据路径
        return None


def resolve_dialog_account(open_id: Optional[str]) -> str:
    """对话/MCP 路径定租户：① 已显式设(X-Account-Id 头/渲染身份) → 信任；
    ② 否则按 open_id 反查登记；③ 都没有 → DEFAULT_ACCOUNT。

    解析出非默认租户时写回 contextvar，使同请求内 binding 写端点等下游对齐（读写同租户）。
    """
    if account_is_set():
        return current_account()
    derived = account_for_open_id(open_id) if open_id else None
    if derived:
        set_current_account(derived)
        return derived
    return current_account()  # DEFAULT_ACCOUNT


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


def ensure_registration(
    open_id: str,
    *,
    name: Optional[str] = None,
    channel: str = "feishu",
    account_id: str = "ecom-app",
) -> str:
    """OAuth 通过后自动登记，把"抄 open_id + SSH 跑 CLI"那一步消灭掉。返回登记结果：

    - "existing"：已有行（任意状态）→ 原样不动。已停用的人保持停用，不被复活成 pending。
    - "boss"：该 (channel, account_id) 下 user_roles 为空 → bootstrap 第一个登录者为 boss
      + is_active（解"要先是 boss 才能进管理页审批别人"的鸡蛋问题；单租户里首登者=搭建者）。
    - "pending"：表非空但无此人 → 建待审批行（role=pending、scope=None、is_active=False），
      自动出现在老板的网页审批列表，老板一键选店铺范围即开通。范围前不可用（fail-closed）。

    open_id 为空 → 直接返回 "existing"（无可登记，交给上层 fail closed）。
    """
    if not open_id:
        return "existing"
    session = SessionLocal()
    try:
        existing = (
            session.query(UserRole)
            .filter(
                UserRole.channel == channel,
                UserRole.account_id == account_id,
                UserRole.open_id == open_id,
            )
            .first()
        )
        if existing is not None:
            return "existing"

        # 该 app 下是否一个角色都还没有：是 → bootstrap 首登者为 boss。
        is_first = (
            session.query(UserRole)
            .filter(UserRole.channel == channel, UserRole.account_id == account_id)
            .first()
            is None
        )
        note = f"申请人：{name}" if name else None
        if is_first:
            row = UserRole(
                channel=channel, account_id=account_id, open_id=open_id,
                role=ROLE_BOSS, allowed_scope_key=None, note=note, is_active=True,
            )
            session.add(row)
            session.commit()
            return "boss"

        row = UserRole(
            channel=channel, account_id=account_id, open_id=open_id,
            role=ROLE_PENDING, allowed_scope_key=None, note=note, is_active=False,
        )
        session.add(row)
        session.commit()
        return "pending"
    finally:
        session.close()


def get_registration_status(
    open_id: str,
    *,
    channel: str = "feishu",
    account_id: str = "ecom-app",
) -> str:
    """给未授权页区分文案用。返回 "pending"（待审批）/ "deactivated"（曾启用已停用）/ "none"（无登记）。

    与 get_user_permission 区别：本函数不要求 is_active，专为"已登录但拿不到权限"时判断到底
    卡在哪一步——好让 403 页给申请人友好的"已提交、等开通"提示而非冷冰冰的"无权限"。
    """
    if not open_id:
        return "none"
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
        if row is None:
            return "none"
        if row.role == ROLE_PENDING and not row.is_active:
            return "pending"
        return "deactivated" if not row.is_active else "none"
    finally:
        session.close()


def resolve_authorized_scope(
    perm: UserPermission,
    *,
    requested_scope_key: Optional[str] = None,
    requested_shop_ids: Optional[list[str]] = None,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    shop_id: Optional[str] = None,
) -> ScopeFilters:
    """把"用户请求的范围"夹进"该用户被授权的范围"，返回最终过滤条件。

    - boss：无上限。请求什么解析什么（resolve_filters 仍校验显式店铺已授权）；
      都不传 → 全范围。
    - operator：硬上限 = allowed_scope_key。把请求的 scope_key/shop_id(s) 展开并入"显式
      店铺"，交给 resolve_filters(scope_key=allowed)——落在 allowed 内则收窄、越界则抛
      ScopeError。operator 未配 allowed_scope_key（None）→ AuthzError（配置错，绝不放行全量）。

    `platform`/`country` 是正交的附加过滤维度（不参与越界判断、只透传给下游收窄），
    `shop_id` 是单值显式店，与 `requested_shop_ids` 同等并入。三者对 boss/operator 都生效。
    """
    requested_scope_key = requested_scope_key or None
    requested_shop_ids = list(requested_shop_ids or [])

    if perm.is_boss:
        # boss 在本租户内无上限，但**绝不跨租户**：全量范围由 resolve_filters 收口为本租户
        # 可见店并集（无 scope 无显式店时），空集 fail-closed。指定 scope/店则在本租户内解析。
        return resolve_filters(
            scope_key=requested_scope_key,
            platform=platform,
            country=country,
            shop_id=shop_id,
            shop_ids=requested_shop_ids or None,
            account_id=perm.account_id,
        )

    # operator：必须有硬上限，否则视为配置错（不允许 operator 看全量）。
    if not perm.allowed_scope_key:
        raise AuthzError(
            f"operator（open_id={perm.open_id}）未配置 allowed_scope_key，拒绝授权"
        )

    # 把请求的 scope_key/shop_id(s) 展开为具体店铺，并入显式 shop 集合，统一交给
    # resolve_filters 与 allowed 取交集；任何越界（不在 allowed 范围内）由它抛 ScopeError。
    explicit_shops = list(requested_shop_ids)
    if shop_id:
        explicit_shops.append(shop_id)
    if requested_scope_key:
        explicit_shops.extend(
            expand_scope(requested_scope_key, account_id=perm.account_id).shop_ids
        )
    explicit_shops = list(dict.fromkeys(s for s in explicit_shops if s))  # 去重保序

    return resolve_filters(
        scope_key=perm.allowed_scope_key,
        platform=platform,
        country=country,
        shop_ids=explicit_shops or None,
        account_id=perm.account_id,
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
