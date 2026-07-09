"""GMV Max 报表解析层回归锁（纯函数，用官方 fixture）。

坐实点不能回退：
- 花费在 metrics.cost（字符串 → Decimal）
- 按天维度键 stat_time_day → date
- 币种带出、跨币种汇总不混算
- 缺失/空 metric → Decimal(0)，无 stat_time_day 的行 metric_date=None（不丢）
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from platforms.tiktok_business.normalize import (
    parse_gmv_max_report,
    summarize_cost,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name):
    return json.loads((FIXTURES / name).read_text())["data"]


def test_parse_daily_fixture():
    data = _load("gmv_max_report_daily.json")
    rows = parse_gmv_max_report(data, store_id="store9", advertiser_id="adv1")

    assert len(rows) == 3
    first = rows[0]
    assert first["metric_date"] == date(2025, 7, 1)
    assert first["cost"] == Decimal("125000.00")
    assert first["net_cost"] == Decimal("120000.00")
    assert first["gross_revenue"] == Decimal("600000.00")
    assert first["currency"] == "IDR"
    assert first["store_id"] == "store9"
    assert first["advertiser_id"] == "adv1"
    # 类型是 Decimal，不是 str
    assert isinstance(first["cost"], Decimal)


def test_zero_cost_row_kept():
    """cost="0.00" 的行照样保留（真实会有花费为 0 的天），归 Decimal(0)。"""
    data = _load("gmv_max_report_daily.json")
    rows = parse_gmv_max_report(data)
    zero = [r for r in rows if r["metric_date"] == date(2025, 7, 3)][0]
    assert zero["cost"] == Decimal("0")
    assert zero["orders"] == Decimal("0")


def test_summarize_cost_by_currency():
    data = _load("gmv_max_report_daily.json")
    rows = parse_gmv_max_report(data)
    totals = summarize_cost(rows)
    # 125000 + 98000.50 + 0
    assert totals == {"IDR": Decimal("223000.50")}


def test_official_example_structure():
    """官方原始示例（item_id 维度、无 stat_time_day）：cost 正确解析，metric_date=None 不丢行。"""
    data = _load("gmv_max_report_official_example.json")
    rows = parse_gmv_max_report(data, store_id="s1")
    assert len(rows) >= 1
    r0 = rows[0]
    assert r0["metric_date"] is None            # 该示例按 item_id 聚合，无按天维度
    assert r0["cost"] == Decimal("0.01")        # 官方示例第一行 cost="0.01"
    assert r0["currency"] == "USD"
    assert r0["dimensions"].get("item_id") is not None


def test_missing_metrics_default_zero():
    rows = parse_gmv_max_report({"list": [{"dimensions": {"stat_time_day": "2025-07-05"}, "metrics": {}}]})
    assert rows[0]["cost"] == Decimal("0")
    assert rows[0]["currency"] is None
    assert rows[0]["metric_date"] == date(2025, 7, 5)


def test_empty_data():
    assert parse_gmv_max_report({}) == []
    assert parse_gmv_max_report({"list": []}) == []
    assert summarize_cost([]) == {}
