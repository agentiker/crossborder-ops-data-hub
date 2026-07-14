"""compute_costs 纯逻辑回归锁（组合拆解 / 单件 / 缺成分跳过 / Σ 计算 / 组合优先）。

真数据基准（见 memory）：809-KH-L = 1黑+1香槟 @7.89 = 15.78；809-HHK-XL = 2黑+1香槟 = 23.67；
单件 XGN396_013Skin-XL 统一成本价 13.96。
"""
from __future__ import annotations

from decimal import Decimal

from services.mabang_cost import compute_costs


def _rows_map(result):
    return {r["seller_sku"]: r["unit_cost_rmb"] for r in result["rows"]}


def test_combo_sum_and_single():
    base = {
        "XGN809-002Black-L": Decimal("7.89"),
        "XGN809-002Champagne-L": Decimal("7.89"),
        "XGN396_013Skin-XL": Decimal("13.96"),  # 单件直卖
    }
    combos = {
        "809-KH-L": [("XGN809-002Black-L", 1), ("XGN809-002Champagne-L", 1)],
        "809-2H-L": [("XGN809-002Black-L", 2)],
        "809-HHK-L": [("XGN809-002Black-L", 2), ("XGN809-002Champagne-L", 1)],
    }
    result = compute_costs(base, combos)
    m = _rows_map(result)
    assert m["809-KH-L"] == Decimal("15.78")   # 1黑+1香槟
    assert m["809-2H-L"] == Decimal("15.78")   # 2黑
    assert m["809-HHK-L"] == Decimal("23.67")  # 2黑+1香槟（三件套）
    assert m["XGN396_013Skin-XL"] == Decimal("13.96")  # 单件
    assert result["combos"] == 3
    # 三个基础SKU 成本均>0 且非组合同名 → 都作为单件候选行产出（多余行无害，见 plan）
    assert result["singles"] == 3
    assert result["skipped"] == []


def test_combo_skipped_when_component_cost_missing_or_zero():
    base = {
        "XGN809-002Black-L": Decimal("7.89"),
        "XGN809-002Champagne-L": Decimal("0"),  # 成分零成本
        # XGN809-002Red-L 完全缺失
    }
    combos = {
        "809-KH-L": [("XGN809-002Black-L", 1), ("XGN809-002Champagne-L", 1)],   # 有零成分 → 跳过
        "809-KR-L": [("XGN809-002Black-L", 1), ("XGN809-002Red-L", 1)],         # 缺成分 → 跳过
        "809-2H-L": [("XGN809-002Black-L", 2)],                                  # 全有 → 计价
        "empty": [],                                                             # 无成分 → 跳过
    }
    result = compute_costs(base, combos)
    m = _rows_map(result)
    # 计价的组合仅 809-2H-L；XGN809-002Black-L 成本>0 也作为单件行产出
    assert m == {"809-2H-L": Decimal("15.78"), "XGN809-002Black-L": Decimal("7.89")}
    assert result["combos"] == 1
    skipped = dict(result["skipped"])
    assert "809-KH-L" in skipped and "809-KR-L" in skipped and "empty" in skipped


def test_single_zero_cost_not_emitted():
    base = {
        "XGN396_013Skin-XL": Decimal("13.96"),
        "2438-Hong-XL": Decimal("0"),      # 僵尸款零成本
        "1696-G6": Decimal("0"),
    }
    result = compute_costs(base, {})
    m = _rows_map(result)
    assert m == {"XGN396_013Skin-XL": Decimal("13.96")}
    assert result["singles"] == 1


def test_combo_takes_priority_over_same_named_single():
    # 若某基础SKU 名恰与组合同名，组合优先，不重复出行
    base = {"809-KH-L": Decimal("99"), "XGN809-002Black-L": Decimal("7.89"),
            "XGN809-002Champagne-L": Decimal("7.89")}
    combos = {"809-KH-L": [("XGN809-002Black-L", 1), ("XGN809-002Champagne-L", 1)]}
    result = compute_costs(base, combos)
    rows = [r for r in result["rows"] if r["seller_sku"] == "809-KH-L"]
    assert len(rows) == 1
    assert rows[0]["unit_cost_rmb"] == Decimal("15.78")  # 组合价，非基础同名的 99


def test_combo_note_has_components():
    base = {"XGN809-002Black-L": Decimal("7.89"), "XGN809-002Champagne-L": Decimal("7.89")}
    combos = {"809-KH-L": [("XGN809-002Black-L", 1), ("XGN809-002Champagne-L", 1)]}
    result = compute_costs(base, combos)
    note = result["rows"][0]["note"]
    assert "XGN809-002Black-L×1" in note and "XGN809-002Champagne-L×1" in note
