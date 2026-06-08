# Data Hub MCP 接入（方案 C：FastAPI-as-MCP）

把 `/api/data/*` 只读端点在**同一个 FastAPI 进程**内暴露成 MCP 工具（`fastapi-mcp`，ASGI 进程内调用，无额外 HTTP 跳），供 openclaw 的 ecom agent 调用，取代旧的"让模型用 HTTP 工具拼 `GET /api/data/*`"方式。

- 工具传输：streamable-http，挂在 `http://127.0.0.1:8000/mcp`（`web/app.py` 里 `FastApiMCP(...).mount_http()`）。
- 暴露 7 个 live 工具：`ops_overview / ops_inventory / ops_products / ops_orders_summary / ops_orders_trend / ops_top_skus / ops_scopes`。`profit/summary`、`alerts`（503）**不暴露**。
- 鉴权：复用现有 `require_internal_token`。openclaw 在 MCP 请求头带 `X-Internal-Token`，fastapi-mcp 经 headers 白名单转发到底层依赖。token 不进 SKILL.md。

## 本地已验证（Level 1）

- `list_tools` 仅返回 7 个 `ops_*`（无 profit/alerts），工具参数无 `X-Internal-Token`。
- 带 token 调 `ops_scopes` / `ops_inventory` → 返回真实数据；不带 token → `401 无效的内部令牌`。
- 现有 `pytest` 全绿（24 passed）。

## 服务器部署（Level 2，在 yamk 服务器执行）

### 1. 拉代码 + 装依赖 + 重启 Data Hub
```bash
cd ~/code/crossborder-ops-data-hub
git pull                      # 切到本分支/合并后
.venv/bin/pip install -r requirements.txt   # 或 uv sync，装 fastapi-mcp
sudo systemctl restart data-hub
curl -s http://127.0.0.1:8000/health        # {"status":"ok"}
```

### 2. 自检 /mcp（先证 MCP 本身通，再配 openclaw）
```bash
curl -s -H "X-Internal-Token: <API__INTERNAL_TOKEN>" \
  http://127.0.0.1:8000/api/data/scopes | head   # 200 + 真实 scope，确认底层在
```
> /mcp 是 MCP 协议端点，curl 不便直接握手；底层 `/api/data/*` 通即可，MCP 层本地已验证。

### 3. 配置 openclaw 挂 MCP

> **重要前提（2026-06-08 实测 yamk 服务器，openclaw 2026.5.27）**：
> - feishu 工具**不是 MCP**——来自官方扩展 `~/.openclaw/extensions/openclaw-lark`（`@openclaw/feishu`）；openclaw.json 里的 `feishu_*` 只是 `tools.alsoAllow` 权限白名单。所以**没有现成 MCP 块可抄**。
> - `openclaw mcp list` 当前为空（一个 MCP server 都没配）。data-hub 是这台机器上**第一个** MCP server。
> - 这版没有 `openclaw mcp add`，用 **`openclaw mcp set <name> <json>`**。MCP server schema 已核实支持 `url` / `transport(sse|streamable-http)` / `headers`（`type:"http"` 会被规范化成 `streamable-http`）；`url` 允许 `http://` loopback。**无 per-server toolFilter**——不需要，工具白名单已在 fastapi-mcp 的 `include_operations` 做了（只暴露 7 个 `ops_*`）。

一条命令配好（`<TOKEN>` 换成服务端 `API__INTERNAL_TOKEN` 的真实值；openclaw.json 已是 600 权限）：
```bash
openclaw mcp set data-hub '{"url":"http://127.0.0.1:8000/mcp","transport":"streamable-http","headers":{"X-Internal-Token":"<TOKEN>"}}'
openclaw mcp list      # 应能看到 data-hub
```

- 生产只有 ecom 一个 agent → 全局 MCP 配置即可。
- ecom 当前 `tools.profile: "full"`（`["*"]`）→ 新工具 `ops_*` 自动被允许，无需改 alsoAllow。若**将来**把 ecom 沙箱到 `messaging` profile，需把 7 个 `ops_*` 加进 `tools.alsoAllow`。
- （无关的既有告警：`plugins.entries.feishu: plugin not installed` 是 feishu 扩展的提示，与本次无关。）

```bash
systemctl --user restart openclaw-gateway
```

### 4. 同步 SKILL.md
```bash
cd ~/code/crossborder-ops-data-hub
./scripts/sync-skill.sh         # 把仓库副本 rsync 到 ~/.openclaw/workspace-ecom/skills/
```

### 5. 飞书端验证（Level 2 验收）
飞书 ecom 对话发 `/new`（重载 SKILL.md），然后：

| 发送 | 期望 |
|---|---|
| `印尼库存` | 日志见 `ops_inventory` 工具调用 + 成功；回复带真实 SKU id + available_stock，首行 `查询范围：...` |
| `本周 GMV` | 调 `ops_orders_summary` |
| `近 7 天爆款` | 调 `ops_top_skus` |
| `有哪些范围` | 调 `ops_scopes` |
| `利润` | **无工具可调**，按 SKILL.md 回"本期未上线" |

看日志：
```bash
journalctl --user -u openclaw-gateway --since "2 min ago" | grep -iE 'ops_|mcp|tool'
```
**关键**：必须看到 `ops_inventory` 真实调用 + 真实数字（与 DB 一致），不再有 `web_fetch ... blocked` 或"无任何工具调用直接编数"。

## 排错
- **工具不出现**：transport 不匹配（openclaw `transport` 与 fastapi-mcp `/mcp` 实际传输不一致）。本实现是 streamable-http；若 openclaw 连不上可退回 `transport: "sse"` 并把挂载改成 `mount_sse()`（端点变 `/sse`）。
- **工具调用 401**：openclaw header 没带或 `${DATA_HUB_TOKEN}` 没解析到；确认 ecom env 有 `DATA_HUB_TOKEN` 且等于服务端 `API__INTERNAL_TOKEN`。
- **连不上 127.0.0.1:8000/mcp**：`data-hub` 服务没重启或没装 `fastapi-mcp`。

## 后续（不在本次范围）
- SKILL.md 完整瘦身：删意图路由散文决策树、请求构造 SOP 等，预计 532→150~200 行（本次只做了"指向 ops_* 工具"的最小重定向）。
- `references/api-contract.md` 补 MCP 工具映射（当前仍只描述 REST 门面，内容仍准确）。
