# 主动推送运维：日报 + 待发货超时告警

> 2026-06-13 上线。两条**主动推送**链路的生产部署 / 改配置 / 排错口径。
> 服务器 `yamk`（`ssh hp`），与数据同步一样**用 systemd user timer，不走 Prefect server**。

## 0. 两条链路一图

| 链路 | 过 LLM？ | 调度 | 投递 | 配置在哪 |
|------|---------|------|------|---------|
| **日报** | ✅ 过（要解读+建议） | **openclaw cron**（agentTurn） | cron `announce` 自动发飞书 | 在 hp 用 `openclaw cron` 命令 |
| **待发货超时告警** | ❌ 不过（确定性） | **systemd timer** `data-scan-alerts` | `openclaw message send` 直投 | repo 代码 + service 文件 |

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

## A. 待发货超时告警（systemd timer，Data Hub 侧）

### A1. 部署了什么

| timer | 跑的 flow | 周期 |
|-------|-----------|------|
| `data-scan-alerts.timer` | `flows.scan_fulfillment_alerts` | 每 30 分钟 `*:10,40` |

- 代码：`services/fulfillment_alerts.py`（判定+文案+去重，纯函数）、`flows/scan_fulfillment_alerts.py`（编排+投递）、`models.FulfillmentAlertState`（去重游标表）。
- 去重规则：超时单数 `overdue` **从 0→非0 或较上次上报增加**才推；持平/下降不复读；清零归零游标。
- 静默：默认 `23:00–08:30 Asia/Shanghai` 不推（`core/config.alert_quiet_*`）。

### A2. 首次部署步骤（hp）

```bash
ssh hp
cd /home/guopeixin/code/crossborder-ops-data-hub
git pull

# 1) 建去重表（create_all 只建不存在的表，安全幂等）
/home/guopeixin/.local/bin/uv run python -c "from core.db import init_db; init_db()"
/home/guopeixin/.local/bin/uv run python -c "from core.db import engine; from sqlalchemy import inspect; print('fulfillment_alert_state' in inspect(engine).get_table_names())"  # 应 True

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
| 临界阈值（小时） | `.env` `FULFILLMENT_WARNING_HOURS`（默认 24） |
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

```bash
read -r -d '' PROMPT <<'EOF'
请生成今日印尼 TikTok Shop 经营日报，依次查询并整理：
1. 经营概览：库存总量/低库存数、近7天 GMV/订单/销量/客单价
2. 7天趋势：按天 GMV/订单/销量，点出关键变化
3. 待发货风险：待发货总数、已超时/临界单数、重点店铺
4. 低库存 Top：快断货的 SKU
5. 近7天爆款 Top
末尾给 3 条以内运营建议。数据口径以接口 caliber 字段为准如实复述。利润/ROI 本期未上线不要编。
【重要】直接输出完整日报全文作为回复，系统会自动投递，不要用 message 工具，不要只回确认语。
EOF

openclaw cron add --name "<客户> 每日经营日报" \
  --agent <agentId> --account <accountId> \
  --cron "30 8 * * *" --tz Asia/Shanghai \
  --announce --channel feishu --to user:<open_id> \
  --message "$PROMPT"
```

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
