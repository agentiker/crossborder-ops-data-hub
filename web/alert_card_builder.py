"""告警 → 飞书 v2 CardKit 卡片 JSON（后端确定性拼装，方案A深色系）。

监控告警卡片化（与日报 report_card_builder 同一套组件/风格）：判定/去重仍在
services/*_alerts.py 与 flows/scan_fulfillment_alerts.py，本模块只负责「结构化告警
数据 → 卡片 JSON」，纯函数无副作用，便于单测。

severity → 卡头色（方案A，用户定稿）：
  告急(待发货超时/断货) = carmine 深绯
  预警(库存偏低/扣点率/及时费率) = indigo 靛蓝
  喜报(爆单) = violet 紫

CardKit 已知坑（沿袭日报）：table 不能嵌 collapsible_panel；列宽用百分比；
v2 无 note 标签，footer 用灰字 markdown；options 列做彩色状态标。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from core.config import settings

# 方案A 深色系模板色
TEMPLATE_CRITICAL = "carmine"  # 告急：待发货超时、断货
TEMPLATE_WARNING = "indigo"    # 预警：库存偏低、费率异常
TEMPLATE_GOOD = "violet"       # 喜报：爆单


def _md(content: str) -> dict:
    return {"tag": "markdown", "content": content}


def _hr() -> dict:
    return {"tag": "hr"}


def _kpi_columns(cells: list[tuple[str, str]]) -> dict:
    """一行小 KPI 分栏：(label, value) 列表。"""
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


def _board_button(url: str, text: str) -> Optional[dict]:
    if not url:
        return None
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": text},
        "type": "primary", "width": "fill",
        "icon": {"tag": "standard_icon", "token": "chart-line_outlined"},
        "behaviors": [{"type": "open_url", "default_url": url}],
    }


def _footer(threshold_note: str = "") -> dict:
    now = datetime.now(ZoneInfo(settings.alert_quiet_tz))
    txt = f"🕐 巡检 {now.month}/{now.day} {now.hour:02d}:{now.minute:02d} (CST)"
    if threshold_note:
        txt += f" · {threshold_note}"
    return _md(f"<font color='grey'>{txt}</font>")


def _card(template: str, title: str, subtitle: str, elements: list[dict]) -> dict:
    header = {
        "template": template,
        "title": {"tag": "plain_text", "content": title},
    }
    if subtitle:
        header["subtitle"] = {"tag": "plain_text", "content": subtitle}
    return {
        "schema": "2.0",
        "config": {"wide_screen_mode": True},
        "header": header,
        "body": {"elements": elements},
    }


def _abbr(n: Optional[float]) -> str:
    if n is None:
        return "—"
    a = abs(n)
    if a >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if a >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if a >= 1_000:
        return f"{n / 1_000:.1f}K"
    return f"{n:.0f}"


# ── 1) 待发货超时（告急/carmine）─────────────────────────────────────────


def build_fulfillment_card(
    *, scope_display: str, overdue: int, critical: int, total: int, delta: int,
    prev_reported: int, by_shop: list[dict], shop_names: dict[str, str],
    board_url: str = "",
) -> dict:
    delta_txt = (
        f"，较上次提醒 <font color='red'>+{delta}</font>" if prev_reported > 0 and delta > 0 else ""
    )
    lead = (f"<font color='red'>**{overdue} 单已超发货时限**</font>{delta_txt}，"
            "请尽快安排发货/揽收，避免平台超时自动取消。")
    elements: list[dict] = [
        _md(lead),
        _kpi_columns([("已超时", f"{overdue} 单"), ("临界(24h内)", f"{critical} 单"),
                      ("待发货总量", f"{total} 单")]),
    ]
    top = sorted(
        (s for s in by_shop if int(s.get("overdue", 0)) > 0),
        key=lambda s: int(s.get("overdue", 0)), reverse=True,
    )[:5]
    if top:
        elements.append(_hr())
        rows = []
        for s in top:
            sid = str(s.get("shop_id") or "")
            rows.append({
                "shop": shop_names.get(sid, sid or "未知店铺"),
                "overdue": f"{int(s['overdue'])} 单",
                "status": [{"text": "超时", "color": "red"}],
            })
        elements.append(_table(
            [
                {"name": "shop", "display_name": "店铺", "data_type": "text",
                 "width": "50%", "horizontal_align": "left"},
                {"name": "overdue", "display_name": "超时", "data_type": "text",
                 "width": "26%", "horizontal_align": "right"},
                {"name": "status", "display_name": "状态", "data_type": "options",
                 "width": "24%", "horizontal_align": "center"},
            ],
            rows,
        ))
    elements.append(_hr())
    btn = _board_button(board_url, "去看板处理")
    if btn:
        elements.append(btn)
    elements.append(_footer("发货截止前预警，超时按平台 SLA 判定"))
    return _card(TEMPLATE_CRITICAL, "🚨 待发货超时告警", scope_display, elements)


# ── 2) 低库存/断货（断货=告急 carmine，否则预警 indigo）────────────────────

_LEVEL_LABEL = {"stockout": "断货", "critical": "告急", "warning": "偏低"}
_LEVEL_COLOR = {"断货": "red", "告急": "orange", "偏低": "yellow"}


def build_stock_card(
    *, scope_display: str, stockout: int, critical: int, warning: int,
    items: list[dict], new_skus: list[str], board_url: str = "",
    critical_days: Optional[int] = None,
) -> dict:
    parts = []
    if stockout:
        parts.append(f"<font color='red'>**{stockout} 个 SKU 已断货**</font>")
    if critical:
        parts.append(f"<font color='orange'>**{critical} 个告急**</font>")
    if warning:
        parts.append(f"{warning} 个偏低")
    lead = " · ".join(parts) + "，按当前销速建议尽快补货。"
    elements: list[dict] = [_md(lead)]

    new_set = set(new_skus)
    # 新进风险的排前面，同级按可售天升序；最多 10 行防过长
    def _sort_key(x: dict):
        d = x.get("days_of_cover")
        return (0 if x.get("sku_id") in new_set else 1, d if d is not None else 9e9)

    show = sorted(items, key=_sort_key)[:10]
    if show:
        elements.append(_hr())
        rows = []
        for x in show:
            level = _LEVEL_LABEL.get(x.get("bucket") or "", x.get("bucket") or "?")
            days = x.get("days_of_cover")
            name = x.get("product_name") or x.get("sku_name") or x.get("sku_id") or "?"
            if x.get("sku_id") in new_set:
                name = f"🆕 {name}"
            rows.append({
                "name": name,
                "stock": str(int(x.get("available_stock") or 0)),
                "days": f"{days:.1f}" if days is not None else "—",
                "level": [{"text": level, "color": _LEVEL_COLOR.get(level, "grey")}],
            })
        elements.append(_table(
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
            rows,
        ))
        omitted = len(items) - len(show)
        if omitted > 0:
            elements.append(_md(f"<font color='grey'>另有 {omitted} 个风险 SKU 未列出，见看板库存卡。</font>"))
    elements.append(_hr())
    btn = _board_button(board_url, "查看库存明细")
    if btn:
        elements.append(btn)
    note = "阈值可在设置页调整"
    if critical_days is not None:
        note = f"告急 <{critical_days} 天 · " + note
    elements.append(_footer(note))
    template = TEMPLATE_CRITICAL if stockout else TEMPLATE_WARNING
    return _card(template, "📦 库存风险预警", scope_display, elements)


# ── 3/4) 扣点率 / 及时费率（预警 indigo）────────────────────────────────


def build_fee_rate_card(
    *, scope_display: str, realtime: bool, currency: str,
    eval_rate: float, baseline_rate: float, abs_change: float,
    eval_gmv: float, eval_window_label: str, baseline_window_label: str,
    board_url: str = "", evidence: Optional[dict] = None,
    policy_references: Optional[list[dict]] = None,
) -> dict:
    kind = "及时费率（未结算预估）" if realtime else "平台扣点率（已结算）"
    lead = (f"{kind}异常升高：<font color='red'>**{eval_rate:.2%}**</font>"
            f"（基准 {baseline_rate:.2%}，<font color='red'>+{abs_change * 100:.1f} 个百分点</font>）。"
            + ("平台可能已调整佣金/费率，请核对费用政策。" if realtime
               else "请核对结算单费用构成是否有新增扣项。"))
    elements: list[dict] = [
        _md(lead),
        _kpi_columns([
            ("评估窗口", eval_window_label),
            ("窗口费率", f"{eval_rate:.2%}"),
            ("基准费率", f"{baseline_rate:.2%}"),
        ]),
        _md(f"<font color='grey'>窗口 GMV {currency} {_abbr(eval_gmv)} · 基准窗口 {baseline_window_label}</font>"),
        _hr(),
    ]
    evidence_items = list((evidence or {}).get("fee_items") or [])[:3]
    if evidence_items:
        lines = ["**检测依据**"]
        for item in evidence_items:
            name = item.get("name") or item.get("key") or "费用项"
            if item.get("delta") is not None and item.get("from") is not None:
                lines.append(
                    f"• {name} +{float(item['delta']):.2%}"
                    f"（{float(item['from']):.2%}→{float(item['to']):.2%}）"
                )
            else:
                lines.append(f"• {name} 当前占 GMV {float(item.get('to') or 0):.2%}")
        elements.append(_md("\n".join(lines)))
        elements.append(_hr())

    refs = list(policy_references or [])[:2]
    if policy_references is not None:
        lines = ["**官方参考资料**"]
        if refs:
            for ref in refs:
                title = ref.get("title") or "TikTok 官方资料"
                url = ref.get("url") or ""
                source = ref.get("source") or "TikTok"
                if url:
                    lines.append(f"• [{title}]({url})")
                else:
                    lines.append(f"• {title}")
                lines.append(f"<font color='grey'>  来源：{source}</font>")
        else:
            lines.append("<font color='grey'>未匹配到近期高相关 TikTok 官方公开资料。</font>")
        elements.append(_md("\n".join(lines)))
        elements.append(_hr())

    btn = _board_button(board_url, "看费率监控")
    if btn:
        elements.append(btn)
    elements.append(_footer("同窗口只报一次"))
    title = "📈 及时费率告警" if realtime else "📊 扣点率异常告警"
    return _card(TEMPLATE_WARNING, title, scope_display, elements)


# ── 5) 爆单（喜报 violet）───────────────────────────────────────────────


def build_hotsell_card(
    *, scope_display: str, date_label: str, threshold: int,
    new_products: list[dict], board_url: str = "",
) -> dict:
    n = len(new_products)
    total_units = sum(int(p.get("units") or 0) for p in new_products)
    lead = (f"今日 <font color='carmine'>**{n} 个商品**</font>销量破爆单阈值"
            f"（{threshold} 件/天），关注库存与投放追量。")
    elements: list[dict] = [
        _md(lead),
        _kpi_columns([("爆单商品", f"{n} 个"), ("合计销量", f"{total_units} 件"),
                      ("阈值", f"{threshold} 件/天")]),
        _hr(),
    ]
    lines = []
    for i, p in enumerate(new_products[:8], 1):
        star = " 🌟新品爆发" if p.get("is_new") else ""
        name = p.get("name") or p.get("product_id") or "?"
        lines.append(f"**{i}. {name}**{star}\n"
                     f"　<font color='grey'>今日</font> **{int(p.get('units') or 0)} 件**")
    if lines:
        elements.append(_md("\n".join(lines)))
    if n > 8:
        elements.append(_md(f"<font color='grey'>另有 {n - 8} 个爆单商品未列出。</font>"))
    elements.append(_hr())
    btn = _board_button(board_url, "看爆款详情")
    if btn:
        elements.append(btn)
    elements.append(_footer(f"{date_label} · 当日已报商品不重复提醒 · 阈值可在设置页调整"))
    return _card(TEMPLATE_GOOD, "🔥 爆单提醒", scope_display, elements)
