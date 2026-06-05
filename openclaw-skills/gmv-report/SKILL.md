---
name: gmv-report
description: "查询 TikTok Shop 等平台的 GMV、订单量、客单价与单品销量榜等只读经营数据；通过本机 Data Hub HTTP API 获取结果。"
version: 1.0.0
user-invocable: true
metadata:
  openclaw:
    requires:
      env:
        - DATA_HUB_URL
        - DATA_HUB_TOKEN
      tools:
        - http_get
---

# GMV 经营报告

## 1. 触发时机

当用户询问销售业绩相关问题时触发，例如：
- "最近 GMV 多少"、"这周卖了多少钱"、"销售额怎么样"
- "订单量"、"客单价"、"卖了多少单"
- "哪个商品卖得最好"、"爆款"、"单品销量排行"

不应触发：库存查询（用 inventory-check）、利润/ROI/退款率分析（当前数据接口暂不支持，需如实说明）、任何写操作。

## 2. 前置检查

- 确认环境变量 `DATA_HUB_URL`（默认 `http://127.0.0.1:8000`）与 `DATA_HUB_TOKEN` 存在。
- 缺失时向用户说明"Data Hub 未配置"，不要伪造数据。

## 3. 请求规则

- 只允许 `GET /api/data/*`，必须携带请求头 `X-Internal-Token: {{DATA_HUB_TOKEN}}`。
- 禁止把 token 写入回答或日志。

## 4. 意图路由

### GMV / 订单量 / 客单价
```
URL: {{DATA_HUB_URL}}/api/data/orders/summary
方法: GET
请求头:
  - X-Internal-Token: {{DATA_HUB_TOKEN}}
参数:
  - start_date: 开始日期 YYYY-MM-DD（可选，默认最近7天）
  - end_date: 结束日期 YYYY-MM-DD（可选，默认今天）
  - platform: 平台标识，如 tiktok_shop / shopee（可选）
  - country: 国家/地区，如 ID / GLOBAL（可选）
  - shop_id: 店铺ID（可选）
```
响应字段：`gmv`、`order_count`、`units_sold`、`avg_order_value`、`start_date`、`end_date`。

### 单品销量榜
```
URL: {{DATA_HUB_URL}}/api/data/orders/top-skus
方法: GET
请求头:
  - X-Internal-Token: {{DATA_HUB_TOKEN}}
参数: 同上，另加
  - limit: 返回数量，默认10（可选）
```
响应字段：`items[]`（`sku_id`、`product_name`、`sku_name`、`units_sold`、`gmv`）、`total`。

用户同时问业绩和爆款时，可组合调用两个 endpoint。

## 5. 结果解释

- 一切以接口返回值为准。GMV/销量口径为**已付款订单**（接口已按 `paid_time` 过滤）；不得自行重算或改写。
- `gmv` 为买家实付总额汇总，`units_sold` 为售出件数，`avg_order_value` = GMV/订单数（接口已算好）。
- 货币单位以店铺所在地为准（印尼为 IDR），如不确定可提示用户。

## 6. 分析输出

不要只复述 JSON。组织成运营语言：
1. **事实摘要**：周期 + GMV + 订单量 + 客单价。
2. **单品亮点**：用表格列出 Top SKU（商品名、销量、GMV）。
3. **建议动作**：基于销量分布给出可执行建议（如补货爆款、关注低动销）。
4. **置信边界**：说明口径（已付款订单、下单 GMV 非真实到账），利润类问题需说明暂不支持。

示例表格：
| 商品 | 销量 | GMV |
|------|------|-----|
| xxx | 120 | 3,600,000 |

## 7. 异常处理

- `401`：内部令牌无效 → "Data Hub 鉴权失败，请检查 DATA_HUB_TOKEN"。
- `503`：服务端未配置令牌 → "Data Hub 未配置内部令牌"。
- 连接失败：提示 Data Hub 服务可能未启动。
- 空数据（`order_count=0`）：说明该周期无已付款订单，建议确认日期范围或同步状态。
- 字段缺失：以已有字段回答，不臆测缺失值。

## 8. 输出与安全约束

- 不输出 `DATA_HUB_TOKEN` 或任何凭据。
- 不调用写接口，不修改任何业务数据。
- 不暴露买家 PII（姓名、电话、地址）；本接口不返回 PII，若未来返回需先脱敏。
- 利润、ROI、退款率等核心公式由服务端产出；本 skill 仅解释接口返回的指标，未返回的指标须如实说明"当前数据接口暂不支持"。
