# 运营看板：演示模块的后端数据管道 backlog

运营看板（`web/routes/board.py` + `frontend/src/pages/BoardPage.tsx`）按 forkStoreClaw 复刻了完整版式。
其中 5 个模块**前端已用演示数据先行落地**（`frontend/src/components/board/demo-data.ts`，对应区块带「演示数据」琥珀徽章），
后端数据源尚未就绪。本文件登记这些待开发项，作为后续 plan 的输入。

数据源就绪后：①后端在 `_collect`（`web/routes/board.py`）补对应字段；②前端把该 tab 从 `demo-data.ts`
切到 `BoardData`，并移除该 tab 的 `<DemoBadge/>`。

---

## A. 近期可真实化（已有数据源，优先）

### A1. 下单趋势 · 按平台拆分
- **现状**：演示堆叠柱（Shopify / Amazon / TikTok Shop），`DEMO_ORDERS`。
- **数据源**：订单表 `OrderHeader` 已有 `platform` 字段——可按 `platform` 分组、按业务日聚合下单数，出**真实**堆叠柱。
- **落地**：仿 `services/order_metrics.py` 的 `get_orders_trend` 增加 `group_by=platform` 维度（或新函数 `get_orders_by_platform`），
  `_collect` 注入 `overview` 兄弟节点；前端 `ordersStackOption` 改吃真实序列。
- **阻塞点**：无（现有数据即可）。当前单平台（TikTok）数据下，多平台堆叠会退化为单色——需确认是否已接入多平台订单，否则该 tab 暂留演示。

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
