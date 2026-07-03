# 运营看板：演示模块的后端数据管道 backlog

运营看板（`web/routes/board.py` + `frontend/src/pages/BoardPage.tsx`）按 forkStoreClaw 复刻了完整版式。
其中 5 个模块**前端已用演示数据先行落地**（`frontend/src/components/board/demo-data.ts`，对应区块带「演示数据」琥珀徽章），
后端数据源尚未就绪。本文件登记这些待开发项，作为后续 plan 的输入。

数据源就绪后：①后端在 `_collect`（`web/routes/board.py`）补对应字段；②前端把该 tab 从 `demo-data.ts`
切到 `BoardData`，并移除该 tab 的 `<DemoBadge/>`。

---

## A. 近期可真实化（已有数据源，优先）

### A1. 下单趋势 · 按平台拆分 —— 已砍 tab（2026-07-03）
- **决定**：从「订单与履约」卡移除「下单趋势」tab（原演示堆叠柱 Shopify/Amazon/TikTok，`DEMO_ORDERS`）。
- **原因**：① 时间维度的下单/销量趋势，顶部大图「订单 / 销量（件）」tab 已用**真实数据**画了，再做一个下单趋势 tab 与之重复；② 该 tab 唯一差异价值是「按 platform 拆分」，但当前**单平台（仅 TikTok Shop）**，多平台堆叠退化为单色一根柱、无拆分意义。两点叠加 → 无独立价值，删之避免重复与空洞占位。
- **何时重做**：将来真正接入第二个平台（如 Shopee）后，可复活「按平台拆分」——那时它相对顶部图有真实增量（多平台构成对比）。落地仍按下方旧方案：`get_orders_trend` 加 `group_by=platform`。
- **旧落地方案（留存备用）**：仿 `services/order_metrics.py` 的 `get_orders_trend` 增加 `group_by=platform` 维度（或新函数 `get_orders_by_platform`），`_collect` 注入 `overview` 兄弟节点；前端吃真实序列。

---

## B. 需新数据源 / 新授权

### B1. 流量趋势（UV / PV / 加购人数）
- **现状**：演示折线+柱，`DEMO_TRAFFIC`。
- **数据源缺口**：站内流量/埋点。TikTok Shop 侧需 analytics/流量类接口（当前 `docs/tiktok-shop-openapi-index.json` 未确认有逐日 UV/PV）；
  自建站需接入埋点。
- **阻塞点**：无现成流量数据表；需先确定数据来源（平台 API vs 自埋点）。

### B2. 转化漏斗（浏览 → 加购 → 下单 → 支付）
- **现状**：演示漏斗图，`DEMO_FUNNEL`。
- **数据源缺口**：同 B1（依赖流量/加购埋点）+ 下单/支付（订单侧已有）。漏斗前两层依赖 B1，后两层可由订单数据派生。
- **阻塞点**：与 B1 同源，建议与 B1 一并规划。

### B3. 退货分析（退货数 / 退货率 / 退货原因分布）
- **现状**：演示双 Y 柱+折线 + 原因环图，`DEMO_RETURNS`。
- **数据源缺口**：退货/售后单 + 退货原因枚举。需接入 TikTok Shop 售后（reverse/return）相关接口并落库（参考 `docs/tiktok-shop-openapi-index.json` 的 reverse 类目）。
- **阻塞点**：尚无退货数据表与同步 flow。

### B4. 退款分析（退款金额 / 退款率，月维度）
- **现状**：演示双折线（金额面积 + 率虚线），`DEMO_REFUNDS`。
- **数据源**：TikTok Shop **Finance API** 结算拆项含退款相关金额（见项目记忆「ROI/ROAS 预警数据源」）。
- **阻塞点**：缺 Finance 授权 scope（code 105005）——需 Partner Center 加 Finance 权限 + 店铺重授权，再写 finance sync。与 ROAS 预警共用该授权解锁。

---

## 备注
- 文中引用的 `docs/tiktok-shop-openapi-index.json` 体积大、**不入库**，仅本地保留（可由 `scripts/generate_tiktok_api_docs.py` 从 `material/` 重新生成）。
- 演示数据是**确定性常量**（不随「时段/范围」筛选变化），刻意不模拟真实联动，避免被误当真实数据。
- 演示金额轴沿用 fork 的 `$` 符号与量级；真实化时统一改为印尼盾 `Rp`（与看板其余真实区块一致）。
