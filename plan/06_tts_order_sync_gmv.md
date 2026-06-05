---
status: active
owner: claude
---

# 06 TTS 印尼订单同步与 GMV 测试版

## Context（为什么做这个）

测试版要给客户看可信的经营数据。店铺 GMV 接口只给粗汇总、且会诱导 AI 成为数字来源（违反 AGENTS.md）。
订单接口才是地基：拿到订单明细后，GMV、订单量、客单价、单品销量都能用确定性 SQL 自己算，粒度任意、可追溯。

本期目标：**TTS 印尼（country=ID）订单增量同步 → 落库 → 确定性日聚合 → 只读 HTTP 接口 → openclaw skill**，
跑通"近 N 天 GMV + 单品销量 Top + 订单量"这一条端到端链路。

口径（已与用户确认）：
- GMV/销量口径 = **已付款订单**（排除 `UNPAID` 与 `CANCELLED`，即 `paid_time` 非空）。
- GMV 金额 = 订单 `payment.total_amount`（买家实付，订单级）。
- 单品销量 = 该 SKU 的 line_item 条数（202309 模型中 line_item 无 quantity 字段，**1 条 = 1 件**）。

数据源：`material/go_sdk_extracted/`（官方 Go SDK，响应模型真相来源）+ `material/TikTokShopPostmanCollection/`（请求侧）。
选用 **API 版本 202309**（与现有 client/签名一致，订单接口稳定）。

## 接口（来自官方 SDK）

- 拉列表：`POST /order/202309/orders/search`
  - query：`page_size`（必填，≤100）、`page_token`、`sort_field`、`sort_order`、`shop_cipher`（必填）
  - body：`create_time_ge/lt`、`update_time_ge/lt`、`order_status` 等（Unix 秒）
  - resp：`data.orders[]`、`data.next_page_token`、`data.total_count`
- 详情（本期可不接，列表已含 line_items/payment）：`GET /order/202309/orders`

注意：现有 `_request_with_headers` 已把 `shop_cipher` 之外的公共参数和 body 纳入签名；需确认 `shop_cipher` 也进 query 并参与签名（SDK 中 shop_cipher 在 query）。

## 实现步骤

### 1. ORM 模型（`models/base_models.py`，新增 2 张表）

沿用现有 scoping 列风格（platform/country/shop_id/seller_id/account_id + idempotency_key + raw_response_id）。

- `OrderHeader`（`orders` 表）：订单级
  - 业务键：`order_id`（= 响应 `id`）；`idempotency_key` 唯一
  - 字段：`order_status`、`create_time`/`paid_time`/`update_time`（DateTime，从 Unix 秒转）、
    `currency`、`total_amount`(Numeric 18,4，取 payment.total_amount)、`is_cod`、`buyer_message`、
    `warehouse_id`、`source_updated_at`、`raw_response_id`
  - 唯一约束：`(platform, country, shop_id, order_id)`
- `OrderLineItem`（`order_line_items` 表）：行级（算单品销量/单品 GMV）
  - 业务键：line_item `id`；`idempotency_key` 唯一
  - 字段：`order_id`(外键关联值，不建物理 FK)、`sku_id`、`seller_sku`、`product_id`、`product_name`、`sku_name`、
    `sale_price`(Numeric)、`original_price`(Numeric)、`currency`、`display_status`、`raw_response_id`
  - 唯一约束：`(platform, country, shop_id, line_item_id)`

### 2. Pydantic 校验模型（`platforms/tiktok_shop/schemas.py`，追加）

参照现有 `InventoryItem` 风格，新增 `OrderPayment` / `OrderLineItemSchema` / `OrderSchema`，
字段名对齐 SDK 的 json tag（`id`/`payment.total_amount`/`line_items[]`/`paid_time` 等），全部 Optional 容错。

### 3. scoping 幂等键（`services/scoping.py`，追加）

仿 `build_inventory_key`，新增：
- `build_order_key(...resource=f"order:{order_id}")`
- `build_order_line_key(...resource=f"order_line:{line_item_id}")`

### 4. client 订单方法（`platforms/tiktok_shop/client.py`，追加）

仿 `iter_inventory`，新增：
- `search_orders_page(*, create_time_ge, create_time_lt, page_token, page_size, sort_field)`：
  组装 query（含 `shop_cipher`、`page_size`）+ body（时间窗口），调 `POST /order/202309/orders/search`
- `iter_orders(...)`：基于 `next_page_token` 翻页生成器
- 复用现有 `_request_with_headers`（已处理签名/401 刷新/body 签名）

### 5. 增量同步 flow（新建 `flows/sync_orders.py`）

完全套用 `sync_inventory.py` 的结构（task 拆分 + raw 落库 + upsert + cursor）：
- 读 `get_cursor(resource="orders")` 决定 `create_time_ge`（无则默认近 7 天，给 overlap 缓冲重叠 1h 防漏）
- `fetch_orders` → `validate_orders`(Pydantic) → `save_orders_to_db`
- `save_orders_to_db`：`record_raw_response` → upsert OrderHeader + OrderLineItem → `upsert_cursor`（window_end=本次 create_time_lt）
- 幂等：order_id/line_item_id 命中即更新，重跑同窗口不产生重复
- resource 名：`"orders"`（header）；行级随 header 一并 upsert

### 6. 确定性聚合（新建 `services/order_metrics.py`）

纯 SQL/Python 计算，**不经 AI**：
- `get_gmv_summary(*, start_date, end_date, platform, country, shop_id) -> dict`
  - 过滤 `paid_time` 在窗口内且非空（已付款口径）
  - 返回：`gmv`(sum total_amount)、`order_count`(distinct order_id)、`units_sold`(line_item 计数)、
    `avg_order_value`(gmv/order_count)
- `get_top_skus(*, start_date, end_date, ..., limit=10) -> list`
  - 按 sku 聚合 line_item：`units_sold`、`gmv`(sum sale_price)、product_name/sku_name

### 7. 只读接口（`web/routes/data.py`，追加 2 个端点）

复用现有 platform/country/shop_id 过滤参数 + 内部 token 鉴权（已挂在 router 上）：
- `GET /api/data/orders/summary`：调 `get_gmv_summary`，返回 GMV/订单量/销量/客单价
- `GET /api/data/orders/top-skus`：调 `get_top_skus`，返回单品销量榜

### 8. openclaw skill

GMV/订单意图路由合并到统一 skill `openclaw-skills/crossborder-ops-data/SKILL.md`，
包含 `/api/data/orders/summary` + `/api/data/orders/top-skus` 的意图路由、口径说明、
数据来源声明。API 契约同步更新 `references/api-contract.md`。

### 9. Prefect 调度（`prefect.yaml`，追加）

新增 `tiktok-order-sync` deployment，entrypoint `flows/sync_orders.py:sync_orders_flow`，interval 3600。

### 10. 测试（`tests/`，新建 `test_order_store.py`）

离线、无网络（mock client 返回 SDK 样例结构）：
- 订单 upsert 幂等：同窗口重跑不重复
- line_item 拆解正确（1 行 1 件）
- `get_gmv_summary` 口径：已付款过滤、total_amount 求和、客单价
- `get_top_skus` 排序与计数

## 复用的现有资产（不重写）

- `services/sync_state.py`：`record_raw_response` / `get_cursor` / `upsert_cursor` 直接用
- `services/scoping.py`：`build_scope_key` / `normalize_scope_value` 模式
- `platforms/tiktok_shop/client.py`：`_request_with_headers`（签名/重试/token）
- `flows/sync_inventory.py`：作为 flow 结构模板
- `web/routes/data.py` + `web/security.py`：鉴权与过滤参数模式
- `ai_tools/operations_read.py`：只读 helper 风格

## 验证（端到端）

1. 单元测试：`API__INTERNAL_TOKEN=test uv run pytest -q`（全部离线通过）
2. 建表：`uv run python -c "from core.db import init_db; init_db()"`
3. 真实拉数（需印尼店铺已授权、有 token）：
   `uv run python -m flows.sync_orders`（或临时脚本调 `sync_orders_flow(country="ID", shop_id=...)`）
   → 检查 `orders` / `order_line_items` / `raw_api_responses` / `sync_cursors` 有数据
4. 起服务：`API__INTERNAL_TOKEN=<token> uv run python main.py --task web`
5. 验接口：
   `curl -H "X-Internal-Token: <token>" "http://127.0.0.1:8000/api/data/orders/summary?country=ID&start_date=2026-05-29&end_date=2026-06-05"`
   `curl -H "X-Internal-Token: <token>" "http://127.0.0.1:8000/api/data/orders/top-skus?country=ID"`
6. 通过后将本文件 status 改为 completed

## 范围外（后续期）

- 订单详情接口、退款/退货（return_refund 模块）
- 财务结算（finance 模块，真实到账金额）→ 才能算真实利润
- 商品/库存补全（已有 inventory 骨架）
- 广告（marketing API，独立授权）
