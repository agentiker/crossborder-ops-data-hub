# Data Hub HTTP Contract

Base URL comes from `DATA_HUB_URL`. All endpoints below require:

```text
X-Internal-Token: {{DATA_HUB_TOKEN}}
```

All endpoints are read-only and live under `/api/data`, **except** `POST /api/data/scope/binding` (the one write — persists a conversation's default scope).

## Shared Query Parameters

| Name | Type | Required | Description |
| --- | --- | --- | --- |
| `scope_id` | string | no | Named business-scope key (e.g. `tts-id-all`). Resolved server-side to a set of shops; takes priority over `platform`/`country`/`shop_id`/`shop_ids`. |
| `shop_ids` | string | no | Comma-separated shop ids (e.g. `7494...,7495...`). Used when no `scope_id` is given. With `scope_id`, narrows the query *within* that scope (out-of-scope ids return 400). |
| `platform` | string | no | Platform code, e.g. `tiktok_shop`, `shopee`. |
| `country` | string | no | Country/region code, e.g. `ID`, `GLOBAL`. |
| `shop_id` | string | no | Single shop id. Same precedence/narrowing rules as `shop_ids`. |

**Scope semantics (must be respected by callers):**

- Pass `scope_id` whenever a conversation has a default scope; the server expands it to a shop set and filters every relevant table by `shop_id IN (...)`.
- `scope_id` + explicit `shop_id`/`shop_ids` → **intersection** (narrowing inside the scope). Out-of-scope ids → 400 `ScopeError`, never silently passed through.
- Unauthorized shop ids (not in `platform_tokens`) → 400.
- All real endpoints echo a `scope` string field in the response describing the resolved scope (e.g. `"TikTok Shop / 印尼 / 3 个店铺"`). Use it to declare the query scope in the user-facing answer.

## Endpoint Inventory

| Path | Status | Notes |
| --- | --- | --- |
| `GET /api/data/overview` | live | Inventory snapshot + last-7-day order summary. **No profit / alerts segments**. |
| `GET /api/data/inventory` | live | Inventory rows + low-stock flag. |
| `GET /api/data/products` | live | Product master from products/search. `min_price`/`currency` often null on cross-border shops (price not in skus[]). |
| `GET /api/data/orders/summary` | live | Paid-order GMV/order_count/units_sold/AOV. |
| `GET /api/data/orders/trend` | live | Per-day paid-order GMV/order_count/units_sold; window with no orders is zero-filled. |
| `GET /api/data/orders/top-skus` | live | Top SKUs by units within paid orders. |
| `GET /api/data/scopes` | live | List of configured business scopes (id, name, shop_ids). Used when user asks "what scopes do I have". |
| `GET /api/data/scope/binding` | live | Read a conversation's persisted **default scope** (by `open_id`). Call before a data query that carries no scope words. |
| `POST /api/data/scope/binding` | live (write) | Persist a conversation's default scope. The **only** write endpoint; called when the user switches scope via menu phrase. |
| `GET /api/data/profit/summary` | **503 — planned** | Needs Finance/Ads/cost data sources, not connected. |
| `GET /api/data/alerts` | **503 — planned** | Depends on profit + inventory metrics. |

The 503 endpoints return JSON `{"detail": "..."}` explaining the gap. Do not show fake zero values.

## GET /api/data/overview

Returns inventory snapshot + last-7-day paid-order summary.

Query parameters: shared parameters only.

Response shape:

```json
{
  "period": "2026-05-31 ~ 2026-06-07",
  "scope": "TikTok Shop / 印尼 / 1 个店铺",
  "inventory": {
    "total_sku": 10,
    "total_stock": 218,
    "low_stock_count": 8
  },
  "orders": {
    "gmv": 100888.0,
    "order_count": 1,
    "units_sold": 1,
    "avg_order_value": 100888.0
  }
}
```

`scope` is omitted when the resolved scope is empty (全部范围 default). Treat it as advisory — declare the query scope in the answer based on this field.

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
      "sku_id": "1735920131561719162",
      "product_id": "1735920126693836154",
      "product_name": "MossWood Kasur Spring Bed Ortho O1 30cm",
      "sku_name": "90 x 200 x 30cm",
      "available_stock": 6,
      "reserved_stock": 1,
      "warehouse_id": "7647506023704676112"
    }
  ],
  "total": 10,
  "low_stock_items": [],
  "scope": "TikTok Shop / 印尼 / 1 个店铺"
}
```

## GET /api/data/products

Returns the local product master populated by the inventory sync flow (`products/search` payload). No extra API cost.

Additional query parameters:

| Name | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `status` | string | no | (any) | Filter on product status, e.g. `ACTIVATE`, `SELLER_DEACTIVATED`, `DRAFT`. |
| `limit` | integer | no | `100` | Maximum number of rows; ordered by `source_update_time desc`. |

Response shape:

```json
{
  "items": [
    {
      "product_id": "1735958509732267386",
      "title": "Blous Linen Premium Oversize Atasan Wanita",
      "status": "ACTIVATE",
      "sales_regions": ["ID"],
      "sku_count": 1,
      "min_price": null,
      "currency": null
    }
  ],
  "total": 1,
  "scope": "TikTok Shop / 印尼 / 1 个店铺"
}
```

**Caveats:**

- `min_price`/`currency` is often `null` on cross-border TikTok shops because `products/search.skus[].price` is not always returned. This is **expected**, not a data gap on our side.
- Category, brand, full attributes and per-region listing details require the per-id `GET /product/{id}` call, which is **not** synced yet.

## GET /api/data/orders/summary

Returns GMV, order count, units sold and average order value for paid orders within a date window.

Additional query parameters:

| Name | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `period` | string | no | — | Relative window resolved server-side in Indonesia time (UTC+7), week starts Monday. One of `today` / `yesterday` / `this_week` / `last_week` / `last_7d` / `last_30d` / `this_month`. **Prefer this for relative time; do not compute dates client-side.** Mutually exclusive with `start_date`/`end_date` — explicit dates win. Invalid value → 400. |
| `start_date` | date string | no | 7 days before service date | Inclusive start date, `YYYY-MM-DD`. Only when the user gives explicit dates. |
| `end_date` | date string | no | service date | Inclusive end date, `YYYY-MM-DD`. |

Response shape:

```json
{
  "start_date": "2026-06-01",
  "end_date": "2026-06-07",
  "window_label": "印尼时间 6/1（周一） ~ 6/7（周日），共 7 天",
  "gmv": 100888.0,
  "order_count": 1,
  "units_sold": 1,
  "avg_order_value": 100888.0,
  "scope": "TikTok Shop / 印尼 / 1 个店铺",
  "caliber": "已付款订单口径（paid_time 非空、排除未付款/已取消，按 paid_time 归日，印尼当地时间 UTC+7）；GMV=订单 total_amount（买家实付，含运费税优惠，非平台结算）；销量=line_item 条数；客单价=GMV/订单数；来源 TikTok /order/202309/orders/search"
}
```

> **`caliber` 字段**：响应自带本端点数据口径文本，agent 在「📐 数据口径」段**直接复述**即可，不必从 skill 散文里背。下面的 Methodology 是同一口径的人读详版。

> **`window_label` 字段**：服务端按印尼业务日算好的人读时间窗口，串首带 **`印尼时间`** 前缀、并含**星期**与**是否含今天**（如 `印尼时间 6/8（周一） ~ 6/9（周二），共 2 天；今天 6/9（周二）`；窗口含今天时才追加"今天…"）。agent 声明时间窗口时**直接复述此串**——这样首行就向客户说明了时区基准（北京 0 点后印尼仍是前一天，避免"今天数据怎么还没动"的误解）。**严禁自己把日期换算成星期、或推断"今天是周几/几号"**（调用方无可靠当前日期，会算错）。`/orders/trend` 与 `/orders/top-skus` 同有此字段。

**Methodology (must be disclosed to users):**

- Scope: **paid orders only** (`paid_time` is not null and falls within the date window). Orders with status `UNPAID` or `CANCELLED` are excluded.
- GMV: sum of `payment.total_amount` across qualifying orders — the amount the buyer actually paid (including shipping, tax, after discounts). This is **not** the platform settlement amount.
- `units_sold`: count of `line_item` rows under paid orders. In the TTS 202309 model each line_item represents one sold unit (no `quantity` field).
- `avg_order_value`: `GMV / order_count` (computed server-side).
- Time grouping: by `paid_time`, bucketed to the **Indonesia local day (UTC+7 / WIB)**. `paid_time` is stored UTC-naive; day windows and per-day trend buckets are computed in Asia/Jakarta time so a sale at UTC 23:57 (07:57 next day in Jakarta) counts on the next local day. Inclusive day window.
- Source: TikTok Shop official API (`/order/202309/orders/search`).

## GET /api/data/orders/trend

Returns per-day paid-order GMV/order_count/units_sold over a date window. Days within the window with no paid orders are **zero-filled** to make trend charts continuous.

Additional query parameters:

| Name | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `period` | string | no | — | Same relative-window keys and precedence as `/orders/summary` (`today`…`this_month`, Indonesia time, Monday start). Prefer for "近 7 天/本周/本月" trends; explicit dates win, invalid → 400. |
| `start_date` | date string | no | 6 days before service date | Inclusive start date, `YYYY-MM-DD`. Use shorter window for "近 3 天", longer for "近 7/30 天". |
| `end_date` | date string | no | service date | Inclusive end date. |

Response shape:

```json
{
  "start_date": "2026-06-04",
  "end_date": "2026-06-07",
  "window_label": "印尼时间 6/4（周四） ~ 6/7（周日），共 4 天",
  "points": [
    {"date": "2026-06-04", "gmv": 100888.0, "order_count": 1, "units_sold": 1},
    {"date": "2026-06-05", "gmv": 0.0, "order_count": 0, "units_sold": 0},
    {"date": "2026-06-06", "gmv": 0.0, "order_count": 0, "units_sold": 0},
    {"date": "2026-06-07", "gmv": 0.0, "order_count": 0, "units_sold": 0}
  ],
  "scope": "TikTok Shop / 印尼 / 1 个店铺",
  "caliber": "（同 /orders/summary 的已付款订单口径文本）"
}
```

Same paid-order methodology as `/orders/summary` (响应 `caliber` 字段与 summary 一致，agent 直接复述). Use this endpoint for shop-level GMV trend by passing `scope_id` for a single-shop scope or `shop_id` directly.

## GET /api/data/orders/top-skus

Returns per-SKU sales ranking within paid orders, sorted by units sold descending.

Additional query parameters:

| Name | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `period` | string | no | — | Same relative-window keys and precedence as `/orders/summary` (`today`…`this_month`, Indonesia time, Monday start). Prefer for relative time; explicit dates win, invalid → 400. |
| `start_date` | date string | no | 7 days before service date | Inclusive start date. |
| `end_date` | date string | no | service date | Inclusive end date. |
| `limit` | integer | no | `10` | Maximum number of SKUs to return. |

Response shape:

```json
{
  "items": [
    {
      "sku_id": "1735920131561719162",
      "product_name": "MossWood Kasur Spring Bed Ortho O1 30cm",
      "sku_name": "90 x 200 x 30cm",
      "units_sold": 1,
      "gmv": 99999.0
    }
  ],
  "total": 1,
  "start_date": "2026-06-01",
  "end_date": "2026-06-07",
  "window_label": "印尼时间 6/1（周一） ~ 6/7（周日），共 7 天",
  "scope": "TikTok Shop / 印尼 / 1 个店铺",
  "caliber": "已付款订单口径；单品 GMV=该 SKU 各 line_item 的 sale_price 之和（商品行售价，不含运费）；排序按销量（line_item 条数）降序"
}
```

> **`caliber` 字段**：同上，响应自带口径文本，agent 直接复述。

**Methodology (must be disclosed to users):**

- Same paid-order scope as `/orders/summary`.
- Per-SKU GMV: sum of `line_item.sale_price` for that SKU (unit retail price, excluding shipping). This differs from the order-level total amount.
- Ranking: by `units_sold` (line_item count) descending.

## GET /api/data/scopes

Lists all configured business scopes. No query parameters, no auth beyond the standard header.

Response shape:

```json
{
  "items": [
    {
      "scope_key": "tts-id-all",
      "scope_name": "印尼TikTok全部店",
      "scope_type": "shop_group",
      "platform": "tiktok_shop",
      "country": "ID",
      "shop_ids": ["7494691994496238970"]
    }
  ],
  "total": 1
}
```

Use this when:
- User asks "什么范围可选" / "可用 scope 有哪些" / "切换范围" / "scope"
- You need to confirm a `scope_key` exists before referencing it in a reply

Do not list scopes proactively (no "你也可以查 X / Y / Z" suffix on every answer).

## GET /api/data/scope/binding

Reads the conversation's persisted **default scope** (set by a prior menu switch). Call this before running a data query that carries **no** scope words, to decide whether to pass a `scope_id`.

Query parameters:

| Name | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `open_id` | string | **yes** | — | Feishu user open id, taken from the system-prompt trusted metadata `sender_id` (`ou_xxx`). |
| `channel` | string | no | `feishu` | Channel. |
| `account_id` | string | no | `ecom-app` | Bot account; distinguishes `ecom-app` vs `ecom-app-gtl`. |

Response shape:

```json
{
  "open_id": "ou_abc123",
  "scope_key": "tts-id-all",
  "scope": "TikTok Shop / 印尼 / 1 个店铺",
  "is_set": true
}
```

- `is_set=false` → the user has never switched scope; treat as **全量** (pass no `scope_id`). `scope` is then `未设置默认范围（全部）`.
- `is_set=true` with a non-null `scope_key` → pass that `scope_key` as `scope_id` to the data tools.
- `is_set=true` with `scope_key=null` → the user explicitly switched to **全部**; query 全量 (pass no `scope_id`). `scope` is `全部范围`.

## POST /api/data/scope/binding

Persists the conversation's default scope. Called when the user switches scope via a menu phrase (`印尼` / `全部`). The **only** write endpoint.

Request body (JSON):

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `open_id` | string | **yes** | — | Feishu user open id (`ou_xxx`, from trusted metadata `sender_id`). |
| `scope_key` | string \| null | no | `null` | Named scope to set as default (e.g. `tts-id-all`). Omit / null / empty string → switch to **全量**. |
| `channel` | string | no | `feishu` | Channel. |
| `account_id` | string | no | `ecom-app` | Bot account. |

Response shape: same as `GET /api/data/scope/binding` (`open_id` / `scope_key` / `scope` / `is_set`). Use the returned `scope` string for the "已切换到 **{scope}**" confirmation line.

- A non-null `scope_key` that does not exist or is inactive → **400** `ScopeError` (nothing is written). Under normal config this should not happen; surface a switch-failure message, do not claim success.
- Upsert by `(channel, account_id, open_id)`; switching again overwrites the previous default.

## GET /api/data/profit/summary

**Status: 503 — planned.** Returns JSON `{"detail": "利润功能规划中：需先接入结算(Finance API)、广告费(Ads API)与商品成本录入后开放。"}` and HTTP 503.

Do not call as if it returns data. The Skill must reply that profit features are not yet enabled and name the missing dependencies (settlement / ad spend / product cost).

## GET /api/data/alerts

**Status: 503 — planned.** Returns JSON `{"detail": "告警功能规划中：依赖利润与库存指标，待结算/广告/成本数据接入后开放。"}` and HTTP 503.

## Error Handling

| Status | Meaning | Skill response guidance |
| --- | --- | --- |
| `400` | `ScopeError`: out-of-scope shop id, unauthorized shop, or invalid query | Explain the scope/shop is rejected; do not retry blind. Surface the `detail` to the operator (no token leak). |
| `401` | Invalid internal token | Explain that Data Hub authorization failed. Do not reveal token values. |
| `503` | Endpoint is planned but not yet available (currently `profit/summary` and `alerts`) | Tell user the feature is on the roadmap and what data sources it needs. Do not invent zeros. |
| `404` | Endpoint not found | Explain that the Skill contract may be out of sync with the Data Hub service. |
| timeout / connection refused | Data Hub unreachable | Ask operator to check local service status and `DATA_HUB_URL`. |
| Empty result (HTTP 200 with `total=0` or empty arrays) | Filter matched no data | State "no rows for this filter window" plainly; do not speculate causes. |
