# openclaw 网关配置手册（飞书接入 + 多租户 agent + MCP + 人格）

openclaw 是独立的 node 项目，是**客户对话 / 飞书推送 / 飞书登录**的网关，也是监控告警直投飞书的出口。
本仓库的 `production-deployment.md` §4.6 只负责把它装上，**全部配置细节在本文**。

> 实证基准：prod 机 openclaw `v2026.6.10`，node `v22.23.1`，两租户（main-app 运维值守 + ecom-app-gtl 客户）。
> 文中 `cli_xxx` / secret / token 一律占位，真实值见各机 `~/.openclaw/` 与密码管理器，**勿入库**。

配置文件总览（都在部署用户 `~/.openclaw/`）：

| 路径 | 作用 |
|---|---|
| `openclaw.json` | 主配置：channels / agents / bindings / mcp / models / plugins / env / gateway |
| `credentials/lark.secrets.json` | 飞书 app secret（被 openclaw.json 以 ref 引用，单独存） |
| `workspace/`、`workspace-<agent>/` | 各 agent 的人格 + 记忆（每轮对话加载） |
| `~/.config/systemd/user/openclaw-gateway.service` | 常驻拉起单元 |

---

## 1. 安装

```bash
# node 走 nvm（scripts/setup-server.sh 第 8 步已自动装）
export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"
nvm install --lts            # prod 实测 v22.23.1
npm install -g openclaw
command -v openclaw           # 记下绝对路径，写进 Data Hub .env 的 OPENCLAW_BIN
```

飞书接入用**飞书官方插件 `@larksuite/openclaw-lark`**（功能比 openclaw 内置 feishu 插件多），
在 `openclaw.json` 里启用、禁用内置 feishu：

```jsonc
"plugins": {
  "entries": {
    "feishu":        { "enabled": false },   // 内置，禁用
    "openclaw-lark": { "enabled": true  },   // 飞书官方插件，启用
    "crossborder-onboarding": { "enabled": true }  // 本仓库自带 /start 引导插件
  },
  "allow": ["crossborder-onboarding", "openclaw-lark"],
  "load": { "paths": ["/home/<user>/code/crossborder-ops-data-hub/openclaw-plugins/crossborder-onboarding"] }
}
```

> 关键：官方插件**复用同一套 `channels.feishu.accounts` 配置 schema**（appId + appSecret ref +
> defaultAgent + allowFrom），换插件不换 channel 结构。下面 §2 的 channels 配置对两种插件通用。

---

## 2. openclaw.json 逐块详解

### 2.1 channels.feishu.accounts —— 每个飞书 app 一个 account

```jsonc
"channels": {
  "feishu": {
    "accounts": {
      "main-app": {                                   // ← account 键 = Data Hub 的 account_id 维度
        "appId": "cli_xxx",                           // 飞书 app client_id
        "appSecret": { "source": "file", "provider": "lark-secrets", "id": "/lark/main/appSecret" },
        "defaultAgent": "main",                       // 该 app 默认路由到哪个 agent
        "allowFrom": ["ou_xxx"]                       // 白名单 open_id（account 级鉴权，覆盖顶层）
      },
      "ecom-app-gtl": {
        "appId": "cli_yyy",
        "appSecret": { "source": "file", "provider": "lark-secrets", "id": "/lark/ecom-gtl/appSecret" },
        "defaultAgent": "ecom-gtl",
        "allowFrom": ["ou_yyy"]
      }
    }
  }
}
```

- `appSecret` **不写明文**，用 ref 指向 `lark.secrets.json` 里的路径（§3）。
- `allowFrom` 是 account 级白名单：只放行列出的 open_id。顶层 `feishu.allowFrom` 留空会刷一条无害 warning（见 §6）。

### 2.2 agents —— defaults + 每个 agent 的 workspace/model

```jsonc
"agents": {
  "defaults": {
    "workspace": "/home/<user>/.openclaw/workspace",   // main 用默认 workspace
    "model": { "primary": "glm/glm-5.2" }
  },
  "list": [
    { "id": "main", "name": "通用助手", "model": { "primary": "glm/glm-5.2" } },
    { "id": "ecom-gtl", "name": "ecom-gtl",
      "workspace": "/home/<user>/.openclaw/workspace-ecom-gtl",   // 客户 agent 独立 workspace
      "model": { "primary": "glm/glm-5.2" } }
  ]
}
```

> 模型 `glm/glm-5.2` 的 provider 定义在 `models`（§2.5），**不是** per-agent 内嵌 apiKey。

### 2.3 bindings —— 把 account 路由到 agent

```jsonc
"bindings": [
  { "agentId": "ecom-gtl", "match": { "channel": "feishu", "accountId": "ecom-app-gtl" } }
]
```

main 走 account 的 `defaultAgent` 即可，不必额外 binding；客户 agent 显式绑定更清晰。

### 2.4 mcp.servers.data-hub —— 接 Data Hub 的 13 个 ops_* 工具

```jsonc
"mcp": {
  "servers": {
    "data-hub": {
      "url": "http://127.0.0.1:8000/mcp",
      "transport": "streamable-http",
      "headers": { "X-Internal-Token": "${DATA_HUB_TOKEN}" }   // 运行时从 env.vars 解析
    }
  }
},
"env": {
  "vars": {
    "DATA_HUB_URL": "http://127.0.0.1:8000",
    "DATA_HUB_TOKEN": "<= Data Hub .env 的 API__INTERNAL_TOKEN，必须完全一致>"
  }
}
```

验证：`openclaw mcp probe data-hub` 应列出 13 个 `ops_*` 工具。
> Data Hub web（`data-hub.service`，端口 8000）必须常驻；它与（过审前停掉的）13 个 `data-*` 同步 timer 无关。

### 2.5 models —— glm-5.2 走 anthropic 兼容中转站

```jsonc
"models": {
  "providers": {
    "glm": {
      "baseUrl": "https://api.agent0101.com",
      "api": "anthropic-messages",          // ⚠ Anthropic 协议，不是 openai
      "models": [{ "id": "glm-5.2", "name": "glm-5.2", "maxTokens": 1048576 }]
    }
  }
}
```

凭据走 `auth`（sqlite + paste-api-key），**不认 models.json 内嵌 apiKey**：

```bash
openclaw auth login            # 选 glm provider，粘贴 api key（与 Data Hub .env 的 LLM key 同源）
# auth.profiles 落 { "glm:manual": { "provider": "glm", "mode": "api_key" } }
```

> 三个 2026.6.10 新版强约束（local 能过、gateway 会挂，易误判，详见 §6）：
> provider 须在 `models.providers`；auth 用 sqlite paste-key；`anthropic-messages` 强制 `maxTokens>0`。

---

## 3. lark.secrets.json —— 飞书 app secret 单独存

```jsonc
// ~/.openclaw/credentials/lark.secrets.json（嵌套，键路径对应 channels 的 appSecret.id）
{ "lark": { "main": { "appSecret": "<main app secret>" },
            "ecom-gtl": { "appSecret": "<gtl app secret>" } } }
```

凭据**用管道搬运、不经对话**（避免明文进日志/上下文）：

```bash
# 从已有机器取某租户 secret 灌到新机（示例）
ssh oldhost 'jq -r ".lark.\"ecom-gtl\".appSecret" ~/.openclaw/credentials/lark.secrets.json' \
  | ssh newhost 'cat > /tmp/s && jq --rawfile s /tmp/s ".lark[\"ecom-gtl\"].appSecret=\$s|.s=null|del(.s)" ...'
# 实操更稳：手填或密码管理器拷贝，别让 secret 落进任何会话记录
```

---

## 4. workspace 人格

每个 agent 一个 workspace 目录，文件**每轮对话从磁盘加载**——改人格不必重启网关（改 `openclaw.json` 才要）。

| 文件 | 职责 |
|---|---|
| `IDENTITY.md` | 「我是谁」速记（名字 / 角色 / emoji / 语言）；可空，人设以 SOUL 为准（飞书惯例） |
| `SOUL.md` | 完整人设与行为基调（语气 / 红线 / 输出风格） |
| `USER.md` | 服务对象是谁（运维 / 店长 / 老板） |
| `TOOLS.md` | 环境/工具速查（prod main 写了全套服务器拓扑当 cheatsheet） |
| `AGENTS.md` | 行为准则、工作方式 |
| `HEARTBEAT.md` / `DREAMS.md` / `MEMORY.md` | 心跳 / autonomous / 记忆索引（按需） |

本项目两个人设：
- **main = 生产运维值守「哨兵」🛡️**（workspace/）：面向中国操作者(CST)，①解读 Data Hub 飞书告警 ②shell 管服务器（健康/日志/timer/隧道/部署）③运维记录。SOUL 核心＝稳字当头（改状态先确认）/不奉承直给/报警三段式/时区意识。profile=`coding`（有 shell/exec）。
- **ecom-gtl = 印尼 TikTok 数据化运营顾问「Adis」📊**（workspace-ecom-gtl/）：面向客户运营，先结论后事实/风险/建议。

---

## 5. 新租户上手 checklist（加一个客户）

openclaw 侧（本文）：
1. 飞书后台建/拿 app → `appId` + `appSecret`。
2. `lark.secrets.json` 加 `lark.<name>.appSecret`（§3，管道搬运）。
3. `openclaw.json` `channels.feishu.accounts` 加一项（appId + appSecret ref + defaultAgent + allowFrom 客户 open_id）。
4. `agents.list` 加 agent（id + `workspace-<name>` + model glm/glm-5.2）。
5. `bindings` 加 `{agentId, match:{channel:feishu, accountId:<account>}}`。
6. 建 `workspace-<name>/` 人格文件（拷一份模板改人设）。
7. `systemctl --user restart openclaw-gateway`，等 2s，`openclaw mcp probe data-hub` + 飞书私聊自测。

Data Hub 侧（见 production-deployment.md §5 飞书后台配置 + .env）：
8. `.env` 的 `FEISHU_OAUTH__APPS` 加该 account 的 app_id/app_secret；单客户独立部署再设 `TENANCY__DEFAULT_ACCOUNT` + `HOST_TO_ACCOUNT` 对齐。
9. 飞书后台：该 app 加 OAuth 重定向 URL（`https://<域名>/board/auth/feishu/callback`）+ 开 `contact:user.base:readonly` 发版。
10. 跑 `uv run python -m scripts.preflight` 确认三处租户对齐绿。

---

## 6. 拉起、维护、坑

### 拉起（systemd user 单元）

单元通常由 openclaw CLI 自动生成在 `~/.config/systemd/user/openclaw-gateway.service`（prod 实测 v2026.6.10 即如此）。
仓库内 `deploy/systemd/openclaw-gateway.service.example` 是参考模板（含具体 node 版本路径，故不被 deploy.sh 自动安装）：

```ini
[Service]
# %h=家目录；NODE_VER 替换成本机 node 版本目录名（command -v node 反推，如 v22.23.1）
ExecStart=%h/.nvm/versions/node/NODE_VER/bin/node %h/.nvm/versions/node/NODE_VER/lib/node_modules/openclaw/dist/index.js gateway --port 18789
Restart=always
RestartSec=5
Environment=NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt
Environment=PATH=%h/.nvm/versions/node/NODE_VER/bin:%h/.local/bin:/usr/local/bin:/usr/bin:/bin
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now openclaw-gateway.service
systemctl --user restart openclaw-gateway.service   # 改 openclaw.json 后；等 ~2s 再验
openclaw validate            # 校验配置
openclaw mcp probe data-hub  # 验 MCP（13 tools）
```

### 坑（本项目实测）

- **飞书 ws 一个 app 仅一实例**：两机同时连同一 app 会互踢（双持互踢）。迁移/换机时**先让新机连上（两 ws ready）→ 再停旧机配置**，把双持窗口压到最短。
- **glm provider/auth/maxTokens 三件套**（2026.6.10 新版强约束）：① provider 必须在 `models.providers`（旧版可 per-agent，新版不行）② auth 用 `openclaw auth login` 落 sqlite，**不认** models.json 内嵌 apiKey ③ `anthropic-messages` 强制 `maxTokens>0`（local 调试能过、gateway 必挂，最易误判为「key 错」）。
- **2 个无害 warning**：顶层 `feishu.allowFrom` 空（account 级 allowFrom 已覆盖鉴权）/ 内置 feishu 插件未装（已 disabled，用官方 lark）。新版校验器（2026.6.10）啰嗦、旧版（2026.5.27）不报——私聊正常即实证无害。可删 `plugins.entries.feishu` 消一条，需重启，不急。
- **改 openclaw.json 务必 restart + 等 2s 再接 ws**；只改 workspace 人格文件不必重启。
- 改配置前先备份：`cp ~/.openclaw/openclaw.json ~/.openclaw/openclaw.json.bak.before-<改动>`。

相关记忆：`prod-openclaw-glm-model-config`、`prod-deployment-status`、`agent-runtime-and-skill-mcp-architecture`、`proactive-push-daily-report-and-alerts`。
