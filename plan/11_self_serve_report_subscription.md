# plan/11 — 客户自助订阅日报（确定性写工具）

> 状态：**待开发**（2026-06-13 记录）。前置「主动推送日报+监控告警」已上线（见记忆 proactive-push-daily-report-and-alerts / 当前 `flows/scan_fulfillment_alerts`、openclaw cron 日报）。

## 背景 / 动机

日报现在**由运营手工 `openclaw cron add` 配置**（ecom 8:30 + ecom-gtl）。我们希望客户能**自然语言自助订阅/退订/改时段**（"每天早上给我发日报""改成晚上""别发了"）。

技术上 ecom/ecom-gtl 是 full profile，agent 本就握有 openclaw `cron` 工具，**裸靠 agent 自己 `cron add` 也能做**——但**不稳**：弱模型 mimo 要①把自由文本时间解析成 cron 表达式、②正确拼 `--agent/--account/--to user:<open_id>/--announce/--message` 一堆参数，错一个就发错人/错时间或建一堆重复 job。这与"onboarding 不敢靠 LLM、做成确定性命令"是同一个病。

**目标**：把易错部分收到服务端，让 LLM 只做"识别意图 + 选一个预设档"，可靠度降到与 `ops_set_scope_binding`（现在很稳）同一量级。

## 方案：`ops_subscribe_report` 确定性写工具

仿 `services/scope_binding.py` + `ops_set_scope_binding` 的成熟范式（DB 表 + 确定性写工具，服务不接收自然语言）。

### 工具契约（新增 MCP 工具，挂在 `web/routes/data.py` + `web/app.py` include_operations）

`ops_subscribe_report(open_id, action, slot=None)`：
- `action ∈ {subscribe, unsubscribe, status}`。
- `slot ∈ {morning, noon, evening}` **预设档**（如 morning=08:30 / noon=12:30 / evening=20:00，Asia/Shanghai）——**不解析任意分钟**，把"每天9点05分"挡在外面（引导到最近档）。
- `open_id` 取 system prompt trusted metadata 的 `sender_id`；`account_id`/`channel` 服务端按默认推断（不让 agent 传，避免像 scope binding 那样写错行）。
- 返回确认文案（"已订阅每天早上 8:30 的经营日报" / "已退订" / 当前订阅状态）。

LLM 职责被压到：判断 action + 从话里挑一个 slot 枚举。**不碰 cron 语法、不拼投递参数。**

### 落地方式（二选一，倾向 A）

日报内容需要 LLM 解读（不是 0-LLM 告警），所以投递仍走 **openclaw cron（agentTurn）**，服务端只是确定性地管理这条 per-user job：

- **方式 A（推荐）：服务端管理 per-user openclaw cron job。**
  - `ops_subscribe_report` → 服务端 `subprocess` 调 `openclaw cron add/edit/rm`，job 名固定 `report-<account_id>-<open_id>`（幂等：已存在则 edit，不新建）。
  - 参数服务端拼死：`--agent <account的defaultAgent>`、`--account <account_id>`、`--to user:<open_id>`、`--announce --channel feishu`、`--cron`（slot→表达式）、`--message`（复用日报标准 prompt 常量）。
  - 退订 = `cron disable/rm`；status = `cron get`/list 过滤。
  - 复用监控已验证的「Data Hub 同机 subprocess 调 openclaw CLI」通道（注意 `OPENCLAW_BIN` 绝对路径，见监控 service）。
- **方式 B：订阅表 + Data Hub 定时扫表。** 新增 `ReportSubscription(channel, account_id, open_id, slot, enabled)`，各档时间由 systemd timer 触发"扫订阅→对每个订阅触发一次日报"。但日报要 LLM，Data Hub 侧没有 agent，还得回头调 openclaw 触发 agent——比 A 多绕一层。除非将来日报也改服务端确定性组装，否则 A 更直接。

> 决策点：A 把"事实源"放在 openclaw cron（与现状一致、少一张表）；B 把事实源放 DB（多租户/审计更干净）。**单租户阶段选 A**；若 plan/09 多租户落地，迁 B。

### SKILL / 文案配套

- SKILL「意图路由」加一行：`订日报/每天给我发/退订日报/改时段 → ops_subscribe_report`；给 slot 词表（早/上午→morning，中午→noon，晚上→evening）。
- 客户要任意分钟（"9点05"）→ 引导到最近预设档或告知"目前支持早/午/晚三档"。
- 更新 onboarding/SKILL：自助订阅上线后，把 plan(11) 前的"客户自助订阅暂未上线"表述改成"可直接说'每天早上发日报'订阅"。
- 注意 onboarding 双源一致（`test_onboarding_sync`）+ 部署需 `sync-skill.sh` + 客户 `/new`（plugin 改动才需重启 gateway）。

## 不稳收敛清单（本计划的核心价值）

1. 时段 = 枚举预设档，**不解析任意时间**。
2. 投递参数（account/open_id/announce/message）**服务端拼死**，agent 不碰。
3. job 名 per-user 固定 → **幂等**，重复订阅是 edit 不是新建，杜绝重复刷屏。
4. LLM 只输出 `{action, slot}` 两个受控字段。

## 测试 / 验证

- 单测：slot→cron 表达式映射；subscribe 幂等（二次订阅 edit 不新建）；unsubscribe；status；未知 slot 报错不落脏。
- 端到端（hp）：飞书对 ecom 发"每天早上给我发日报" → agent 调 `ops_subscribe_report(subscribe, morning)` → `openclaw cron list` 出现 `report-ecom-app-<open_id>`、8:30 → `cron run` 验证投递 → 发"别发了"→ 退订消失。
- 回归：现有运营手配的 ecom/ecom-gtl 日报 job 不被自助逻辑误删（job 名前缀区分）。

## 未决问题

- 预设档的具体时间（早 8:30 是否与运营手配的全局日报重复？自助订阅是否替代/叠加现有手配 job）。
- status 的展示粒度（只说"已订早上档"还是列下次发送时间）。
- 是否允许多档（一天早+晚两次）——初版建议单档，降复杂度。
