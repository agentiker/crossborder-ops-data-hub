---
status: deferred
owner: codex
depends_on: [07_scope_foundation, 08_feishu_scope_resolution]
trigger: 接入第二个客户时启动
---

# 09 多租户数据隔离（安全专项）

## Context（为什么做这个、以及为什么现在不做）

当前只有一个客户（老板A），其所有店铺（印尼 TikTok 多店 / 印尼虾皮 / 拉美 TikTok 多店）
都属于**同一个租户**。这些平台/国家/店铺差异是 **scope 维度**，由 07/08 处理，**不需要租户概念**。

**多租户隔离要等接入第二个客户（另一家公司）时才做**——那时两个客户的数据必须互相不可见，
`tenant_id` 才成为真正的安全边界。在单客户阶段：
- `tenant_id` 是恒定值，过滤了等于没过滤，**当前零隔离价值**；
- 更危险的是"半拖鞋"做法：scope 表带 `tenant_id`、但事实表（orders/inventory/...）不带，
  会让人**误以为有隔离、实则没有**，接第二个客户时直接串数据。

因此本期被**显式推迟**（`status: deferred`），作为一次性的**安全工程**统一做，不与功能混。

## 触发条件（满足任一即启动）

- 即将接入第二个客户/商家主体。
- 出现"同一部署服务多个互不信任主体"的需求。

## 实现步骤（启动后）

### 1. 全表加 `tenant_id`（数据迁移）

给所有承载业务数据与配置的表加 `tenant_id`（非空，建索引）：
- 事实/主数据：`inventory` / `products` / `orders` / `order_line_items` / `fact_profit_daily` /
  `fact_alerts` / `shops`
- 配置：`business_scopes` / `conversation_scope_bindings` / `platform_tokens` / `sync_cursors` /
  `raw_api_responses`
- 迁移：现有单租户数据回填一个默认 `tenant_id`。
- `scope_key`/`idempotency_key` 等唯一键纳入 `tenant_id`，防跨租户键冲突。

### 2. 全链路租户过滤（强制，不可绕过）

- 采集入库：写入时带当前 `tenant_id`。
- scope 展开（07 `scope_resolution`）：`expand_scope`/`resolve_filters` 必须在 `tenant_id` 内查店，
  scope 的 shop_ids 必须属于该租户的 `shops`，否则拒绝。
- Data API（`web/routes/data.py` + `services/order_metrics.py` + `ai_tools/operations_read.py`）：
  每个查询都强制 `WHERE tenant_id = ?`。建议在 `_scope_filters` 或 session 层做**默认租户过滤**，
  让"忘了加过滤"在架构上不可能，而非靠每处自觉。
- 会话绑定（08）：`conversation_scope_bindings` 按 `tenant_id` 隔离，飞书会话上下文需带租户身份。

### 3. 租户身份来源

- openclaw/飞书侧确定 `tenant_id`（按客户主体/授权关系映射），随请求传入。
- 服务端校验调用方有权访问该 `tenant_id`。

### 4. 数据隔离测试（安全验收，重点）

- 跨租户读越权：以 A 租户上下文查 B 的 shop_id/scope_id → 返回空或 403，**绝不串数据**。
- scope 越界：A 的 scope 配了 B 的店 → 配置即被拒。
- 默认过滤生效：构造一个"忘记显式传 tenant"的查询，验证 session 层默认过滤仍兜住。
- 唯一键隔离：A、B 同名 scope_key / 同 shop_id 互不覆盖。

### 5. 安全评审

- 过一遍所有读路径，确认无可绕过租户过滤的入口（含 overview、raw 回放、CLI）。

## Done When

- 所有业务表带 `tenant_id` 且历史数据已迁移。
- 所有读写路径强制租户过滤，默认兜底不可绕过。
- 跨租户越权测试全部返回空/403。
- 安全评审通过。
- `uv run pytest` 通过。

## 范围外

- 客户自助开通/计费等 SaaS 化运营功能。
- 租户级配额、限流、审计看板（可后续单独立项）。
