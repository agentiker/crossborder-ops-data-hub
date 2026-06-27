# plan/18 — plan17 收尾：生产财务数据验证 + 费率异常监控

> 2026-06-27 定方向。本文是 plan17 的收尾执行计划，供新会话无缝接续（上一会话规划完、换会话执行以防上下文满）。
> 关联记忆：[[plan17-webui-ops-requirements]] [[prod-deployment-status]] [[tiktok-unsettled-estimated-fee-api]] [[roi-roas-alert-data-source]] [[plan17-stage5-channel-pie]]
> 来源：客户会议纪要 `~/Downloads/智能纪要：TK小店财务系统需求沟通会议 2026年6月23日.md`。

## 前置状态（已就位）

- 生产机 `prod` 已过审、真实印尼店已授权入库（shop_id=7494172960764429390，token 密文落 account=ecom-app-gtl）。**沙箱阻塞已解除**——上一条 [[plan17-webui-ops-requirements]] 记的"连入沙箱店永不结算"已不成立，现有真实店可复验。
- 业务 timer 全停（过审前停的），基建 timer（backup/anchor/verify）在跑。
- plan17 已上线：阶段0(扣点入库)、3a(预估利润 MVP)、5(渠道饼图)；半成品：阶段1(剩 F)、2(剩 H)。

## 决策（已拍板）

① **数据验证优先**（已完成功能的真正收尾，也是利润/费率准确性前提）；② **手动验证一轮再启 timer**；③ 剩余功能里**先做费率异常监控**。会议费率诉求：监控费率异常、**及时发现平台调费率**（痛点"突然多收两三个点、月底才发现"）、按计费项定位哪项涨、对照真实印尼后台校准、(进阶)异常推 TikTok Shop 大学文章。

---

## Track A — 生产财务数据真实化与验证（重心，在 prod 操作）

> 全程在 prod 手动跑、不先启 timer；每步对照真实印尼 TikTok 后台核对。

**A1. 数据覆盖摸底**：prod 跑探查（各业务表行数/sync_cursors/token 有效期/订单时间跨度）。`~/.local/bin/uv run python -c "..."` 走 `TENANT_BYPASS` 读，或直连 MySQL。

**A2. 按依赖顺序手动跑同步链**（`~/.local/bin/uv run python -m flows.<x>`）：
1. `refresh_tokens`
2. 并行无依赖：`sync_orders` / `sync_inventory` / `sync_fulfillments` / `sync_unsettled_fees` / `sync_ad_spend` / `sync_sku_variants`
3. `aggregate_profit`（依赖订单+unsettled+ad_spend+成本+退货率）
4. `scan_fulfillment_alerts`（dry-run 先验）

**A3. 财务数据正确性复验**（真实店会暴露、沙箱验不了的）：
- **扣点** `fact_finance_transaction`（flows/sync_ad_spend.py → services/order_fee_store.py）：fee 子项符号（正=对卖家扣款）、`PROMOTED_FEE_COLUMNS` 字段名是否匹配 202501 真实返回、currency 取 statement 级。
- **unsettled** `fact_unsettled_fee`（flows/sync_unsettled_fees.py → services/unsettled_fee_store.py）：核对真实 JSON——顶层键 `transactions` vs `unsettled_transactions`、交易 `id`、`fee_tax_breakdown.fee` 广告费子键名、`estimated_*` 顶层键、`order_create_time`、currency 位置。**字段不符只改 `unsettled_fee_store.py` 的 `PROMOTED_*_COLUMNS` 映射常量**（单文件、改动局限）。
- **利润** `fact_profit_daily`（services/profit_aggregation.py）：扣点符号是否致利润倒挂、unsettled/settled 按 order_id 双源去重、成本为 0（未导 CSV 则虚高）、退货率占位 5%、汇率固定 0.00045。对照后台核量级。
- **渠道三分**（services/channel_metrics.py）：若真实店有直播/视频 GMV，验 LIVE/VIDEO/PRODUCT_CARD 比例。

**A4. 补前置配置表**：`product_costs`（`scripts/import_product_costs.py` 导 CSV，否则成本=0 利润虚高）、`return_rate_configs`（按需）、`alert_recipients`（确认费率告警收件人）。

**A5. 验证无误后逐个启用业务 timer**：`systemctl --user enable --now data-sync-*.timer` + `daemon-reload`（不 reload 则 NEXT 空，见 docs/ops-runbook.md）。

---

## Track B — 费率异常监控增强（会议点名优先功能）

现状：结算口径 `_scan_fee_rate`（flows/scan_fulfillment_alerts.py:260）已上线——总费率 vs 历史基准、相对+绝对双阈值、文案已列 top 分项构成、**有结算滞后（正是会议痛点"月底才发现"）**。取数 `services/fee_rate_metrics.py:get_settled_fee_rate`、判定 `services/fee_rate_alerts.py:build_decision`、去重 `get/upsert_fee_rate_alert_state`。

**B1. 及时费率告警（unsettled 预估口径）— 核心**
平台一调佣、unsettled 预估费率立即变 → 结算前即可告警。对称复用结算版三件套：
- `services/fee_rate_metrics.py` 加 `get_unsettled_fee_rate(...)`：仿 `get_settled_fee_rate`，从 `FactUnsettledFee`（与 FactFinanceTransaction 同构）取预估费率，**无滞后**，返回同结构 `{currency:{gmv,total_fee,rate,components}}`。
- `flows/scan_fulfillment_alerts.py` 加 `_scan_unsettled_fee_rate`：eval=最近 N 天 unsettled；baseline=`get_settled_fee_rate` 历史已结算费率（真实费率作稳基准，检测"政策刚变、尚未结算"）。
- 复用 `fee_rate_alerts.build_decision`；新 `ALERT_TYPE="fee_rate_anomaly_realtime"` + 独立去重状态。
- 文案标注"预估口径/反映最新费率政策"（去掉结算版"已剔除未结算"注脚）。
- `core/config.py` 加 realtime 阈值参数（或复用现有 `fee_rate_alert_rel_pct/abs_pct/min_gmv`）。
- 接入 `scan_fulfillment_alerts` 主循环作为第 5 条规则。

**B2. 分项费率异常判定（可选增强）**
会议"按计费项定位哪项涨"。现 `build_decision` 只判总费率、分项仅文案展示。增强：对 components 各项费率(component/gmv) 也 vs 基准判定，命中点名（如交易手续费 +2pct）。改 `services/fee_rate_alerts.py`。判断价值后再做。

**B3. 异常推 TikTok Shop 大学文章（后续，本轮不做）**
openclaw/AI 能力（告警→web 搜 TikTok Shop 大学费率规则文章→附告警）。需求清晰后单独规划。

---

## 测试 / 部署 / 验证

- **测试**：B1 仿 `tests/test_fee_rate_alerts.py` 加 unsettled 版单测（离线 SQLite 构造 `FactUnsettledFee`+`OrderHeader`）；全量 `uv run pytest -q` 保持绿（现 ~406 测试，全离线 monkeypatch/SQLite）。Track A 是真实数据人工复验。
- **部署**：B 代码走 `./deploy/deploy.sh --pull --restart-web`（B 无前端改动）；A 的 timer 见 A5。
- **验证**：A 每 flow 跑完看落库行数+对照后台、利润端点 `GET /api/data/profit/summary` 真实值、看板渠道三分；B1 unsettled 数据在后 dry-run 跑 `scan_fulfillment_alerts` 看及时费率判定、构造突变验告警+去重。
- 收尾更新 memory（[[plan17-webui-ops-requirements]] 标进度）。

## 本轮不做（边界）
F 补货审核页 / H 新品曲线 / 成本录入 UI / 3b 退货真实采集 / 汇率易宝实装 / settled 利润回填 / 阶段4 马帮（阻塞待开通）。会议提的汇率对比、退货率 20-30 天口径、广告费准确性测试属利润增强，待费率监控落地后另排。

---

## 起手（新会话从这里开始）

1. 切到最新 main（`git checkout main && git pull`），确认在 prod 还是本地——Track A 在 prod 操作，先 `ssh prod` 或确认 [[local-lan-hp-egress]] 是否本地可直打。
2. 先做 **A1 摸底**（探查真实店数据覆盖），据结果决定 A2 跑哪些 flow。
3. A 验证暴露的字段/符号问题就地修（多为 `unsettled_fee_store.py` 映射常量）。
4. 数据跑通后转 **B1**（及时费率告警），代码开发 + 单测。
5. A 全绿后 A5 逐个启 timer。
