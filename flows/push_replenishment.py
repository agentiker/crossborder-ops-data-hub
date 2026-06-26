"""补货采购单定时推送 flow（飞书直投，0 经 LLM）。

与监控告警同构、复用同一收件人表与飞书直投：按 alert_recipients 逐收件人，按其租户/范围算
补货建议（compute_replenishment），组装采购单文案（replenishment_report）直投飞书私聊。
日级 digest，无去重游标（每次发当前快照）；无待补货 SKU 不发空单。

触发：systemd user timer，每日凌晨一次（见 deploy/systemd/data-push-replenishment.*）。
在途 MVP=0（马帮未接通），文案已提示。
"""
import logging
from typing import Optional

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


def push_replenishment_flow(dry_run: bool = False):
    """补货采购单推送主流程：遍历收件人投递。"""
    recipients = load_recipients()
    results: list[str] = []
    for recipient in recipients:
        try:
            results.append(_push_one(recipient, dry_run=dry_run))
        except Exception as exc:  # 单收件人失败不阻断其余
            results.append(f"{recipient.get('account')}/补货: 异常 {exc}")
    for line in results:
        print(line)
    return results


if __name__ == "__main__":
    push_replenishment_flow()
