"""按租户可配业务阈值的统一读取层（查 biz_configs 表 → 回落 core/config settings）。

boss 在 /settings 页面调的阈值存 biz_configs 表；本模块提供「按 account_id 查表、查不到回落
settings.<key> 全局默认」的统一读取。account_id 缺省从 core.tenancy contextvar 兜底取
（web 请求有 perm.account_id、flow 有 set_current_account），故取数点无需层层传 account_id。

进程内轻缓存（按 account_id+key），写入后 clear_config_cache() 失效。查库任何异常吞掉回落
settings（fail-safe，绝不让配置查询中断业务）。

CONFIGURABLE_KEYS 白名单三用：① 校验（拒非法 key/越界值）② 前端表单元数据（label/unit/范围/
分组）③ 分派 source——数值类走 biz_configs 表（本模块 get_config_*），退货率默认级走
return_rate_configs、补货三系数走 replenishment_config（路由按 source 分派，见 web/routes/admin.py）。
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

from core.config import settings
from core.tenancy import current_account_or_none

logger = logging.getLogger(__name__)


# ── 可配阈值白名单：key → 元数据 ────────────────────────────────────────────
# source: "biz_config"=存 biz_configs 表（本模块读）；"return_rate"/"replenishment"=专表（路由分派）。
# type: "int"（天/件/时，读时取整）/"float"（比例/倍数，保留小数）。
# default 运行时从 settings.<key> 取（不硬编码，避免与 core/config 漂移）。
CONFIGURABLE_KEYS: dict[str, dict] = {
    # 爆款 / 新品
    "hotsell_daily_units_threshold": {
        "label": "爆单阈值", "unit": "件/天", "type": "int", "min": 1, "max": 100000,
        "group": "爆款与新品", "source": "biz_config",
        "hint": "某商品单日已付款销量达到此值即标记爆单",
    },
    "new_product_lookback_days": {
        "label": "新品窗口", "unit": "天", "type": "int", "min": 1, "max": 365,
        "group": "爆款与新品", "source": "biz_config",
        "hint": "商品上架在此天数内算新品",
    },
    # 库存
    "stock_cover_critical_days": {
        "label": "库存告急线", "unit": "天", "type": "int", "min": 0, "max": 365,
        "group": "库存预警", "source": "biz_config",
        "hint": "可售天数低于此值记为断货告急（最高档风险）",
    },
    "stock_cover_warning_days": {
        "label": "库存偏低线", "unit": "天", "type": "int", "min": 1, "max": 365,
        "group": "库存预警", "source": "biz_config",
        "hint": "可售天数低于此值且高于告急线记为偏低预警",
    },
    "stock_velocity_window_days": {
        "label": "销速窗口", "unit": "天", "type": "int", "min": 1, "max": 90,
        "group": "库存预警", "source": "biz_config",
        "hint": "日均销速按近此天数的已付款销量折算",
    },
    # 发货
    "fulfillment_warning_hours": {
        "label": "发货超时预警", "unit": "小时", "type": "int", "min": 1, "max": 168,
        "group": "发货时效", "source": "biz_config",
        "hint": "距平台发货截止不足此小时数记为临界",
    },
    # 采集
    "unsettled_lookback_days": {
        "label": "未结算回看天数", "unit": "天", "type": "int", "min": 1, "max": 30,
        "group": "数据采集", "source": "biz_config",
        "hint": "未结算预估费用采集按下单时间回看的天数",
    },
    # 利润：退货率（读 return_rate_configs default 级）
    "estimated_return_rate_default": {
        "label": "默认退货率", "unit": "%", "type": "float", "min": 0, "max": 1,
        "group": "利润预估", "source": "return_rate",
        "hint": "无细分配置时的全店预估退货率（0.05=5%）",
    },
    # 补货三系数（读 replenishment_config 租户级）
    "replenish_velocity_days": {
        "label": "补货销速窗口", "unit": "天", "type": "int", "min": 1, "max": 180,
        "group": "补货", "source": "replenishment",
        "hint": "补货建议按近此天数销量作基数",
    },
    "replenish_normal_multiplier": {
        "label": "普通补货倍数", "unit": "倍", "type": "float", "min": 0.1, "max": 20,
        "group": "补货", "source": "replenishment",
        "hint": "普通 SKU 目标备货 = 销量 × 此倍数",
    },
    "replenish_superhot_multiplier": {
        "label": "爆品补货倍数", "unit": "倍", "type": "float", "min": 0.1, "max": 20,
        "group": "补货", "source": "replenishment",
        "hint": "超级爆品目标备货 = 销量 × 此倍数",
    },
}

# 本模块（get_config_*）只负责 source=biz_config 的 key；其余由路由分派到专表。
_BIZ_CONFIG_KEYS = {k for k, m in CONFIGURABLE_KEYS.items() if m.get("source") == "biz_config"}

# ── 进程内缓存：(account_id, config_key) → Decimal ────────────────────────────
_CACHE: dict[tuple[Optional[str], str], Decimal] = {}


def clear_config_cache() -> None:
    """清空进程内配置缓存（写入后调，令下次读取拿新值）。"""
    _CACHE.clear()


def default_of(config_key: str) -> Decimal:
    """某 key 的全局默认值（settings.<key>），Decimal。"""
    return Decimal(str(getattr(settings, config_key)))


def get_config_num(config_key: str, *, account_id: Optional[str] = None,
                   session=None) -> Decimal:
    """按租户取数值型阈值：查 biz_configs 命中返 value_num，否则回落 settings.<key>。

    account_id 缺省从 contextvar 兜底。仅对 source=biz_config 的 key 查表；其它 key（退货率/
    补货，读专表）直接回落 settings（本函数不是它们的读取路径，仅作兜底不报错）。
    """
    if account_id is None:
        account_id = current_account_or_none()

    ck = (account_id, config_key)
    cached = _CACHE.get(ck)
    if cached is not None:
        return cached

    if account_id is not None and config_key in _BIZ_CONFIG_KEYS:
        try:
            from core.db import SessionLocal
            from models.base_models import BizConfig

            own = session is None
            s = session or SessionLocal()
            try:
                row = (
                    s.query(BizConfig)
                    .filter(BizConfig.account_id == account_id,
                            BizConfig.config_key == config_key)
                    .first()
                )
                if row is not None and row.value_num is not None:
                    val = Decimal(str(row.value_num))
                    _CACHE[ck] = val
                    return val
            finally:
                if own:
                    s.close()
        except Exception:  # noqa: BLE001 — fail-safe：查库异常回落默认
            logger.warning("biz_config 查表失败，回落默认 %s", config_key, exc_info=True)

    return default_of(config_key)


def get_config_int(config_key: str, *, account_id: Optional[str] = None,
                   session=None) -> int:
    """天/件/时类阈值：取整。"""
    return int(round(get_config_num(config_key, account_id=account_id, session=session)))


def upsert_config_num(session, *, account_id: str, config_key: str,
                      value: Decimal) -> None:
    """写入/更新某租户某 key 的 biz_configs 覆盖值（flush，由调用方 commit）。写后清缓存。"""
    from models.base_models import BizConfig

    row = (
        session.query(BizConfig)
        .filter(BizConfig.account_id == account_id, BizConfig.config_key == config_key)
        .first()
    )
    if row is None:
        row = BizConfig(account_id=account_id, config_key=config_key, value_num=value)
        session.add(row)
    else:
        row.value_num = value
    session.flush()
    clear_config_cache()


def delete_config(session, *, account_id: str, config_key: str) -> bool:
    """删除某租户某 key 的覆盖行（回落默认）。返回是否删到行。写后清缓存。"""
    from models.base_models import BizConfig

    n = (
        session.query(BizConfig)
        .filter(BizConfig.account_id == account_id, BizConfig.config_key == config_key)
        .delete(synchronize_session=False)
    )
    session.flush()
    clear_config_cache()
    return n > 0


def get_biz_config_overrides(session, account_id: str) -> dict[str, Decimal]:
    """列出某租户在 biz_configs 表里的所有覆盖值（config_key → value_num）。"""
    from models.base_models import BizConfig

    rows = (
        session.query(BizConfig)
        .filter(BizConfig.account_id == account_id)
        .all()
    )
    return {r.config_key: Decimal(str(r.value_num)) for r in rows}
