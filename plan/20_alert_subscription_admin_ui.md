# 告警订阅管理 UI（/admin 用户管理内嵌）

## 背景与目标

- 2026-07-10 告警链路完成两次升级（`b2944c3` / `5f5f455`）：
  1. **告警范围 = 用户授权(user_roles) ∩ 订阅(alert_recipients.scope_key)**——权限是上限（与看板/对话同一真相源 `resolve_authorized_scope`），订阅只能在权限内收窄、永不放大；无 user_roles 行 fail-closed 跳过。
  2. 告警投递升级 v2 CardKit 卡片直投（`web/alert_card_builder.py`，方案A深色系）。
- **机制已就位，但没有管理界面**：加/改告警收件人及其订阅范围目前要手写 SQL 插 `alert_recipients` 行。本 plan 把订阅管理并进 `/admin` 用户管理页。
- 触发时机：等真实需求出现（客户提出「只想收部分店告警」/多收件人管理成为日常）再做。**现阶段不急**——hp 1 个收件人、prod 告警 timer 未开。

## 概念模型（已实现，UI 只是暴露它）

| 概念 | 存储 | 语义 |
|------|------|------|
| 权限（上限） | `user_roles.allowed_scope_key` | 这个人**被允许**看什么；boss=租户全店 |
| 订阅（选择） | `alert_recipients` 行 + `scope_key` | 这个人**想被打扰**什么；NULL=全授权范围 |

推送比查看更需要减法：不能收窄就等于逼人忍受噪声,噪声多了告警就被忽略。

## 落地清单

### 后端（web/routes/admin.py）
- `GET /api/admin/alert-subscriptions` — 列本租户 alert_recipients（join user_roles 显示姓名/角色/授权范围；boss only）。
- `PUT /api/admin/alert-subscriptions/{open_id}` — upsert：`{is_active, scope_key}`。
  - 校验:scope_key 必须是本租户 business_scopes 里的活跃 scope 或 NULL。
  - 校验:open_id 必须在 user_roles 有活跃行（fail-closed 前置到写入时,别等巡检才发现跳过）。
  - 审计:写 AuditLog（告警订阅变更属敏感操作,沿用现有插桩模式）。
- `DELETE .../{open_id}` — 停用（置 is_active=False,不物理删,保留去重游标关联）。

### 前端（frontend/src/pages/AdminPage.tsx）
- 用户列表每行加「告警」列：开关（订阅/不订阅）+ 范围下拉（全部授权范围 / 各命名 scope / 单店——复用 board 店铺下拉的 `shop:` 前缀语义需后端同步支持,或先只支持命名 scope,单店后续）。
- 交互约束:范围下拉只显示 **该用户权限内** 的选项（operator 只能在 allowed 的子集里选）。
- 仿现有 AdminPage 表格风格;**移动端必须可用**（用户明确强调 mobile-first）。

### 可选二期（等更明确需求）
- 按告警类型订阅（只要断货不要爆单）→ alert_recipients 加 `alert_types` JSON 列,`_scan_one` 按类型过滤。
- 静默时段个性化（现在全局 23:00~08:30 CST）。

## 已知约束 / 坑

- `alert_recipients` 主键 (channel, account_id, open_id)——一人一租户一行;若未来要「同一人多份不同范围订阅」需改表,当前模型不支持（也暂无需求）。
- 去重游标键是 `(alert_type, account_id, scope_key)`:改订阅 scope_key 会换游标 → 已报过的可能重报一次,UI 上无需处理,知道即可。
- 收件人无 user_roles 活跃行时巡检 fail-closed 跳过（`_resolve_recipient_scope`）,UI 写入时应提前拦截并提示「请先在用户管理开通」。
- 新增 admin 端点须挂 `require_boss` + 多租户按 `perm.account_id` 收口（fail-closed）,照抄现有 /api/admin/scopes 模式。

## 验收

- boss 在 /admin 给某用户开告警、选单店 scope → 下轮巡检该用户只收到该店告警卡。
- operator 的范围下拉不出现 allowed 之外的选项;直接调 API 传越权 scope → 400。
- 停用订阅 → 巡检跳过该收件人。
- 移动端表格可操作。
- 测试:端点 CRUD + 越权校验 + 与 `_resolve_recipient_scope` 的集成断言。
