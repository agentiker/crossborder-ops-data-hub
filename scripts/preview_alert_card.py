"""告警卡片视觉小样（假数据,一次性预览用,不入库）。

拼 3 张不同 severity 的告警卡直投飞书,给用户看视觉效果:
  1. 红卡:待发货超时(告急)
  2. 橙卡:低库存预警
  3. 蓝卡:爆单喜报
用法: uv run python scripts/preview_alert_card.py <open_id> [account_id]
"""
from __future__ import annotations

import sys

from web.feishu_card_sender import send_interactive_card


def _md(content: str) -> dict:
    return {"tag": "markdown", "content": content}


def _hr() -> dict:
    return {"tag": "hr"}


def _kpi_columns(cells: list[tuple[str, str]]) -> dict:
    return {
        "tag": "column_set", "flex_mode": "stretch",
        "horizontal_spacing": "default",
        "columns": [
            {
                "tag": "column", "width": "weighted", "weight": 1,
                "elements": [_md(f"<font color='grey'>{label}</font>\n**{val}**")],
            }
            for label, val in cells
        ],
    }


def _table(columns: list[dict], rows: list[dict]) -> dict:
    return {"tag": "table", "columns": columns, "rows": rows,
            "row_height": "low", "header_style": {"bold": True, "background_style": "grey"}}


def _board_button(text: str = "打开运营看板") -> dict:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": text},
        "type": "primary", "width": "fill",
        "icon": {"tag": "standard_icon", "token": "chart-line_outlined"},
        "behaviors": [{"type": "open_url", "default_url": "https://ecom.agenticker.cc/app/board"}],
    }


def card_fulfillment() -> dict:
    """红卡:待发货超时告急。"""
    return {
        "schema": "2.0",
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "red",
            "title": {"tag": "plain_text", "content": "🚨 待发货超时告警"},
            "subtitle": {"tag": "plain_text", "content": "全部店铺 · 2 个店铺"},
        },
        "body": {"elements": [
            _md("<font color='red'>**3 单已超发货时限**</font>,较上次提醒 <font color='red'>+2</font>,请尽快安排发货,避免平台取消/扣分。"),
            _kpi_columns([("超时未发", "3 单"), ("临界(<6h)", "2 单"), ("待发货总量", "18 单")]),
            _hr(),
            _table(
                [
                    {"name": "order", "display_name": "订单", "data_type": "text",
                     "width": "34%", "horizontal_align": "left"},
                    {"name": "shop", "display_name": "店铺", "data_type": "text",
                     "width": "26%", "horizontal_align": "left"},
                    {"name": "overdue", "display_name": "超时", "data_type": "text",
                     "width": "20%", "horizontal_align": "right"},
                    {"name": "status", "display_name": "状态", "data_type": "options",
                     "width": "20%", "horizontal_align": "center"},
                ],
                [
                    {"order": "…578042", "shop": "SasaQueen.id", "overdue": "9.5h",
                     "status": [{"text": "超时", "color": "red"}]},
                    {"order": "…331207", "shop": "SasaQueen.id", "overdue": "4.2h",
                     "status": [{"text": "超时", "color": "red"}]},
                    {"order": "…895164", "shop": "GTL Store", "overdue": "1.1h",
                     "status": [{"text": "超时", "color": "red"}]},
                ],
            ),
            _hr(),
            _board_button("去看板处理"),
            _md("<font color='grey'>🕐 巡检时间 2026-07-10 21:30 (CST) · 阈值:发货时限前 6h 预警</font>"),
        ]},
    }


def card_stock() -> dict:
    """橙卡:低库存预警。"""
    return {
        "schema": "2.0",
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "orange",
            "title": {"tag": "plain_text", "content": "📦 库存风险预警"},
            "subtitle": {"tag": "plain_text", "content": "全部店铺 · 2 个店铺"},
        },
        "body": {"elements": [
            _md("<font color='red'>**1 个 SKU 已断货**</font> · <font color='orange'>**2 个告急**</font>(可售 <3 天),按当前销速建议尽快补货。"),
            _hr(),
            _table(
                [
                    {"name": "name", "display_name": "商品", "data_type": "text",
                     "width": "42%", "horizontal_align": "left"},
                    {"name": "stock", "display_name": "库存", "data_type": "text",
                     "width": "16%", "horizontal_align": "right"},
                    {"name": "days", "display_name": "可售天", "data_type": "text",
                     "width": "20%", "horizontal_align": "right"},
                    {"name": "level", "display_name": "状态", "data_type": "options",
                     "width": "22%", "horizontal_align": "center"},
                ],
                [
                    {"name": "Gamis Kaftan Warna Sage — XL", "stock": "0", "days": "0",
                     "level": [{"text": "断货", "color": "red"}]},
                    {"name": "Hijab Instan Premium — Mocca", "stock": "6", "days": "1.8",
                     "level": [{"text": "告急", "color": "orange"}]},
                    {"name": "Setelan Anak Muslim — 120", "stock": "11", "days": "2.6",
                     "level": [{"text": "告急", "color": "orange"}]},
                ],
            ),
            _hr(),
            _board_button("查看库存明细"),
            _md("<font color='grey'>🕐 巡检时间 2026-07-10 21:30 (CST) · 阈值:告急 <3 天 / 断货 =0(可在设置页调整)</font>"),
        ]},
    }


def card_hotsell() -> dict:
    """蓝卡(喜报):爆单提醒。"""
    return {
        "schema": "2.0",
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "wathet",
            "title": {"tag": "plain_text", "content": "🔥 爆单提醒"},
            "subtitle": {"tag": "plain_text", "content": "全部店铺 · 7/10"},
        },
        "body": {"elements": [
            _md("今日 <font color='carmine'>**2 个商品**</font>销量破爆单阈值(50 件/天),其中 1 个为新品🌟,关注库存与投放追量。"),
            _kpi_columns([("爆单商品", "2 个"), ("合计销量", "137 件"), ("合计 GMV", "Rp 8.2M")]),
            _hr(),
            _md("**1. Gamis Kaftan Premium Warna Sage** 🌟新品爆发\n"
                "　<font color='grey'>今日</font> **86 件** · Rp 5.1M　<font color='grey'>(阈值 50)</font>\n"
                "**2. Hijab Instan Ceruti Babydoll**\n"
                "　<font color='grey'>今日</font> **51 件** · Rp 3.1M　<font color='grey'>(阈值 50)</font>"),
            _hr(),
            _board_button("看爆款详情"),
            _md("<font color='grey'>🕐 巡检时间 2026-07-10 21:30 (CST) · 爆单阈值 50 件/天(可在设置页调整) · 当日已报商品不重复提醒</font>"),
        ]},
    }


if __name__ == "__main__":
    open_id = sys.argv[1] if len(sys.argv) > 1 else "ou_7afe4514b269e5a0abfbd395f3f26410"
    account = sys.argv[2] if len(sys.argv) > 2 else "ecom-app"

    def _variant(card: dict, template: str, title_prefix: str, tag: str) -> dict:
        import copy
        c = copy.deepcopy(card)
        if template:
            c["header"]["template"] = template
        else:
            c["header"].pop("template", None)
        t = c["header"]["title"]["content"]
        # 去掉原 emoji,换成方案前缀
        t = t.split(" ", 1)[1] if " " in t else t
        c["header"]["title"]["content"] = f"{title_prefix} {t}"
        c["header"]["subtitle"]["content"] = tag + " · " + c["header"]["subtitle"]["content"]
        return c

    # 方案A 深色系:carmine / indigo / violet
    for name, builder, tpl, prefix in [
        ("待发货(深绯)", card_fulfillment, "carmine", "🚨"),
        ("库存(靛蓝)", card_stock, "indigo", "📦"),
        ("爆单(紫)", card_hotsell, "violet", "🔥"),
    ]:
        msg_id = send_interactive_card(account, open_id, _variant(builder(), tpl, prefix, "方案A·深色系"))
        print(f"已发 A/{name}: {msg_id}")

    # 方案B 中性极简:统一 grey 卡头,severity 用色点 emoji
    for name, builder, prefix in [
        ("待发货(灰头+红点)", card_fulfillment, "🔴"),
        ("库存(灰头+橙点)", card_stock, "🟠"),
        ("爆单(灰头+蓝点)", card_hotsell, "🔵"),
    ]:
        msg_id = send_interactive_card(account, open_id, _variant(builder(), "grey", prefix, "方案B·中性"))
        print(f"已发 B/{name}: {msg_id}")
