"""Web 自建 agent 的工具层（plan/15 Phase A）。

把现有 ops_* 取数端点封装成 LLM 可调用的工具：
- 工具 schema（TOOL_SPECS）交给 Provider；
- 执行时**进程内直调** data.py 的端点函数（无额外 HTTP 跳，仿 board.py::_collect）；
- 范围**不暴露给模型**：由 services.user_authz 按登录 open_id 的角色自动夹紧
  （boss 全部 / operator 锁定其 allowed_scope 且不可越界），模型只能选 period/limit。
  这样 Web 对话与看板/飞书对话共用同一权限上限，模型无法越权取数。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from services.llm import ToolSpec
from services.user_authz import UserPermission, resolve_authorized_scope
from web.routes.data import (
    get_fulfillments_pending,
    get_low_stock,
    get_orders_summary,
    get_orders_top_skus,
    get_orders_trend,
    get_overview,
)

logger = logging.getLogger(__name__)

# 相对时间窗口枚举（与 services/timezone.resolve_period 对齐；模型不自己算日期）
_PERIOD_ENUM = [
    "today", "yesterday", "this_week", "last_week",
    "last_7d", "last_30d", "this_month",
]

_PERIOD_PROP = {
    "type": "string",
    "enum": _PERIOD_ENUM,
    "description": "时间窗口（按印尼时区、周一起算）。默认 last_7d。",
}


TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(
        name="ops_overview",
        description="店铺经营概览：当前库存快照（SKU 数/总库存/低库存数）+ 近 7 天已付款订单概览（GMV/订单数/销量/客单价）。问'整体情况/概览/现状'时用。",
        parameters={"type": "object", "properties": {}, "required": []},
    ),
    ToolSpec(
        name="ops_orders_summary",
        description="已付款订单汇总：GMV、订单数、销量、客单价。问某时段销售额/成交时用。",
        parameters={
            "type": "object",
            "properties": {"period": _PERIOD_PROP},
            "required": [],
        },
    ),
    ToolSpec(
        name="ops_orders_trend",
        description="按天的订单趋势：每天的 GMV / 订单数 / 销量。问'趋势/走势/每天'时用。",
        parameters={
            "type": "object",
            "properties": {"period": _PERIOD_PROP},
            "required": [],
        },
    ),
    ToolSpec(
        name="ops_top_skus",
        description="爆款单品榜：按销量降序的 SKU（含单品 GMV）。问'卖得最好/爆款/Top 商品'时用。",
        parameters={
            "type": "object",
            "properties": {
                "period": _PERIOD_PROP,
                "limit": {"type": "integer", "description": "返回条数，默认 10", "minimum": 1, "maximum": 50},
            },
            "required": [],
        },
    ),
    ToolSpec(
        name="ops_low_stock",
        description="断货风险 SKU：按可售天数（可用库存÷日均销速）分桶断货/告急/预警。问'要补货/快断货/库存风险'时用。",
        parameters={"type": "object", "properties": {}, "required": []},
    ),
    ToolSpec(
        name="ops_fulfillments_pending",
        description="待发货订单快照及超时预警（超时/临界/正常分桶）。问'待发货/有没有超时/要发的单'时用。",
        parameters={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "返回订单条数，默认 50", "minimum": 1, "maximum": 200},
            },
            "required": [],
        },
    ),
    ToolSpec(
        name="ops_report",
        description="生成经营报告签名链接（可视化图表），可附加在回复中。问'经营日报/周报/数据报告/可视化报告'时用。",
        parameters={
            "type": "object",
            "properties": {
                "template_name": {"type": "string", "enum": ["daily_brief", "weekly_review"], "description": "报告类型：daily_brief=日报/区间报（KPI+趋势+爆款+断货，版型按时间窗自动判定）；weekly_review=经营周报（商品健康度视角：客单价+爆款集中度+动销率+新品表现+周度复盘建议）。问'周报/周度复盘/本周经营'用 weekly_review，其余用 daily_brief。", "default": "daily_brief"},
                "period": {"type": "string", "description": "时间窗口：today/yesterday/this_week/last_week/last_7d/last_30d/this_month。【日报】单日(today/yesterday)出日报版型、多日出区间报版型；问'今天/日报'传 today、'近30天/月报'传 last_30d。【周报 weekly_review】传 last_week=上周整周(周对周)、this_week=本周截至此刻(vs 上周同期)；问'上周周报'传 last_week，'本周到现在'传 this_week。", "default": "last_7d"},
            },
            "required": [],
        },
    ),
]

TOOL_NAMES = {t.name for t in TOOL_SPECS}


def _asdict(obj: Any) -> Any:
    return obj.model_dump() if hasattr(obj, "model_dump") else obj


def _run(coro):
    """在当前线程跑一个 data 端点协程到完成。

    data.py 端点是 async def 但函数体全程同步（SQLAlchemy 同步），不真正 await，
    故可在 Starlette 线程池的工作线程里用独立事件循环安全驱动到底。
    """
    return asyncio.run(coro)


def run_tool(name: str, arguments: dict, perm: UserPermission) -> dict:
    """执行一个工具：按权限闸夹紧范围 → 进程内调对应端点 → 返回数据 dict。

    范围由 resolve_authorized_scope(perm) 决定（不接受模型传 scope），越界天然不可能。
    未知工具 / 端点异常向上抛，由 agent loop 兜成给模型的错误观察。
    """
    if name not in TOOL_NAMES:
        raise ValueError(f"未知工具：{name}")

    # 按登录身份夹紧范围：boss=全部、operator=其 allowed_scope（不可越界）
    filters = resolve_authorized_scope(perm)
    shop_ids = ",".join(filters.shop_ids) if filters.shop_ids else None
    platform, country = filters.platform, filters.country

    args = arguments or {}
    period = args.get("period") or "last_7d"

    common = dict(
        platform=platform, country=country, shop_id=None,
        scope_id=None, shop_ids=shop_ids, open_id=None,
    )

    if name == "ops_overview":
        result = _run(get_overview(**common))
    elif name == "ops_orders_summary":
        result = _run(get_orders_summary(
            start_date=None, end_date=None, period=period, **common))
    elif name == "ops_orders_trend":
        result = _run(get_orders_trend(
            start_date=None, end_date=None, period=period, **common))
    elif name == "ops_top_skus":
        limit = int(args.get("limit") or 10)
        result = _run(get_orders_top_skus(
            start_date=None, end_date=None, period=period, limit=limit, **common))
    elif name == "ops_low_stock":
        result = _run(get_low_stock(
            critical_days=None, warning_days=None, **common))
    elif name == "ops_fulfillments_pending":
        limit = int(args.get("limit") or 50)
        result = _run(get_fulfillments_pending(
            warning_hours=None, limit=limit, **common))
    elif name == "ops_report":
        from web.routes.data import get_report_link
        template = args.get("template_name", "daily_brief")
        period_val = args.get("period", "last_7d")
        # ops_report 需要 open_id（从 perm 取）而非 common dict 的 open_id=None
        # WebUI 在浏览器里，用裸链（applink 只在飞书客户端有意义）
        result = _run(get_report_link(
            open_id=perm.open_id, template_name=template, period=period_val,
            wrap_applink=False))
        return result.markdown
    else:  # 不会到这（已校验），保险
        raise ValueError(f"未实现工具：{name}")

    return _asdict(result)
