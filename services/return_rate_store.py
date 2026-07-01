"""退货率配置写入（default 级，供 /settings 阈值配置页）。

read 路径在 services/return_rate.py（三级优先级）。本模块只负责 boss 页面写「全店 default
级」退货率——写 return_rate_configs(scope_level="default", scope_value="")。category/sku 细化
留 3b。get_default_override 读当前 default 覆盖值（无行返 None，前端显 settings 默认）。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from models.base_models import ReturnRateConfig

_DEFAULT_LEVEL = "default"
_DEFAULT_VALUE = ""  # default 级 scope_value 恒空串


def get_default_override(session, *, account_id: str, platform: str = "tiktok_shop") -> Optional[Decimal]:
    """读某租户 default 级退货率覆盖值；无配置行返 None（调用方回落 settings 默认）。"""
    row = (
        session.query(ReturnRateConfig.return_rate)
        .filter_by(account_id=account_id, platform=platform,
                   scope_level=_DEFAULT_LEVEL, scope_value=_DEFAULT_VALUE)
        .first()
    )
    return Decimal(str(row[0])) if row else None


def upsert_default_return_rate(session, *, account_id: str, rate: Decimal,
                               platform: str = "tiktok_shop") -> ReturnRateConfig:
    """写/更新某租户 default 级退货率（小数，0.05=5%）。flush，由调用方 commit。"""
    row = (
        session.query(ReturnRateConfig)
        .filter_by(account_id=account_id, platform=platform,
                   scope_level=_DEFAULT_LEVEL, scope_value=_DEFAULT_VALUE)
        .first()
    )
    if row is None:
        row = ReturnRateConfig(account_id=account_id, platform=platform,
                               scope_level=_DEFAULT_LEVEL, scope_value=_DEFAULT_VALUE,
                               return_rate=rate)
        session.add(row)
    else:
        row.return_rate = rate
    session.flush()
    return row


def delete_default_return_rate(session, *, account_id: str, platform: str = "tiktok_shop") -> bool:
    """删除 default 级覆盖行（回落 settings 默认）。返回是否删到行。flush，由调用方 commit。"""
    n = (
        session.query(ReturnRateConfig)
        .filter_by(account_id=account_id, platform=platform,
                   scope_level=_DEFAULT_LEVEL, scope_value=_DEFAULT_VALUE)
        .delete(synchronize_session=False)
    )
    session.flush()
    return n > 0
