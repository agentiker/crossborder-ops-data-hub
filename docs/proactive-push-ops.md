# 主动推送运维：日报 + 待发货超时告警

> 2026-06-13 上线。两条**主动推送**链路的生产部署 / 改配置 / 排错口径。
> 服务器 `yamk`（`ssh hp`），与数据同步一样**用 systemd user timer，不走 Prefect server**。

## 0. 两条链路一图

| 链路 | 过 LLM？ | 调度 | 投递 | 配置在哪 |
|------|---------|------|------|---------|
| **日报** | ✅ 过（要解读+建议） | **openclaw cron**（agentTurn） | cron `announce` 自动发飞书 | 在 hp 用 `openclaw cron` 命令 |
| **监控告警**（待发货超时 + 低库存/断货） | ❌ 不过（确定性） | **systemd timer** `data-scan-alerts` | `openclaw message send` 直投 | repo 代码 + service 文件 |

> 为什么分两套：cron job 必经 agent/LLM，适合要解读的日报；告警要阈值/去重/稳定文案、每跑必准，走 Data Hub 确定性判定 + `openclaw message send`（0-LLM、走本地 gateway RPC `127.0.0.1:18789`，不出网、不受 TikTok 出口 IP / WARP 坑影响）。

---

## 一键部署（推荐）

```bash
ssh hp
cd ~/code/crossborder-ops-data-hub
./deploy/deploy.sh --pull     # 拉代码 + uv sync + 建表 + 装/启用所有 systemd timer
# 可选：--sync-skill（同步文案）、--restart-web、--restart-gateway、--dry-run（只看不动）
```

- 所有 systemd unit 已纳入 repo `deploy/systemd/`（用 `%h`，换机/换用户免改）；`deploy.sh` 负责 cp → `daemon-reload` → `enable --now` 所有 timer、建表、补 `.env` 的 `OPENCLAW_BIN`。
- **失败告警**：每个任务 service 带 `OnFailure=data-onfailure@%n.service` → `deploy/notify-failure.sh` 取最近日志、用 `openclaw message send` 发飞书给运维（`main-app` + `NOTIFY_OPENID`，可环境覆盖）。补上了 systemd「没界面、没告警」的硬伤。
- 改周期/加任务：改 `deploy/systemd/` 里的 unit，重跑 `deploy.sh`。
- **日报 openclaw cron 不在 deploy.sh 内**（手工，见 B 节）。

下面 A/B/C 是各环节细节与排错 reference。

---

## A. 监控告警（systemd timer，Data Hub 侧）

### A1. 部署了什么

| timer | 跑的 flow | 周期 | 覆盖规则 |
|-------|-----------|------|---------|
| `data-scan-alerts.timer` | `flows.scan_fulfillment_alerts` | 每 30 分钟 `*:10,40` | ① 待发货超时 ② 低库存/断货 |

> 一个 flow 跑两条规则，每个收件人每轮各自独立判定/去重/投递（互不影响）。文件名沿用 `scan_fulfillment_alerts` 以免动 timer，现已是「告警总巡检」。

**① 待发货超时**
- 代码：`services/fulfillment_alerts.py`（判定+文案+去重，纯函数）、`models.FulfillmentAlertState`（去重游标表）。
- 去重规则：超时单数 `overdue` **从 0→非0 或较上次上报增加**才推；持平/下降不复读；清零归零游标。

**② 低库存/断货**
- 代码：`services/stock_metrics.py`（可售天数=库存÷日均销速、分桶，取数）、`services/stock_alerts.py`（判定+文案，纯函数）、`models.StockAlertState`（去重游标表，存已报 SKU 集合 JSON）。
- 口径：只统计仍有销量(velocity>0)的 SKU；库存 0 且有销量=断货、可售<`critical_days`=告急、<`warning_days`=预警；销速看近 `velocity_window_days` 天。
- 去重规则：按**风险 SKU 集合**——有新 SKU 跌入风险才推；老 SKU 持续低库存不复读；SKU 补货恢复后再次跌入会重报。
- config 旋钮（`core/config.py`，可 `.env` 覆盖）：`stock_cover_critical_days`(3) / `stock_cover_warning_days`(7) / `stock_velocity_window_days`(7)。

- 编排+投递：`flows/scan_fulfillment_alerts.py`。静默：默认 `23:00–08:30 Asia/Shanghai` 不推（`core/config.alert_quiet_*`，两条规则共用）。

### A2. 首次部署步骤（hp）

```bash
ssh hp
cd /home/guopeixin/code/crossborder-ops-data-hub
git pull

# 1) 建去重表（create_all 只建不存在的表，安全幂等）
/home/guopeixin/.local/bin/uv run python -c "from core.db import init_db; init_db()"
/home/guopeixin/.local/bin/uv run python -c "from core.db import engine; from sqlalchemy import inspect; t=inspect(engine).get_table_names(); print('fulfillment_alert_state' in t, 'stock_alert_state' in t)"  # 应 True True

# 2) dry-run 验证（不实发，只打印判定/文案）
/home/guopeixin/.local/bin/uv run python -c "from flows.scan_fulfillment_alerts import scan_fulfillment_alerts_flow; scan_fulfillment_alerts_flow(dry_run=True)"
```

**3) 写 systemd unit**（service + timer）：

```bash
cat > ~/.config/systemd/user/data-scan-alerts.service <<'EOF'
[Unit]
Description=待发货超时监控告警巡检
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/home/guopeixin/code/crossborder-ops-data-hub
Environment=PYTHONUNBUFFERED=1
Environment=OPENCLAW_BIN=/home/guopeixin/.nvm/versions/node/v22.22.2/bin/openclaw
ExecStart=/home/guopeixin/.local/bin/uv run python -m flows.scan_fulfillment_alerts
TimeoutStartSec=600
EOF

cat > ~/.config/systemd/user/data-scan-alerts.timer <<'EOF'
[Unit]
Description=每 30 分钟跑待发货超时告警巡检
[Timer]
OnCalendar=*-*-* *:10,40:00
Persistent=true
[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now data-scan-alerts.timer
systemctl --user list-timers data-scan-alerts.timer --no-pager
```

> ⚠️ `Environment=OPENCLAW_BIN=...绝对路径` **必须有**——systemd 默认 PATH 不含 nvm，否则 `openclaw message send` 调不到。

### A3. 配置在哪改

| 改什么 | 在哪 |
|--------|------|
| 收件人（account + open_id + scope） | `flows/scan_fulfillment_alerts.py` 顶部 `RECIPIENTS` |
| 静默时段 | `core/config.py` `alert_quiet_start/end/tz`（或 `.env` 覆盖） |
| 巡检频率 | `~/.config/systemd/user/data-scan-alerts.timer` 的 `OnCalendar=` → `daemon-reload && restart` |
| 待发货临界阈值（小时） | `.env` `FULFILLMENT_WARNING_HOURS`（默认 24） |
| 库存告急/预警阈值（可售天数） | `.env` `STOCK_COVER_CRITICAL_DAYS`(3) / `STOCK_COVER_WARNING_DAYS`(7) |
| 库存销速窗口（天） | `.env` `STOCK_VELOCITY_WINDOW_DAYS`（默认 7） |
| openclaw 路径 | service 的 `OPENCLAW_BIN`（或 `.env`） |

### A4. 运维命令

```bash
systemctl --user list-timers data-scan-alerts.timer        # 下次/上次触发
journalctl --user -u data-scan-alerts -n 50 --no-pager     # 日志（看 [alert] 行）
systemctl --user start data-scan-alerts.service            # 立刻真跑一次
systemctl --user disable --now data-scan-alerts.timer      # 停
systemctl --user enable  --now data-scan-alerts.timer      # 恢复
```

### A5. 改代码后重新部署

```bash
ssh hp 'cd ~/code/crossborder-ops-data-hub && git pull'
# 改了 models（新增/改表）→ 再跑一次 init_db 建表
# 不需重启任何常驻进程：timer 每次 oneshot 用 `uv run` 起新进程，下次触发即用新代码
```

---

## B. 日报（openclaw cron，过 LLM）

### B1. 现有 job（每天 8:30 `Asia/Shanghai`）

| 客户 | cron job | agent | account | 收件人 open_id |
|------|----------|-------|---------|---------------|
| ecom | id `eca7f330-7981-4aed-b362-78a99745dbb7` | ecom | ecom-app | `ou_7afe4514b269e5a0abfbd395f3f26410` |
| ecom-gtl | （`cron add` 时生成的新 id） | ecom-gtl | ecom-app-gtl | `ou_5a27000e3e67de797de432a43bac29da` |

### B2. 改时间 / 改 prompt

```bash
openclaw cron edit eca7f330-7981-4aed-b362-78a99745dbb7 \
  --cron "30 8 * * *" --tz Asia/Shanghai --message "$PROMPT"
```

### B3. 给新客户加日报（模板）

> **⚠️ 飞书排版铁律（务必保留在 prompt 里）**：飞书私聊**不渲染 Markdown 表格和 `#` 标题**（见 `feishu-bot-onboarding.md`），表格会变成满屏 `|---|` 竖线、手机尤其难读。模板内已写死「禁止表格/编号或 bullet 替代/emoji 小标题」的约束 + 示范，**改 prompt 时不要删这段**，否则 agent 又会输出表格。
> **日报开头先挂图表版链接**：prompt 第一步让 agent 调 `ops_report_link`（`daily_brief`/`period=yesterday`）把可点击链接放最前——cron 无人值守，agent 无上下文 open_id，故 **open_id 必须在 prompt 里写死**（换成该客户的真实 open_id，下方 `<填客户 open_id>`）。

```bash
read -r -d '' PROMPT <<'EOF'
请生成并推送【昨日（完整日）】印尼 TikTok Shop 经营日报。今天推的是昨天完整业绩，勿用"今日"（今早数据未累计完）。

第一步——先出图表版链接：调用 ops_report_link（template_name=daily_brief、period=yesterday、open_id=<填客户 open_id>），把返回的 markdown 字段原样放在日报最开头一行（形如"📊 图表版日报 → 点击查看"）。

第二步——文字详情，依次：
1. 📊 经营概览：库存总量/低库存数；昨日 GMV/订单/销量/客单价（各环比前日带升降箭头）；附近7天累计
2. 📈 近7天趋势：逐日 GMV/订单/销量，点出峰值与关键变化
3. 🚚 待发货风险：待发货总数、超时/临界单数、重点店铺品类、SLA 截止
4. 📦 低库存/断货：已断货/告急/预警各几个 SKU，点名有动销却零库存的款
5. 🔥 近7天爆款 Top 5：商品名 + 规格 + 销量 + GMV
末尾给 ≤3 条运营建议。数据口径以接口 caliber 字段为准。利润/ROI 本期未上线，不要编。

⚠️ 飞书排版铁律（飞书私聊不渲染表格和 # 标题，违反会变满屏竖线、手机难读）：
- 绝对禁止表格（不要出现 | 和 |---|）；不要用 #/##/### 标题
- 分段用 emoji + **粗体小标题**；明细用「• 」每条一行；趋势/排行用「1. 2. 3.」编号
- 数字千分位、关键数字加粗。示范（7天趋势就这样写，别用表格）：
  • 6/27（六）🔺 GMV **7,810万** · 575单 · 594件（周峰值）
  • 6/28（日）GMV 5,603万 · 399单 · 418件

【重要】直接输出完整日报全文作为回复，系统会自动投递，不要用 message 工具，不要只回确认语。
EOF

openclaw cron add --name "<客户> 每日经营日报" \
  --agent <agentId> --account <accountId> \
  --cron "30 8 * * *" --tz Asia/Shanghai \
  --announce --channel feishu --to user:<open_id> \
  --message "$PROMPT"
```

### B3b. 给客户加【定时周报】（plan/19 W2，模板 — 按"准备不开"原则，命令备好待手动执行）

与日报对称，周报走同一套 openclaw cron + LLM，调 `ops_report_link` 的 `weekly_review` 模板看上周整周（`period=last_week`）。**周一晨推**（cron `0 9 * * 1`，避开与日报 8:30 撞点）。同样遵守上方飞书排版铁律 + 开头挂图表版周报链接。

```bash
read -r -d '' WPROMPT <<'EOF'
请生成并推送【上周（完整一周，周一至周日）】印尼 TikTok Shop 经营周报。

第一步——先出图表版链接：调用 ops_report_link（template_name=weekly_review、period=last_week、open_id=<填客户 open_id>），把返回的 markdown 字段原样放在周报最开头一行（形如"📊 图表版周报 → 点击查看"）。

第二步——文字详情，依次：
1. 📊 周度概览：上周 GMV/订单/销量/客单价，与上上周环比（带升降箭头）
2. 📈 趋势与动销：逐日 GMV/订单走势、动销率、连续区间表现
3. 📦 商品健康度：爆款集中度、新品表现、断货风险
4. 🚚 待发货与履约概况
末尾给 ≤3 条运营复盘建议。数据口径以接口 caliber 字段为准。利润/ROI 本期未上线，不要编。

⚠️ 飞书排版铁律（飞书私聊不渲染表格和 # 标题，违反会变满屏竖线、手机难读）：
- 绝对禁止表格（不要出现 | 和 |---|）；不要用 #/##/### 标题
- 分段用 emoji + **粗体小标题**；明细用「• 」每条一行；趋势/排行用「1. 2. 3.」编号
- 数字千分位、关键数字加粗

【重要】直接输出完整周报全文作为回复，系统会自动投递，不要用 message 工具，不要只回确认语。
EOF

openclaw cron add --name "<客户> 每周经营周报" \
  --agent <agentId> --account <accountId> \
  --cron "0 9 * * 1" --tz Asia/Shanghai \
  --announce --channel feishu --to user:<open_id> \
  --message "$WPROMPT"
```

> 现状：定时日报已在跑（B1）；**定时周报 cron 尚未在服务器添加**——本计划只备命令、不执行，由用户确认后手动 `cron add`。问答周报（飞书发"看周报"/webUI `ops_report` template=weekly_review）已可用，不依赖本 cron。

### B4. 运维命令

```bash
openclaw cron list                  # 所有 job：周期/收件人/agent/上次状态
openclaw cron get <id>              # 单 job JSON
openclaw cron run <id>             # 立刻跑一次（调试，会真发）
openclaw cron runs <id>            # 运行历史
openclaw cron disable/enable <id>  # 停/启
openclaw cron rm <id>              # 删
```

---

## C. 文案同步（任何能力上线 / 改 onboarding 时）

主动推送上线后，bot 文案不能再说"日报/告警不存在"。改完 repo 文案后：

```bash
ssh hp 'cd ~/code/crossborder-ops-data-hub && git pull && ./scripts/sync-skill.sh'
# sync-skill.sh 把 SKILL + AGENTS.md + SOUL.md 推到 workspace-ecom / workspace-ecom-gtl
```

- **改了 `openclaw-plugins/crossborder-onboarding/index.js`（ONBOARDING_ZH）**：plugin 直接从 repo 路径加载，但 `ONBOARDING_ZH` 是 gateway 启动时固化的内存常量——**必须 `systemctl --user restart openclaw-gateway`** 才重载。`/start` 重启后即时生效。
- **只改了 SKILL/AGENTS/SOUL**：`sync-skill.sh` 即可，**不用重启 gateway**；客户在飞书发 `/new` 重载（`/reset` 不够）。
- onboarding 文案有双源一致测试 `tests/test_onboarding_sync.py`（index.js ↔ SKILL 同步块逐字一致），改一处必同步另一处。

---

## D. 已知坑（实测）

- **`OPENCLAW_BIN` 绝对路径**：systemd service PATH 无 nvm，flow/脚本调 openclaw 要用绝对路径（deploy.sh 自动写进 `.env`）。
- **openclaw 调用要补 PATH**：openclaw 是 node CLI、内部还要调 `node`，而 systemd service 的 PATH 不含 nvm 目录——所以调 openclaw 前必须把它所在目录（同目录就有 `node`）加进 PATH，否则 `message send` 在 service 里失败（手动 shell 因 source 过 nvm 才成功，极易误判"通了"）。已在 `flows/scan_fulfillment_alerts.py` 的 `send_feishu_message` 和 `deploy/notify-failure.sh` 内处理。
- **hp 用 systemd timer，不是 Prefect worker**：`prefect.yaml` 里的 deployments 仅文档性，没有 worker 在跑。新增定时任务要写 systemd unit（照 `data-sync-*` / `data-scan-alerts`），不是 `prefect deploy`。
- **`openclaw cron` 必经 LLM**：payload 只有 `--message`/`--system-event`，没有纯命令/HTTP 直投 job。要 0-LLM 投递只能走 `openclaw message send`。
- **`plugins.entries.feishu: plugin not installed`** 是无关的既有 warning（feishu 走 extension 不是 plugin），忽略。
- 收件人 open_id / 群 oc_ 来源：hp `~/.openclaw/openclaw.json` 的 `channels.feishu.accounts[].allowFrom`；查 ID 用 `openclaw directory`。

---

## E. 下一步

客户**自助订阅日报**（自然语言订阅，确定性写工具 `ops_subscribe_report`）见 [plan/11](../plan/11_self_serve_report_subscription.md)，待开发。
