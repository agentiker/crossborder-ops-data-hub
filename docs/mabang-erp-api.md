# 马帮 ERP 开放平台 API 参考（v2）

> 用途：本中台对接马帮 ERP（拿**产品成本/SKU 映射/库存与在途/退货/汇率/利润**）的接口参考。
> 字段为 2026-06-23 从马帮文档站实测抓取整理。**对接前以官方文档站最新版为准**。
> 关联：`plan/17_webui_ops_requirements.md` §3.B、记忆 `plan17-webui-ops-requirements`。
> **全量**：v2 全部 **205 个接口**的请求/返回完整字段已抓存 `docs/mabang-erp-api-full.json`（机器可读，`jq` 可查）；本 md 为人读精选。查某接口：`jq '.[]|select(.api=="<名>")' docs/mabang-erp-api-full.json`。（该 json 体积大、**不入库**，仅本地保留，按下方「抓字段速记」可重抓。）
>
> ⚠️ **版本**：文档站有 v1（旧/精简，仅十余接口）与 **v2（当前完整版，14 分类上百接口）**。两版 **cid 编号互不相同**，本文以 **v2** 为准（链接 `https://gwapi.mabangerp.com/web/#/api/v2`）。v1 仅在文末留索引备查。
> ⚠️ 另有一份「海外仓 API」（旧 `api.mabangerp.com/mabang3/api`，2015 版）**与本需求无关，已作废**。

## 1. 接入信息

| 项 | 值 |
|---|---|
| 业务接口地址 | `POST https://gwapi.mabangerp.com/api/v1`（body 内 `version` 字段指定版本，v2 接口传 `version=2`；以文档站「接口通用参数」为准） |
| 请求方式 | POST，`Content-Type: application/json` |
| 鉴权 | Header `Authorization: <签名>`；签名 = 对 **HTTP Body 全部入参的 JSON 字符串** + 开发者密钥 做 **HMAC-SHA256**，输出二进制再 **十六进制编码** |
| 文档站（人读） | `https://gwapi.mabangerp.com/web/#/api/v2` |
| 文档数据接口（抓字段用，免鉴权） | `GET https://gwapi.mabangerp.com/w/api/detail?version=2&api=<接口名>` 或 `?version=2&cid=<分类id>` → 返回 JSON，含 `requestParams`/`responseJson` |

### 通用请求参数（每个业务请求 Body 必带）

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `api` | String | 是 | 马帮接口名，如 `order-get-order-list-new` |
| `appkey` | String | 是 | 马帮分配给应用的 AppKey |
| `data` | Json | 是 | 该接口的业务请求参数 |
| `timestamp` | String | 是 | 10 位时间戳，GMT+8，服务端允许误差 ≤10 分钟 |
| `version` | String | 是 | 版本号，v2 接口传 `2` |

> 抓字段速记：`curl -s 'https://gwapi.mabangerp.com/w/api/detail?version=2&api=stock-do-search-sku-list-new' | jq '.data.selectedApi.requestParams, .data.responseJson'`

## 2. 三类诉求 → 接口/字段速查（最重要）

| 诉求 | 接口 | 关键字段 |
|---|---|---|
| **产品成本（标准成本，首选）** | `stock-do-search-sku-list-new` 查询库存SKU(新) | `defaultCost` 统一成本价 · `stockCost` 仓库成本价 · `standardPrice` 标准采购价 · `purchasePrice` 最新采购价（**含运费口径待实测确认**） |
| **类目（利润类目拆分）** | 同上 | `parentCategoryName`/`categoryName`/`thirdCategoryName` 一/二/三级目录 |
| **SKU 映射（马帮↔平台）** | `order-get-order-list-new` 的 `orderItem[]` | `stockSku`(马帮) ↔ `platformSku`(平台) 天然同现 + `platformId` |
| **当前库存 + 在途** | `stock-get-stock-quantity` | `availableStockQuantity` 可用库存 · `stockQuantity` 总库存 · `shippingQuantity` 采购在途 · `allotShippingQuantity` 调拨在途 · `processingQuantity` 加工在途 · `waitingQuantity` 未发货 |
| **退货（退货率数据源）** | `order-get-return-order-list-v2` | `stockSku`/`platformSku` · `quantity` 退货数量 · `refundTime` · `returnReasons` · `currencyRate` |
| **汇率** | `sys-get-currency-rate-list` | `currency` · `rate` 汇率值 · `fixedCurrency` 固定汇率 |
| **现成利润表（战略备选）** | `multi-platform-item-details` 多平台利润表商品明细 | `mb_item_total_cost` 成本 · `commission_fee` 佣金 · `tax` 税 · `seller_return_refund` 退款 · `profit_rate` 利润率 · `currency_rate` 人民币汇率 |

## 3. 核心接口详解（v2）

### 3.1 `stock-do-search-sku-list-new` 查询库存SKU(新) ★成本+类目
**请求 `data`（关键）：** `stockSku`/`stockSkuList` · `timeCreatedStart/End` · `updateTimeStart/End` · `showProvider`/`showWarehouse`/`showattributes`/`showMachining`（是否带供应商/仓库/多属性/加工信息）· `maxRows` · `cursor`（游标分页）。

**返回（节选）：**

| 字段 | 说明 |
|---|---|
| `stockSku` / `salesSku` / `originalSku` | 库存SKU / 主SKU / 原厂SKU |
| **`defaultCost`** | **统一成本价** |
| `stockCost` | 仓库成本价 |
| `standardPrice` / `purchasePrice` | 标准采购价 / 最新采购价 |
| `salePrice` / `declareValue` | 售价 / 申报价格 |
| `parentCategoryName`/`categoryName`/`thirdCategoryName` | 一/二/三级商品目录 |
| `stockWarningDays` / `stockWarningQuantity` | 库存警戒天数 / 警戒库存 |

> ⚠️ `defaultCost`「统一成本价」是否含国内头程运费，**接入时实测确认**（若不含，需叠加采购单 `oneExpressMoney` 或采购流水到仓均价）。

### 3.2 `stock-get-stock-quantity` 查询库存SKU库存 ★库存+在途
**请求 `data`：** `stockSkus`（逗号隔开）· `updateTime` · `warehouseName` · `page`。

**返回（按 SKU，仓库维度）：**

| 字段 | 说明 |
|---|---|
| `stockQuantity` | 库存总数 |
| `availableStockQuantity` | **可用库存量**（补货公式直接用） |
| `shippingQuantity` | **采购在途数量** |
| `allotShippingQuantity` | 调拨在途 |
| `processingQuantity` | 加工在途量 |
| `hwc_in_transit_quantity` | 海外仓调拨在途量 |
| `manualInboundQuantity` | 手工入库在途量 |
| `waitingQuantity` | 自营订单未发货数量 |
| `fbaWaitingQuantity` / `transfer_warehouse_quantity` | fba/分仓调拨未发货量 |

### 3.3 `order-get-order-list-new` 新查询订单 ★SKU映射+成本
**请求 `data`（关键）：** `status` · `platformOrderIds`/`salesRecordNumbers` · `shopName`/`shopId` · `paidtimeStart/End` · `updateTimeStart/End` · `expressTimeStart/End` · `canSend` · `cursor`/`maxRows`（游标分页）。

**返回单级（节选，沿用 v1 等价字段）：** `platformOrderId` · `platformId` 来源平台 · `currencyId` 币种 · `orderFee`/`itemTotal` 金额 · `platformFee` 平台费 · `orderCost`/`itemTotalCost` 订单/商品成本 · `shippingCost` 真实运费 · `isReturned` 退货标记。

**返回 `orderItem[]`（SKU 映射所在）：** `stockSku`(马帮) · `platformSku`(平台SKU=seller_sku) · `itemId` 平台itemId · `costPrice` 商品成本价 · `sellPrice` · `quantity` · `specifics` 多属性。

### 3.4 `order-get-return-order-list-v2` 获取退货订单v2 ★退货率
**请求 `data`（关键）：** `platformOrderIds`/`salesRecordNumbers` · `createDateStart/End` · `updateDateStart/End` · `status`(1待处理/2已退款/3已重发/4已完成/5已作废) · `shopIdList` · `cursor`/`maxRows`。

**返回（单+明细，节选）：** `refundsOrderId`/`returnOrderId` 退货单号 · `platformOrderId`/`salesRecordNumber` · `status` · `refundTime` 退款时间 · `currencyId`/`currencyRate` · `isPlatformSettlement` 是否平台结算 · `businessType`(1普通/2本土COD)；明细 `stockSku` · `platformSku` · `quantity` 退货数量 · `sellPrice` · `returnReasons` 退货原因。

### 3.5 `sys-get-currency-rate-list` 查询汇率
无请求参数。返回 `currency` 货币 · `rate` 汇率值 · `fixedCurrency` 固定汇率 · `percent` 折扣汇率。

### 3.6 `multi-platform-item-details` 多平台利润表-商品明细 ★★现成利润（战略备选）
**请求 `data`：** `platformId` · `currencyType` · `reportDate` · `orderId` · `modelSku` · `lastId`/`size`（分页）· `cod`/`isEvaluation`。

**返回（节选，马帮已算好的利润拆分）：**

| 字段 | 说明 |
|---|---|
| `model_sku` / `item_id` | 平台SKU / 平台商品ID |
| `original_price` | 订单原始金额 |
| `mb_item_total_cost` | 商品成本 |
| `commission_fee` | 平台佣金 |
| `tax` / `fee_and_tax_amount` | 税费 / 支出和税费合计 |
| `seller_return_refund` | 退款金额 |
| `return_shipping_label_fee_amount` | 买家支付退货费 |
| `platform_other_fee` / `platform_discount` / `escrow_amount` | 其他平台费 / 平台优惠 / 平台拨款 |
| `shipping_cost_amount` | 卖家运费 |
| `profit_rate` | 利润率/毛利率 |
| `currency_rate` / `usd_rate` | 人民币汇率 / 美元汇率 |

> **战略选择点**：利润(#3)可「自建」（从 TikTok Finance + 成本拼，实时、口径自控）或「直接取马帮这张利润表」（现成、省事，但依赖马帮成本录入齐全 + 同步及时性 + 其 TikTok 口径可信）。建议自建为主、马帮利润表作交叉校验。

## 4. v2 分类总览（cid / 接口名 → 中文）

- **商品 API (16)**：`stock-do-search-sku-list-new` 查询库存SKU(新) · `stock-get-stock-quantity` 库存 · `stock-get-stock-supplier` 关联供应商(`lastPrice`/`lowerPrice`) · `stock-do-search-sales-sku` 查主SKU · `stock-do-add-stock`/`stock-do-change-stock` 增改库存SKU · `stock-do-add-combo-sku`/`stock-do-search-combo-sku` 组合SKU · `stock-query-sku-type` · `stock-change-grid` 改仓位 · `stock-bind-product-link` 绑第三方链接
- **采购 API (19)**：`pur-get-purchase-list` 查采购单 · `pur-do-add-purchase`/`pur-do-change-purchase` 增改采购单 · `pur-in-storage-purchase`/`pur-in-storage-purchase-v2` 采购入库 · `pur-do-add-purchase-refund`/`pur-get-purchase-refund-list` 采购退货 · `pur-do-add-provider`/`pur-do-get-provider`/`provider-productlist` 供应商 · `pur-do-search-purchase-payment` 采购付款单 · `purchase-get-quality-inspection-info` 质检
- **订单 API (18)**：`order-get-order-list-new` 新查订单 · `get-history-order-list` 历史订单 · `order-get-return-order-list-v2`/`order-get-return-order-list` 退货订单 · `order-get-refund-list`/`create-refund-order` 退款 · `get-order-coupon` 订单优惠 · `order-do-create-order`/`order-do-change-order`/`order-do-split-order`/`order-do-deliver-order` 创建/修改/拆分/发货 · `order-do-order-normal`/`order-do-order-abnormal` 异常处理
- **财务 API (21)**：`multi-platform-item-details`/`multi-platform-order-details` 多平台利润表商品/订单明细 · `get-multi-platform-cost-managemen-list` 多平台费用管理 · `fin-search-paymentorder` 查收付款/费用单 · `fin-create-receive-payment-order` 创建收付款单 · `fin-payment-order-approval` 付款审核
- **系统 API (14)**：`sys-get-currency-rate-list`/`sys-do-change-currency-rate` 查/改汇率 · `sys-category-list` 商品目录 · `sys-get-shop-list`/`sys-do-create-shop`/`sys-edit-shop` 店铺 · `sys-get-warehouse-list`/`sys-create-warehouse` 仓库 · `sys-get-employee-list`/`sys-create-employee` 员工 · `report-product-report-list` 商品销量报表【测试中】
- **原始报告 API (28)**：`get-stock-age-analysis`/`report-health-list-new` 库龄 · `get-stock-subtotal-report` 库存分类汇总 · `get-stock-sub-ledger` 库存分类账明细 · `get-storagefee-longlist` 长期仓储费 · `fba-get-settlementsum-list` 结算汇总
- **仓库 API (26)**：`warehouse-get-storage-log-data` 出入库流水 · `warehouse-get-purchase-storage-list-new` 采购入库流水V2 · `warehouse-do-add-storage`/`-out`/`-in` 盘点/手工出入库 · `get-inventory-list-v2` 盘点列表 · `hwc-*-allocation-warehouse-*` 分仓调拨
- **物流 API (13)**：`wl-get-mylogisticschannel` 物流渠道 · `wl-query-track-info` 物流跟踪 · `wl-get-order-logistics-label` 物流标签 · 自定义物流增改查
- **加工 API (46)**：`warehouse-get-process-list` 加工列表 · `warehouse-do-add-process`/`-custom`/`-cancel` 加工单
- **全托管 API (45)**：TEMU 发货/箱唛/条码（`get-temu-*`）
- 其余：**专业版 API (15)**（亚马逊广告/FBA 报告）· **刊登 API (17)** · **海外版 API (23)** · **海外仓物流 API (24)**

## 5. 口径边界 / 接入坑

1. **成本首选 `stock-do-search-sku-list-new.defaultCost`（标准成本，带三级类目）**，比从采购单反推简单。`defaultCost` 是否含国内头程运费**接入实测确认**；不含则叠加采购单 `oneExpressMoney`（见 `pur-get-purchase-list.Item[]`）或采购入库流水到仓均价。
2. **SKU 映射来自订单** `order-get-order-list-new`（`stockSku↔platformSku`）→ 只覆盖出过单的 SKU；纯新品无（新品无销速，对补货影响小）。
3. **筛 TikTok**：订单/利润表用 `platformId` 过滤来源平台，需确认 TikTok 对应取值。
4. **补货在途**直接用 `stock-get-stock-quantity` 的 `availableStockQuantity`（可用库存）与 `shippingQuantity`（采购在途）；多种在途（调拨/加工/海外仓）按需叠加。
5. **退货**用专门接口 `order-get-return-order-list-v2`，不必靠订单 `update_time` 反推。
6. **汇率**马帮自带 `sys-get-currency-rate-list`，与 TikTok 结算单 `exchange_rate` 二选一/交叉校验。
7. **利润表 `multi-platform-item-details`** 是马帮已算好的拆分，可作自建利润的交叉校验源，不建议直接替代自建（口径/及时性依赖马帮）。
8. 鉴权签名 = **HMAC-SHA256(body JSON 字符串, 密钥) → hex**；body 入参类型均为 String；`timestamp` 用 GMT+8 且误差 ≤10 分钟。新接口多用 `cursor`/`maxRows` 游标分页。

## 附：v1 旧版（精简，仅备查）

v1 接口名无前缀（如 `get-order-list`/`get-stock-quantity`/`get-purchase-list`），分类少、商品 API 仅库存查询、无标准成本/退货/利润表接口。**新对接一律用 v2**，v1 仅用于读懂历史/对照。抓 v1 字段：`?api=<名>`（不带 version 或 version=1）。
