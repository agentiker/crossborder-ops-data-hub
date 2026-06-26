"""审计链每日锚定 flow（plan 审计合规第 2 节）：把各租户哈希链尾外发飞书留痕。

哈希链让「单行被改 → 其后 row_hash 全断」可被 scripts/verify_audit_chain 检出，但若攻击者
能改库，理论上可整条重算 row_hash 抹平痕迹。**锚定 = 把今日链尾（tip_id + tip_hash）发到
飞书运维群外部留痕**：链一旦被整段重写，今日链尾就与已外发的昨日锚点对不上 → 不可抵赖。

每次顺手内联 verify_chain：锚定前先确认链未断，把完整性结果一并写进留痕消息（断了即在飞书
告警，不必等独立 verify timer）。锚点收件人 settings.audit_anchor_account/open_id（运维）；
任一为空 → 只 print（journald，同机可被同管理员改，无外部留痕）+ 警告，不发飞书。

调度：systemd timer data-anchor-audit（每日一次）。本地：uv run python -m flows.anchor_audit_chain
"""
from __future__ import annotations

from core.config import settings
from core.db import SessionLocal
from core.tenancy import TENANT_BYPASS, set_current_account
from core.timezone import business_today
from flows.scan_fulfillment_alerts import send_feishu_message
from services.audit import CHAIN_MODELS, chain_tips, verify_chain


def _build_message(session) -> tuple[str, int]:
    """组装锚定留痕文案；返回 (文案, 断裂总数)。跨租户读，调用方须已 set TENANT_BYPASS。"""
    today = business_today()
    lines = [f"🔒 审计链每日锚定 {today.month}/{today.day}"]
    integrity_lines: list[str] = []
    tip_blocks: list[str] = []
    total_breaks = 0

    for model, canonical_fn in CHAIN_MODELS:
        table = model.__tablename__
        breaks = verify_chain(session, model, canonical_fn)
        total_breaks += len(breaks)
        integrity_lines.append(
            f"{table}: {'完好' if not breaks else f'✗ {len(breaks)} 行断裂'}"
        )
        tips = chain_tips(session, model)
        block = [f"[{table}]"]
        if not tips:
            block.append("  (空链)")
        for t in sorted(tips, key=lambda x: x["account_id"] or ""):
            h = (t["tip_hash"] or "")[:12]
            block.append(
                f"  {t['account_id'] or '(无租户)'}: #{t['tip_id']} {h}… (共 {t['count']})"
            )
        tip_blocks.append("\n".join(block))

    lines.extend(integrity_lines)
    lines.append("— 链尾 —")
    lines.extend(tip_blocks)
    if total_breaks:
        lines.append("⚠️ 检出哈希链断裂，疑似篡改，请立即排查！")
    return "\n".join(lines), total_breaks


def anchor_audit_chain_flow(dry_run: bool = False) -> dict:
    """每日锚定主流程：内联校验两链 → 链尾外发飞书留痕。"""
    if not settings.audit_anchor_enabled:
        print("[anchor] audit_anchor_enabled=False，跳过锚定")
        return {"skipped": True}

    set_current_account(TENANT_BYPASS)  # 跨租户读全部链
    session = SessionLocal()
    try:
        message, total_breaks = _build_message(session)
    finally:
        session.close()

    print(f"[anchor] 锚定文案：\n{message}")

    account = (settings.audit_anchor_account or "").strip()
    open_id = (settings.audit_anchor_open_id or "").strip()
    if not (account and open_id):
        print("[anchor] ⚠️ 未配置 audit_anchor_account/open_id，仅写 journald 无外部留痕——"
              "「不可抵赖」失效，生产请在 .env 配置锚点收件人")
        return {"sent": False, "breaks": total_breaks, "reason": "no_recipient"}

    if dry_run:
        print("[anchor][dry-run] 不实发")
        return {"sent": False, "breaks": total_breaks, "dry_run": True}

    sent = send_feishu_message(account=account, open_id=open_id, text=message)
    if sent:
        print(f"[anchor] ✅ 已投递运维 {account}/{open_id}（链尾留痕，breaks={total_breaks}）")
    else:
        print("[anchor] ✗ 飞书投递失败（本轮无外部锚点，下次 timer 重试）")
    return {"sent": sent, "breaks": total_breaks}


if __name__ == "__main__":
    anchor_audit_chain_flow()
