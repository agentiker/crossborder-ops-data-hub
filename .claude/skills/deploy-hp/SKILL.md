---
name: deploy-hp
description: Push 当前分支并部署到 hp 测试环境（yamk 服务器），含健康检查与多租户隔离回归验证。当用户说「部署到 hp / 测试环境」「push 并部署」「发测试」时使用。覆盖 push→deploy→verify 全流程，固化仓库路径/uv 路径/deploy.sh 参数语义等已知信息，避免每次重新探索。
---

# 部署到 hp 测试环境

Data Hub 跑在 yamk 服务器上，`ssh hp` 免密登录，systemd user timer 调度，`deploy/deploy.sh` 一键幂等部署。这个 skill 把「本地 push → hp 部署 → 验证」固化成确定性流程。

## 关键已知信息（不用再探索）

- **登录**：`ssh hp`，免密。
- **hp 仓库路径**：`~/code/crossborder-ops-data-hub`（= `/home/guopeixin/code/...`）。
- **uv 不在非交互 shell 的 PATH**：在 hp 上必须用全路径 `~/.local/bin/uv`，直接 `uv` 会报「未找到命令」。
- **web 服务**：`data-hub.service`（FastAPI），监听 `127.0.0.1:8000`，是**长驻进程**。
- **本地 push 代理坑**：本机 git 走 Clash，`fetch`/`push` 可能因 fake-IP（`198.18.x.x`）瞬时报 `Connection closed` 或显示 `Everything up-to-date`。**判定以 `git status -sb` 的 ahead/behind 为准**，不要被单条命令输出误导。

## 流程

### 1. 本地 push

```bash
git push origin main          # 或当前分支
git status -sb                # 确认 main...origin/main 无 ahead/behind = 已同步
```

若 push 报代理错误但 `git status -sb` 显示持平 → 实际已同步，继续。

### 2. hp 部署

```bash
ssh hp 'cd ~/code/crossborder-ops-data-hub && ./deploy/deploy.sh --pull <restart-flags>'
```

`deploy.sh` 幂等做：`git pull` → `uv sync` → `init_db`（只建不存在的表）→ 装 systemd units → `daemon-reload` + enable 所有 timer + `data-hub.service`。

**restart flag 按改了什么选**（脚本默认**不**重启任何对外服务）：

| 改了什么 | 加的 flag | 原因 |
|---------|----------|------|
| `core/` `web/` `services/` 等 web 进程加载的代码 | `--restart-web` | 长驻进程不会自动加载新代码 |
| 只改 `flows/` 定时任务代码 | （无） | timer 每次新起 python 进程，自动拿新代码 |
| openclaw plugin 文案 | `--restart-gateway` | 重载 gateway |
| cloudflared / 看板隧道 | `--restart-tunnel` | |
| 客户 skill 文案 | `--sync-skill` | 同步后客户飞书 `/new` 重载 |

典型代码修复（本次 P0 在 `core/db.py`）：`--pull --restart-web`。

### 3. 验证

**web 健康**：
```bash
ssh hp 'systemctl --user is-active data-hub.service && journalctl --user -u data-hub.service -n 8 --no-pager'
```
期望 `active`，日志末尾见 `Application startup complete` + `Uvicorn running on http://127.0.0.1:8000`。

**回归测试**（在 hp 上跑实际部署的代码）：
```bash
# 多租户隔离回归（ORM lambda 固化 P0 的命门，必跑）
ssh hp 'cd ~/code/crossborder-ops-data-hub && ~/.local/bin/uv run pytest tests/test_tenant_filter.py -v 2>&1 | tail -25'

# 或全量
ssh hp 'cd ~/code/crossborder-ops-data-hub && ~/.local/bin/uv run pytest -q 2>&1 | tail -15'
```
`test_switching_tenants_same_session_no_fixation` 必须 PASS —— 它在同一 session 连切两租户，专抓 lambda 缓存固化导致的跨租户泄漏。详见 memory `multitenant-orm-filter-lambda-fixation`。

## 不做 / 注意

- **日报 openclaw cron** 不在 deploy.sh 里，手工配，见 `docs/proactive-push-ops.md` B 节。
- 重启对外服务（web/gateway/tunnel）属 outward-facing，仅在确实改了对应代码时显式加 flag。
- 生产环境是另一回事，这个 skill 只覆盖 hp 测试环境。
