# MVP 数据查询 API（供 AI Agent 接入）

本期 MVP 提供**订单 + 库存 + 商品**的只读查询能力，数据来自 TikTok Shop 定时同步后入库的真实数据。
利润、告警两类能力**本期未上线**（缺结算/广告/商品成本数据源），调用会返回 `503` 并附说明。

## 基础信息

- **Base URL**：`http://127.0.0.1:8000`（默认仅监听回环地址；如需对外由部署侧反向代理）
- **鉴权**：所有 `/api/data/*` 请求必须带请求头 `X-Internal-Token: <token>`，
  token 值见服务端 `.env` 的 `API__INTERNAL_TOKEN`。缺失或错误返回 `401`。
- **返回格式**：JSON。金额为数值，日期为 `YYYY-MM-DD` 字符串。
- **通用维度过滤**（多数端点支持，省略即不过滤，为多店铺扩展预留）：
  - `platform`：平台标识，如 `tiktok_shop`
  - `country`：国家/地区，如 `GB` / `GLOBAL`
  - `shop_id`：店铺 ID

```bash
# 约定：下文示例用 $TOKEN 代表内部令牌
export TOKEN="你的 API__INTERNAL_TOKEN"
export BASE="http://127.0.0.1:8000"
```

---

## 1. 库存列表 `GET /api/data/inventory`

返回库存明细，并单独列出低库存商品（便于补货）。一行 = 一个 SKU 在一个仓库的库存。

| 参数 | 必填 | 说明 |
|------|------|------|
| `platform` / `country` / `shop_id` | 否 | 维度过滤 |
| `low_stock_threshold` | 否 | 低库存阈值，默认 `10` |

```bash
curl -s -H "X-Internal-Token: $TOKEN" "$BASE/api/data/inventory?low_stock_threshold=10"
```

```json
{
  "items": [
    {"sku_id": "1729...", "product_id": "1729...", "product_name": "Wireless Earbuds",
     "sku_name": "BLACK", "available_stock": 120, "reserved_stock": 3, "warehouse_id": "7068..."}
  ],
  "total": 8,
  "low_stock_items": [
    {"sku_id": "1730...", "product_id": "1730...", "product_name": "Phone Case",
     "sku_name": "CLEAR", "available_stock": 4, "reserved_stock": 0, "warehouse_id": "7068..."}
  ]
}
```

---

## 2. 商品目录 `GET /api/data/products`

全店商品主数据，支持按状态过滤，用于商品目录、上下架盘点、滞销分析（有商品但近期无订单）。

| 参数 | 必填 | 说明 |
|------|------|------|
| `platform` / `country` / `shop_id` | 否 | 维度过滤 |
| `status` | 否 | 商品状态：`ACTIVATE`（在售）/ `SELLER_DEACTIVATED` / `DRAFT` / `PLATFORM_DEACTIVATED` / `FREEZE` 等 |
| `limit` | 否 | 返回数量上限，默认 `100` |

```bash
curl -s -H "X-Internal-Token: $TOKEN" "$BASE/api/data/products?status=ACTIVATE&limit=50"
```

```json
{
  "items": [
    {"product_id": "1729...", "title": "Wireless Earbuds", "status": "ACTIVATE",
     "sales_regions": ["GB"], "sku_count": 3, "min_price": 9.99, "currency": "GBP"}
  ],
  "total": 3
}
```

---

## 3. 订单概览 `GET /api/data/orders/summary`

已付款订单的 GMV / 订单量 / 销量 / 客单价汇总。默认最近 7 天，按 `paid_time` 归日。

口径：已付款 = 订单 `paid_time` 非空且落在窗口内；GMV = 买家实付 `total_amount` 求和；销量 = 订单行项条数。

| 参数 | 必填 | 说明 |
|------|------|------|
| `start_date` / `end_date` | 否 | `YYYY-MM-DD`，默认近 7 天 |
| `platform` / `country` / `shop_id` | 否 | 维度过滤 |

```bash
curl -s -H "X-Internal-Token: $TOKEN" "$BASE/api/data/orders/summary?start_date=2026-06-01&end_date=2026-06-07"
```

```json
{"start_date": "2026-06-01", "end_date": "2026-06-07",
 "gmv": 1234.56, "order_count": 42, "units_sold": 57, "avg_order_value": 29.39}
```

---

## 4. 销售趋势 `GET /api/data/orders/trend`

已付款订单按**天**的 GMV / 订单量 / 销量序列。窗口内没有订单的日期补 0，返回连续日期，便于直接画折线。

- **近 3 天 / 近 7 天趋势**：传不同 `start_date` 即可。
- **店铺 GMV 趋势**：传 `shop_id` 过滤。

| 参数 | 必填 | 说明 |
|------|------|------|
| `start_date` / `end_date` | 否 | `YYYY-MM-DD`，默认近 7 天 |
| `platform` / `country` / `shop_id` | 否 | 维度过滤 |

```bash
# 近 3 天
curl -s -H "X-Internal-Token: $TOKEN" "$BASE/api/data/orders/trend?start_date=2026-06-05&end_date=2026-06-07"
```

```json
{
  "start_date": "2026-06-05", "end_date": "2026-06-07",
  "points": [
    {"date": "2026-06-05", "gmv": 320.00, "order_count": 11, "units_sold": 14},
    {"date": "2026-06-06", "gmv": 0.0,    "order_count": 0,  "units_sold": 0},
    {"date": "2026-06-07", "gmv": 410.50, "order_count": 13, "units_sold": 18}
  ]
}
```

---

## 5. 爆款 SKU 榜 `GET /api/data/orders/top-skus`

已付款订单内按销量排序的单品榜。默认最近 7 天。

| 参数 | 必填 | 说明 |
|------|------|------|
| `start_date` / `end_date` | 否 | `YYYY-MM-DD`，默认近 7 天 |
| `platform` / `country` / `shop_id` | 否 | 维度过滤 |
| `limit` | 否 | 返回数量，默认 `10` |

```bash
curl -s -H "X-Internal-Token: $TOKEN" "$BASE/api/data/orders/top-skus?limit=5"
```

```json
{
  "items": [
    {"sku_id": "1729...", "product_name": "Wireless Earbuds", "sku_name": "BLACK",
     "units_sold": 23, "gmv": 459.77}
  ],
  "total": 5
}
```

---

## 6. 经营概览 `GET /api/data/overview`

一次拿到**库存快照 + 近 7 天订单概览**的综合视图（本期不含利润/告警）。

| 参数 | 必填 | 说明 |
|------|------|------|
| `platform` / `country` / `shop_id` | 否 | 维度过滤 |

```bash
curl -s -H "X-Internal-Token: $TOKEN" "$BASE/api/data/overview"
```

```json
{
  "period": "2026-05-31 ~ 2026-06-07",
  "inventory": {"total_sku": 8, "total_stock": 845, "low_stock_count": 1},
  "orders": {"gmv": 1234.56, "order_count": 42, "units_sold": 57, "avg_order_value": 29.39}
}
```

---

## 规划中（本期不提供）

以下端点已保留，但因缺数据源会返回 `503` + 说明，**请勿在 MVP 中依赖**：

| 端点 | 状态 | 说明 |
|------|------|------|
| `GET /api/data/profit/summary` | 503 | 利润：需接入结算(Finance API)、广告费(Ads API)、商品成本录入 |
| `GET /api/data/alerts` | 503 | 告警：依赖利润与库存指标，待上述数据接入后开放 |

```bash
curl -s -o /dev/null -w "%{http_code}\n" -H "X-Internal-Token: $TOKEN" "$BASE/api/data/profit/summary"
# 503
```
