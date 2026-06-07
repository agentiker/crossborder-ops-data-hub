---
status: draft
owner: codex
depends_on: [08a_feishu_menu_text]
---

# 08b 飞书菜单事件持久化（后做）

## Context（为什么后做）

08a 用文字消息打通了范围解析链路，但有个明确缺口：**没有跨消息的持久 binding**——
用户每次新会话都要重选范围，或者每条消息都带范围词。

08b 引入飞书机器人菜单**事件回调**机制：用户点击菜单按钮 → 飞书推送事件到独立 webhook
→ 写入 `conversation_scope_bindings` 表 → openclaw 下次收到该用户消息时，skill 查 binding，
**默认就是上次选的范围**。

## 关键架构约束（基于服务器实证）

1. **openclaw 走 WebSocket 模式**接飞书消息，但 `lark-cli` 事件总线**不支持** `im.chat.menu.bot_menu_clicked`
   ——菜单事件**进不了 openclaw 链路**。
2. 因此**必须独立部署一个 Feishu webhook 服务**接菜单事件。
   该 webhook 与 openclaw 互不干扰，只共享数据库。
3. openclaw 已经把 `sender_id`（`ou_xxx`）注入 system prompt 的 trusted metadata。
   **agent 知道当前用户的 open_id，直接拿来查 binding**，不需要 openclaw 改任何代码。

## 实现步骤

### 1. ConversationScopeBinding 表

```python
class ConversationScopeBinding(Base):
    __tablename__ = "conversation_scope_bindings"
    id = Column(Integer, primary_key=True)
    channel = Column(String(16), nullable=False, default="feishu")
    account_id = Column(String(64), nullable=False)   # ecom-app
    open_id = Column(String(64), nullable=False)      # 飞书用户 open_id
    scope_key = Column(String(64), nullable=True)     # None = 显式全量
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("channel", "account_id", "open_id",
                         name="uq_conv_scope_binding"),
    )
```

**为什么用 `open_id` 而不是 `chat_id`：**
- openclaw `dmScope: per-account-channel-peer`，每个用户独立 session，session key 编码 `open_id`。
- 飞书菜单事件回调里 `operator.open_id` 就是同一个 `ou_xxx`，**两边对得上**。
- 用 `chat_id` 也行（私聊 chat_id 形如 `user:ou_xxx`），但 `open_id` 更稳定（万一未来扩群聊也能复用）。

### 2. 独立 Feishu webhook 服务

新增 `web/routes/feishu.py`，挂载到 Data Hub FastAPI 下：

- `POST /webhook/feishu/menu`：接收飞书菜单事件
  - 校验 `verification_token`（飞书 v2 事件签名）
  - 处理 v2 URL 验证（challenge 请求）
  - 解析 `event.event_key` + `event.operator.open_id`（v2 schema）
  - 查 `FEISHU_MENU_SCOPE_MAP[event_key]` → `scope_key`
  - upsert `ConversationScopeBinding`
  - 调飞书 OpenAPI 给用户发确认消息（"已切换到 印尼 TikTok 全部店"）

**为什么不复用现有 X-Internal-Token：** 飞书 webhook 用飞书自己的签名机制，不应混淆。

### 3. event_key → scope_key 映射

在配置文件或环境变量定义（**不写死在代码里**）：

```python
FEISHU_MENU_SCOPE_MAP = {
    "scope_tts_id":    "tts-id-all",
    "scope_tts_latam": "tts-latam-all",
    "scope_tts_all":   None,           # None = 显式全量
}
```

飞书后台菜单配置时，每个按钮的 event_key 填上面的 key（与 08a 的中文按钮文字配套）。

### 4. skill 契约升级

`SKILL.md` 范围解析 SOP 增加一步**在 08a 解析顺序之前**：

1. **查会话默认 binding**：用 system prompt 的 trusted metadata 里的 `sender_id` 调
   `GET /api/data/scope/binding?open_id={sender_id}` → 拿到 `default_scope_key`。
   （新增 endpoint）
2. 文本里的菜单短语/NL 范围词如果出现 → 临时覆盖该次查询（不改 binding）。
3. 都没有 → 用默认 binding。
4. 默认 binding 也没有 → 全量。

**关键产品决策：** 用户点菜单是"改默认"，文字范围词是"临时覆盖"。
默认通过菜单管理（持久），临时通过文字（一次性）。

### 5. 新增 `GET /api/data/scope/binding`

```python
@router.get("/scope/binding")
async def get_scope_binding(open_id: str, channel: str = "feishu",
                            account_id: str = "ecom-app"):
    """查会话默认 scope 绑定（skill 在解析前调用）。"""
    binding = ... # 查 conversation_scope_bindings
    if not binding:
        return {"scope_key": None, "display_text": "未设置默认范围"}
    scope = resolve_filters(scope_key=binding.scope_key)
    return {"scope_key": binding.scope_key, "display_text": scope.display_text}
```

不挂在 `/scope_id=` 共享参数下，因为它是"取 binding"而非"用 binding 过滤数据"。

### 6. 飞书后台事件订阅配置

08b 范围外（你手动操作）：
- 飞书开发者后台 `事件与回调 > 事件配置`，订阅 `im.chat.menu.bot_menu_clicked`
- 回调地址填 webhook 公网入口（Data Hub 监听回环，需 nginx 反代 + HTTPS）
- 菜单按钮的响应动作从「发送文字消息」改回「推送事件」，每个按钮配 event_key

## 测试计划

- 飞书 webhook：模拟飞书事件（含 v2 签名）→ binding 表写入正确
- challenge：URL 验证返回正确响应
- skill 流程：点菜单 → binding 写入 → 再发"本周 GMV" → agent 自动用上次选的范围
- 覆盖：有 binding 但消息里写"只看 A 店" → 该次查询用 A 店，binding 不变
- 切换：点不同菜单 → binding 更新
- 边界：飞书事件重放/签名错误 → 拒绝

## Done When

- 飞书菜单点击 → webhook → binding 持久 → agent 默认用上次选的范围
- skill 文字解析（08a）作为临时覆盖仍工作
- agent 每条回答声明本次查询范围
- 飞书签名校验/challenge 正确处理

## 范围外

- 群聊菜单（飞书不支持）
- 自然语言深度解析（保持 08a 简单词表）
- openclaw 改造（不改）
- 多租户 → 09

## 决策记录

- **不用 openclaw 的 lark-cli 事件**：菜单事件不在 lark-cli 支持列表内
- **不写 openclaw memory 文件**：架构上 binding 是会话级业务状态，不属于 agent 长期记忆
- **不用 chat_id 作 binding 主键**：用 open_id 更稳定，未来扩群聊也能复用
