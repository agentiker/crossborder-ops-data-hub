"""补货采购单定时推送 flow（飞书直投，0 经 LLM）。

与监控告警同构、复用同一收件人表与飞书直投：按 alert_recipients 逐收件人，按其租户/范围算
补货建议（compute_replenishment），组装采购单文案（replenishment_report）直投飞书私聊。
日级 digest，无去重游标（每次发当前快照）；无待补货 SKU 不发空单。

触发：systemd user timer，每日凌晨一次（见 deploy/systemd/data-push-replenishment.*）。
在途 MVP=0（马帮未接通），文案已提示。
"""
import logging
from typing import Optional

from core.config import settings

logger = logging.getLogger(__name__)

from core.tenancy import set_current_account
from core.timezone import business_today
from flows.scan_fulfillment_alerts import load_recipients, send_feishu_message
from services.replenishment import compute_replenishment
from services.replenishment_config import get_effective_config
from services.replenishment_report import build_replenishment_message
from services.scope_resolution import resolve_filters
from core.db import SessionLocal


def _push_one(recipient: dict, *, dry_run: bool) -> str:
    """为一个收件人算补货并（必要时）投递采购单。返回一行状态。"""
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
            session=session,
        )
    finally:
        session.close()

    message = build_replenishment_message(
        rows,
        scope_display=scope.display_text,
        date_label=f"{today.month}/{today.day}",
        velocity_days=cfg.velocity_days,
        intransit_connected=False,
    )
    if not message:
        return f"{account}/补货: 无待补货 SKU，不推送"

    if dry_run:
        print(f"[replenish][dry-run] → {account}/{open_id}:\n{message}")
        return f"{account}/补货: 待推送 {len(rows)} SKU（dry-run）"

    sent = send_feishu_message(account=account, open_id=open_id, text=message)
    if sent:
        return f"{account}/补货: 已推送 {len(rows)} SKU"
    return f"{account}/补货: 推送失败"


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
            session=session,
        )
    finally:
        session.close()

    message = build_replenishment_message(
        rows,
        scope_display=scope.display_text,
        date_label=f"{today.month}/{today.day}",
        velocity_days=cfg.velocity_days,
        intransit_connected=False,
    )
    if not message:
        return f"运维抄送({cc_account}): 无待补货 SKU，不推送"

    if dry_run:
        print(f"[replenish][dry-run] → 抄送 {cc_account}/{cc_open_id}:\n{message}")
        return f"运维抄送({cc_account}): 待推送 {len(rows)} SKU（dry-run）"

    sent = send_feishu_message(account=cc_account, open_id=cc_open_id, text=message)
    if sent:
        return f"运维抄送({cc_account}): 已推送 {len(rows)} SKU"
    return f"运维抄送({cc_account}): 推送失败"


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
