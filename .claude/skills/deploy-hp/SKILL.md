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
- **前端 SPA（关键缺口）**：`deploy.sh` **只管后端，不 build 前端**。SPA 由 data-hub 从 `frontend/dist` 经 StaticFiles 挂在 `/app`；`frontend/dist` 不入库（被 gitignore），所以改了 `frontend/` 必须**单独在 hp 上 `npm run build`** 才生效，否则 `git pull` 后页面不变。hp 的 node v22 在 nvm 路径，非交互 shell 的 PATH **没有 npm**（同 uv 坑），须先 `export PATH="$(dirname $(ls $HOME/.nvm/versions/node/*/bin/node|head -1)):$PATH"`。详见 §2.5。
- **本地 push 代理坑**：本机 git 走 Clash，`fetch`/`push` 可能因 fake-IP（`198.18.x.x`）瞬时报 `Connection closed` 或显示 `Everything up-to-date`。**判定以 `git status -sb` 的 ahead/behind 为准**，不要被单条命令输出误导。
- **⚠️ `deploy.sh` 自更新滞后一轮**：`deploy.sh` 第一步 `git pull` 会更新脚本自身，但 bash 早已把**旧版脚本**读进内存在跑——所以本次部署跑的是**旧逻辑**，本次 pull 引入的**新 timer gate / 新跳过规则当次不生效，下一轮部署才生效**。踩过：2026-07-15 hp 补 19 提交时，旧 deploy.sh（无马帮 gate）把刚 pull 进来的 `data-sync-mabang-costs.timer` 误 enable 了（hp 无 `MABANG__USER` 本不该跑）。**应对**：pull 里含"新增/改了 timer gate"的提交时，部署后**核对新 gate 覆盖的 timer 状态**（`systemctl --user is-enabled <timer>`），必要时手动 `disable --now`；下一轮部署会自愈。**untracked 文件挡 pull**：hp 上若有与 origin 同名的 untracked 文件（如别处 `git show origin/main:<path>` 存在），`git pull` 报"未跟踪文件将被覆盖"并**整体中止**（deploy.sh 卡在第一步）——先 `diff` 确认内容后删/备份该 untracked 文件再重跑。

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
| 客户 skill / 人格文案：`openclaw-skills/**` 或 `openclaw-docs/{SKILL,AGENTS,SOUL}.md`、`references/api-contract.md` 等 | `--sync-skill` | **改了这些文档必带此 flag**，否则只更新后端、skill 没同步（最易漏）。同步后**必须去飞书发 `/new`** 重载（`/reset` 不够）。详见 §2.6 |
| `frontend/` SPA（页面/组件/`api.ts`） | （**另跑前端构建**，见 §2.5；**不**重启 web） | dist 不入库、由 StaticFiles 直接服务，build 完即时生效 |

典型代码修复（本次 P0 在 `core/db.py`）：`--pull --restart-web`。
全栈改动（后端 + 前端，如 plan17 渠道饼图）：`--pull --restart-web` + §2.5 前端构建。

### 2.5 前端构建（仅当改了 `frontend/`）

`deploy.sh` 不 build 前端。改了 SPA 必须在 hp 上重建 `frontend/dist`（StaticFiles 直接读盘服务，**无需重启 web**）：

```bash
ssh hp 'cd ~/code/crossborder-ops-data-hub/frontend \
  && export PATH="$(dirname $(ls $HOME/.nvm/versions/node/*/bin/node|head -1)):$PATH" \
  && npm install && npm run build'   # npm run build = tsc && vite build（tsc 兼当类型检查）
```

坑：hp 的 `frontend/package-lock.json` 偶有本机 build 残差挡 `git pull`，pull 前 `git checkout -- frontend/package-lock.json` 丢弃即可（远端 lock 权威，install 会重建）。

### 2.6 skill / 人格文案同步（仅当改了 `openclaw-skills/` 或 `openclaw-docs/`）

判定规则（**最易漏的一步**）：改动里**只要有** `openclaw-skills/**`（SKILL.md、`references/api-contract.md` 等）或 `openclaw-docs/{AGENTS,SOUL}.md`、`openclaw-docs/workspaces/<ws>/USER.md`，部署就**必须带 `--sync-skill`**。这些文件是 openclaw 读的、不是 data-hub web 进程读的，所以**纯文案改动不需要 `--restart-web`**，但不带 `--sync-skill` 就只 `git pull` 了仓库、workspace 里的实文件没更新（openclaw 拒绝软链，必须实文件，见 `scripts/sync-skill.sh` 注释）。

```bash
ssh hp 'cd ~/code/crossborder-ops-data-hub && ./deploy/deploy.sh --pull --sync-skill'
# 验证两 workspace 一致：
ssh hp 'cd ~/code/crossborder-ops-data-hub && ./scripts/sync-skill.sh --check'   # 全 ✅ = 已同步
```

**同步后必须去飞书对应对话发 `/new`** 才重载新 SKILL（`/reset` 只清上下文、不重载文件）。

**⚠️ hp 与 prod 的 workspace 不同**（`sync-skill.sh` 的 `WORKSPACES` 写死 `workspace-ecom` + `workspace-ecom-gtl`）：

| 机器 | 存在的 ecom 类 workspace | 同步行为 |
|------|------------------------|---------|
| **hp** | `workspace-ecom`（内部测试）+ `workspace-ecom-gtl`（客户） | 两个都同步，验证发 `/new` 各发一次 |
| **prod** | **只有 `workspace-ecom-gtl`**（无 ecom） | 同步时打印 `⚠️ 跳过 workspace-ecom：目录不存在`——**这是正常的良性跳过**，不是失败；prod 验证只在 ecom-gtl 客户对话发 `/new` |

`sync-skill.sh` 对不存在的 workspace 是安全跳过（`[[ ! -d ]]` 分支），所以同一脚本在两台都能跑、不用改列表。

### 3. 验证

**web 健康**：
```bash
ssh hp 'systemctl --user is-active data-hub.service && journalctl --user -u data-hub.service -n 8 --no-pager'
```
期望 `active`，日志末尾见 `Application startup complete` + `Uvicorn running on http://127.0.0.1:8000`。

**前端构建生效（改了 `frontend/` 才需）**：确认对外 index 引用的是新 build 的 bundle hash：
```bash
ssh hp 'curl -s http://127.0.0.1:8000/app/ | grep -oE "assets/index-[A-Za-z0-9_-]+\.js"'
```
与 build 输出的 `dist/assets/index-*.js` 文件名一致 = 新前端已上线（hash 可能含下划线，匹配时带 `_`）。

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
