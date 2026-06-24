# plan/17 — WebUI 经营需求实施评估（GMV/利润/Top5/新品/补货）

> 客户 2026-06-22 新需求评估。主要落在 WebUI。本文是实施评估 + API 范围清单（谈价用）+ 分阶段路线。
> 关联记忆：[[report-artifact]] [[weekly-report]] [[roi-roas-alert-data-source]] [[mvp-data-api]] [[proactive-push-daily-report-and-alerts]] [[fulfillment-sla-field-semantics]]

## 0. 决策记录（已拍板 / 待确认）

| 决策点 | 结论 | 来源 |
|---|---|---|
| 产品成本来源 | **CSV 先行 + 同步去要正确马帮 ERP 文档**（两手准备）；马帮 gwapi 已实测可行（成本含运费 defaultCost+oneExpressMoney），谈成后无缝切 ERP 同步 | 用户 2026-06-22 |
| 马帮文档 | **用户去要正确 ERP 接口文档/申请开通**；gwapi 站三类诉求已实测全覆盖（详见 §3.B），海外仓旧 PDF 作废 | 用户 2026-06-22 |
| 投流口径 | **已定 = 只有站内投流，无站外**（运营 2026-06-22 确认）→ 不需要 Marketing API；广告成本/ROAS 分母用站内三项广告费 | 运营 2026-06-22 |
| 补货推送 | **走飞书（复用 openclaw message send）**，收件人 = 运营助理/相关人**配置化**，不硬编码"灯灯" | 用户 2026-06-22（修正原需求） |
| 首阶段（起手式） | **阶段0 扣点全字段入库**（零依赖/1-2天/利润+扣点告警共同前置，无悔投资）；马帮开通后台并行；阶段0 完成后视马帮是否开通定下一锤 | 用户 2026-06-22 拍板 |
| 补货公式 | 按 SKU 拉**过去30天销量**，补齐到 **30天销量×1.5**（=45天备货）；**超级爆品人工标记→×2**；扣可售库存+在途；结果≤0剔除。**系数/超级爆品名单运营可配**，且支持**人工纠偏/干涉**。输出采购单=**款号-颜色-尺码**，推送**指定飞书用户**（配置化） | 用户 2026-06-22 拍板 |
| 利润口径 | **含产品成本（真净利）**：利润=GMV−扣点−广告−产品成本(含运费)−**预估退货** | 用户 2026-06-22 拍板 |
| 退货口径 | **用预估退货率，非真实退货**（真退货滞后会高估当期利润）。成熟订单(下单≥20-30天)算 SKU/类目历史退货率×当期GMV 预扣；`return_refund` 真数据仅回填校准历史率 | 用户 2026-06-22 拍板 |

## 1. 对客户调研报告（~/Downloads）的纠偏

调研方向靠谱，但按现有代码资产，多处把活儿估重/估漏：

1. **小店扣点不用拼联盟接口**：已接通 `seller.finance.info`，结算交易明细 `fee_tax_breakdown.fee` 里就有完整扣点拆项（`platform_commission_amount`/`referral_fee_amount`/`transaction_fee_amount` 等十几项）。现仅提取了 3 项广告费（`flows/sync_ad_spend.py:43-47`），**补提取即可，零新接口零新费用**。
2. **投流大概率不用接 Marketing API**：站内广告费（GMV Max/TAP/联盟）已从结算口径入库。仅当确认有站外 Ads Manager 美金投放才需 Marketing API。
3. **产品成本是唯一真要谈钱的外部 API**（马帮）。可 CSV 先行。
4. **推送是飞书+openclaw 不是企业微信**：`message send` 直投私聊已上线（告警在用），补货/爆单复用。
5. **汇率过度设计**：单机/systemd 规模，每天几次调用，进程内缓存 + 一个免费源足够；TikTok 结算单自带 `exchange_rate` 可直接折 IDR。无需 Redis 二级缓存/熔断/压测阶段。
6. **渠道饼图可行性已上调（2026-06-22 hp 实测调通）**：不走订单接口（TTS 订单无 traffic_source），走 Shop Analytics `GET /analytics/202509/shop_products/{id}/performance`，返回 `sales.breakdowns[].content_type` **实测三分 LIVE/VIDEO/PRODUCT_CARD（=直播/视频/商品卡）**，每桶挂 `sales.gmv.{amount,currency}`/`items_sold`，并有对称的 `traffic.breakdowns`。scope `data.shop_analytics.public.read` **已在现授权内（零新申请）**——它不叫 `seller.analytics`，故此前没找到。仅"达人 vs 自营素材"细分需叠 `creators/bestselling` 二次归因，不够则展示三分即可。⚠️ 字段名以实测为准：是 `sales.breakdowns[].content_type`，非官方文档写的 `gmv_breakdowns[].type`。

## 2. 需求 × 现状 × 差距

| # | 需求 | 现状 | 差距 |
|---|---|---|---|
| 1 | GMV / 单量 | ✅ 已有（日报/周报/data API） | 无 |
| 2 | ROI | ✅ ROAS=GMV/广告费（结算口径） | ROI 口径对齐（是否含成本） |
| 3 | 预估利润 | 🟡 公式+`fact_profit_daily` 表已就绪，端点返 503（`web/routes/data.py:550`） | 缺扣点(补提取,易)/产品成本(CSV→马帮)/退货率(全新) + 汇率换算 |
| 4 | 扣点实时监控+异常报警 | ❌ 无 | 补提取扣点→算费率→接现有 scan flow |
| 5 | Top5 爆款 + 渠道饼图 | 🟡 Top5 表格已有（`services/order_metrics.py:152`）；饼图无 | 渠道归因(精度受限)+前端饼图 |
| 6 | 新品销量曲线 + 爆单提醒(单日50) | 🟡 周报新品卡（静态）；曲线/爆单无 | 按天曲线 + 爆单实时告警(复用 scan+去重缓存) |
| 7 | 补货计划(公式+skill+纠偏+推送) | ❌ 无；但销速 `daily_velocity` 已算(`services/stock_metrics.py`) | 补货公式+采购单+纠偏+飞书推送 |

## 3. API 范围清单（给客户谈价用）

**A. TikTok Shop（已是 ISV，多为补 scope，基本不额外收费）**
- ✅ 已有：`seller.order.info`（订单/GMV/单量/Top/新品/商品/补货销量）、`seller.finance.info`（结算→广告费+扣点）
- ✅ **已申请（2026-06-22）`seller.return_refund.basic`**：售后退货（退货率所需）。接口族 `/return_refund/202309/returns/search`、`cancellations/search`、`refunds/calculate`、`aftersales/search`。待写采集 flow 验证。
- ✅ **已在授权内（2026-06-22 hp 实测调通）`data.shop_analytics.public.read`**：Shop Analytics（渠道饼图 #5），**零新申请**。`/analytics/202509/shop_products/{product_id}/performance` 返回 `sales.breakdowns[].content_type` 实测拆 **LIVE/VIDEO/PRODUCT_CARD**；达人维度 `/analytics/202511/creators/bestselling` 同样已过授权关，仅差补入参（`TimeSlot` 等枚举）。当前 granted_scopes 全集：`seller.product.basic, seller.order.info, seller.finance.info, seller.authorization.info, data.shop_analytics.public.read`。

**B. 马帮 ERP（要谈价的核心，建议只谈这两类接口）**
1. **商品成本查询**：库存SKU成本价（**明确含国内头程预缴运费**）+ 马帮SKU ↔ TikTok seller_sku 映射
2. **库存/在途查询**：当前可用库存 + 在途采购数量（补货扣减项；TikTok 只有现货、无在途）

> ✅ **2026-06-23 实测（gwapi 文档站 v2，数据接口 `/w/api/detail?version=2&api=<名>`）：马帮 ERP **v2** 开放平台不仅覆盖三类诉求，还白送类目/退货/汇率/现成利润表**（原"海外仓 PDF 对不上"早已推翻；注意 **v2 才是完整版**，v1 精简且 cid 编号不同）。**完整字段表见 `docs/mabang-erp-api.md`**。精要：
>
> | 诉求 | v2 接口 | 关键字段 |
> |---|---|---|
> | **成本(标准成本+类目)** | `stock-do-search-sku-list-new` | `defaultCost`统一成本价/`stockCost`仓库成本价/`standardPrice`标准采购价/`purchasePrice`最新采购价 + 三级类目 `parentCategoryName/categoryName/thirdCategoryName`（**推翻原"无标准成本接口"**，不必再从采购单反推；含运费口径待实测） |
> | **SKU 映射** | `order-get-order-list-new` 的 `orderItem[]` | `stockSku`↔`platformSku` 天然同现 + `platformId`/`costPrice` |
> | **库存+在途** | `stock-get-stock-quantity` | `availableStockQuantity`可用库存/`shippingQuantity`采购在途/`allotShippingQuantity`调拨在途/`processingQuantity`加工在途/`waitingQuantity`未发货 |
> | **退货(退货率源)** | `order-get-return-order-list-v2` | `stockSku`/`platformSku`/`quantity`退货数量/`refundTime`/`returnReasons`/`currencyRate`（**专门接口，推翻原"靠订单 update_time 反推"**） |
> | **汇率** | `sys-get-currency-rate-list` | `currency`/`rate`/`fixedCurrency`（**阶段3 汇率有解，马帮自带**） |
> | **现成利润表(战略备选)** | `multi-platform-item-details` | `mb_item_total_cost`成本/`commission_fee`佣金/`tax`税/`seller_return_refund`退款/`profit_rate`利润率/`currency_rate`人民币汇率（马帮已算好，作自建利润的交叉校验源） |
>
> **口径边界（实现时确认）**：①`defaultCost` 是否含国内头程运费待实测，不含则叠加采购单 `oneExpressMoney`/采购入库到仓均价；②`platformSku` 映射来自订单 → 只覆盖出过单 SKU，纯新品无（影响小）；③订单/利润表按 `platformId` 筛 TikTok，需确认取值；④利润优先自建(TikTok Finance 实时)、马帮利润表仅交叉校验。

**C. 汇率（免费）**：IDR↔CNY、USD↔CNY，免费日度源 + 缓存

**D. ~~TikTok Ads Marketing API~~（已作废）**：运营 2026-06-22 确认**只有站内投流**，站内广告费已在结算口径入库，**不需要** Marketing API（省独立 OAuth + advertiser 授权 + 7–10 天审核）

## 4. 分阶段实施（按见效快/依赖少排序）

> **进度看板（2026-06-24 更新）**
>
> | 阶段 | 状态 | 说明 |
> |---|---|---|
> | 0 扣点全字段入库 | ✅ 上线 | merge `7fcfdbb`，hp 部署；`fact_finance_transaction` 表+解析就绪。**数据为空非缺陷**——连入的是沙箱店永不结算，接真实生产店即有值（见 [[roi-roas-alert-data-source]]） |
> | 1 补货计划 | 🟡 大头已上线 | 变体同步(07:05 timer)+补货公式+飞书采购单(07:35 timer) 端到端通；**剩 F 审核页(WebUI 改量/跳过/标爆品)+ 手动触发按钮**；在途 MVP=0（待马帮） |
> | 2 扣点告警+爆单 | 🟡 告警已上线 | 2-A 扣点率告警 + 2-B 爆单提醒 已接 `scan_fulfillment_alerts` 第3/4规则；**剩 H 新品按天销量曲线(echarts 前端)** |
> | 3 汇率+成本+退货率→利润 | ⬜ 未开始 | 链最长：汇率 service → 成本 CSV 录入 → 退货率预估 → profit 端点 503→真数据 + 利润卡 |
> | 4 马帮对接 | ⬜ 阻塞 | 待马帮开通申请；gwapi v2 已实测可行（`docs/mabang-erp-api.md`） |
> | 5 渠道饼图 | ⬜ 未开始 | 零依赖、scope 已就绪，**可前移当快赢** |
>
> 部署/真数据验证已揪修 3 个 bug（印尼语色码属性名/长名截断/漏建变体 timer），详见 [[plan17-webui-ops-requirements]]。

### 阶段 0 — 结算扣点全字段入库（1–2 天，零依赖）✅ 上线（merge 7fcfdbb）
- 扩 `flows/sync_ad_spend.py` 的 `AD_FEE_FIELDS` → 提取全部扣点拆项；新增/扩 fact 表存订单级费用拆项
- 解锁 #4 扣点监控数据源 + #3 利润的扣点项

### 阶段 1 — 补货计划（销速已就绪；在途待马帮开通，先 0/手动）🟡 公式+同步+推送已上线，剩 F 审核页+手动按钮
- ✅ **公式**：普通 SKU 补货量 = 近30天销量×1.5 − 可用库存 − 在途；超级爆品(人工标记)×2；结果 ≤0 剔除（`services/replenishment.py`）
- ✅ **可配置（运营侧）**：系数 1.5 / 超级爆品系数 2 / 超级爆品名单 全部落配置表（`replenishment_config`/`super_hot_products`），运营可改，不硬编码
- ✅ **采购单输出**：款号 − 颜色 − 尺码。新增 SKU 级表 `sku_variants` + 解析 `get_product` 的 `skus[].sales_attributes`（变体同步 flow + 07:05 timer）。**真数据修：印尼语属性名 `Warna`/`ukuran` 子串匹配、长名只截名保色码**
- 🟡 **在途数量**：MVP 马帮未开通前按 0（采购单已提示）；马帮接通后取 `stock-get-stock-quantity`(v2) 的 `availableStockQuantity` 与 `shippingQuantity`（按需叠加 `allotShippingQuantity`/`processingQuantity`）
- ⬜ **skill + 纠偏（F，未做）**：人工改数量/改系数/跳过 SKU/标超级爆品；WebUI 审核页 + 落库"待审核"状态
- ✅ **推送**：飞书（复用 openclaw message send），收件人配置化（`alert_recipients` 表）；07:35 timer 已上线
- 🟡 触发：✅ 定时(07:35) / ⬜ WebUI 手动按钮（未做）

### 阶段 2 — 扣点异常告警 + 新品曲线/爆单提醒（接现有 scan flow）🟡 告警已上线，剩 H 新品曲线
- ✅ #4 扣点告警（2-A）：实际费率 vs 基准历史，异常推飞书（`scan_fulfillment_alerts._scan_fee_rate` 第3规则）；沙箱无结算数据时优雅跳过
- ✅ #6 爆单提醒（2-B）：单日≥阈值（`hotsell_daily_units_threshold`）当日去重推送（`_scan_hotsell` 第4规则）
- ⬜ #6 新品按天销量曲线（H，未做）：前端 echarts（后端 `get_units_by_product` 已就绪，缺前端图表）

### 阶段 3 — 汇率服务 + 产品成本录入 → 利润端点上线 ⬜ 未开始
- 汇率 service：免费源+进程内缓存 / 结算单 `exchange_rate` / **马帮 `sys-get-currency-rate-list`**（马帮开通后优先），按订单支付时间取历史汇率
- 产品成本：CSV/手动录入入口（SKU↔成本，RMB含运费）；**马帮开通后切 `stock-do-search-sku-list-new.defaultCost`（带三级类目，利润可类目拆分）**
- 退货率（**预估口径，非真实**）：用成熟订单(下单≥20-30天、退货窗口基本走完)算 SKU/类目历史退货率 → 乘当期 GMV **预扣**；真数据仅**回填校准**该率，不参与当期扣减；无历史则人工初始值
  - 退货数据源：TTS `return_refund`（已申请 scope）或**马帮 `order-get-return-order-list-v2`**（专门接口，含 stockSku/platformSku/退款时间/退货原因），二选一/交叉校验
- 拉通 `analytics/profit_alerts.py` → `/api/data/profit/summary` 由 503 转真数据 + WebUI 利润卡

### 阶段 4 — 马帮对接（gwapi **v2** 已实测可行，待开通申请；详见 `docs/mabang-erp-api.md`）⬜ 阻塞（待开通）
- 接口(v2)：成本+类目 `stock-do-search-sku-list-new`(defaultCost/三级类目) / SKU映射 `order-get-order-list-new`(stockSku↔platformSku) / 在途+库存 `stock-get-stock-quantity`(availableStockQuantity/shippingQuantity) / 退货 `order-get-return-order-list-v2` / 汇率 `sys-get-currency-rate-list` / 现成利润表 `multi-platform-item-details`(交叉校验)
- 替换 CSV 成本为马帮 `defaultCost` 同步；用订单 platformSku↔stockSku 自动建映射表；在途回补阶段1补货公式
- 取数口径先定：`defaultCost` 是否含运费（不含则叠 `oneExpressMoney`）、按 platformId 筛 TikTok

### 阶段 5 — Top5 渠道饼图（可行性已上调，scope 已就绪可前移）⬜ 未开始（零依赖，快赢候选）
- **不走订单 traffic_source（TTS 无此字段），走 Shop Analytics**：`/analytics/202509/shop_products/{product_id}/performance` 的 `sales.breakdowns[].content_type` 拆 LIVE/VIDEO/PRODUCT_CARD（字段名以实测为准，非文档的 `gmv_breakdowns[].type`）
- 必填入参 `start_date_ge`+`end_date_lt`（YYYY-MM-DD，缺则 400/`36009004`）；签名复用 `platforms/tiktok_shop/client.py` 的 `_request_with_headers`/`_generate_sign`
- ⚠️ client wrapper 在 code≠0 时 raise 吞掉业务 code，接 analytics 时需显式读 `data`/`code`/`message`
- "达人 vs 自营素材"细分：VIDEO 含两者，需叠加 `creators/bestselling` 二次归因；若不够则展示三分(直播/视频/商品卡)
- ~~依赖补 Shop Analytics scope~~ → **scope `data.shop_analytics.public.read` 已在授权内，无前置依赖**

## 5. 风险/待确认（2026-06-22 已查证更新）
- [x] **投流口径**：运营 2026-06-22 确认**只有站内投流**→ 不申请 Marketing API（D 节作废）
- [x] 退货明细：`seller.return_refund.basic` **已申请**（2026-06-22），待写采集 flow 验证
- [x] SKU 颜色/尺码：现未存，需阶段1补 `get_product` 的 `sales_attributes` 同步
- [x] 渠道饼图：Shop Analytics **已实测调通**，scope `data.shop_analytics.public.read` 已在授权内，`sales.breakdowns[].content_type` 拆 LIVE/VIDEO/PRODUCT_CARD（达人/自营细分需叠 creators）
- [x] **马帮（已查证可行）**：gwapi 新文档实测三类诉求全覆盖（成本含运费=采购单 defaultCost+oneExpressMoney、SKU映射=订单 stockSku↔platformSku、在途=库存 shippingQuantity，详见 §3.B）。剩开通申请 + 取数口径细节（最近成本规则/costPrice 是否含运费/platformId 筛选）。阶段4 可落地，CSV 仅作开通前过渡
- [ ] 汇率口径：按支付时间历史汇率 vs 当日汇率（需求写"当日汇率"，但跨天结算偏差需评估）；TikTok 结算单自带 `exchange_rate` 可直接折算
