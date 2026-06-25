"""预估退货率取数（阶段3a，可配置率占位）。

预估退货 = 退货率 × 当期 GMV，避免真实退货滞后高估当期利润（真实采集+历史率回填校准属 3b）。
取数优先级：sku > category > default(表) > settings.estimated_return_rate_default。
MVP 聚合只用全店 default 一级（category/sku 入参预留 3b 细化）。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from core.config import settings
from core.db import SessionLocal
from models.base_models import ReturnRateConfig


def _lookup(session, *, account_id, platform, scope_level, scope_value) -> Optional[Decimal]:
    row = (
        session.query(ReturnRateConfig.return_rate)
        .filter_by(
            account_id=account_id,
            platform=platform,
            scope_level=scope_level,
            scope_value=scope_value,
        )
        .first()
    )
    return Decimal(str(row[0])) if row else None


def get_return_rate(
    *,
    account_id: Optional[str],
    platform: str = "tiktok_shop",
    category: Optional[str] = None,
    sku: Optional[str] = None,
    session=None,
) -> Decimal:
    """按 sku > category > default 表 > settings 常数 的优先级返回预估退货率（小数，0.05=5%）。"""
    own = session is None
    session = session or SessionLocal()
    try:
        if sku:
            r = _lookup(session, account_id=account_id, platform=platform,
                        scope_level="sku", scope_value=sku)
            if r is not None:
                return r
        if category:
            r = _lookup(session, account_id=account_id, platform=platform,
                        scope_level="category", scope_value=category)
            if r is not None:
                return r
        r = _lookup(session, account_id=account_id, platform=platform,
                    scope_level="default", scope_value="")
        if r is not None:
            return r
        return Decimal(str(settings.estimated_return_rate_default))
    finally:
        if own:
            session.close()
