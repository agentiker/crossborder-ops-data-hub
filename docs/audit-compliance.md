# 审计合规：哈希链 + 锚定（Audit Compliance）

上线前合规三支柱之一（另两为 token 透明加密、每日加密备份）。本文说明**审计链的三层防线**、
**断链告警怎么处置**，以及**授权批量改动后如何重新封链**。代码在 `services/audit.py`，
CLI 在 `scripts/`，flow 在 `flows/anchor_audit_chain.py`。

> 相关记忆：`audit-compliance-token-encryption`、`audit-chain-broken-by-account-id-migration`。

---

## 一、三层防线（务必分清"记录"和"防抵赖"是两回事）

| 层 | 干什么 | 载体 | prod 状态 |
|---|---|---|---|
| **1. 审计记录** | 每次 API 调用 / 权限·授权·账号操作追加一行（append-only 只增不改） | 表 `api_call_logs` / `audit_log`，`services/audit.record_*` 插桩 | ✅ 一直在记 |
| **2. 哈希链** | 每行 `row_hash = sha256(上一行hash ‖ 本行规范串)` 环环相扣；改任一行内容 → 其后 hash 全断可检出 | `verify-audit-chain` timer 每天复算 4 次，断则 OnFailure 发飞书 | ✅ 一直在跑 |
| **3. 锚定 anchor** | 每日把链尾 `(tip_id, tip_hash)` 外发飞书运维群留痕 → 防"能改库者整条重算抹平痕迹"的抵赖 | `anchor-audit` timer 每日 02:07，`flows/anchor_audit_chain` | ⚠️ 默认关（见 §五） |

**为什么需要第 3 层**：第 2 层只防"改单行不重算"。若攻击者能直接写库，可把整条链从头重算得
hash 全自洽，verify 就查不出（这正是 `reseal_chain` 干的事，只不过那是授权操作）。anchor 把每日
链尾发到**数据库管理员改不到的外部**（飞书），链一旦被整段重写，今日重算的链尾就与昨日已外发的
锚点对不上 → 不可抵赖。外部那条消息就是不可篡改的证人。

**分链维度**：按 `account_id` 分链（不是全局单链），与 `core/db.py` 的 ORM 自动租户过滤契合，
并发争用只在同租户内。

---

## 二、断链告警（verify-audit-chain 失败）处置 SOP

收到飞书「定时任务失败：data-verify-audit-chain.service」时，**先定性：是真篡改，还是授权批量改动**。

1. **看断裂形态**（跨租户全表校验，绕过 ORM 过滤）：
   ```bash
   ~/.local/bin/uv run python -m scripts.verify_audit_chain --json | python3 -m json.tool | head
   ```
   - `per_table` 哪张表、`breaks` 哪些 account/id。
2. **定性**：
   - **零散 1~2 行断** → 疑似真篡改某行内容，**立即排查**（谁改的、改了什么），别急着 reseal。
   - **整段断、整段好交替 / 大面积断** → 几乎一定是**授权的批量改动碰了被哈希字段**，最常见是改
     `account_id`（它是 canonical 第一个字段）。典型元凶：租户合并/迁移脚本 `UPDATE ... SET account_id=...`。
     这不是数据坏了，是链的自洽性被合法操作打破。
3. **确认是授权改动后** → 走 §三 重新封链。**确认是真篡改** → 走应急响应（溯源、备份对比、通知），不 reseal。

> 被哈希字段（改了必断链）：见 `services/audit.api_call_canonical_parts` / `audit_event_canonical_parts`。
> `account_id / category / method / path / http_status / business_code / created_at ...` 均在内。

---

## 三、重新封链（reseal）— 授权改动后的唯一正解

**语义**：按 id 顺序用**当前** canonical 重算某租户整条链的 `prev_hash`+`row_hash` 写回，使链重新
自洽、verify 通过。**这是对不可篡改日志的授权重写**，只应在确认过的管理操作后执行，且会自动记一条
`event_type=audit_maintenance` 的 AuditLog 元事件留痕，令重封本身可追溯。

```bash
# 先 dry-run 看影响面（只统计，不写库）
~/.local/bin/uv run python -m scripts.reseal_audit_chain --account <acct> --reason "..." --dry-run
# 正式执行（写库 + 提交后自动复验）
~/.local/bin/uv run python -m scripts.reseal_audit_chain --account <acct> --reason "并租户 X→Y 后重新封链"
```

**⚠️ 并发接缝**：`reseal_chain` 对该 account 全链加 `FOR UPDATE`（仅 MySQL）。这是必须的——否则
重封期间并发的 `record_api_call`（每次 TikTok 调用都写）读到旧链尾，追加的新行会断在重封接缝。
实测无锁重封曾冒出 1 行新断裂；加锁后并发写入阻塞到 commit 再读新链尾，复验通过。活跃写入的库
几秒内就能涨上千行，别在高峰期跑、跑完务必看复验结果。

**只改 `prev_hash`/`row_hash` 两列，绝不动 `created_at` 等被哈希业务字段**（否则等于二次篡改内容）。

---

## 四、迁移/批量改 account_id 的坑（治本）

任何迁移脚本若要改审计表的 `account_id`（如租户合并），**不能只盲目 `UPDATE`**——改完必须紧接
`reseal_chain` 重新封链，两者绑在一起。参考 `scripts/migrate_merge_gtl_into_ecom_hp.py`：审计表
已从"盲改 account_id"的表清单里拆出，单独一步「改 account_id + reseal + 记元事件」。

> 2026-07-12 hp 事故复盘：该迁移把 `api_call_logs`/`audit_log` 也列进盲改清单，导致 gtl 行改归
> ecom-app 后按 id 交织进链、7004 行断裂、verify 每次失败告警。修法即 §三 reseal（hp 已修复转绿）。

---

## 五、anchor 配置（每日锚定留痕）

`core/config.py` 三项（默认 `enabled=True`，但 **prod .env 显式设了 false**，故从未外发）：

| env | 含义 | hp | prod |
|---|---|---|---|
| `AUDIT_ANCHOR_ENABLED` | 每日锚定开关 | `true` | `false` |
| `AUDIT_ANCHOR_ACCOUNT` | 发送用的飞书 app（运维机器人） | `main-app` | 未配 |
| `AUDIT_ANCHOR_OPEN_ID` | 收件人 open_id（运维） | `ou_1f3…` | 未配 |

**要开启 prod 的每日留痕**：三项都要配（account+open_id 任一为空只 print 到 journald 不外发），且需
确认 prod 已有 `main-app` 的飞书 app 凭证；改完下次 02:07 生效或手动 `systemctl --user start
data-anchor-audit.service` 触发验证。

> 说明：anchor 关着时，prod 仍有第 1、2 层（记录在写、单行/局部篡改 verify 能抓）；缺的只是"防 DB
> 管理员级整链重写"的外部锚点 + 每日正向回执。是否上按合规节奏决定，非必开。
