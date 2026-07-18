"""补货采购单定时推送 flow（飞书卡片直投；清仓嫌疑分析块走 LLM，失败降级确定性）。

与监控告警同构、复用同一收件人表与飞书直投：按 alert_recipients 逐收件人，按其租户/范围算
补货建议（compute_replenishment），组装采购单卡片（replenishment_card_builder，与日报/告警
同一套 CardKit 风格）直投飞书私聊；卡片投递失败回落 openclaw 文本（同告警 send_alert 链路，
凭证缺失/JSON 错时不丢消息）。日级 digest，无去重游标（每次发当前快照）；无待补货 SKU 不发空单。

清仓嫌疑（_clearance_advice）：对补货列表里的款算折扣加深/销量突刺/变体死亡率信号，命中嫌疑
的款用 LLM 合成「补货前请与采购确认」提醒（强约束防编造），插入卡片表格后。LLM 未配置/超时/
返回空 → 降级确定性 reason 列表；信号计算异常 → 跳过分析块。任一降级都不阻断补货推送。

触发：systemd user timer，每日 09:30 一次（见 deploy/systemd/data-push-replenishment.*）。
在途 MVP=0（马帮未接通），卡片已提示。
"""
import logging
from typing import Optional

from core.config import settings

logger = logging.getLogger(__name__)

from core.tenancy import set_current_account
from core.timezone import business_today
from flows.scan_fulfillment_alerts import load_recipients, send_feishu_message
from services.clearance_signals import compute_clearance_signals
from services.llm import ChatMessage, complete_text, get_provider
from services.replenishment import compute_replenishment
from services.replenishment_config import get_effective_config
from services.replenishment_report import build_replenishment_message
from services.scope_resolution import resolve_filters
from core.db import SessionLocal
from web.feishu_card_sender import send_interactive_card
from web.replenishment_card_builder import build_replenishment_card

# 日均销速低于此值的 SKU 不进采购单（慢销/滞销），与看板库存明细「日均<1 件/天」同口径。
# 注意补货窗口默认 30 天，故此门槛 ≈ 月销 < 30 件，比库存视图（7 天窗口 <7 件）激进。
_MIN_DAILY_VELOCITY = 1.0


def _clearance_advice(rows, *, scope, session) -> Optional[str]:
    """对补货列表里的款算清仓嫌疑，合成「补货前请与采购确认」提醒文案。

    无嫌疑款 → None（卡片不加分析块）。有嫌疑 → LLM 合成自然语言（基于信号 reason，
    强约束防编造）；LLM 未配置/超时/返回空 → 降级为确定性 reason 列表（不丢提醒）。
    返回纯文本（⚠️ 标题 + 结构），由卡片/文本两个 builder 各自渲染。
    """
    pids = sorted({r.get("product_id") for r in rows if r.get("product_id")})
    if not pids:
        return None
    try:
        sigs = compute_clearance_signals(
            pids,
            platform=scope.platform,
            country=scope.country,
            shop_ids=scope.shop_ids or None,
            session=session,
        )
    except Exception as exc:  # DB/SQL 异常——清仓是增强项，不阻断补货推送
        logger.warning("清仓信号计算失败，跳过分析块：%s", exc)
        return None
    # product_id → 该款在补货列表里的 seller_sku（采购能认的短码；一款多变体都在补则全列）
    pid_skus: dict[str, list[str]] = {}
    for r in rows:
        pid = r.get("product_id")
        if not pid:
            continue
        sku = r.get("seller_sku") or r.get("sku_id") or pid
        lst = pid_skus.setdefault(pid, [])
        if sku not in lst:
            lst.append(sku)
    suspects = [
        {"skus": pid_skus.get(pid, [pid]), "reason": (sigs.get(pid) or {}).get("reason") or ""}
        for pid in pids
        if (sigs.get(pid) or {}).get("suspect")
    ]
    if not suspects:
        return None
    return _llm_clearance_text(suspects)


def _llm_clearance_text(suspects: list[dict]) -> str:
    """LLM 把嫌疑信号合成克制、自然的运营提醒；失败降级为确定性 • 列表。"""
    def _fallback() -> str:
        parts = []
        for s in suspects:
            tag = "/".join(str(x) for x in s["skus"][:3])  # 最多列 3 个标识防爆版
            parts.append(f"• {tag}：{s['reason']}")
        return "⚠️ 疑似清仓，补货前请与采购确认：\n" + "\n".join(parts)

    try:
        provider = get_provider()
    except Exception:  # LLM 未配置（api_key/model 缺）——降级，定时 flow 不因此中断
        return _fallback()

    # 喂 LLM 的事实清单：seller_sku + 系统已判定的信号 reason（括号数字均真实计算）
    facts = "\n".join(
        f"{i}. 款 {'/'.join(str(x) for x in s['skus'][:3])}：{s['reason']}"
        for i, s in enumerate(suspects, 1)
    )
    prompt = (
        "下面是补货列表中疑似「清仓甩卖」的商品及其判别信号（已由系统从订单数据判定，"
        "括号内数字均为真实计算结果，不是示例）。请改写成一句自然、克制的运营提醒：\n"
        "- 用「1. 2.」逐款编号，每款一句话点明主要信号即可，不要在每款结尾重复「补货前与采购确认」（该建议已在标题给出）。\n"
        "- 只能使用下面给出的款号和信号；严禁新增任何其它 SKU、数字、库存、GMV、日期、原因。\n"
        "- 语气克制，禁止「严重/暴跌/崩盘」等夸张措辞；总长不超过 120 字。\n\n"
        f"{facts}"
    )
    try:
        text = complete_text(
            provider,
            [
                ChatMessage(role="system", content="你是跨境电商补货运营助手，只基于给定信号改写文案，绝不编造数据。"),
                ChatMessage(role="user", content=prompt),
            ],
        )
    except Exception as exc:  # 网络/鉴权/非 200——降级，不丢提醒
        logger.warning("清仓分析 LLM 合成失败，降级确定性文案：%s", exc)
        return _fallback()
    text = (text or "").strip()
    if not text:
        return _fallback()
    return "⚠️ 疑似清仓，补货前请与采购确认：\n" + text


def _deliver(*, account: str, open_id: str, card, text: str, dry_run: bool,
             label: str, n: int) -> str:
    """卡片优先投递；失败回落 openclaw 文本（同告警 send_alert 链路）。

    卡片投递（send_interactive_card）失败原因：飞书 app 凭证未配（如运维 main-app 未进
    FEISHU_OAUTH__APPS）/ 卡片 JSON 字段错 / 网络——任一失败回落文本，绝不丢消息。
    """
    if dry_run:
        print(f"[replenish][dry-run] → {label} {account}/{open_id}:\n{text}")
        return f"{label}: 待推送 {n} SKU（dry-run）"
    if card:
        try:
            send_interactive_card(account, open_id, card)
            return f"{label}: 已推送 {n} SKU（卡片）"
        except Exception as exc:  # 卡片失败 → 回落文本
            print(f"[replenish] 卡片投递失败，回落文本 ({account}): {exc}")
    sent = send_feishu_message(account=account, open_id=open_id, text=text)
    if sent:
        return f"{label}: 已推送 {n} SKU（{'文本回落' if card else '文本'}）"
    return f"{label}: 推送失败"


def _push_one(recipient: dict, *, dry_run: bool) -> str:
    """为一个收件人算补货并（必要时）投递采购单卡片。返回一行状态。"""
    account = recipient["account"]
    open_id = recipient["open_id"]
    scope_id = recipient.get("scope_id")
    set_current_account(account)  # ORM 自动按租户隔离

    scope = resolve_filters(scope_key=scope_id, account_id=account)
    today = business_today()

    session = SessionLocal()
    try:
        cfg = get_effective_config(session, account_id=account, scope_key=scope_id)
        rows = compute_replenishment(
            account_id=account,
            scope_key=scope_id,
            platform=scope.platform,
            country=scope.country,
            shop_ids=scope.shop_ids or None,
            min_daily_velocity=_MIN_DAILY_VELOCITY,
            session=session,
        )
        clearance_hint = _clearance_advice(rows, scope=scope, session=session)
    finally:
        session.close()

    common = dict(
        scope_display=scope.display_text,
        date_label=f"{today.month}/{today.day}",
        velocity_days=cfg.velocity_days,
        intransit_connected=False,
        clearance_hint=clearance_hint,
    )
    text = build_replenishment_message(rows, **common)
    if not text:
        return f"{account}/补货: 无待补货 SKU，不推送"
    card = build_replenishment_card(rows, **common)
    return _deliver(account=account, open_id=open_id, card=card, text=text,
                    dry_run=dry_run, label=f"{account}/补货", n=len(rows))


def _push_cc(*, dry_run: bool) -> str:
    """运维抄送：把【数据源租户】的全量补货单用【运维 app】凭证投递给运维 open_id。

    与主收件人解耦——飞书 open_id 是 per-app 的，运维 app（main-app）的 open_id 必须用
    main-app 凭证投递，但补货单内容必须用客户租户（source_account）的数据算，故取数租户
    与投递凭证分离。三个 cc 配置全配才启用，缺一即静默跳过（返回空串，不入 results）。
    内容为主收件人同款全量补货单（scope=None）；无待补货不发空单。
    """
    cc_account = settings.replenishment_cc_account
    cc_open_id = settings.replenishment_cc_open_id
    source_account = settings.replenishment_cc_source_account
    if not (cc_account and cc_open_id and source_account):
        return ""  # 未配置运维抄送，静默跳过

    set_current_account(source_account)  # 取数租户 = 客户租户（非运维 app）
    scope = resolve_filters(scope_key=None, account_id=source_account)
    today = business_today()

    session = SessionLocal()
    try:
        cfg = get_effective_config(session, account_id=source_account, scope_key=None)
        rows = compute_replenishment(
            account_id=source_account,
            scope_key=None,
            platform=scope.platform,
            country=scope.country,
            shop_ids=scope.shop_ids or None,
            min_daily_velocity=_MIN_DAILY_VELOCITY,
            session=session,
        )
        clearance_hint = _clearance_advice(rows, scope=scope, session=session)
    finally:
        session.close()

    common = dict(
        scope_display=scope.display_text,
        date_label=f"{today.month}/{today.day}",
        velocity_days=cfg.velocity_days,
        intransit_connected=False,
        clearance_hint=clearance_hint,
    )
    text = build_replenishment_message(rows, **common)
    if not text:
        return f"运维抄送({cc_account}): 无待补货 SKU，不推送"
    card = build_replenishment_card(rows, **common)
    return _deliver(account=cc_account, open_id=cc_open_id, card=card, text=text,
                    dry_run=dry_run, label=f"运维抄送({cc_account})", n=len(rows))


def push_replenishment_flow(dry_run: bool = False):
    """补货采购单推送主流程：遍历收件人投递，末尾按配置抄送运维。"""
    recipients = load_recipients()
    results: list[str] = []
    for recipient in recipients:
        try:
            results.append(_push_one(recipient, dry_run=dry_run))
        except Exception as exc:  # 单收件人失败不阻断其余
            results.append(f"{recipient.get('account')}/补货: 异常 {exc}")
    # 运维抄送（可选，未配置即跳过）；独立 try，失败不阻断主流程已发的消息
    try:
        cc_line = _push_cc(dry_run=dry_run)
        if cc_line:
            results.append(cc_line)
    except Exception as exc:
        results.append(f"运维抄送: 异常 {exc}")
    for line in results:
        print(line)
    return results


if __name__ == "__main__":
    push_replenishment_flow()
