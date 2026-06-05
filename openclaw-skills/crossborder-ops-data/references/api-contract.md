# Data Hub HTTP Contract

Base URL comes from `DATA_HUB_URL`. All endpoints below require:

```text
X-Internal-Token: {{DATA_HUB_TOKEN}}
```

All endpoints are read-only and live under `/api/data`.

## Shared Query Parameters

| Name | Type | Required | Description |
| --- | --- | --- | --- |
| `platform` | string | no | Platform code, for example `tiktok_shop`, `shopee`, `amazon`. |
| `country` | string | no | Country or region code, for example `ID` or `GLOBAL`. |
| `shop_id` | string | no | Shop identifier. |

## GET /api/data/overview

Returns a compact operating overview for the last 7 days plus inventory and open alert summaries.

Query parameters: shared parameters only.

Response shape:

```json
{
  "period": "2026-05-29 ~ 2026-06-05",
  "inventory": {
    "total_sku": 120,
    "total_stock": 5600,
    "low_stock_count": 8
  },
  "profit": {
    "gmv": 12888.88,
    "gross_profit": 2888.88,
    "order_count": 320,
    "units_sold": 410
  },
  "alerts": {
    "total": 3,
    "critical": 1,
    "items": []
  }
}
```

## GET /api/data/inventory

Returns SKU inventory rows and low-stock rows.

Additional query parameters:

| Name | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `low_stock_threshold` | integer | no | `10` | Items with `available_stock` below this value are returned in `low_stock_items`. |

Response shape:

```json
{
  "items": [
    {
      "sku_id": "SKU001",
      "product_id": "P001",
      "product_name": "Product name",
      "sku_name": "Black / M",
      "available_stock": 50,
      "reserved_stock": 5,
      "warehouse_id": "WH001"
    }
  ],
  "total": 100,
  "low_stock_items": []
}
```

## GET /api/data/profit/summary

Returns trusted profit aggregates for a date range.

Additional query parameters:

| Name | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `start_date` | date string | no | 7 days before service date | Inclusive start date, `YYYY-MM-DD`. |
| `end_date` | date string | no | service date | Inclusive end date, `YYYY-MM-DD`. |

Response shape:

```json
{
  "start_date": "2026-05-29",
  "end_date": "2026-06-05",
  "gmv": 12888.88,
  "gross_profit": 2888.88,
  "ad_cost": 900.0,
  "order_count": 320,
  "units_sold": 410,
  "profit_margin": 22.41
}
```

`profit_margin` is returned by the API as a percentage value. The Skill may display it as `22.41%`.

## GET /api/data/orders/summary

Returns GMV, order count, units sold and average order value for paid orders within a date window.

Additional query parameters:

| Name | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `start_date` | date string | no | 7 days before service date | Inclusive start date, `YYYY-MM-DD`. |
| `end_date` | date string | no | service date | Inclusive end date, `YYYY-MM-DD`. |

Response shape:

```json
{
  "start_date": "2026-05-29",
  "end_date": "2026-06-05",
  "gmv": 1280000.0,
  "order_count": 320,
  "units_sold": 410,
  "avg_order_value": 4000.0
}
```

**Methodology (must be disclosed to users):**

- Scope: **paid orders only** (`paid_time` is not null and falls within the date window). Orders with status `UNPAID` or `CANCELLED` are excluded.
- GMV: sum of `payment.total_amount` across qualifying orders — the amount the buyer actually paid (including shipping, tax, after discounts). This is **not** the platform settlement amount.
- `units_sold`: count of `line_item` rows under paid orders. In the TTS 202309 model each line_item represents one sold unit (no `quantity` field).
- `avg_order_value`: `GMV / order_count` (computed server-side).
- Source: TikTok Shop official API (`/order/202309/orders/search`).

## GET /api/data/orders/top-skus

Returns per-SKU sales ranking within paid orders, sorted by units sold descending.

Additional query parameters:

| Name | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `start_date` | date string | no | 7 days before service date | Inclusive start date, `YYYY-MM-DD`. |
| `end_date` | date string | no | service date | Inclusive end date, `YYYY-MM-DD`. |
| `limit` | integer | no | `10` | Maximum number of SKUs to return. |

Response shape:

```json
{
  "items": [
    {
      "sku_id": "SKU001",
      "product_name": "Product A",
      "sku_name": "Black / M",
      "units_sold": 120,
      "gmv": 360000.0
    }
  ],
  "total": 10
}
```

**Methodology (must be disclosed to users):**

- Same paid-order scope as `/orders/summary`.
- Per-SKU GMV: sum of `line_item.sale_price` for that SKU (unit retail price, excluding shipping). This differs from the order-level total amount.
- Ranking: by `units_sold` (line_item count) descending.

## GET /api/data/alerts

Returns currently open business alerts.

Additional query parameters:

| Name | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `limit` | integer | no | `20` | Maximum number of alerts. |

Response shape:

```json
{
  "alerts": [
    {
      "metric_date": "2026-06-05",
      "alert_type": "low_stock",
      "severity": "critical",
      "title": "SKU stock is below threshold",
      "message": "Available stock is lower than the configured threshold.",
      "impact_scope": "shop"
    }
  ],
  "total": 1
}
```

## Error Handling

| Status | Meaning | Skill response guidance |
| --- | --- | --- |
| `401` | Invalid internal token | Explain that Data Hub authorization failed. Do not reveal token values. |
| `503` | Internal token missing or service unavailable | Explain that the data service is not fully configured or temporarily unavailable. |
| `404` | Endpoint not found | Explain that the Skill contract may be out of sync with the Data Hub service. |
| timeout / connection refused | Data Hub unreachable | Ask operator to check local service status and `DATA_HUB_URL`. |
