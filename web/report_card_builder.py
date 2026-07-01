"""经营报告 summary → 飞书 v2 CardKit 卡片 JSON（后端确定性拼装）。

日报/周报共用。数字全部来自 summary（web/routes/data.py:_extract_report_summary，
与 HTML 报告页同源），LLM 只提供 `analysis` 定性分析段，不碰数字 → 零编造。

v2 CardKit（schema="2.0"）组件（已在 hp 飞书实测渲染 OK）：
- header.template 模板色 + 副标题
- column_set 分栏做 KPI
- table 原生表格做爆款/库存
- collapsible_panel 折叠面板收纳库存明细
- button 底部按钮跳可视化报告
- markdown 块内 <font color=...> 彩色文字

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


# ── 环比彩色标记：涨红 / 跌蓝 / 平灰。飞书 markdown <font color> 支持 red/green/grey 等 ──
# 电商语境「涨=好」用红（喜庆），跌用蓝，与看板一致（见 BoardPage 涨红跌蓝）。
def _change_tag(change: Optional[float]) -> str:
    if change is None:
        return ""
    if change > 0:
        return f" <font color='red'>↑{abs(change):.1f}%</font>"
    if change < 0:
        return f" <font color='blue'>↓{abs(change):.1f}%</font>"
    return " <font color='grey'>持平</font>"


def _md(content: str) -> dict:
    return {"tag": "markdown", "content": content}


def _hr() -> dict:
    return {"tag": "hr"}


def _kpi_cell(label: str, value_str: str, kpi: Optional[dict], low_volume: bool,
              is_money: bool = False) -> dict:
    """单个 KPI 列：标签 + 大字值 + 环比（低单量则改示绝对基准，不显噪声%）。"""
    line2 = f"**{value_str}**"
    if kpi:
        change = kpi.get("change")
        baseline = kpi.get("baseline")
        if low_volume and baseline is not None:
            # 低单量护栏：不显环比%（噪声），改示基准绝对值
            base_str = _money(baseline) if is_money else _int(baseline)
            line2 += f"\n<font color='grey'>基准 {base_str}</font>"
        else:
            line2 += _change_tag(change)
    return {
        "tag": "column",
        "width": "weighted",
        "weight": 1,
        "elements": [_md(f"<font color='grey'>{label}</font>\n{line2}")],
    }


def _kpi_columns(cells: list[dict]) -> dict:
    return {"tag": "column_set", "flex_mode": "stretch", "columns": cells}


def _table(columns: list[dict], rows: list[dict]) -> dict:
    return {"tag": "table", "columns": columns, "rows": rows}


def build_report_card(summary: dict, analysis: str, report_url: str,
                      ttl_text: str = "") -> dict:
    """把 summary + LLM 分析段 + 报告链接拼成 v2 CardKit 卡片 JSON。"""
    kind = summary.get("kind") or "daily"
    is_weekly = kind == "weekly"
    kpi = summary.get("kpi") or {}
    low_volume = bool(summary.get("low_volume"))

    # ── header：日报蓝 / 周报靛蓝；标题 + 范围副标 ────────────────────────
    title = summary.get("title") or ("经营周报" if is_weekly else "经营日报")
    template = "indigo" if is_weekly else "blue"
    subtitle_parts = [p for p in (
        summary.get("scope"),
        summary.get("cutoff_label") or summary.get("period_label"),
    ) if p]
    header = {
        "template": template,
        "title": {"tag": "plain_text", "content": f"📊 {title}"},
    }
    if subtitle_parts:
        header["subtitle"] = {"tag": "plain_text", "content": " · ".join(subtitle_parts)}

    elements: list[dict] = []

    # ── AI 分析段（唯一来自 LLM）────────────────────────────────────────
    if analysis and analysis.strip():
        elements.append(_md(analysis.strip()))
        elements.append(_hr())

    # ── 空窗口（周报整周无订单）：直接给空状态，不堆空 KPI ──────────────
    if summary.get("empty_window"):
        elements.append(_md("<font color='grey'>本周期暂无订单数据。</font>"))
    else:
        # ── KPI 区：GMV / 订单 /（周报客单价）/ 广告 / ROAS ──────────────
        change_label = summary.get("change_label")
        elements.append(_md(f"**📈 核心指标**" + (
            f"　<font color='grey'>{change_label}</font>" if change_label else "")))

        row1 = [
            _kpi_cell("GMV", _money((kpi.get("gmv") or {}).get("value")),
                      kpi.get("gmv"), low_volume, is_money=True),
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
                      roas if roas_val else None, False),
        ]
        elements.append(_kpi_columns(row2))

        # ── 爆款 Top（原生 table）─────────────────────────────────────
        top_skus = summary.get("top_skus") or []
        if top_skus:
            elements.append(_hr())
            elements.append(_md("**🔥 爆款商品 Top**"))
            rows = []
            for i, t in enumerate(top_skus[:5], 1):
                name = t.get("name") or "?"
                # 商品名截断，避免撑爆表格列
                if len(name) > 16:
                    name = name[:16] + "…"
                share = t.get("share")
                rows.append({
                    "rank": str(i),
                    "name": name,
                    "units": _int(t.get("units")),
                    "gmv": _money(t.get("gmv")),
                    "share": f"{share:.1f}%" if share is not None else "—",
                })
            elements.append(_table(
                [
                    {"name": "rank", "display_name": "#", "data_type": "text", "width": "40px"},
                    {"name": "name", "display_name": "商品", "data_type": "text"},
                    {"name": "units", "display_name": "销量", "data_type": "text"},
                    {"name": "gmv", "display_name": "GMV", "data_type": "text"},
                    {"name": "share", "display_name": "占比", "data_type": "text"},
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
                parts.append(f"爆款集中度：Top3 占 GMV **{conc['top3_share']:.1f}%**")
            if sell.get("rate") is not None:
                parts.append(
                    f"动销率：**{sell['rate']:.1f}%**"
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

        # ── 库存风险（折叠面板，默认收起）────────────────────────────
        # ⚠️ 飞书 CardKit 限制：table 不能嵌在 collapsible_panel 内（报 200621），
        # 故面板内用 markdown 列表呈现库存明细，不用 table。
        low_stock = summary.get("low_stock") or []
        if low_stock:
            risk = [x for x in low_stock if x.get("level") in ("stockout", "critical", "warning")]
            n_out = sum(1 for x in low_stock if x.get("level") == "stockout")
            n_crit = sum(1 for x in low_stock if x.get("level") == "critical")
            head_title = f"**📦 库存风险**（断货 {n_out} · 告急 {n_crit}）"
            lines = []
            for x in low_stock[:20]:
                name = x.get("name") or "?"
                if len(name) > 16:
                    name = name[:16] + "…"
                days = x.get("days")
                days_str = f"{days:.1f} 天" if days is not None else "无销量"
                level = x.get("level_label") or ""
                lines.append(f"· **{name}** — 库存 {_int(x.get('stock'))} · 可售 {days_str} · {level}")
            elements.append(_hr())
            elements.append({
                "tag": "collapsible_panel",
                "expanded": bool(risk),  # 有真实风险则默认展开，否则收起
                "header": {"title": {"tag": "markdown", "content": head_title}},
                "elements": [_md("\n".join(lines))],
            })

    # ── 底部：查看完整报告按钮 + footer ──────────────────────────────
    elements.append(_hr())
    if report_url:
        elements.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": "📊 查看完整可视化报告"},
            "type": "primary",
            "width": "fill",
            "behaviors": [{"type": "open_url", "default_url": report_url}],
        })
    footer_txt = "🤖 数据来自系统真实统计"
    if ttl_text:
        footer_txt += f" · 链接 {ttl_text}内有效"
    elements.append(_md(f"<font color='grey'>{footer_txt}</font>"))

    return {
        "schema": "2.0",
        "config": {"wide_screen_mode": True},
        "header": header,
        "body": {"elements": elements},
    }
