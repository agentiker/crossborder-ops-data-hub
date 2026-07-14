"""马帮成本 → {seller_sku: unit_cost_rmb} 计算（纯逻辑，无浏览器/无 DB，可单测）。

背景（见 memory mabang-cost-scrape-feasibility）：TikTok 的 seller_sku 有两类，都用马帮
「统一成本价」(库存SKU 的 defaultCost) 作成本源：
  - 组合SKU（短码如 809-KH-L，占销量 ~92%）：seller_sku == 马帮组合SKU 名，
    成本 = Σ(成分基础SKU 统一成本价 × 件数)。
  - 单件基础SKU（XGN 前缀如 XGN396_013Skin-XL，占销量 ~7%）：seller_sku == 基础SKU 名，
    成本 = 自身统一成本价。

只产出「成本 > 0」的行：缺成本的 SKU 不写（利润链对缺失 seller_sku 自动计 0 + 告警，
客户在马帮补录后下次同步自动带上）。组合只要有**任一**成分缺成本就整条跳过——
用不完整成本会低估利润、误导，宁可留空计 0。
"""
from __future__ import annotations

from decimal import Decimal

# 组合成分：(基础SKU, 件数)
Component = tuple[str, int]


def _combo_note(comps: list[Component]) -> str:
    """组合成本的可读成分明细（存 product_costs.note，便于审计追溯），截断防超 500。"""
    parts = [f"{sku}×{qty}" for sku, qty in comps]
    note = "马帮组合: " + " + ".join(parts)
    return note[:500]


def compute_costs(
    base_costs: dict[str, Decimal],
    combos: dict[str, list[Component]],
) -> dict:
    """由基础SKU统一成本价 + 组合成分构成，算出可入 product_costs 的行。

    Args:
        base_costs: {基础SKU: 统一成本价 Decimal}（0 或缺失视为无成本）。
        combos: {组合SKU: [(成分基础SKU, 件数), ...]}。

    Returns:
        {
          "rows": [{"seller_sku", "unit_cost_rmb": Decimal, "note"}...]  # 仅成本>0
          "singles": int,        # 单件行数
          "combos": int,         # 成功计价的组合行数
          "skipped": [(combo_sku, reason)],  # 因缺成分成本跳过的组合
        }
    """
    rows: list[dict] = []
    skipped: list[tuple[str, str]] = []

    # 组合优先：seller_sku==组合名，避免与同名基础SKU（若有）冲突时被单件覆盖。
    combo_costed = 0
    combo_skus = set(combos)
    for combo_sku, comps in combos.items():
        if not comps:
            skipped.append((combo_sku, "无成分"))
            continue
        total = Decimal("0")
        missing = None
        for base_sku, qty in comps:
            c = base_costs.get(base_sku)
            if c is None or c <= 0:
                missing = base_sku
                break
            total += c * qty
        if missing is not None:
            skipped.append((combo_sku, f"成分缺成本: {missing}"))
            continue
        rows.append({
            "seller_sku": combo_sku,
            "unit_cost_rmb": total,
            "note": _combo_note(comps),
        })
        combo_costed += 1

    # 单件：基础SKU 自身成本>0，且不与组合同名（组合已产出）。
    singles = 0
    for base_sku, cost in base_costs.items():
        if cost is None or cost <= 0:
            continue
        if base_sku in combo_skus:
            continue
        rows.append({
            "seller_sku": base_sku,
            "unit_cost_rmb": cost,
            "note": "马帮统一成本价",
        })
        singles += 1

    return {
        "rows": rows,
        "singles": singles,
        "combos": combo_costed,
        "skipped": skipped,
    }
