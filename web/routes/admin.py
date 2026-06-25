"""角色权限可配置 admin API（plan/15 Phase C，boss-only）。

把本地 CLI scripts/user_admin 的 list/set(upsert)/deactivate 三段业务规则原样搬到
HTTP 端，给 Web 对话端的老板在浏览器里管理 user_roles（services/user_authz 的真相源），
免登服务器跑 CLI。校验口径与 cmd_set 完全一致：
- operator 必须给 allowed_scope_key，且须能通过 expand_scope（未知/停用 scope → 400）；
- boss 忽略 scope_key（存 None）；
- upsert：有则更新、无则创建；account_id 默认 ecom-app、channel 默认 feishu。

鉴权：boss-only。复用 require_web_user_api 拿登录 open_id（未登录 401），再查
user_authz 确认 role==boss，否则 403（与既有 AuthzError/ScopeError→403 约定一致）。
"""

from __future__ import annotations

import csv
import io
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from core.db import SessionLocal
from core.tenancy import set_current_account
from models.base_models import UserRole
from services.product_cost_store import import_costs_from_rows
from services.scope_resolution import ScopeError, expand_scope, list_scopes
from services.user_authz import UserPermission, get_user_permission
from web.web_security import require_web_user_api

router = APIRouter(prefix="/api/admin", tags=["管理"])


# ── boss-only 守卫 ─────────────────────────────────────────────────────────────


def require_boss(perm: UserPermission = Depends(require_web_user_api)) -> UserPermission:
    """boss-only 依赖：先过登录闸（require_web_user_api：未登录 401、未授角色 403），
    再确认是 boss，否则 403（operator/未登记一律拒绝管理权限表）。"""
    if perm is None or perm.role != "boss":
        raise HTTPException(status_code=403, detail="仅 boss 可管理用户角色")
    return perm


# ── 请求/响应模型 ──────────────────────────────────────────────────────────────


class RoleOut(BaseModel):
    open_id: str
    role: str
    allowed_scope_key: Optional[str]
    note: Optional[str]
    is_active: bool
    account_id: str
    channel: str
    created_at: Optional[str] = None  # ISO 字符串，前端按申请时间排序/显示待审批


def _role_out(row: UserRole) -> "RoleOut":
    """UserRole 行 → RoleOut（统一序列化，含 created_at ISO 化）。"""
    return RoleOut(
        open_id=row.open_id,
        role=row.role,
        allowed_scope_key=row.allowed_scope_key,
        note=row.note,
        is_active=row.is_active,
        account_id=row.account_id,
        channel=row.channel,
        created_at=row.created_at.isoformat() if row.created_at else None,
    )


class RoleListOut(BaseModel):
    items: list[RoleOut]


class RoleUpsertIn(BaseModel):
    open_id: str
    role: str  # boss / operator
    scope_key: Optional[str] = None  # operator 必填且须 active；boss 忽略
    note: Optional[str] = None
    account_id: str = "ecom-app"
    channel: str = "feishu"


class RoleDeactivateIn(BaseModel):
    open_id: str
    account_id: str = "ecom-app"
    channel: str = "feishu"


class ScopeOptionOut(BaseModel):
    scope_key: str
    scope_name: str


class ScopeListOut(BaseModel):
    items: list[ScopeOptionOut]


# ── 路由 ───────────────────────────────────────────────────────────────────────


@router.get("/roles", response_model=RoleListOut, include_in_schema=False)
async def list_roles(boss: UserPermission = Depends(require_boss)):
    """列出**本租户**的 user_roles（结构化 JSON）。对应 CLI cmd_list。

    多租户隔离：只返回 boss 自己 (channel, account_id) 下的角色，gtl boss 看不到 ecom-app 的人。
    """
    set_current_account(boss.account_id)
    session = SessionLocal()
    try:
        rows = (
            session.query(UserRole)
            .filter(
                UserRole.channel == boss.channel,
                UserRole.account_id == boss.account_id,
            )
            .order_by(UserRole.role, UserRole.open_id)
            .all()
        )
        return RoleListOut(items=[_role_out(r) for r in rows])
    finally:
        session.close()


@router.post("/roles", response_model=RoleOut, include_in_schema=False)
async def upsert_role(body: RoleUpsertIn, boss: UserPermission = Depends(require_boss)):
    """创建/更新一个用户角色（upsert）。校验照搬 CLI cmd_set。

    多租户防越权：账号维度**强制用 boss 自己的** (channel, account_id)，忽略 body 里的
    account_id/channel——gtl boss 不能往 ecom-app 租户里增删改人。
    """
    set_current_account(boss.account_id)
    if body.role not in ("boss", "operator"):
        raise HTTPException(status_code=400, detail="role 仅支持 boss / operator")
    account_id, channel = boss.account_id, boss.channel

    scope_key = (body.scope_key or "").strip() or None
    if body.role == "operator":
        if not scope_key:
            raise HTTPException(
                status_code=400, detail="operator 必须指定 scope_key（不可越界的硬上限）"
            )
        # 校验：未知/停用 scope 直接拒，绝不落脏权限。多租户：只在 boss 自己租户内找 scope，
        # gtl boss 给 operator 配的 scope 必须是 gtl 名下的，配 ecom 的 scope_key → 校验失败。
        try:
            expand_scope(scope_key, account_id=account_id)
        except ScopeError as e:
            raise HTTPException(status_code=400, detail=f"scope 校验失败：{e}") from e
    else:  # boss 看全部，忽略 scope（存 None）
        scope_key = None

    session = SessionLocal()
    try:
        row = (
            session.query(UserRole)
            .filter(
                UserRole.channel == channel,
                UserRole.account_id == account_id,
                UserRole.open_id == body.open_id,
            )
            .first()
        )
        if row is None:
            row = UserRole(
                channel=channel,
                account_id=account_id,
                open_id=body.open_id,
                role=body.role,
                allowed_scope_key=scope_key,
                note=body.note,
                is_active=True,
            )
            session.add(row)
        else:
            row.role = body.role
            row.allowed_scope_key = scope_key
            if body.note is not None:
                row.note = body.note
            row.is_active = True
        session.commit()
        return _role_out(row)
    finally:
        session.close()


@router.get("/scopes", response_model=ScopeListOut, include_in_schema=False)
async def list_admin_scopes(boss: UserPermission = Depends(require_boss)):
    """boss 选择 operator 数据范围时的可选项；纯只读，复用 services.list_scopes()。

    多租户：只列 boss 自己租户的 scope，gtl boss 选不到 ecom-app 的范围。
    """
    set_current_account(boss.account_id)
    return ScopeListOut(items=[
        ScopeOptionOut(scope_key=s["scope_key"], scope_name=s["scope_name"])
        for s in list_scopes(boss.account_id)
    ])


@router.post("/roles/deactivate", response_model=RoleOut, include_in_schema=False)
async def deactivate_role(body: RoleDeactivateIn, boss: UserPermission = Depends(require_boss)):
    """停用一个用户角色（is_active=False）。未找到返 404。对应 CLI cmd_deactivate。

    多租户防越权：只能停用 boss 自己 (channel, account_id) 下的人，忽略 body 的 account_id/channel。
    """
    set_current_account(boss.account_id)
    session = SessionLocal()
    try:
        row = (
            session.query(UserRole)
            .filter(
                UserRole.channel == boss.channel,
                UserRole.account_id == boss.account_id,
                UserRole.open_id == body.open_id,
            )
            .first()
        )
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"未找到用户角色：{body.open_id}"
            )
        row.is_active = False
        session.commit()
        return _role_out(row)
    finally:
        session.close()


# ── 产品成本导入（阶段3a，利润公式的成本项）────────────────────────────────────


@router.post("/product-costs/import", include_in_schema=False)
async def import_product_costs(
    file: UploadFile = File(...),
    boss: UserPermission = Depends(require_boss),
):
    """CSV 批量导入 SKU 产品成本（RMB 含运费），boss-only。

    CSV 列：seller_sku,unit_cost_rmb[,note]（首行表头）。按 (account_id, platform, seller_sku)
    幂等 upsert。坏行收集进 errors 不中断。多租户：钉死 boss 自己的 account_id。
    """
    set_current_account(boss.account_id)
    try:
        content = (await file.read()).decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="CSV 须为 UTF-8 编码")
    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames or "seller_sku" not in reader.fieldnames:
        raise HTTPException(
            status_code=400, detail="CSV 缺表头列 seller_sku（需 seller_sku,unit_cost_rmb[,note]）"
        )
    rows = [dict(r) for r in reader]
    session = SessionLocal()
    try:
        result = import_costs_from_rows(
            session, rows, account_id=boss.account_id, platform="tiktok_shop"
        )
        session.commit()
        return result
    finally:
        session.close()
