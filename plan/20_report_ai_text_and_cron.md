# plan/20 — 定时日报/周报：AI 文字报告 + 链接 + cron 固化

## 背景与目标

现状：定时日报/周报（openclaw cron 唤起 ecom agent）只发**一条链接**——cron prompt 明确写「不要写任何报告正文」。这是早期"怕 LLM 编数字"的保守设计。

用户诉求：改成 **AI 文字报告（摘要 + 运营建议）+ 附链接**，充分发挥 AI 的运营顾问价值。另外把 cron 任务**固化到部署流程**（现全靠手工 `openclaw cron add`，导致 prod 客户号一条 cron 都没建）。

核心平衡（不可破）：**数字严格来自服务端工具返回值（不编不算），但基于准确数字的解读/归因/建议要主动专业**。靠"服务端把算好的摘要一起返回、agent 只复述+点评"实现，既发挥 AI 又不回到编数字老坑。

## 方案总览（4 块改动）

### A. 服务端：`ops_report_link` 额外返回权威摘要

让报告链接端点复用报告页已算好的结构化数据（`_collect`/`_collect_weekly`），随链接一起返回。agent 拿到的数字 = 链接里可视化报告的数字，**同源同口径**。

**改动文件**：
- `web/routes/data.py`
  - `ReportLinkResponse` 模型（L348）增 `summary: Optional[dict] = None`
  - `get_report_link` 端点（L1089-1175）：签链接后，按 template_name 调 `_collect_weekly`（weekly_review）或 `_collect`（其余），摘取 KPI/top_skus/low_stock/health 等关键字段塞进 summary 返回
  - import：`from web.routes.report import _collect, _collect_weekly`（探索确认无循环依赖——report.py 反向 import data.py 的函数不 import report，安全）
  - 多租户：调 `_collect` 前确保 `set_current_account(account_id)` 已生效（同 /report 页路由做法）
- `web/agent_tools.py`（L98 `ops_report` WebUI 入口、L201 分支）：同步透出 summary，保证 WebUI 与飞书行为一致
- `openclaw-skills/crossborder-ops-data/references/api-contract.md`：report/link 契约补 summary 字段说明

**summary 摘取字段**（只取 agent 写文字需要的，不传整个 trend 序列以免 token 膨胀）：
- 日报/区间：`period_label`, `scope`, `kpi`(gmv/orders/ad_spend/roas + change/baseline), `top_skus`(Top5), `low_stock`(Top N 风险), `low_volume` 护栏标记
- 周报额外：`kpi.aov`, `health`(concentration 集中度 / sell_through 动销率 / new_products 新品)

### B. cron prompt：从"只发链接"改为"文字报告 + 链接"

改 `~/.openclaw/cron/jobs.json` 里 4 条（hp 2 条 enabled + 2 条 disabled 的 gtl）的 payload.message：
- 旧：「调 ops_report... 直接把返回 markdown 原样发出，不要写任何正文」
- 新：「调 ops_report 拿到 summary + 链接 → 基于 summary 的**真实数字**写：①一句 AI 摘要 + 运营建议（开头）②关键数/风险（随后）③附报告链接。数字只用 summary 返回值，不得编造/估算；无数据如实说」

cron 改动通过 §D 的 setup-cron.sh 固化，不手工 edit jobs.json。

### C. skill / 人格：强化"运营顾问"定位 + 定时报告文字规范（**克制，不堆量**）

> 注意：三份文档刚瘦身（SKILL 334→201/SOUL 126→60/AGENTS 130→71）。本次只做**精炼增补**，不重新膨胀。探索3 建议的"大张信号表/大段定时vs查询区别"**不采纳**，改用几句话。

- `SKILL.md`：意图路由 `ops_report_link` 行（L143 附近）补一句——「summary 字段含权威数字，定时报告据此写摘要+建议+关键数，数字只复述 summary 不编造」；「分析与输出」节补一句运营顾问导向（基于数据主动给归因和优先级建议，但不超出数据边界）
- `SOUL.md`：「核心价值」已有"主动建议"，仅强化措辞为"运营顾问"基调（1-2 句，不加表）
- `AGENTS.md`：定位行措辞从"运营助手"提到"运营顾问/专家"一致化

### D. cron 固化到部署流程

新增 `scripts/setup-cron.sh`（仿 sync-skill.sh 的数组参数化 + 幂等 + --check/--dry-run）：
- 顶部 `CRON_JOBS` 数组：`account:agentId:open_id:name`，每客户日报+周报各一条
- 幂等：`openclaw cron list` 查重，已存在则跳过/更新，不重复建
- prod 时序约束：收件人 open_id 须客户飞书 bootstrap 后才知（同业务 timer，属"客户授权后"投产收尾步骤）
- 文档：`docs/proactive-push-ops.md` §B 补"脚本一键化"指向 setup-cron.sh；`docs/ops-runbook.md` 投产 checklist 加 cron 一步
- **不塞进 deploy.sh**（cron 是 openclaw 侧、deploy 是 systemd 侧，两套；独立脚本更干净）

## 测试

- `tests/test_report.py` 增：
  - `test_report_link_includes_summary` — 日报链接返回含 kpi/top_skus/low_stock
  - `test_report_link_weekly_includes_health` — 周报 summary 含 health
  - `test_report_link_summary_matches_collect` — summary 数字与 _collect 返回一致（防口径漂移）
- 多租户隔离回归（`test_tenant_filter.py`）必过——summary 走 _collect、确认 account 隔离没破
- `setup-cron.sh --dry-run` 本地/hp 验证不产生重复 job

## 验证与上线节奏

1. 本地/hp 跑通测试 + curl 实打 `report/link` 看 summary 返回正确
2. hp 改 cron（setup-cron.sh）+ 重启 gateway，飞书等次日 8:30 cron 触发或手动触发验证文字报告效果
3. 你 review 文字报告形态满意后 → 部署 prod（`--pull --sync-skill --restart-gateway` + setup-cron.sh，prod 仅 ecom-gtl）

## 不做 / 边界

- 不改报告 HTML 可视化页（链接内容不动，只加文字层）
- 不动链接有效期（hp 7 天 / prod 30 分钟的差异是另一个话题，本次不碰）
- 不引入 LLM 自由查多工具拼报告（坚持服务端给摘要、agent 只复述+点评，避免口径打架和超时）
- cron 收件人本次仍脚本数组配置；迁 DB 表驱动留作后续

## 风险

- import `_collect` 跨模块：探索已确认无循环依赖，但要在 data.py 顶部 import 还是函数内 import 需注意（report.py 在函数内 import data.py 避免循环；data.py 反向也宜函数内 import _collect 保险）
- summary 增大 MCP 响应体：只取必要字段、trend 序列可不传或截断，控制 token
- cron prompt 放开正文后 LLM 可能编数字：靠 skill 强约束"只复述 summary"+ summary 提供完整数字双保险
