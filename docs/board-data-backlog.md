# 运营看板：演示模块的后端数据管道 backlog

运营看板（`web/routes/board.py` + `frontend/src/pages/BoardPage.tsx`）按 forkStoreClaw 复刻了完整版式。
早期其中若干模块**前端用演示数据先行落地**（`frontend/src/components/board/demo-data.ts`，带「演示数据」琥珀徽章），
后端数据源尚未就绪。本文件登记这些待开发项，作为后续 plan 的输入。看板当前的区块布局见 `docs/board-layout.md`。

> **2026-07-04 更新**：演示 tab 已全部下线（P0 精简）。原「流量趋势 / 转化漏斗」两个 `<DemoBadge/>` 占位
> tab 从「经营概览」删除（详见下方 B1/B2、`docs/board-layout.md` 变更记录）。看板现已**不含任何演示数据模块**，
> `DemoBadge` / `DemoPlaceholder` 组件也已随之移除。本 backlog 转为「未来若要做，数据源怎么接」的规划留存。

数据源就绪后：①后端在 `_collect`（`web/routes/board.py`）补对应字段；②前端新建对应真实卡片/区块吃 `BoardData`。

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
- **现状**：演示 tab 已删（2026-07-04 P0 精简，原 `DEMO_TRAFFIC` + `<DemoPlaceholder/>` 占位）。当前看板无此模块。
- **数据源缺口**：站内流量/埋点。TikTok Shop 侧需 analytics/流量类接口（当前 `docs/tiktok-shop-openapi-index.json` 未确认有逐日 UV/PV）；
  自建站需接入埋点。
- **阻塞点**：无现成流量数据表；需先确定数据来源（平台 API vs 自埋点）。
- **何时重做**：数据源就绪后，作**独立卡**新建（不再走演示 tab 老路），置入区1「按所选日期」。

### B2. 转化漏斗（浏览 → 加购 → 下单 → 支付）
- **现状**：演示 tab 已删（2026-07-04 P0 精简，原 `DEMO_FUNNEL` + `<DemoPlaceholder/>` 占位）。当前看板无此模块。
- **数据源缺口**：同 B1（依赖流量/加购埋点）+ 下单/支付（订单侧已有）。漏斗前两层依赖 B1，后两层可由订单数据派生。
- **阻塞点**：与 B1 同源，建议与 B1 一并规划。

### B3 + B4. 退货 / 退款分析 —— 已真实化（2026-07-03，合并为「退款/取消」tab）
- **决定**：退货、退款两空占位（`DEMO_RETURNS` / `DEMO_REFUNDS`）合并为一个「退款/取消」tab，用真实数据。退货与退款同源（都是取消派生），不再分两 tab。
- **实测调研**：TikTok `return_refund/202603/aftersales/search` 接口打通、授权**已含** `seller.return_refund.basic` + `seller.finance.info`（推翻本文原「缺 Finance scope 105005 需重授权」判断——已授权）。但该店（SasaQueen.id）近 90/365/730 天**平台退货单数 = 0**：印尼 COD 店买家以「取消/拒收」完成售后，不走「签收后申请退货」流程。
- **落地口径**：退款 = 付款后取消（`order_status=CANCELLED` 且 `paid_time` 非空，金额取 `sub_total`、率 = 退款额÷展示GMV），基于现有 orders 表派生——**零新接口/表/授权/flow**。发货前流失（未付款取消）单列、不计退款。见 `services/refund_metrics.py` 与 business-rules §2.4。
- **诚实留白**：OrderHeader 无「取消原因」字段（reason 仅在平台退货接口、该店为 0），故不做「退货原因分布」。
- **何时补平台退货口径**：将来该店真有平台退货量（return_refund total>0），再接 `sync_aftersales` flow + returns 表补「原因分布」等（接口/授权已就绪，届时只差落库）。

---

## 备注
- 文中引用的 `docs/tiktok-shop-openapi-index.json` 体积大、**不入库**，仅本地保留（可由 `scripts/generate_tiktok_api_docs.py` 从 `material/` 重新生成）。
- 演示数据是**确定性常量**（不随「时段/范围」筛选变化），刻意不模拟真实联动，避免被误当真实数据。
- 演示金额轴沿用 fork 的 `$` 符号与量级；真实化时统一改为印尼盾 `Rp`（与看板其余真实区块一致）。
