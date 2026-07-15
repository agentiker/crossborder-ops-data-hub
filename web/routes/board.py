"""独立运营看板（plan/14 Phase 4）：飞书 OAuth 登录态 + 数据层权限闸。

与 plan/13 的 /dashboard?t=（签名链接）共存、互不影响。区别：
- 鉴权：飞书 OAuth 登录 cookie（require_web_user），非一次性签名链接。
- 范围：经 services/user_authz 的硬权限闸——boss 看全部、operator 锁定 allowed_scope
  且不可越界（改 ?scope= 越界 → 403）。

取数复用 web/routes/data.py 的路由函数，但范围由 resolve_authorized_scope 夹紧后以显式
shop_ids 传入（open_id=None / scope_id=None，绕开会话 binding）——这样看板与对话侧最终
共用同一套取数 + 同一权限上限。
"""

import logging

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from core.config import settings
from core.tenancy import set_current_account
from core.timezone import business_now, business_today, previous_window
from services.ad_metrics import get_ad_spend_summary, get_roas
from services.biz_config import get_config_int
from services.channel_metrics import get_channel_gmv_breakdown
from services.fee_rate_metrics import get_fee_rate_monitor
from services.fx_series import get_fx_series, list_currencies
from services.order_metrics import (
    get_gmv_summary,
    get_gmv_summary_intraday_range,
    get_new_product_trends,
    get_product_sku_breakdown,
    get_top_products,
)
from services.product_channel_metrics import get_product_channel_breakdown
from services.product_cost_store import list_product_costs
from services.profit_summary import get_profit_card
from services.refund_metrics import (
    get_refund_summary,
    get_refund_top_products,
    get_refund_trend,
)
from services.scope_resolution import ScopeError, list_scopes, resolve_filters
from services.shop_directory import get_shop_names
from services.user_authz import AuthzError, UserPermission, resolve_authorized_scope
from web.routes.data import (
    _resolve_window,
    get_fulfillments_pending,
    get_low_stock,
    get_orders_trend,
    get_overview,
)
from web.web_security import require_web_user

logger = logging.getLogger(__name__)
router = APIRouter()


def _asdict(obj):
    return obj.model_dump() if hasattr(obj, "model_dump") else obj


def _pct(cur: float, prev: float):
    """环比百分比（保留 1 位小数）。上期基准为 0（无可比）→ None，前端不渲染该行，不臆造。"""
    if not prev:
        return None
    return round((cur - prev) / prev * 100, 1)


def _overview_window_and_gmv(
    cur_start, cur_end, prev_start, prev_end, *, platform, country, shop_ids,
):
    """当期/上期 GMV 摘要 + 窗口元信息。

    窗口结束在「今天」(WIB 业务日)时,当期含半天今天。若直接整窗 vs 上期整窗,环比会被半天
    拉成「假暴跌」(半天比全天)。含今日则改用 intraday 公平比较:cur/prev 都钉「截至此刻」
    (末日截到 cutoff、中间天整天),与日报 _weekly_windows 同款口径(get_gmv_summary_intraday_range);
    不含今日则照旧整天对整天。返回 (cur, prev, window_meta)。
    """
    includes_today = cur_end == business_today()
    if includes_today:
        cutoff = business_now().time()
        cur = get_gmv_summary_intraday_range(
            start_date=cur_start, end_date=cur_end, cutoff=cutoff,
            platform=platform, country=country, shop_ids=shop_ids, display=True,
        )
        prev = get_gmv_summary_intraday_range(
            start_date=prev_start, end_date=prev_end, cutoff=cutoff,
            platform=platform, country=country, shop_ids=shop_ids, display=True,
        )
        as_of_label = (
            f"数据截至 {business_now().strftime('%m-%d %H:%M')}（印尼时间）· 今日为当日累计"
        )
    else:
        cur = get_gmv_summary(
            start_date=cur_start, end_date=cur_end,
            platform=platform, country=country, shop_ids=shop_ids, display=True,
        )
        prev = get_gmv_summary(
            start_date=prev_start, end_date=prev_end,
            platform=platform, country=country, shop_ids=shop_ids, display=True,
        )
        as_of_label = None
    window_meta = {
        "start": cur_start.isoformat(),
        "end": cur_end.isoformat(),
        "includes_today": includes_today,
        "as_of_label": as_of_label,
    }
    return cur, prev, window_meta


def _scope_options(perm: UserPermission) -> list[dict]:
    """范围切换条数据。boss：全部店铺 + 每个单店（+ 真正的子集分组）；operator：仅其 allowed（锁定单项）。

    下拉直接由「本租户库里有几个店」算出，与店铺数一一对应，加店自动多一项、永不重复漂移：
      · 「全部店铺」（key=""）= 动态并集，恒为首项。
      · 每个店铺一项（`shop:<shop_id>`，店名取自 platform_tokens.seller_name），单店也列出
        （固定「全部店铺」+逐店枚举更自然；此前单店隐藏整条筛选，客户反馈不直觉）。
    命名 scope（business_scopes）只在它是**真正的子集分组**（店数 < 全部，如未来「北区3店」）时
    才额外列出；像 tts-id-all 这种「恰好=全部店」的不进下拉——它只是 operator 的授权锚点，与老板
    筛选 UX 无关（去掉它就不会和「全部店铺」重复）。
    """
    if perm.is_boss:
        all_shops = resolve_filters(scope_key=None, account_id=perm.account_id).shop_ids
        all_set = set(all_shops)
        opts = [{"key": "", "label": "全部店铺"}]
        # 每个店铺一项：固定「全部店铺」+ 逐店枚举是更自然的筛选体验，单店也列出
        # （此前单店时整条隐藏，客户反馈不直觉；单店时「全部店铺」与该店指向同一数据但保留可见性）。
        if all_shops:
            names = get_shop_names(perm.account_id)
            opts += [
                {"key": f"shop:{sid}", "label": names.get(str(sid), str(sid))}
                for sid in all_shops
            ]
        # 真正的子集分组 scope（严格小于全部店）才进下拉；恰好=全部的（tts-id-all）跳过，避免与首项重复。
        opts += [
            {"key": s["scope_key"], "label": s["scope_name"]}
            for s in list_scopes(perm.account_id)
            if s["shop_ids"] and set(s["shop_ids"]) < all_set
        ]
        return opts
    # operator：只暴露被授权的那个 scope，不可切换到其它
    allowed = perm.allowed_scope_key
    label = allowed
    for s in list_scopes(perm.account_id):
        if s["scope_key"] == allowed:
            label = s["scope_name"]
            break
    return [{"key": allowed, "label": label}]


def _authorize_scope(perm, requested: str | None, *, platform=None, country=None):
    """把前端下拉选中的 key 夹进授权范围。`shop:<id>` 前缀 → 按单店筛（走显式 shop_ids，
    仍受 resolve_filters 授权校验，operator 也不会越界）；否则按命名 scope_key 解析。"""
    requested = requested or None
    if requested and requested.startswith("shop:"):
        return resolve_authorized_scope(
            perm,
            requested_shop_ids=[requested[len("shop:"):]],
            platform=platform,
            country=country,
        )
    return resolve_authorized_scope(
        perm,
        requested_scope_key=requested,
        platform=platform,
        country=country,
    )


async def _collect(
    perm: UserPermission,
    period: str,
    requested_scope_key: str,
    start_date: str | None = None,
    end_date: str | None = None,
    platform_q: str | None = None,
    country_q: str | None = None,
    granularity: str | None = None,
) -> dict:
    """按权限闸夹紧范围后取看板各块数据。越界由 resolve_authorized_scope 抛 ScopeError。

    start_date/end_date：显式起止日期（YYYY-MM-DD，日历筛选）；传了即覆盖 period。
    platform_q/country_q：平台/区域筛选（正交附加维度，叠加在 scope 的 shop_ids 之上、不参与越界判断）。
    granularity：趋势粒度，hour=单天逐小时（前端选单天时传）；多天时 get_orders_trend 内部静默回退 day。
    """
    # /board 渲染链路不走 X-Account-Id 注入；复用 data.py 路由前先把当前老板的租户写进 context，
    # 让下游 _resolve_scope / ORM 自动过滤按同一 account_id 生效。
    set_current_account(perm.account_id)
    filters = _authorize_scope(
        perm,
        requested_scope_key,
        platform=platform_q or None,
        country=country_q or None,
    )
    # 夹紧后的具体店集合作为显式条件传入；open_id/scope_id 钉 None，绕开会话 binding。
    shop_ids = ",".join(filters.shop_ids) if filters.shop_ids else None
    platform, country = filters.platform, filters.country

    overview = await get_overview(
        platform=platform, country=country, shop_id=None,
        scope_id=None, shop_ids=shop_ids, open_id=None,
    )
    trend = await get_orders_trend(
        start_date=start_date, end_date=end_date, period=period,
        platform=platform, country=country, shop_id=None,
        scope_id=None, shop_ids=shop_ids, open_id=None,
        granularity=granularity or "day",
    )
    trend_dict = _asdict(trend)
    low = await get_low_stock(
        platform=platform, country=country, shop_id=None,
        scope_id=None, shop_ids=shop_ids,
        critical_days=None, warning_days=None, open_id=None,
        include_all=True,  # 看板「商品明细」列全部在库 SKU（配合筛选/分页）；buckets 计数仍只算风险桶
    )
    fulfillment = await get_fulfillments_pending(
        platform=platform, country=country, shop_id=None,
        scope_id=None, shop_ids=shop_ids,
        warning_hours=None, limit=50, open_id=None,
    )

    overview = _asdict(overview)
    # 环比：取当期 + 紧邻等长上期，把 overview.orders 统一成跟随当期窗口的口径，并注入 change。
    # 当期窗口复用 data._resolve_window：显式 start/end 优先（日历筛选），否则按 period 预设。
    # previous_window 对任意等长窗口成立（纯算术），无需 period 对齐。
    cur_start, cur_end = _resolve_window(
        start_date, end_date, period, default_back_days=6
    )
    prev_start, prev_end = previous_window(cur_start, cur_end)
    shop_id_list = filters.shop_ids or None
    # 销售趋势「上期对比线」：取等长上期（单天=前一天、多天=等长上一期）的趋势点，
    # 复用当期实际生效的 granularity（hour/day），前端在同一张图画一条虚线做对比。
    # 上期窗口同样单天（prev_start==prev_end）→ granularity=hour 生效，逐小时自然对齐。
    # 取数失败不阻断整页：缺上期线只是少一条对比曲线，趋势主体照常展示。
    # 注：_asdict 统一 TrendResponse(Pydantic) 与测试 mock 的 dict，二者都可取 points/window_label。
    try:
        prev_trend = await get_orders_trend(
            start_date=prev_start.isoformat(), end_date=prev_end.isoformat(),
            platform=platform, country=country, shop_id=None,
            scope_id=None, shop_ids=shop_ids, open_id=None,
            granularity=trend_dict.get("granularity") or "day",
        )
        prev_dict = _asdict(prev_trend)
        trend_dict["prev_points"] = prev_dict.get("points") or []
        trend_dict["prev_window_label"] = prev_dict.get("window_label")
    except Exception:  # noqa: BLE001 — 上期对比线兜底，失败不炸看板
        logger.warning("prev trend points failed", exc_info=True)
    # 爆款「商品」榜（按 product_id 聚合，带小图/款号；窗口同当期）。直调服务（不走 data 路由），
    # 与 channels/profit 一致：商品级语义 + 单品渠道拆分 join 都按 product_id。
    top_items = get_top_products(
        start_date=cur_start, end_date=cur_end,
        platform=platform, country=country, shop_ids=shop_id_list, limit=10,
        by_create=True,
    )
    # 渠道 GMV 拆分（直播/视频/商品卡，实时调 TikTok analytics + 进程缓存）。沙箱店无
    # analytics 数据时内部降级返回 available=False，不抛错、不阻断看板其它块。
    channels = get_channel_gmv_breakdown(
        start_date=cur_start, end_date=cur_end,
        platform=platform, country=country, shop_ids=shop_id_list,
    )
    # 预估利润卡（折 CNY）：从 fact_profit_daily 聚合预估/真实双套；无聚合数据 available=False。
    profit = get_profit_card(
        start_date=cur_start, end_date=cur_end,
        platform=platform, country=country, shop_ids=shop_id_list,
        account_id=perm.account_id,
    )
    # 费率监控卡（实时算、复用 B1 及时口径）：当前预估费率 vs 已结算基准 + 趋势 + 三态徽章。
    # 不随 period 变（固定近 N 天 unsettled vs 历史 settled）。出错不阻断看板其它块。
    try:
        fee_rate = get_fee_rate_monitor(
            platform=platform, country=country, shop_ids=shop_id_list,
            scope_display=filters.display_text,
        )
    except Exception:  # noqa: BLE001 — 单卡兜底，费率取数失败不整页挂
        logger.warning("fee_rate monitor card failed", exc_info=True)
        fee_rate = {"status": "insufficient", "skip_reason": "取数失败", "trend": []}
    # 退款/取消分析（随 period 变，与 GMV 同窗口）：退款=付款后取消（真实退款），
    # 附取消构成拆分。出错兜底不阻断整页（仿 fee_rate）。
    try:
        refund_summary = get_refund_summary(
            start_date=cur_start, end_date=cur_end,
            platform=platform, country=country, shop_ids=shop_id_list,
        )
        refund = {
            **refund_summary,
            "trend": get_refund_trend(
                start_date=cur_start, end_date=cur_end,
                platform=platform, country=country, shop_ids=shop_id_list,
            ),
            "top_products": get_refund_top_products(
                start_date=cur_start, end_date=cur_end,
                platform=platform, country=country, shop_ids=shop_id_list,
            ),
        }
    except Exception:  # noqa: BLE001 — 单卡兜底
        logger.warning("refund analysis failed", exc_info=True)
        refund = None
    cur, prev, window_meta = _overview_window_and_gmv(
        cur_start, cur_end, prev_start, prev_end,
        platform=platform, country=country, shop_ids=shop_id_list,
    )
    # 广告消耗（结算口径）：复用同一当期/上期窗口与 filters，各取一次摘要算环比；当期再取 ROAS。
    # 无结算数据时 get_* 返回 0/None，不抛错；前端按 0/None 做降级展示。
    cur_ads = get_ad_spend_summary(
        start_date=cur_start, end_date=cur_end,
        platform=platform, country=country, shop_ids=shop_id_list,
    )
    prev_ads = get_ad_spend_summary(
        start_date=prev_start, end_date=prev_end,
        platform=platform, country=country, shop_ids=shop_id_list,
    )
    cur_roas = get_roas(
        start_date=cur_start, end_date=cur_end,
        platform=platform, country=country, shop_ids=shop_id_list,
    )
    prev_roas = get_roas(
        start_date=prev_start, end_date=prev_end,
        platform=platform, country=country, shop_ids=shop_id_list,
    )
    overview["orders"] = {
        "gmv": cur["gmv"], "order_count": cur["order_count"],
        "units_sold": cur["units_sold"], "avg_order_value": cur["avg_order_value"],
        "cancelled_count": cur.get("cancelled_count", 0),
        "unpaid_count": cur.get("unpaid_count", 0),
    }
    overview["ads"] = {
        "total_ad_spend": cur_ads["total_ad_spend"],
        "paid_ad_spend": cur_ads["paid_ad_spend"],       # 付费投放 = 仅 GMV Max，ROAS 口径
        "creator_commission": cur_ads["creator_commission"],  # 达人佣金 = TAP + 联盟（CPS）
        "roas": cur_roas["roas"],
        "gmv_max_fee": cur_ads["gmv_max_fee"],
        "tap_commission": cur_ads["tap_commission"],
        "affiliate_commission": cur_ads["affiliate_commission"],
        "currency": cur_ads["currency"],
        # 结算滞后护栏：complete=False 时前端标注「结算中·近 N 天不完整」，settled_through=结算完整线
        "complete": cur_ads["complete"],
        "settled_through": cur_ads["settled_through"],
        "latest_covered_date": cur_ads["latest_covered_date"],
    }
    # ROAS 环比：任一期 roas 为 None（该期无付费投放）则不可比 → None，不臆造。
    roas_change = (
        _pct(cur_roas["roas"], prev_roas["roas"])
        if cur_roas["roas"] is not None and prev_roas["roas"] is not None
        else None
    )
    overview["change"] = {
        "gmv": _pct(cur["gmv"], prev["gmv"]),
        "order_count": _pct(cur["order_count"], prev["order_count"]),
        "units_sold": _pct(cur["units_sold"], prev["units_sold"]),
        "avg_order_value": _pct(cur["avg_order_value"], prev["avg_order_value"]),
        # 广告环比按付费投放（与 ROAS 口径一致），佣金随成交波动不算"广告增减"
        "ad_cost": _pct(cur_ads["paid_ad_spend"], prev_ads["paid_ad_spend"]),
        "roas": roas_change,
    }
    return {
        "scope": filters.display_text,
        "scope_key": requested_scope_key or "",
        "can_switch": perm.is_boss,
        "scopes": _scope_options(perm),
        "role": perm.role,
        "period": period,
        # 当期窗口元信息:含今日时前端显「当日累计」徽章 + 利润卡提示,避免客户把半天今天当下降。
        "window": window_meta,
        "overview": overview,
        "trend": trend_dict,
        "top": {"items": top_items},
        "low": _asdict(low),
        "fulfillment": _asdict(fulfillment),
        "channels": channels,
        "profit": profit,
        "fee_rate": fee_rate,
        "refund": refund,
    }


@router.get("/board/scopes", include_in_schema=False)
async def board_scopes(perm: UserPermission = Depends(require_web_user)):
    """店铺下拉选项的轻量端点：只算 _scope_options（读 tokens/scopes，毫秒级），不碰看板聚合。

    前端页面挂载即优先拉它填充店铺下拉，避免下拉被重的 /board/data（概览/趋势/利润/渠道…全算完
    才返回）拖到最后才渲染。返回 {can_switch, scopes}，与 /board/data 里同名字段同构。
    """
    set_current_account(perm.account_id)
    return JSONResponse({"can_switch": perm.is_boss, "scopes": _scope_options(perm)})


@router.get("/board/data", include_in_schema=False)
async def board_data(
    perm: UserPermission = Depends(require_web_user),
    period: str = Query("last_30d"),
    scope: str = Query(""),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    platform: str | None = Query(None),
    country: str | None = Query(None),
    granularity: str | None = Query(None, description="趋势粒度：hour=单天逐小时（前端选单天时传）"),
):
    """切换日期/范围/平台/区域用的 JSON 端点：前端 AJAX 局部重绘。越界 → 403 JSON。"""
    try:
        data = await _collect(perm, period, scope, start_date, end_date, platform, country, granularity)
    except (ScopeError, AuthzError) as exc:
        return JSONResponse({"error": "forbidden", "detail": str(exc)}, status_code=403)
    return JSONResponse(data)


@router.get("/board/product-detail", include_in_schema=False)
async def board_product_detail(
    perm: UserPermission = Depends(require_web_user),
    product_id: str = Query(..., description="商品 product_id"),
    period: str = Query("last_30d"),
    scope: str = Query(""),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    platform: str | None = Query(None),
    country: str | None = Query(None),
):
    """商品详情弹窗懒加载端点：点击爆款卡某商品时才请求，返回 {channels, skus}。

    - channels：单品渠道 4 分（达人/自营素材/商品卡/店铺页）；沙箱/无 analytics→available=False。
    - skus：该商品各 SKU 已付款销量/GMV（前端算占比条）。
    窗口/范围与看板同源（_resolve_window + resolve_authorized_scope 夹紧），不拖慢看板首载。
    """
    set_current_account(perm.account_id)
    try:
        filters = _authorize_scope(
            perm, scope, platform=platform or None, country=country or None,
        )
    except (ScopeError, AuthzError) as exc:
        return JSONResponse({"error": "forbidden", "detail": str(exc)}, status_code=403)
    cur_start, cur_end = _resolve_window(start_date, end_date, period, default_back_days=6)
    channels = get_product_channel_breakdown(
        product_id=product_id,
        start_date=cur_start, end_date=cur_end,
        country=filters.country, shop_ids=filters.shop_ids or None,
    )
    skus = get_product_sku_breakdown(
        product_id=product_id,
        start_date=cur_start, end_date=cur_end,
        platform=filters.platform, country=filters.country,
        shop_ids=filters.shop_ids or None,
        by_create=True,
    )
    return JSONResponse({"channels": channels, "skus": skus})


@router.get("/board/new-products", include_in_schema=False)
async def board_new_products(
    perm: UserPermission = Depends(require_web_user),
    scope: str = Query(""),
    platform: str | None = Query(None),
    country: str | None = Query(None),
):
    """「近 N 天新品」卡懒加载端点：返回近 N 天上线在售商品 + 每日销量曲线 + 爆单判定。

    窗口 N = settings.new_product_lookback_days（默认 60）。口径「付款口径销量 / 爆单阈值 =
    settings.hotsell_daily_units_threshold」，
    与飞书爆单告警同阈（见 docs/business-rules §4.4 / §7）。范围经权限闸夹紧，无数据则 available=False，
    不阻断看板其它卡。不塞进主 /board/data，保持首载快（与 product-detail 同策略）。
    """
    set_current_account(perm.account_id)
    try:
        filters = _authorize_scope(
            perm, scope, platform=platform or None, country=country or None,
        )
    except (ScopeError, AuthzError) as exc:
        return JSONResponse({"error": "forbidden", "detail": str(exc)}, status_code=403)

    as_of = business_today()
    lookback_days = get_config_int("new_product_lookback_days")
    threshold = get_config_int("hotsell_daily_units_threshold")
    try:
        items = get_new_product_trends(
            as_of=as_of,
            lookback_days=lookback_days,
            threshold=threshold,
            platform=filters.platform,
            country=filters.country,
            shop_ids=filters.shop_ids or None,
            by_create=True,
        )
        available = True
    except Exception:  # 取数异常（无 Product 表数据 / DB 抖动）→ 降级，不抛 500
        logger.exception("new-products query failed")
        items, available = [], False

    return JSONResponse({
        "items": items,
        "threshold": threshold,
        "window": {"lookback_days": lookback_days, "as_of": as_of.isoformat()},
        "available": available,
    })


# 汇率走势（/board/fx 页面）：中行牌价日序列。汇率非隔离数据（fact_exchange_rate 无
# account_id），仅需登录态、不做 scope 夹紧——boss/operator 都能看同一份全局牌价。
_FX_ALLOWED_DAYS = {30, 90, 365}


@router.get("/board/fx/currencies", include_in_schema=False)
async def board_fx_currencies(perm: UserPermission = Depends(require_web_user)):
    """汇率页币种下拉：库里有数据的常用币种（IDR 恒在），每项 {code, name}。"""
    return JSONResponse({"items": list_currencies()})


@router.get("/board/fx/series", include_in_schema=False)
async def board_fx_series(
    perm: UserPermission = Depends(require_web_user),
    currency: str = Query("IDR", description="ISO 币种码，如 IDR/USD"),
    days: int = Query(90, description="回看天数，仅接受 30/90/365"),
):
    """汇率走势序列：近 days 天中行折算价日均值（1 外币→CNY）。口径同利润折算 fx_rate。"""
    if days not in _FX_ALLOWED_DAYS:
        days = 90
    return JSONResponse(get_fx_series(currency, days))


# 马帮成本（/costs 页面）：product_costs 当前快照（RMB 含运费）+ 关联商品图。
# 成本按 account_id 隔离（set_current_account + 显式 account_id 过滤）；商品图按 platform
# 分派取源（马帮无图，取自平台商品主数据，见 product_cost_store._resolve_sku_images）。
@router.get("/board/costs", include_in_schema=False)
async def board_costs(
    perm: UserPermission = Depends(require_web_user),
    platform: str = Query("tiktok_shop", description="平台，如 tiktok_shop / shopee"),
):
    """马帮成本页数据：某租户某平台全部 SKU 单位成本 + 关联商品图/款号。

    与汇率页同为「基础数据」——所有登录用户可见，不做 scope 夹紧（成本是租户级主数据，
    非按店铺分割）。筛选/排序在前端做（数据量小，约数百行）。
    """
    set_current_account(perm.account_id)
    items = list_product_costs(account_id=perm.account_id, platform=platform)
    return JSONResponse({"items": items, "platform": platform})
