---
status: active
owner: codex
depends_on: []
---

# 07 多店业务范围（scope）基础：集合查询能力

## Context（为什么做这个）

客户（老板A）在多个平台/国家有多个店铺：印尼 TikTok 多店、印尼虾皮、拉美 TikTok 多店。
他问"本周订单趋势"时，往往指的是**一组店**（如"印尼 TikTok 全部店"），而不是单店。
当前 Data API 只支持**单值 `shop_id`** 过滤（`services/order_metrics.py:_scope_filters`），
无法表达"一次查 3 个店的汇总"，也没有"业务范围"这个语义。

本期目标：引入 **scope（业务范围/视图）** 作为查询语义，让 `/api/data/*` 能按
`scope_id`（一个命名的店铺集合）或显式 `shop_ids` 集合查询，并支持"在 scope 基础上收窄到某店"。

**本期是纯后端能力，不含飞书、不含自然语言、不含租户隔离**（见 08、09）。
单客户场景，**不引入 `tenant_id`**——一个客户 = 一个租户，当前租户是恒定的，加隔离列只会制造
"看着隔离实则没有"的假象（详见 09 的判断）。scope 只切**平台/国家/店铺**三个维度。

## 概念

- **scope**：一个命名的店铺集合，如 `印尼TikTok全部店 = TikTok Shop / ID / [shop1, shop2, shop3]`。
- 本期只做**显式列举型** scope：`single_shop`（单店）、`shop_group`（显式 shop_ids 列表）。
  规则型（`country_platform` 自动含某平台某国全部店）依赖店铺主数据，放到 08。
- **店铺登记来源**：复用现有 `platform_tokens`——每条已授权 token = 一个合法店铺 scope。
  scope 里写的 shop_ids 必须能在 `platform_tokens` 找到对应授权店，否则视为非法（防止瞎配）。

## 实现步骤

### 1. ORM 模型（`models/base_models.py` 新增 1 张表）

`BusinessScope`（表 `business_scopes`），列风格对齐现有 scope 五元组：

- `id` PK
- `scope_key` String unique（稳定 slug，如 `tts-id-all`，供 API/配置引用）
- `scope_name` String（展示名，如 `印尼TikTok全部店`，回答里用）
- `scope_type` String：`single_shop` / `shop_group`
- `platform` String 可空（集合跨平台时为空）
- `country` String 可空
- `shop_ids` JSON（字符串数组）
- `is_active` Boolean 默认 True
- `created_at` / `updated_at`

不加 `tenant_id`（见 Context）。

### 2. scope 解析服务（`services/scope_resolution.py`，纯确定性）

**不接收任何自然语言**，只做结构化展开与校验：

```python
@dataclass
class ScopeFilters:
    platform: str | None
    country: str | None
    shop_ids: list[str]        # 展开后的具体店铺集合
    scope_key: str | None
    display_text: str          # "TikTok Shop / 印尼 / 3 个店铺"

def list_scopes() -> list[dict]: ...
def get_scope(scope_key: str) -> BusinessScope | None: ...
def expand_scope(scope_key: str) -> ScopeFilters: ...   # 展开为 shop_ids 集合
def resolve_filters(
    *, scope_key: str | None = None,
    platform: str | None = None, country: str | None = None,
    shop_id: str | None = None, shop_ids: list[str] | None = None,
) -> ScopeFilters: ...
```

`resolve_filters` 的**收窄语义**（解决原 plan "scope_id+显式参数→400" 的矛盾）：
- 只传 scope_key：展开为该 scope 的 shop_ids。
- scope_key + 显式 shop_id/shop_ids：**取交集**（在 scope 范围内收窄到指定店）。
  指定的店**不在** scope 内 → 抛 `ScopeError`（API 层转 400 + 明确信息），**绝不放行范围外的店**。
- 只传显式 platform/country/shop_id(s)、无 scope_key：保持现有行为（兼容旧调用）。
- shop_ids 里任何一个店在 `platform_tokens` 查不到授权 → 拒绝（400）。

校验店铺合法性时查 `platform_tokens`（已是每店一行的授权登记）。

### 3. 指标/查询层支持 shop_ids 集合（只动有真实数据的端点）

- `services/order_metrics.py:_scope_filters(query, model, platform, country, shop_id, shop_ids=None)`：
  - `shop_ids` 非空 → `model.shop_id.in_(shop_ids)`；否则维持 `shop_id ==` 单值兼容。
  - 三个函数 `get_gmv_summary` / `get_gmv_trend` / `get_top_skus` 增加 `shop_ids` 入参透传。
- `web/routes/data.py`：`/inventory`、`/products` 的内联查询同样支持 `shop_ids.in_()`。
- **不动 `/profit/summary`、`/alerts`**——它们目前是 503 占位（无数据源），给它们加多店过滤是无用功。

### 4. Data API 增加 scope 入参（`web/routes/data.py`）

各真实端点（inventory / products / orders/summary / orders/trend / orders/top-skus / overview）
增加可选 query：

- `scope_id`（实为 `scope_key`，命名沿用对外习惯，内部映射）
- `shop_ids`（逗号分隔）

端点内统一走 `scope_resolution.resolve_filters(...)` 得到 `ScopeFilters`，再把
`platform/country/shop_ids` 传给指标函数。响应可附 `scope`（`display_text`）一行，便于回答声明范围。
`ScopeError` → `HTTPException(400, detail=...)`。

### 5. scope 配置方式（运维，先简单）

- 第一版：直接 DB 插入/更新 `business_scopes`。
- 附一个**仅本地**的管理 CLI `scripts/scope_admin.py`（不起 HTTP、不对公网）：
  `list` / `create --key --name --type --platform --country --shop-ids` / `deactivate`。
  创建时即校验 shop_ids 是否在 `platform_tokens` 中。

## 测试计划（离线，沿用 tests/conftest 的 in-memory session）

- `expand_scope`：`shop_group` → 正确 shop_ids 集合与 display_text。
- `resolve_filters` 收窄：scope + 范围内 shop_id → 交集为该单店。
- `resolve_filters` 越界：scope + 范围外 shop_id → 抛 ScopeError。
- 非法店：shop_ids 含 `platform_tokens` 没有的店 → 拒绝。
- 多店集合过滤：`get_gmv_summary/trend/top_skus(shop_ids=[a,b])` 只聚合这两个店。
- 兼容：仅 `shop_id` 的现有订单/库存测试继续通过。

## Done When

- `business_scopes` 表 + `scope_resolution` 服务可用，单测通过。
- `/api/data/*` 真实端点可用 `scope_id` 或 `shop_ids` 查询多店集合，并能在 scope 内收窄到单店。
- 越界/非法店被拒，不泄露范围外数据。
- 旧的单 `shop_id` 调用与现有测试不破。
- 回答可声明查询范围（`display_text`）。
- `uv run pytest` 通过。

## 范围外（交给后续期）

- 飞书会话默认范围、自然语言解析、追问、群聊回答策略、skill 契约升级 → **08**。
- 店铺主数据表、规则型 scope（`country_platform`/`tenant_all` 自动含全部店）→ **08**。
- 多租户 `tenant_id` 隔离 → **09**（接第二个客户时才做）。
