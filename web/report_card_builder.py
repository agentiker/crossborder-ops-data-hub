"""经营报告 summary → 飞书 v2 CardKit 卡片 JSON（后端确定性拼装）。

日报/周报共用。数字全部来自 summary（web/routes/data.py:_extract_report_summary，
与 HTML 报告页同源），LLM 只提供 `analysis` 定性分析段，不碰数字 → 零编造。

v2 CardKit（schema="2.0"）组件（已在 hp 飞书实测渲染 OK）：
- header.template 模板色 + 副标题
- column_set 分栏做 KPI
- table 原生表格做爆款/库存（⚠️ table 不能嵌 collapsible_panel，报 200621）
- collapsible_panel 折叠面板收纳新品明细
- button 底部按钮跳可视化报告（applink 端内打开）
- markdown 块内 <font color=...> 彩色文字（red/green/blue/grey/orange 等）

纯函数、无副作用，便于单测（喂假 summary 断言结构）。
"""
from __future__ import annotations

from typing import Optional


# ── 金额缩写（Rp K/M/B，对齐 HTML 报告展示层）─────────────────────────────
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


def _money(n: Optional[float]) -> str:
    return "—" if n is None else "Rp " + _abbr(n)


def _int(n: Optional[float]) -> str:
    if n is None:
        return "—"
    return f"{int(round(n)):,}"


# ── 范围文本简化：卡片头空间紧，长平台名缩写（不动全局 display_text 口径）──────
def _short_scope(scope: Optional[str]) -> Optional[str]:
    if not scope:
        return scope
    return scope.replace("TikTok Shop", "TK Shop")


# ── 环比彩色标记：涨红 / 跌蓝 / 平灰。飞书 markdown <font color> 支持 red/green/grey 等 ──
# 电商语境「涨=好」用红（喜庆），跌用蓝，与看板一致（见 BoardPage 涨红跌蓝）。
def _change_tag(change: Optional[float]) -> str:
    if change is None:
        return ""
    if change > 0:
        return f"<font color='red'>▲{abs(change):.1f}%</font>"
    if change < 0:
        return f"<font color='blue'>▼{abs(change):.1f}%</font>"
    return "<font color='grey'>持平</font>"


def _md(content: str) -> dict:
    return {"tag": "markdown", "content": content}


def _hr() -> dict:
    return {"tag": "hr"}


def _kpi_cell(label: str, value_str: str, kpi: Optional[dict], low_volume: bool,
              is_money: bool = False, accent: str = "") -> dict:
    """单个 KPI 列：小标签 + 大字值(带色) + 同行环比。紧凑两行，省空间。"""
    val = f"**{value_str}**"
    if accent:
        val = f"<font color='{accent}'>{val}</font>"
    tail = ""
    if kpi:
        change = kpi.get("change")
        baseline = kpi.get("baseline")
        if low_volume and baseline is not None:
            base_str = _money(baseline) if is_money else _int(baseline)
            tail = f"　<font color='grey'>基准 {base_str}</font>"
        else:
            t = _change_tag(change)
            tail = f"　{t}" if t else ""
    return {
        "tag": "column",
        "width": "weighted",
        "weight": 1,
        "elements": [_md(f"<font color='grey'>{label}</font>\n{val}{tail}")],
    }


def _kpi_columns(cells: list[dict]) -> dict:
    return {"tag": "column_set", "flex_mode": "stretch",
            "horizontal_spacing": "default", "columns": cells}


def _table(columns: list[dict], rows: list[dict]) -> dict:
    return {"tag": "table", "columns": columns, "rows": rows,
            "row_height": "low", "header_style": {"bold": True, "background_style": "grey"}}


# 库存状态 → options 标签颜色（飞书 table options 支持 color）
_LEVEL_COLOR = {
    "断货": "red", "告急": "orange", "偏低": "yellow",
    "充足": "green", "无销量": "grey",
}


def build_report_card(summary: dict, analysis: str, report_url: str,
                      ttl_text: str = "") -> dict:
    """把 summary + LLM 分析段 + 报告链接拼成 v2 CardKit 卡片 JSON。"""
    kind = summary.get("kind") or "daily"
    is_weekly = kind == "weekly"
    kpi = summary.get("kpi") or {}
    low_volume = bool(summary.get("low_volume"))

    # ── header：日报 turquoise(青绿) / 周报 indigo(靛蓝)。
    # 日期(period_label,形如「印尼时间 6/30（周一）」)提到标题第一行，去掉「印尼时间」
    # 前缀（时区基准全局口径不变，仅卡片标题精简）；副标只留 scope，避免第二行挤到截断。
    title = summary.get("title") or ("经营周报" if is_weekly else "经营日报")
    template = "indigo" if is_weekly else "turquoise"
    period_label = summary.get("period_label")
    title_text = f"🔖 {title}"
    if period_label:
        date_txt = period_label.replace("印尼时间 ", "").replace("印尼时间", "").strip()
        title_text += f" · {date_txt}"
    header = {
        "template": template,
        "title": {"tag": "plain_text", "content": title_text},
    }
    scope = _short_scope(summary.get("scope"))
    if scope:
        header["subtitle"] = {"tag": "plain_text", "content": scope}

    elements: list[dict] = []

    # ── AI 分析段（唯一来自 LLM）────────────────────────────────────────
    if analysis and analysis.strip():
        elements.append(_md(analysis.strip()))
        elements.append(_hr())

    # ── 空窗口（周报整周无订单）：直接给空状态，不堆空 KPI ──────────────
    if summary.get("empty_window"):
        elements.append(_md("<font color='grey'>本周期暂无订单数据。</font>"))
    else:
        # ── KPI 区：紧凑分栏（GMV / 订单 /（周报客单价））+（广告 / ROAS）──
        change_label = summary.get("change_label")
        elements.append(_md("**📈 核心指标**" + (
            f"　<font color='grey'>{change_label}</font>" if change_label else "")))

        row1 = [
            _kpi_cell("GMV", _money((kpi.get("gmv") or {}).get("value")),
                      kpi.get("gmv"), low_volume, is_money=True, accent="carmine"),
            _kpi_cell("订单数", _int((kpi.get("orders") or {}).get("value")),
                      kpi.get("orders"), low_volume),
        ]
        if is_weekly and kpi.get("aov"):
            row1.append(_kpi_cell("客单价", _money((kpi.get("aov") or {}).get("value")),
                                  kpi.get("aov"), low_volume, is_money=True))
        elements.append(_kpi_columns(row1))

        # 广告 / ROAS 一行（ROAS 可能为 None → 未投 GMV Max）
        roas = kpi.get("roas") or {}
        roas_val = roas.get("value")
        row2 = [
            _kpi_cell("广告消耗", _money((kpi.get("ad_spend") or {}).get("value")),
                      kpi.get("ad_spend"), False, is_money=True),
            _kpi_cell("ROAS", (f"{roas_val:.1f}" if roas_val else "—"),
                      roas if roas_val else None, False, accent="green" if roas_val else ""),
        ]
        elements.append(_kpi_columns(row2))

        # ── 爆款 Top（原生 table，列宽百分比自适应）──────────────────────
        top_skus = summary.get("top_skus") or []
        if top_skus:
            elements.append(_hr())
            elements.append(_md("**🔥 爆款商品 Top**"))
            rows = []
            for i, t in enumerate(top_skus[:5], 1):
                name = t.get("name") or "?"  # 全名不截断，飞书点击单元格可查看完整
                share = t.get("share")
                rows.append({
                    "name": f"{i}. {name}",
                    "units": _int(t.get("units")),
                    "gmv": _money(t.get("gmv")),
                    "share": f"{share:.1f}%" if share is not None else "—",
                })
            # 列宽百分比分配：商品最宽，销量/占比窄（点4：销量别占太宽）
            elements.append(_table(
                [
                    {"name": "name", "display_name": "商品", "data_type": "text",
                     "width": "44%", "horizontal_align": "left"},
                    {"name": "units", "display_name": "销量", "data_type": "text",
                     "width": "16%", "horizontal_align": "right"},
                    {"name": "gmv", "display_name": "GMV", "data_type": "text",
                     "width": "24%", "horizontal_align": "right"},
                    {"name": "share", "display_name": "占比", "data_type": "text",
                     "width": "16%", "horizontal_align": "right"},
                ],
                rows,
            ))

        # ── 商品健康度（仅周报）──────────────────────────────────────
        if is_weekly and summary.get("health"):
            h = summary["health"]
            conc = h.get("concentration") or {}
            sell = h.get("sell_through") or {}
            parts = []
            if conc.get("top3_share") is not None:
                parts.append(f"爆款集中度：Top3 占 GMV <font color='carmine'>**{conc['top3_share']:.1f}%**</font>")
            if sell.get("rate") is not None:
                parts.append(
                    f"动销率：<font color='green'>**{sell['rate']:.1f}%**</font>"
                    f"（{sell.get('active_sku', 0)}/{sell.get('total_sku', 0)} SKU 出单）")
            new_prods = h.get("new_products") or []
            if parts or new_prods:
                elements.append(_hr())
                elements.append(_md("**🧬 商品健康度**"))
                if parts:
                    elements.append(_md("\n".join(f"· {p}" for p in parts)))
                if new_prods:
                    lines = [
                        f"· {np.get('title', '?')[:16]} — {_int(np.get('units_sold'))} 件 / {_money(np.get('gmv'))}"
                        for np in new_prods[:5]
                    ]
                    elements.append({
                        "tag": "collapsible_panel",
                        "expanded": False,
                        "header": {"title": {"tag": "markdown",
                                             "content": f"**🆕 本期新品（{len(new_prods)}）**"}},
                        "elements": [_md("\n".join(lines))],
                    })

        # ── 库存风险（顶层 table + options 彩色状态标签）──────────────────
        # ⚠️ 飞书 CardKit：table 不能嵌 collapsible_panel（200621），故用顶层 table 不折叠。
        # 只列风险桶（断货/告急/偏低）+ 断货优先，最多 10 行，避免过长。
        low_stock = summary.get("low_stock") or []
        if low_stock:
            risk_items = [x for x in low_stock
                          if x.get("level") in ("stockout", "critical", "warning")]
            show = (risk_items or low_stock)[:10]
            n_out = sum(1 for x in low_stock if x.get("level") == "stockout")
            n_crit = sum(1 for x in low_stock if x.get("level") == "critical")
            elements.append(_hr())
            head = "**📦 库存风险**"
            if n_out or n_crit:
                head += (f"　<font color='red'>断货 {n_out}</font>"
                         f" · <font color='orange'>告急 {n_crit}</font>")
            elements.append(_md(head))
            rows = []
            for x in show:
                name = x.get("name") or "?"  # 全名不截断，飞书点击可查看完整
                days = x.get("days")
                days_str = f"{days:.1f}" if days is not None else "—"
                level = x.get("level_label") or ""
                rows.append({
                    "name": name,
                    "stock": _int(x.get("stock")),
                    "days": days_str,
                    "level": [{"text": level, "color": _LEVEL_COLOR.get(level, "grey")}],
                })
            elements.append(_table(
                [
                    {"name": "name", "display_name": "商品", "data_type": "text",
                     "width": "44%", "horizontal_align": "left"},
                    {"name": "stock", "display_name": "库存", "data_type": "text",
                     "width": "16%", "horizontal_align": "right"},
                    {"name": "days", "display_name": "可售天", "data_type": "text",
                     "width": "18%", "horizontal_align": "right"},
                    {"name": "level", "display_name": "状态", "data_type": "options",
                     "width": "22%", "horizontal_align": "center"},
                ],
                rows,
            ))

    # ── 底部：查看完整报告按钮 + footer ──────────────────────────────
    elements.append(_hr())
    if report_url:
        elements.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": "查看完整可视化报告"},
            "type": "primary",
            "width": "fill",
            "icon": {"tag": "standard_icon", "token": "chart-line_outlined"},
            "behaviors": [{"type": "open_url", "default_url": report_url}],
        })
    footer_txt = "数据来自系统真实统计"
    if ttl_text:
        footer_txt += f" · 链接 {ttl_text}内有效"
    elements.append(_md(f"<font color='grey'>🚀 {footer_txt}</font>"))

    return {
        "schema": "2.0",
        "config": {"wide_screen_mode": True},
        "header": header,
        "body": {"elements": elements},
    }
