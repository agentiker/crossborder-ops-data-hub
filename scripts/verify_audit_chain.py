"""审计哈希链完整性校验 CLI（plan 审计合规第 2 节）。

重算 api_call_logs / audit_log 两表所有租户链：每行用「实际前一行 row_hash + 本行规范串」
复算 row_hash 并与存库值比对（prev_hash 指针也校验），任意断裂即报告。配 systemd timer
周期跑 + 非零退出码触发 OnFailure→飞书，使「单行被改 → 其后 hash 全断」可被主动发现。

退出码：0 = 两链完好；1 = 检出断裂行（详情打印 table/account_id/id）；2 = 运行异常。

用法：
  uv run python -m scripts.verify_audit_chain          # 校验，断裂则非零退出
  uv run python -m scripts.verify_audit_chain --json   # 结果以 JSON 输出（供监控解析）
"""
from __future__ import annotations

import argparse
import json
import sys

from core.db import SessionLocal
from core.tenancy import TENANT_BYPASS, set_current_account
from services.audit import CHAIN_MODELS, verify_chain


def run(as_json: bool = False) -> int:
    # 跨租户全表校验：绕过 ORM 自动租户过滤，否则只看得到当前租户链。
    set_current_account(TENANT_BYPASS)
    session = SessionLocal()
    try:
        all_breaks: list[dict] = []
        per_table: dict[str, int] = {}
        for model, canonical_fn in CHAIN_MODELS:
            breaks = verify_chain(session, model, canonical_fn)
            per_table[model.__tablename__] = len(breaks)
            all_breaks.extend(breaks)
    finally:
        session.close()

    if as_json:
        print(json.dumps({"ok": not all_breaks, "per_table": per_table,
                          "breaks": all_breaks}, ensure_ascii=False))
    else:
        for t, n in per_table.items():
            print(f"  {t}: {'完好' if n == 0 else f'✗ {n} 行断裂'}")
        if all_breaks:
            print("断裂行：")
            for b in all_breaks:
                print(f"  - {b['table']} account={b['account_id']} id={b['id']}")
        else:
            print("两条审计链完好，未检出篡改。")
    return 1 if all_breaks else 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="审计哈希链完整性校验（断裂非零退出）")
    p.add_argument("--json", action="store_true", help="结果以 JSON 输出，供监控解析")
    try:
        raise SystemExit(run(as_json=p.parse_args().json))
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"✗ 校验运行异常: {exc}", file=sys.stderr)
        raise SystemExit(2)
