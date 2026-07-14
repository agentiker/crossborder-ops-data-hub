"""马帮成本同步 flow：无头浏览器抓「统一成本价」→ 算 {seller_sku: 成本} → 入 product_costs。

全局单账户流（**不**按店循环）：一个马帮账号(421030)对应一个 TikTok 店(SasaQueen.id/gtl)，
成本按 seller_sku 归该租户。成本源 = 库存SKU 的统一成本价(defaultCost)，组合 = Σ(成分×件数)，
单件 = 自身成本（详见 services/mabang_cost、memory mabang-cost-scrape-feasibility）。

成本更新不频繁、价差不大 → timer 周更即可。入库前做健全性校验（抽空/登录失效即 raise，
不落脏数据），失败 → systemd OnFailure 飞书告警。

登录失败**不重试**（马帮 23 次错误锁 1h，凭证错时重试只会更快锁号）；浏览器会话整体重来
代价高、收益低，故本流不套 @retry，失败即告警人工查。

用法：
    uv run python -m flows.sync_mabang_costs            # 抓数入库
    uv run python -m flows.sync_mabang_costs --dry-run  # 只抓数+算+打印，不写库
"""
import argparse
import logging
from datetime import datetime, timezone
from decimal import Decimal

from core.config import settings
from core.db import SessionLocal, init_db
from core.tenancy import current_account, set_current_account
from services.mabang_client import MabangClient
from services.mabang_cost import compute_costs
from services.product_cost_store import import_costs_from_rows
from services.sync_state import record_raw_response, upsert_cursor

logger = logging.getLogger(__name__)

PLATFORM = "tiktok_shop"          # product_costs 按 seller_sku join tiktok_shop 订单
SOURCE = "mabang"                  # 审计 raw 的 platform 标识
RESOURCE = "product_cost"

# 健全性阈值：抓到的基础SKU 数与可计价成本行数低于此即判抽取异常（登录失效/页面改版返回空）。
MIN_BASE_SKUS = 100
MIN_COST_ROWS = 100
# 抽样护栏：已知组合 809-KH-L（1黑+1香槟）≈ 15.78，落此区间外疑似单位/解析错。存在才校验。
_SAMPLE_SKU = "809-KH-L"
_SAMPLE_MIN, _SAMPLE_MAX = Decimal("5"), Decimal("40")


def _resolve_account() -> str:
    """成本归属租户：MABANG__ACCOUNT 优先，否则回落 current_account()（=DEFAULT_ACCOUNT）。"""
    return (settings.mabang.account or "").strip() or current_account()


def extract() -> dict:
    """登录马帮 → 抓基础SKU成本 + 组合成分 → 算 rows。返回 compute_costs 结果 + base 统计。"""
    with MabangClient(settings.mabang.user, settings.mabang.password) as mb:
        base_costs = mb.fetch_base_costs()
        combos = mb.fetch_combos(base_costs)
    result = compute_costs(base_costs, combos)
    result["base_total"] = len(base_costs)
    result["base_nonzero"] = sum(1 for v in base_costs.values() if v > 0)
    return result


def _assert_sane(result: dict) -> None:
    """入库前硬校验：抓取规模够 + 抽样成本合理。失败 raise（不落脏数据 → OnFailure 告警）。"""
    if result["base_total"] < MIN_BASE_SKUS:
        raise RuntimeError(
            f"马帮基础SKU 仅 {result['base_total']} 个（<{MIN_BASE_SKUS}）——疑似登录失效/页面改版返回空"
        )
    if len(result["rows"]) < MIN_COST_ROWS:
        raise RuntimeError(
            f"可计价成本行仅 {len(result['rows'])} 条（<{MIN_COST_ROWS}）——疑似成本抓取异常"
        )
    sample = next((r for r in result["rows"] if r["seller_sku"] == _SAMPLE_SKU), None)
    if sample and not (_SAMPLE_MIN <= sample["unit_cost_rmb"] <= _SAMPLE_MAX):
        raise RuntimeError(
            f"抽样 {_SAMPLE_SKU} 成本={sample['unit_cost_rmb']} 超出合理区间 "
            f"[{_SAMPLE_MIN},{_SAMPLE_MAX}]——疑似单位/解析错"
        )


def save_to_db(result: dict, account: str) -> dict:
    """单事务：记 raw 审计摘要 → import_costs_from_rows 幂等 upsert → 更新 cursor。"""
    set_current_account(account)
    summary = {
        "base_total": result["base_total"],
        "base_nonzero": result["base_nonzero"],
        "combos_costed": result["combos"],
        "combos_skipped": len(result["skipped"]),
        "singles": result["singles"],
        "rows": len(result["rows"]),
    }
    session = SessionLocal()
    try:
        raw = record_raw_response(
            session,
            platform=SOURCE,
            country="GLOBAL",
            account_id=account,
            resource=RESOURCE,
            method="GET",
            path="/index.php?mod=stock.getStockList+combosku.getCombosSkuList",
            request_params={},
            request_body={},
            response_payload=summary,
            http_status=200,
            business_code="0",
        )
        imp = import_costs_from_rows(
            session, result["rows"], account_id=account, platform=PLATFORM
        )
        upsert_cursor(
            session,
            platform=SOURCE,
            country="GLOBAL",
            account_id=account,
            resource=RESOURCE,
            window_end=datetime.now(timezone.utc),
            extra={**summary, **imp, "raw_response_id": raw.id},
        )
        session.commit()
        return imp
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def sync_mabang_costs_flow(dry_run: bool = False) -> dict:
    """主流程。dry_run=True 只抓数+算+打印覆盖统计，不写库。"""
    init_db()
    account = _resolve_account()
    logger.info("马帮成本同步开始（account=%s, dry_run=%s）", account, dry_run)
    result = extract()
    _assert_sane(result)

    summary = (
        f"基础SKU {result['base_total']}（非零 {result['base_nonzero']}）| "
        f"组合计价 {result['combos']}、跳过 {len(result['skipped'])} | "
        f"单件 {result['singles']} | 可入库行 {len(result['rows'])}"
    )
    if dry_run:
        print("[dry-run] " + summary)
        for r in sorted(result["rows"], key=lambda x: x["seller_sku"])[:20]:
            print(f"  {r['seller_sku']:24} {r['unit_cost_rmb']}")
        if result["skipped"]:
            print(f"  跳过样例: {result['skipped'][:5]}")
        return {"dry_run": True, **{k: result[k] for k in ("combos", "singles")}}

    imp = save_to_db(result, account)
    print(f"马帮成本同步完成: {summary} → 入库 新增{imp['inserted']}/更新{imp['updated']}"
          f"（坏行 {len(imp['errors'])}）")
    return imp


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只抓数+算+打印，不写库")
    args = ap.parse_args()
    sync_mabang_costs_flow(dry_run=args.dry_run)
