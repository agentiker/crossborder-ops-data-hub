# Plan 12 — ROI/ROAS 预警 + 授权流程 cipher 修复

> 状态：调研完成，待真实生产店接入后开建。2026-06-13 记录。
> 关联记忆：`roi-roas-alert-data-source`、`proactive-push-daily-report-and-alerts`、`tiktok-api-direct-connect`。

## 背景 / 目标

在已上线的两条监控告警（待发货超时、低库存/断货）之上，加 **ROI/ROAS 预警**：广告**投入↑ 但 ROAS↓** 时主动提醒。指标先做 **ROAS = GMV ÷ 广告花费**（真利润 ROI=毛利÷投入需整个利润栈，仍 503，本期不做）。

## 已验证的关键结论（2026-06-13 实测）

- **广告花费不用接独立 Marketing API**——就在现用 TikTok Shop **Finance API** 的结算交易费用拆项里（现有 Shop 授权域名）：
  - `...fee_tax_breakdown.fee.gmv_max_ad_fee_amount`（GMV Max 广告费）、`fee.tap_shop_ads_commission`（TAP 达人广告佣金）、`affiliate_ads_commission_amount`（联盟广告佣金）。
  - 接口：`GET /finance/202507/orders/unsettled`（sort_field=`order_create_time`）、`GET /finance/202309/statements`（sort_field=`statement_time`）+ `/finance/202501/statements/{id}/statement_transactions`（SKU 级）。
- **授权**：加 `seller.finance.info`（Scope ID 430596）+ 店铺重授权后，finance 接口由 105005 变 `200/Success`，链路全通。
- **阻塞点**：当前在库的店是 **SANDBOX 沙箱店**（`7494691994496238970`），有测试订单但 finance 返回 0 结算/0 交易 → 看不到广告费实际值。**需真实生产店才能验证字段语义（粒度/符号/币种）并开建。**

口径边界：覆盖 GMV Max + 达人/联盟这类**店铺内广告**，不含独立 Ads Manager 外部 campaign；结算视角有滞后（`orders/unsettled` 可拉近时效）；ROAS 为 GMV÷广告费的大盘口径，非广告归因真 ROAS。

---

## Phase 0（可现在做，沙箱即可验证）：授权流程 cipher 修复

**Bug**：`/api/v2/token/get` 不返回 `shop_cipher / shop_id`（实测仅返回 access/refresh token、`granted_scopes`、`open_id`、`seller_name`、`seller_base_region`、`user_type`）。而 `platforms/tiktok_shop/client.py` 的 `authenticate()`：
- 不调 get-authorized-shops 去补 cipher/shop_id/region；
- client 默认 `country=GLOBAL`、`shop=None` → `save_token` 建出 `country=GLOBAL|shop=_` 无 cipher 的占位行，未命中真实店行 → 重授权/新店授权都会留下脏的 GLOBAL 行，且 finance/order 等需 cipher 的接口拿不到 cipher。

**修复**（`platforms/tiktok_shop/client.py`）：
1. 新增 `get_authorized_shops()`：`GET /authorization/202309/shops`（header 带 `x-tts-access-token`，无需 cipher），返回 `data.shops[] = {id, region, name, cipher, code}`。
2. `authenticate()` 换完 token 后调用它，**按每个店**用 `region`→country、`id`→shop_id、`cipher`→shop_cipher 调 `save_token`（一个 seller 多店则各落一行）。不再依赖 token/get 返回 shop 信息，也不再默认 GLOBAL。
3. 兼容已有：`save_token` 对已存在行"cipher 为空保留旧值"的保护保留；现在 cipher 来自 get-shops 一定非空，写入即权威值。
4. 清理一次性脚本：`tmp/fix_finance_token.py`（手工补 cipher 的临时修复）在 Phase 0 落地后可删。

**验证**（沙箱即可）：重新授权一次 → 直接生成 `country=ID|shop=7494...` 行且 cipher=YDZsvo，无 GLOBAL 脏行。`tests/` 加 `authenticate` 对 get-shops 的 mock 单测。

> 价值：这是**接入任何真实新店的前置**，不止为 ROI——当前每次授权都要手工跑 `fix_finance_token.py` 补 cipher。建议优先做。

---

## Phase 1（待真实店）：广告花费 sync + ROAS

前置：真实生产店已授权（带 finance scope）、有结算流水。先重跑 `tmp/finance_ad_probe.py` 看到真实 `gmv_max_ad_fee_amount`，**确认字段语义（按订单/按SKU、正负号、币种、是否已含税）** 再动手。

1. **client**：加 `get_unsettled_transactions(...)`（`/finance/202507/orders/unsettled`）和/或 statements 两接口（带 sort_field/分页/shop_cipher，复用现有签名）。
2. **model**：新表 `fact_ad_spend_daily`（platform/country/shop_id/scope/metric_date/gmv_max_fee/tap_commission/affiliate_commission/total_ad_spend/currency），或直接复用 `fact_profit_daily.ad_cost`（已存在空字段）。按业务日归集（印尼 UTC+7）。
3. **flow**：`flows/sync_ad_spend.py`——拉时间窗交易→按天累加各广告费项→upsert。systemd timer（照 `data-sync-*`）。
4. **service**：`get_roas(scope, period)` = GMV(已有 `get_gmv_summary`) ÷ total_ad_spend；返回 spend / gmv / roas / 环比。
5. **查询端点**：`GET /api/data/roas`（`ops_roas`）+ 加进 `web/app.py` MCP `include_operations` 白名单（勿忘——上次 ops_low_stock 差点漏）。

## Phase 2（待真实店）：ROAS 预警

复用现有 scan flow 框架（`flows/scan_fulfillment_alerts` 的 RECIPIENTS/静默/`send_feishu_message`/去重）：
- `services/roas_alerts.py`：环比判定——本期 vs 上期，**spend 上升 且 ROAS 下降超阈值** → 推送。阈值进 `core/config`（如 `roas_drop_pct`、`spend_rise_pct`、对比窗口）。
- 去重游标：新表或复用模式（记上次已报的 spend/roas 区间，避免每轮复读）。
- 文案：飞书私聊（emoji+粗体，无表格），含范围/期间/spend 环比/ROAS 环比/Top 拖累店或SKU/建议。
- 文案同步纪律：上线后扫 onboarding/SKILL/AGENTS/SOUL，把 ROAS 预警加入"主动推送已上线"，加 `ops_roas` 工具说明。

## 验证（端到端，待真实店）

1. 探测脚本看到真实广告费值，核对字段语义。
2. `sync_ad_spend` dry-run / 单跑，核对按天累加与后台对得上。
3. `ops_roas` 端点 200 + caliber。
4. `roas_alerts` 单测（环比升/降/持平分支）+ scan flow dry-run。
5. hp 真跑一轮，飞书确认收到。

## 备注

- 真利润 ROI（毛利÷投入）依赖完整利润栈（商品成本录入 + 结算费用 + 物流 + 退款），仍 503，另案。
- 外部 TikTok Ads Manager campaign 花费（品牌/引流）需 Marketing API `report/integrated/get`（独立 app/OAuth/advertiser），本计划不含；若客户重投放外部广告再评估。
