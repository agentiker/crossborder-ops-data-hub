"""告警卡片 builder 单测：喂结构化假数据,断言 CardKit JSON 结构与关键内容。

纯函数测试,不触网不连库。校验点:
- schema 2.0 + 方案A模板色(carmine/indigo/violet)
- 关键数字/名称出现在卡片 JSON 里
- table 行数截断(库存最多10行/爆单最多8条)
- board_url 空时省略按钮
"""
from __future__ import annotations

import json

from web.alert_card_builder import (
    TEMPLATE_CRITICAL,
    TEMPLATE_GOOD,
    TEMPLATE_WARNING,
    build_fee_rate_card,
    build_fulfillment_card,
    build_hotsell_card,
    build_stock_card,
)


def _dump(card: dict) -> str:
    return json.dumps(card, ensure_ascii=False)


def _tags(card: dict) -> list[str]:
    return [e.get("tag") for e in card["body"]["elements"]]


def test_fulfillment_card_structure():
    card = build_fulfillment_card(
        scope_display="全部店铺 · 2 个店铺",
        overdue=3, critical=2, total=18, delta=2, prev_reported=1,
        by_shop=[{"shop_id": "111", "overdue": 2}, {"shop_id": "222", "overdue": 1}],
        shop_names={"111": "SasaQueen.id"},
        board_url="https://x.example/app/board",
    )
    assert card["schema"] == "2.0"
    assert card["header"]["template"] == TEMPLATE_CRITICAL
    s = _dump(card)
    assert "3 单已超发货时限" in s
    assert "+2" in s  # delta（prev_reported>0 才显示）
    assert "SasaQueen.id" in s  # 店名富化
    assert "222" in s  # 无名店回落裸 id
    assert "button" in _tags(card)


def test_fulfillment_card_first_time_no_delta():
    card = build_fulfillment_card(
        scope_display="s", overdue=1, critical=0, total=5, delta=1,
        prev_reported=0, by_shop=[], shop_names={}, board_url="",
    )
    s = _dump(card)
    assert "较上次" not in s  # 首次不显 delta
    assert "button" not in _tags(card)  # 无 board_url 省略按钮


def test_stock_card_stockout_is_critical_template():
    items = [
        {"sku_id": f"sku{i}", "product_name": f"P{i}", "available_stock": i,
         "days_of_cover": float(i), "bucket": "critical"}
        for i in range(15)
    ]
    card = build_stock_card(
        scope_display="s", stockout=1, critical=14, warning=0,
        items=items, new_skus=["sku3"], board_url="", critical_days=3,
    )
    assert card["header"]["template"] == TEMPLATE_CRITICAL  # 有断货 → carmine
    s = _dump(card)
    assert "🆕 P3" in s  # 新进风险标注且排最前
    assert "另有 5 个风险 SKU 未列出" in s  # 15-10 截断提示
    table = next(e for e in card["body"]["elements"] if e.get("tag") == "table")
    assert len(table["rows"]) == 10


def test_stock_card_no_stockout_is_warning_template():
    card = build_stock_card(
        scope_display="s", stockout=0, critical=2, warning=1,
        items=[], new_skus=[], board_url="",
    )
    assert card["header"]["template"] == TEMPLATE_WARNING  # 无断货 → indigo


def test_fee_rate_card_realtime_vs_settled():
    kw = dict(
        scope_display="s", currency="IDR", eval_rate=0.223, baseline_rate=0.198,
        abs_change=0.025, eval_gmv=52_000_000.0,
        eval_window_label="7/8~7/10", baseline_window_label="6/10~7/7",
        board_url="",
    )
    rt = build_fee_rate_card(realtime=True, **kw)
    st = build_fee_rate_card(realtime=False, **kw)
    assert rt["header"]["template"] == TEMPLATE_WARNING
    assert "及时费率" in _dump(rt) and "未结算预估" in _dump(rt)
    assert "扣点率" in _dump(st) and "已结算" in _dump(st)
    assert "22.30%" in _dump(rt)
    assert "+2.5 个百分点" in _dump(rt)
    assert "52.0M" in _dump(rt)


def test_fee_rate_card_shows_evidence_and_policy_refs():
    card = build_fee_rate_card(
        scope_display="s", currency="IDR", eval_rate=0.28, baseline_rate=0.20,
        abs_change=0.08, eval_gmv=52_000_000.0,
        eval_window_label="7/8~7/10", baseline_window_label="6/10~7/7",
        realtime=True, board_url="",
        evidence={
            "fee_items": [{
                "key": "dynamic_commission_amount",
                "name": "动态佣金",
                "from": 0.10,
                "to": 0.14,
                "delta": 0.04,
                "source_field": "fee_tax_breakdown.fee.dynamic_commission_amount",
            }],
        },
        policy_references=[{
            "title": "TikTok Shop 佣金政策",
            "url": "https://seller-id.tiktok.com/university/policy",
            "source": "TikTok Shop Academy",
        }],
    )
    s = _dump(card)
    assert "检测依据" in s
    assert "动态佣金" in s
    assert "fee_tax_breakdown.fee.dynamic_commission_amount" not in s
    assert "官方参考资料" in s
    assert "https://seller-id.tiktok.com/university/policy" in s


def test_hotsell_card_structure_and_cap():
    prods = [
        {"product_id": str(i), "units": 50 + i, "name": f"商品{i}", "is_new": i == 0}
        for i in range(10)
    ]
    card = build_hotsell_card(
        scope_display="s", date_label="7/10", threshold=50,
        new_products=prods, board_url="https://x.example/app/board",
    )
    assert card["header"]["template"] == TEMPLATE_GOOD
    s = _dump(card)
    assert "10 个商品" in s
    assert "🌟新品爆发" in s
    assert "另有 2 个爆单商品未列出" in s  # 10-8 截断
    assert "button" in _tags(card)
