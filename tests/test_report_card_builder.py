"""report_card_builder 单测：summary → v2 CardKit 卡片结构断言。

不打飞书 API（那属集成），只验纯函数拼装：组件齐全、低单量护栏、周报健康度、
数字全部来自 summary（analysis 不含数字）。
"""
from __future__ import annotations

from web.report_card_builder import build_report_card, _abbr, _money, _change_tag


def _iter_tags(elements):
    """递归收集所有 element 的 tag（含 column/collapsible 里的嵌套）。"""
    tags = []
    for el in elements:
        if not isinstance(el, dict):
            continue
        tags.append(el.get("tag"))
        for k in ("elements", "columns"):
            if isinstance(el.get(k), list):
                tags.extend(_iter_tags(el[k]))
    return tags


DAILY_SUMMARY = {
    "kind": "daily",
    "title": "经营日报",
    "scope": "TikTok Shop / 印尼 / 1 个店铺",
    "period_label": "6月30日",
    "cutoff_label": None,
    "change_label": "较前一日",
    "low_volume": False,
    "kpi": {
        "gmv": {"value": 178_200_000, "change": 12.0, "baseline": 159_100_000, "currency": "IDR"},
        "orders": {"value": 1393, "change": 8.0, "baseline": 1290},
        "ad_spend": {"value": 450_000, "change": -3.0, "currency": "IDR"},
        "roas": {"value": 27.4, "change": 4.1},
    },
    "top_skus": [
        {"name": "Kaos V Neck Wanita Lengan Panjang Biru Premium", "units": 236, "gmv": 30_000_000, "share": 16.8},
        {"name": "连衣裙 A", "units": 180, "gmv": 22_000_000, "share": 12.3},
    ],
    "low_stock": [
        {"name": "MossWood Spring Bed", "stock": 0, "velocity": 0, "days": 0.0, "level": "stockout", "level_label": "断货"},
        {"name": "Blous Linen 女士上衣", "stock": 72, "velocity": 0.5, "days": 5.0, "level": "warning", "level_label": "偏低"},
    ],
}

WEEKLY_SUMMARY = {
    **DAILY_SUMMARY,
    "kind": "weekly",
    "title": "经营周报",
    "kpi": {
        **DAILY_SUMMARY["kpi"],
        "aov": {"value": 128_000, "change": 4.0, "baseline": 123_000, "currency": "IDR"},
    },
    "health": {
        "concentration": {"top1_name": "Kaos V Neck", "top1_share": 28.5, "top3_share": 62.3},
        "sell_through": {"active_sku": 142, "total_sku": 245, "rate": 58.0},
        "new_products": [
            {"title": "新品 X", "units_sold": 18, "gmv": 350_000},
            {"title": "新品 Y", "units_sold": 9, "gmv": 120_000},
        ],
    },
}


def test_daily_card_has_core_components():
    card = build_report_card(DAILY_SUMMARY, analysis="**今日 GMV 环比上升**，建议给爆款补货。", report_url="https://x/report?t=abc")
    assert card["schema"] == "2.0"
    assert card["header"]["template"] == "blue"
    assert "经营日报" in card["header"]["title"]["content"]
    tags = _iter_tags(card["body"]["elements"])
    # 关键组件齐全：分栏 KPI、爆款表格、库存折叠面板、底部按钮
    assert "column_set" in tags
    assert "table" in tags
    assert "collapsible_panel" in tags
    assert "button" in tags


def test_weekly_card_has_health_and_aov():
    card = build_report_card(WEEKLY_SUMMARY, analysis="本周复盘。", report_url="https://x/r")
    assert card["header"]["template"] == "indigo"
    flat = str(card)
    assert "客单价" in flat          # 周报独有 KPI
    assert "动销率" in flat          # 健康度
    assert "爆款集中度" in flat


def test_analysis_injected_and_button_url():
    card = build_report_card(DAILY_SUMMARY, analysis="独特分析标记_XYZ", report_url="https://link/report?t=tok")
    flat = str(card)
    assert "独特分析标记_XYZ" in flat
    # 底部按钮指向裸链
    assert "https://link/report?t=tok" in flat


def test_low_volume_hides_change_pct_shows_baseline():
    s = {**DAILY_SUMMARY, "low_volume": True,
         "kpi": {"gmv": {"value": 79_000, "change": None, "baseline": 50_000, "currency": "IDR"},
                 "orders": {"value": 1, "change": None, "baseline": 0.3}}}
    card = build_report_card(s, analysis="", report_url="")
    flat = str(card)
    # 低单量：不出现环比箭头百分比，改示"基准"
    assert "基准" in flat
    assert "↑" not in flat and "↓" not in flat


def test_empty_window_weekly():
    s = {"kind": "weekly", "title": "经营周报", "empty_window": True, "kpi": {}, "top_skus": [], "low_stock": []}
    card = build_report_card(s, analysis="", report_url="https://x/r")
    flat = str(card)
    assert "暂无订单" in flat


def test_no_table_nested_in_collapsible_panel():
    """飞书 CardKit 限制：table 不能嵌在 collapsible_panel 内（报 200621）。守此回归。"""
    for s in (DAILY_SUMMARY, WEEKLY_SUMMARY):
        card = build_report_card(s, analysis="x", report_url="https://x/r")

        def _panels_contain_table(elements):
            for el in elements:
                if not isinstance(el, dict):
                    continue
                if el.get("tag") == "collapsible_panel":
                    inner = _iter_tags(el.get("elements", []))
                    if "table" in inner:
                        return True
                for k in ("elements", "columns"):
                    if isinstance(el.get(k), list) and _panels_contain_table(el[k]):
                        return True
            return False

        assert not _panels_contain_table(card["body"]["elements"]), "table 不能嵌在折叠面板内"


def test_money_and_change_helpers():
    assert _abbr(178_200_000) == "178.2M"
    assert _abbr(1500) == "1.5K"
    assert _money(None) == "—"
    assert "red" in _change_tag(12.0)      # 涨红
    assert "blue" in _change_tag(-5.0)     # 跌蓝
    assert _change_tag(None) == ""
