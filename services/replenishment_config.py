"""补货配置读写：系数（按范围覆盖默认）+ 超级爆品名单。运营可配，不硬编码。

读：get_effective_config（行优先、缺行回退 settings 默认）、get_super_hot_product_ids。
写：upsert_config、set_super_hot（供 CLI / WebUI 审核页复用）。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from core.config import settings
from models.base_models import ReplenishmentConfig, SuperHotProduct


@dataclass(frozen=True)
class EffectiveConfig:
    velocity_days: int
    normal_multiplier: float
    superhot_multiplier: float


def _config_key(account_id: Optional[str], scope_key: Optional[str]) -> str:
    return f"{account_id or ''}|{scope_key or ''}"


def _mark_key(account_id: Optional[str], product_id: str) -> str:
    return f"{account_id or ''}|{product_id}"


def get_effective_config(
    session, *, account_id: Optional[str] = None, scope_key: Optional[str] = None
) -> EffectiveConfig:
    """取生效补货系数：先查本范围行，缺则查租户级(scope_key=None)行，再缺回退 settings 默认。

    单字段级回退：行存在但某字段为 NULL 时该字段也用默认（允许只覆盖部分参数）。
    """
    row = (
        session.query(ReplenishmentConfig)
        .filter_by(config_key=_config_key(account_id, scope_key))
        .first()
    )
    if row is None and scope_key is not None:
        row = (
            session.query(ReplenishmentConfig)
            .filter_by(config_key=_config_key(account_id, None))
            .first()
        )
    vd = settings.replenish_velocity_days
    nm = settings.replenish_normal_multiplier
    sm = settings.replenish_superhot_multiplier
    if row is not None:
        if row.velocity_days is not None:
            vd = int(row.velocity_days)
        if row.normal_multiplier is not None:
            nm = float(row.normal_multiplier)
        if row.superhot_multiplier is not None:
            sm = float(row.superhot_multiplier)
    return EffectiveConfig(velocity_days=vd, normal_multiplier=nm, superhot_multiplier=sm)


def get_super_hot_product_ids(session, *, account_id: Optional[str] = None) -> set[str]:
    """本租户当前生效（is_active）的超级爆品 product_id 集合。"""
    q = session.query(SuperHotProduct.product_id).filter(SuperHotProduct.is_active.is_(True))
    if account_id is not None:
        q = q.filter(SuperHotProduct.account_id == account_id)
    return {pid for (pid,) in q.all()}


def upsert_config(
    session,
    *,
    account_id: Optional[str] = None,
    scope_key: Optional[str] = None,
    velocity_days: Optional[int] = None,
    normal_multiplier: Optional[float] = None,
    superhot_multiplier: Optional[float] = None,
) -> ReplenishmentConfig:
    """写补货系数配置（部分字段可 None=不覆盖默认）。flush，由调用方 commit。"""
    key = _config_key(account_id, scope_key)
    row = session.query(ReplenishmentConfig).filter_by(config_key=key).first()
    if row is None:
        row = ReplenishmentConfig(config_key=key, account_id=account_id, scope_key=scope_key)
        session.add(row)
    row.velocity_days = velocity_days
    row.normal_multiplier = Decimal(str(normal_multiplier)) if normal_multiplier is not None else None
    row.superhot_multiplier = (
        Decimal(str(superhot_multiplier)) if superhot_multiplier is not None else None
    )
    session.flush()
    return row


def set_super_hot(
    session,
    *,
    product_id: str,
    account_id: Optional[str] = None,
    is_active: bool = True,
    note: Optional[str] = None,
) -> SuperHotProduct:
    """标记/撤销超级爆品（按款）。flush，由调用方 commit。"""
    key = _mark_key(account_id, product_id)
    row = session.query(SuperHotProduct).filter_by(mark_key=key).first()
    if row is None:
        row = SuperHotProduct(mark_key=key, account_id=account_id, product_id=product_id)
        session.add(row)
    row.is_active = is_active
    if note is not None:
        row.note = note
    session.flush()
    return row
