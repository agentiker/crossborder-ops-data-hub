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
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from core.db import SessionLocal
from core.tenancy import set_current_account
from models.base_models import UserRole
from services.audit import record_audit_event_safe
from services.biz_config import (
    CONFIGURABLE_KEYS,
    default_of,
    delete_config,
    get_biz_config_overrides,
    upsert_config_num,
)
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
    name: Optional[str]
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
        name=row.name,
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
    name: Optional[str] = None  # 用户名（飞书昵称）；自助登记自动落，boss 手动建号可填
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
            before = None
            row = UserRole(
                channel=channel,
                account_id=account_id,
                open_id=body.open_id,
                role=body.role,
                allowed_scope_key=scope_key,
                name=body.name,
                note=body.note,
                is_active=True,
            )
            session.add(row)
        else:
            # commit 前捕获旧值快照（权限变更审计须记 before/after）
            before = {
                "role": row.role,
                "allowed_scope_key": row.allowed_scope_key,
                "is_active": row.is_active,
            }
            row.role = body.role
            row.allowed_scope_key = scope_key
            if body.name is not None:
                row.name = body.name
            if body.note is not None:
                row.note = body.note
            row.is_active = True
        session.commit()
        out = _role_out(row)
        record_audit_event_safe(
            session,
            event_type="authz_change", event_action="role.upsert",
            actor_open_id=boss.open_id, actor_source="web", account_id=account_id,
            target=body.open_id,
            summary=("新建" if before is None else "更新") + f"角色 {body.role}",
            before=before,
            after={"role": body.role, "allowed_scope_key": scope_key, "is_active": True},
        )
        return out
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
        before = {"role": row.role, "is_active": row.is_active}
        row.is_active = False
        session.commit()
        out = _role_out(row)
        record_audit_event_safe(
            session,
            event_type="authz_change", event_action="role.deactivate",
            actor_open_id=boss.open_id, actor_source="web", account_id=boss.account_id,
            target=body.open_id, summary=f"停用角色 {row.role}",
            before=before, after={"is_active": False},
        )
        return out
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
        record_audit_event_safe(
            session,
            event_type="account_op", event_action="product_costs.import",
            actor_open_id=boss.open_id, actor_source="web", account_id=boss.account_id,
            target=file.filename,
            summary=f"导入产品成本 CSV：{file.filename}",
            after=result,
        )
        return result
    finally:
        session.close()


# ── 业务阈值配置（/settings 页面，boss-only）──────────────────────────────────
# 一张通用配置页统一管理三类来源的阈值：数值类走 biz_configs 表（services/biz_config），
# 退货率 default 级走 return_rate_configs，补货三系数走 replenishment_config。路由按
# CONFIGURABLE_KEYS[key]["source"] 分派，对前端透明（前端只看到一组 {key,value}）。


class BizConfigItemOut(BaseModel):
    config_key: str
    label: str
    unit: Optional[str]
    type: str  # int / float
    group: str
    hint: Optional[str] = None
    min: Optional[float] = None
    max: Optional[float] = None
    default_value: float
    current_value: float
    is_overridden: bool  # 是否已被本租户覆盖（非默认）


class BizConfigListOut(BaseModel):
    items: list[BizConfigItemOut]


class BizConfigUpsertIn(BaseModel):
    config_key: str
    value: float


class BizConfigResetIn(BaseModel):
    config_key: str


def _replenish_field(config_key: str) -> str:
    """补货 key → ReplenishmentConfig 列名。"""
    return {
        "replenish_velocity_days": "velocity_days",
        "replenish_normal_multiplier": "normal_multiplier",
        "replenish_superhot_multiplier": "superhot_multiplier",
    }[config_key]


def _read_current(session, account_id: str, config_key: str) -> tuple[Decimal, bool]:
    """按 source 读某 key 的当前生效值 + 是否被覆盖。返回 (current_value, is_overridden)。"""
    meta = CONFIGURABLE_KEYS[config_key]
    source = meta["source"]
    default = default_of(config_key)

    if source == "biz_config":
        overrides = get_biz_config_overrides(session, account_id)
        if config_key in overrides:
            return overrides[config_key], True
        return default, False

    if source == "return_rate":
        from services.return_rate_store import get_default_override
        ov = get_default_override(session, account_id=account_id)
        return (ov, True) if ov is not None else (default, False)

    if source == "replenishment":
        from models.base_models import ReplenishmentConfig
        row = (
            session.query(ReplenishmentConfig)
            .filter_by(config_key=f"{account_id}|")  # 租户级 scope_key=None → key 尾部空
            .first()
        )
        if row is not None:
            raw = getattr(row, _replenish_field(config_key))
            if raw is not None:
                return Decimal(str(raw)), True
        return default, False

    return default, False


def _dispatch_write(session, account_id: str, config_key: str, value: Decimal) -> None:
    """按 source 写入某 key 的覆盖值。"""
    source = CONFIGURABLE_KEYS[config_key]["source"]
    if source == "biz_config":
        upsert_config_num(session, account_id=account_id, config_key=config_key, value=value)
    elif source == "return_rate":
        from services.return_rate_store import upsert_default_return_rate
        upsert_default_return_rate(session, account_id=account_id, rate=value)
    elif source == "replenishment":
        # 补货三系数共享一行：读现有三字段原始值，改目标字段，其它保持（None=回落默认）。
        from services.replenishment_config import upsert_config
        vals = _replenish_raw_values(session, account_id)
        field = _replenish_field(config_key)
        vals[field] = value
        upsert_config(
            session, account_id=account_id, scope_key=None,
            velocity_days=int(vals["velocity_days"]) if vals["velocity_days"] is not None else None,
            normal_multiplier=float(vals["normal_multiplier"]) if vals["normal_multiplier"] is not None else None,
            superhot_multiplier=float(vals["superhot_multiplier"]) if vals["superhot_multiplier"] is not None else None,
        )


def _dispatch_reset(session, account_id: str, config_key: str) -> None:
    """按 source 删除某 key 的覆盖（回落默认）。"""
    source = CONFIGURABLE_KEYS[config_key]["source"]
    if source == "biz_config":
        delete_config(session, account_id=account_id, config_key=config_key)
    elif source == "return_rate":
        from services.return_rate_store import delete_default_return_rate
        delete_default_return_rate(session, account_id=account_id)
    elif source == "replenishment":
        # 把目标字段设回 None（回落默认），其它两个保持原值。
        from services.replenishment_config import upsert_config
        vals = _replenish_raw_values(session, account_id)
        vals[_replenish_field(config_key)] = None
        upsert_config(
            session, account_id=account_id, scope_key=None,
            velocity_days=int(vals["velocity_days"]) if vals["velocity_days"] is not None else None,
            normal_multiplier=float(vals["normal_multiplier"]) if vals["normal_multiplier"] is not None else None,
            superhot_multiplier=float(vals["superhot_multiplier"]) if vals["superhot_multiplier"] is not None else None,
        )


def _replenish_raw_values(session, account_id: str) -> dict:
    """读补货租户级行的三字段**原始值**（含 None，不回落）。"""
    from models.base_models import ReplenishmentConfig
    row = (
        session.query(ReplenishmentConfig)
        .filter_by(config_key=f"{account_id}|")
        .first()
    )
    if row is None:
        return {"velocity_days": None, "normal_multiplier": None, "superhot_multiplier": None}
    return {
        "velocity_days": row.velocity_days,
        "normal_multiplier": row.normal_multiplier,
        "superhot_multiplier": row.superhot_multiplier,
    }


@router.get("/biz-configs", response_model=BizConfigListOut, include_in_schema=False)
async def list_biz_configs(boss: UserPermission = Depends(require_boss)):
    """列出本租户所有可配业务阈值（元数据 + 当前生效值 + 是否覆盖）。boss-only。"""
    set_current_account(boss.account_id)
    session = SessionLocal()
    try:
        items = []
        for key, meta in CONFIGURABLE_KEYS.items():
            current, overridden = _read_current(session, boss.account_id, key)
            items.append(BizConfigItemOut(
                config_key=key,
                label=meta["label"], unit=meta.get("unit"), type=meta["type"],
                group=meta["group"], hint=meta.get("hint"),
                min=meta.get("min"), max=meta.get("max"),
                default_value=float(default_of(key)),
                current_value=float(current),
                is_overridden=overridden,
            ))
        return BizConfigListOut(items=items)
    finally:
        session.close()


@router.post("/biz-configs", response_model=BizConfigItemOut, include_in_schema=False)
async def upsert_biz_config(body: BizConfigUpsertIn, boss: UserPermission = Depends(require_boss)):
    """覆盖某业务阈值（按 source 分派写入）。校验白名单 + 范围。boss-only，钉死 boss 租户。"""
    set_current_account(boss.account_id)
    key = body.config_key
    meta = CONFIGURABLE_KEYS.get(key)
    if meta is None:
        raise HTTPException(status_code=400, detail=f"未知配置项：{key}")
    try:
        value = Decimal(str(body.value))
    except (InvalidOperation, ValueError):
        raise HTTPException(status_code=400, detail="value 必须是数字")
    lo, hi = meta.get("min"), meta.get("max")
    if (lo is not None and value < Decimal(str(lo))) or (hi is not None and value > Decimal(str(hi))):
        raise HTTPException(status_code=400, detail=f"{meta['label']} 须在 [{lo}, {hi}] 之间")
    if meta["type"] == "int" and value != value.to_integral_value():
        raise HTTPException(status_code=400, detail=f"{meta['label']} 须为整数")

    session = SessionLocal()
    try:
        before_val, before_ov = _read_current(session, boss.account_id, key)
        _dispatch_write(session, boss.account_id, key, value)
        session.commit()
        after_val, after_ov = _read_current(session, boss.account_id, key)
        record_audit_event_safe(
            session,
            event_type="account_op", event_action="biz_config.upsert",
            actor_open_id=boss.open_id, actor_source="web", account_id=boss.account_id,
            target=key, summary=f"设置 {meta['label']} = {body.value}",
            before={"value": float(before_val), "is_overridden": before_ov},
            after={"value": float(after_val), "is_overridden": after_ov},
        )
        return BizConfigItemOut(
            config_key=key, label=meta["label"], unit=meta.get("unit"), type=meta["type"],
            group=meta["group"], hint=meta.get("hint"), min=lo, max=hi,
            default_value=float(default_of(key)), current_value=float(after_val),
            is_overridden=after_ov,
        )
    finally:
        session.close()


@router.post("/biz-configs/reset", response_model=BizConfigItemOut, include_in_schema=False)
async def reset_biz_config(body: BizConfigResetIn, boss: UserPermission = Depends(require_boss)):
    """恢复某业务阈值为默认（删除本租户覆盖）。boss-only。"""
    set_current_account(boss.account_id)
    key = body.config_key
    meta = CONFIGURABLE_KEYS.get(key)
    if meta is None:
        raise HTTPException(status_code=400, detail=f"未知配置项：{key}")
    session = SessionLocal()
    try:
        before_val, before_ov = _read_current(session, boss.account_id, key)
        _dispatch_reset(session, boss.account_id, key)
        session.commit()
        after_val, after_ov = _read_current(session, boss.account_id, key)
        record_audit_event_safe(
            session,
            event_type="account_op", event_action="biz_config.reset",
            actor_open_id=boss.open_id, actor_source="web", account_id=boss.account_id,
            target=key, summary=f"恢复 {meta['label']} 为默认",
            before={"value": float(before_val), "is_overridden": before_ov},
            after={"value": float(after_val), "is_overridden": after_ov},
        )
        return BizConfigItemOut(
            config_key=key, label=meta["label"], unit=meta.get("unit"), type=meta["type"],
            group=meta["group"], hint=meta.get("hint"),
            min=meta.get("min"), max=meta.get("max"),
            default_value=float(default_of(key)), current_value=float(after_val),
            is_overridden=after_ov,
        )
    finally:
        session.close()
