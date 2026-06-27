---
status: active
owner: codex
depends_on: [07_scope_foundation]
---

# 08a 飞书菜单文字解析（先做）

## Context（为什么先做这个）

服务器实证发现：
- openclaw 的飞书菜单按钮**已经配成「发送文字消息」模式**（session 里收到的是纯文本 `tk-印尼`）。
- openclaw 每条消息自动把 `sender_id` / `chat_id` / `channel` / `account_id` 注入 system prompt 的 trusted metadata 块，**agent 已经知道用户身份**，无需 `session_status` 等工具。
- `lark-cli` 事件总线**不支持** `im.chat.menu.bot_menu_clicked`，菜单事件走不了 openclaw 现有链路。
- ecom agent 当前 **没有安装 `crossborder-ops-data` skill**，靠 AGENTS.md 让 agent 直接 `curl` Data Hub。
- 服务器上 Data Hub **尚未部署运行**（127.0.0.1:8000 无服务）。

因此**先用「菜单按文字消息触发」打通整条链路**，零额外接入成本，把部署、skill 安装、范围解析这些前置工程一次做完。**菜单事件回调持久化**留给 08b。

## 菜单文字约定

飞书菜单支持二级结构。上级菜单 `TikTok`，下级菜单是国家/地区。当前测试值 `tk-印尼` 只是占位，08a 统一改为简短中文：

| 上级 | 下级（按钮文字 = 发送内容） | 解析后 |
|---|---|---|
| TikTok | `印尼` | scope_key = `tts-id-all` |
| TikTok | `拉美` | 触发追问（需先建拉美 scope） |
| TikTok | `全部` | 不限 scope（全量） |
| （上级独立） | `切换范围` 等运维短语 | 列出可用 scope |

按钮发送的就是纯中文词，不带前缀，因为 `TikTok` 已经在上级菜单里语义化了。**这是约定，不是技术限制**——按钮配什么文字、agent 怎么解析，都是配置。

## 范围解析规则

收到用户消息时，按下面顺序判断（确定性，不上 LLM 抽取）：

1. **菜单短语**：消息正文等于某个已配置短语（如 `印尼`/`拉美`/`全部`）→ 该消息**本身不是数据请求**，agent 答复"已切换到 印尼 TikTok 全部店，请问需要查什么？"（短语 → scope 的映射在 SKILL.md 里写死）。
2. **自然语言里嵌的范围词**：消息正文含 `印尼 TikTok` / `只看 A 店` / `拉美` → 当次查询用该范围（不改全局默认）。
3. **都没有**：本期没持久 binding（留给 08b），直接全量查询，回答首行声明"未限定店铺范围"。

### 词表（写在 skill 里，不写死在代码里）

```
平台 alias：TikTok / TTS / 抖音小店国际 → tiktok_shop
            虾皮 / Shopee → shopee
国家 alias：印尼 / Indonesia / ID → ID
            拉美 → 触发追问（覆盖一组国家，不擅自定）
菜单短语 → scope_key 映射（08a 期）：
    印尼 → tts-id-all
    全部 → 不传 scope（全量）
    拉美 → （暂无 scope，触发追问）
```

## 实现步骤

### 1. 部署 Data Hub 到服务器

服务器上 `127.0.0.1:8000` 当前无服务。需要：
- 把 repo 部署到服务器，安装依赖
- `init_db` 建好所有表（包括 07 的 `business_scopes`、`products`、scope `tts-id-all`）
- 用 systemd 或 supervisor 起 `python main.py --task web --no-reload`
- 服务器侧的 `platform_tokens` 也要回填真实 shop_id（同本地，shop_id=`7494691994496238970`）

### 2. 安装 skill 到 ecom workspace

`~/.openclaw/workspace-ecom/skills/` 目前不存在。把 `openclaw-skills/crossborder-ops-data/` 软链或复制过去：

```bash
mkdir -p ~/.openclaw/workspace-ecom/skills
ln -s /path/to/repo/openclaw-skills/crossborder-ops-data \
      ~/.openclaw/workspace-ecom/skills/crossborder-ops-data
```

确认 agent 能加载（看 `systemPromptReport.injectedWorkspaceFiles` 或重启会话后 trajectory）。

### 3. ecom agent 加 `http_get` 工具

当前 agent 只有 `exec`，靠 `curl`。给 ecom workspace 加 `http_get` 工具（openclaw 标准 tool），避免 agent 误用 shell 或暴露 token。
（如果 openclaw 默认就有 `http_get`，跳过这步——查文档/配置确认。）

### 4. skill 契约升级

`openclaw-skills/crossborder-ops-data/SKILL.md` 加入：

- **请求规则**新增 `scope_id` / `shop_ids` 参数说明，告诉 agent 透传。
- **范围解析 SOP**（新增章节）：
  - 列菜单短语 → scope_key 表（写死可枚举的映射）
  - 列 alias 词表
  - 解析顺序：菜单短语 → NL 范围词 → 默认（全量）
  - 当短语命中："本消息是范围切换指令，回复 `已切换到 {display_text}，请问需要查什么？`，不调用 Data API"
- **回答首行声明范围**（用 Data API 返回的 `scope` 字段，如 `查询范围：TikTok Shop / 印尼 / 1 个店铺`）。
- **拉美追问 SOP**：触发追问询问具体国家。

`references/api-contract.md` 同步补 `scope_id` / `shop_ids` 参数、`scope` 响应字段。

### 5. 飞书菜单按钮文案

08a 范围外（这是飞书后台配置，你手动改），但建议你按上面表格的中文短语调整：`tk-印尼` → `印尼`，加 `拉美`、`全部` 兄弟项。

### 6. 服务器侧 scope 配置

在服务器侧 `business_scopes` 表创建 `tts-id-all`（同本地）。`tts-latam-all` 暂不建，等真有拉美店再配。

## 测试计划

- 部署后：服务器 `curl -H "X-Internal-Token: $TOKEN" http://127.0.0.1:8000/api/data/orders/summary?scope_id=tts-id-all` 返回正确数据。
- agent 链路：飞书发"印尼"→ agent 回复"已切换到 TikTok Shop / 印尼 / 1 个店铺，请问需要查什么？"。
- agent 链路：飞书发"印尼本周 GMV" → agent 单次解析为 scope=tts-id-all + 本周窗口，调 `/api/data/orders/summary?scope_id=tts-id-all&start_date=...&end_date=...`，回答首行声明范围。
- agent 链路：飞书发"本周 GMV"（无范围词）→ agent 调全量，回答首行声明"未限定店铺范围"。
- agent 链路：飞书发"拉美" → agent 触发追问。

## Done When

- 服务器上 Data Hub 服务起来，端点可调用。
- ecom agent 加载了 crossborder-ops-data skill。
- 飞书菜单按钮文案调整为中文短语。
- 用户点菜单 / 发文字都能在当次对话里正确解析范围。
- 回答首行声明范围。

## 范围外

- **菜单按钮事件回调持久化** → 08b（绑定状态跨消息持久）。
- 拉美 scope 实际创建（等接入拉美店）。
- 多租户隔离 → 09。

## 已知限制（明确告知用户）

08a 期内**无持久 binding**：用户**每次新对话**都要重新点菜单或在消息里带范围词；
否则默认全量。这个限制由 08b 解决。
