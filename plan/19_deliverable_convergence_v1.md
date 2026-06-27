# plan/19 — 交付版收敛 v1（客户可用最小集）

> 2026-06-27 定。目标：**收敛一个能交付客户的版本，不求功能齐全，但要有真实可用的核心功能立得住。** webUI 为主、飞书对话为辅。
> 关联记忆：[[plan17-webui-ops-requirements]] [[prod-deployment-status]] [[business-rules-doc]] [[report-artifact]] [[weekly-report]] [[proactive-push-daily-report-and-alerts]] [[plan15-web-chat-console]]
> 上游：plan/18（费率告警逻辑 B1/B2 已完成、Track A 数据验证部分完成）。本计划是「对外收敛」而非「功能扩张」。

## 决策（已与用户拍板，2026-06-27）

- **交付脊柱 = ① 报告（日报/周报，定时+问答，最成熟）+ ② 费率监控卡（webUI 看板）。**
- **飞书对话 skill 本轮不做**。输出跑偏（TUI 长分割线）出在 openclaw agent 自由输出侧，Data Hub 投递路径碰不到；确定性修需 openclaw 出站插件（工量另算）。交付版飞书保持现状（问答可用、偶有格式毛刺）。
- **预估利润**：不隐藏，但**改标「未扣商品成本」口径**（看板 ProfitCard + 报告模板两处），不展示误导性毛利率。COGS 录入留后续。
- **prod timer**：本轮**只准备 enable 脚本/runbook，不在 prod 执行**。用户亲自决定何时开。
- **webUI 定时任务页 + skill 菜单**：均标「待开发」徽章/横幅，不展示假功能（两页当前都是纯前端 mock / 装饰）。
- 费率监控卡**实时算、不新建告警事件表**（复用 `get_settled_fee_rate`/`get_unsettled_fee_rate`/`build_decision`）。告警历史落库留作后续增强。

---

## 工作项

### W1. 费率监控卡（webUI 看板）—— 本轮主要新代码

**目标**：看板新增一张「费率监控」卡，有无告警都能演示。展示当前费率、基准、近 N 日趋势、状态徽章（正常/异常）；异常时挂分项归因详情（复用 `_attributions` 文案）。

- **服务层** `services/fee_rate_metrics.py`（或新 `services/fee_rate_monitor.py`）加 `get_fee_rate_monitor(...)`：
  - eval = 近 `fee_rate_realtime_eval_days`（默认 3）天 unsettled 预估费率（`get_unsettled_fee_rate`）；
  - baseline = 近 `fee_rate_baseline_days`（默认 28）天 settled 已结算费率（`get_settled_fee_rate`）；
  - 主币种 + `build_decision(realtime=True)` 得 `should_alert` / 升幅 / 分项归因；
  - 趋势：近 14 个业务日逐日费率（unsettled 优先、缺则 settled），供前端折线；
  - 返回 `{currency, current_rate, baseline_rate, status:'normal'|'alert'|'insufficient', delta, rel_delta, attributions:[...], trend:[{date,rate}], components:[{name,rate}]}`。
  - 数据不足（无主币种 / GMV 不过护栏 / baseline<=0）→ `status='insufficient'`，前端显「数据积累中」而非误报。
- **API** `web/routes/data.py`：在 `DashboardSummaryResponse` 加 `fee_rate_monitor` 字段，`get_dashboard_summary()` 里用 `_safe("fee_rate_monitor", ...)` 取数（单卡容错，沿用现有范围/权限解析）。
- **前端** `frontend/src/`：
  - 新组件 `components/board/FeeRateMonitor.tsx`：状态徽章 + 当前/基准费率 + echarts 折线趋势 + 异常时分项明细。
  - `pages/BoardPage.tsx` 挂入该卡；`components/board/demo-data.ts` 补 mock。
  - 三态视觉：正常（绿）/ 异常（红，挂详情）/ 数据积累中（灰）。
- **测试** `tests/`：仿 `test_fee_rate_alerts.py`，离线 SQLite 构造 `FactUnsettledFee`+`FactFinanceTransaction`+`OrderHeader`，验三态（正常/异常/不足）与趋势聚合。
- **可选**：独立飞书看板 `web/routes/board.py` `_collect()` 是否同步加该卡——webUI 为主，**默认只做 SPA**，board.py 视余力。

### W2. 定时周报 cron

- 定时日报 openclaw cron 已在跑（08:30 CST）。**补定时周报**：周一晨推 last_week。
- 准备 `openclaw cron add` 命令 + prompt 模板（仿 `docs/proactive-push-ops.md` 日报模板，调 `ops_report_link` template=weekly_review period=last_week）。
- 按「准备不开」原则：把命令/模板写进 `docs/proactive-push-ops.md`，**不在服务器执行**，交用户手动加。

### W3. 利润口径标注

- 看板 `components/board/ProfitCard`（或 BoardPage 内）：预估利润旁标「未扣商品成本」+ 隐藏/置灰毛利率，或注「成本未录入，毛利偏高」。
- 报告模板 `web/routes/report.py` `DAILY_BRIEF_HTML` / `WEEKLY_REVIEW_HTML` 利润处同口径标注。
- 不动利润计算逻辑，仅前端/模板文案。

### W4. webUI「待开发」标注

- `frontend/src/pages/ScheduledPage.tsx`：顶部加「功能待开发」横幅/徽章（当前纯前端 mock）。
- `frontend/src/pages/SkillsPage.tsx`：同上（当前 skill 卡片开关是纯装饰、无后端按需启用）。
- 侧边栏 `Sidebar.tsx` 对应入口可加「Beta/待开发」小标（视设计）。

### W5. prod 上线脚本/runbook（准备不执行）

- 写/校 `data-scan-alerts` + 报告/费率所需 sync timer 的 enable 流程到 `docs/ops-runbook.md`：
  - 依赖顺序、`daemon-reload` 坑（不 reload 则 NEXT 空）、逐个 `enable --now`、dry-run 验证。
  - 明确费率告警需 ~2 周结算历史才会真正触发（监控卡当下走 unsettled 实时口径、无此限制）。
- **不在 prod 执行任何 enable**。交付脚本，用户决定何时开。

---

## 本轮不做（边界）

- 飞书 skill 重构（最小修复 + 模块化均不做）。
- webUI 真实按需启用 skill 后端（`GET /api/admin/tools`、定时任务 CRUD + 执行器）。
- 告警事件落库 / 看板告警历史列表（监控卡实时算即可）。
- COGS 成本录入（利润仅改标注）。
- 类目轴费率归因（见 docs/business-rules.md §7.1 暂缓决策）。
- prod 真启 timer（仅备脚本）。

## 测试 / 部署 / 验证

- **测试**：W1 新单测 + 全量 `uv run pytest -q` 保持绿。
- **部署**：W1/W3/W4 有前端改动 → `./deploy/deploy.sh --pull --restart-web`（含前端构建）。W2/W5 是脚本/文档，不触发部署。
- **验证**：本地 `uvicorn web.app:app` 起服务看板看费率监控卡三态（构造数据验异常/正常/不足）；报告端点看利润标注；ScheduledPage/SkillsPage 看「待开发」标。
- 收尾更新 memory（标交付版收敛进度）。

## 起手

1. W1 服务层 `get_fee_rate_monitor` + 单测（纯离线，先把数据契约钉死）。
2. W1 API 字段 + 前端卡片。
3. W3/W4 文案/标注（轻、可并行）。
4. W2/W5 脚本+文档（不执行）。
