# 飞书机器人 Onboarding 文案

用户进入与机器人的 1v1 会话时，机器人主动 push 一条欢迎消息，给出能力清单、问法示例和已知限制。

## 触发事件

参考：<https://open.feishu.cn/document/group/chat-member/event/bot_p2p_chat_entered>

- **事件名**：`im.chat.access_event.bot_p2p_chat_entered_v1`
- **Schema**：`2.0`（事件名里的 `_v1` 是事件本身版本，不是 schema）
- **应用类型**：自建应用
- **触发时机**：用户进入与机器人的 1v1 会话时（不是仅首次！见下"去重"）
- **客户端版本要求**：用户端 ≥ **v7.18**，更早版本不触发
- **权限要求**：订阅事件本身**无需权限**；payload 字段中 `union_id` / `open_id` 默认返回，`user_id` 需要 `contact:user.employee_id:readonly` 才返回
- **限流**：单个机器人推送事件超 **300/秒** 会被丢弃；无重试 / 去重语义
- **响应模式**：fire-and-forget。Webhook 回 200 是给飞书的 ack，**不会**变成用户能看到的消息。要发文案必须**主动调** `im/v1/messages`，用 `chat_id` 或 `operator_id.open_id` 作为 receiver。

兼容入口（建议同时支持）：

- 用户发 `/start` → 回同一份文案（telegram 风格手动唤起；也是用户错过 onboarding 后自救的入口）

### 关键去重要求

**事件每次进入会话都会触发**——用户每点开聊天窗口都会再发一次。如果不去重，用户会被反复刷 onboarding。

落地建议：

- 服务端维护 `feishu_user_onboarded` 标记（`open_id` 或 `union_id` 为 key），第一次进入时发文案、入库；之后命中标记直接 ack 不发消息。
- 或者：每用户 24h 最多发一次（更宽容，给客户长时间不用后再次进入时的提醒机会）。
- `/start` 路径**始终发**——这是用户主动请求，不去重。

### Payload 形状（精简）

```json
{
  "schema": "2.0",
  "header": {
    "event_id": "...",
    "event_type": "im.chat.access_event.bot_p2p_chat_entered_v1",
    "create_time": "1608725989000",
    "token": "...",
    "app_id": "cli_...",
    "tenant_key": "..."
  },
  "event": {
    "chat_id": "oc_413871888e0d5492e25b173f0812efb7",
    "operator_id": {
      "union_id": "on_8ed6aa67826108097d9ee143816345",
      "user_id": "e33ggbyz",
      "open_id": "ou_84aad35d084aa403a838cf73ee18467"
    },
    "last_message_id": "om_dc13264520392913993dd051dba21dcf",
    "last_message_create_time": "1615380573411"
  }
}
```

- 发消息时优先用 `event.chat_id` + `receive_id_type=chat_id`，或 `event.operator_id.open_id` + `receive_id_type=open_id`。
- 去重 key 用 `event.operator_id.open_id`（稳定且必返回）；不要用 `user_id`（需额外 scope）。
- `last_message_id` 可在场景需要时拉历史最新一条（普通 onboarding 用不上）。

收到事件后，按下面文案给该用户发**一条** Feishu 私聊消息。文案已按 Feishu 私聊渲染约束写好（emoji + 粗体行 + 短列表，无 Markdown 表格，无 `### / ##### ` 多级标题）。

## 文案（中文，直接复制）

```text
👋 你好，我是 **Adis**，跨境电商数据化运营顾问。
我可以帮你查印尼 TikTok Shop 的库存、商品、GMV、订单、销量与趋势。

✨ **我能查什么**
- 📦 **库存 / 低库存 / 缺货**：例 "印尼有哪些低库存"、"哪些 SKU 快断货了"
- 🛍️ **商品目录 / 上下架**：例 "印尼有多少在售商品"、"哪些商品下架了"
- 💰 **GMV / 订单数 / 销量**：例 "本周 GMV"、"印尼最近 7 天卖了多少单"
- 📈 **销售趋势**：例 "近 7 天 GMV 趋势"、"最近 3 天每天卖了多少"
- 🔥 **爆款单品榜**：例 "最近 7 天哪个 SKU 卖得最好"

💬 **怎么用**
直接发问题就行，例如："印尼库存"、"本周 GMV"、"印尼最近 7 天爆款"。
我会先说明本次查询的范围和时间窗口，再给结论 + 数据 + 建议。

🌏 **切换查询范围**
- 发送「**印尼**」→ 后续查询默认看印尼 TikTok 全部店
- 发送「**全部**」→ 看所有已授权店
- 发送「**有哪些范围**」→ 我列出可选的业务范围
- 也可以直接在问题里带范围，例 "印尼最近 7 天 GMV"
- ⚠️ 本期范围不会跨消息保存，每条消息建议带范围词；下个版本会持久化。

⚠️ **本期还不能回答**
- **利润 / 毛利 / ROI / 广告花费**：等结算 + 广告 + 商品成本数据接入后开放
- **利润 / ROI 类正式告警**：本期没有；不过**经营日报**我会每天早上主动推、**待发货超时**有风险也会主动提醒。对话里我也能基于库存/订单给"观察到的疑似风险"（非平台告警）
- **任何写操作**：不能补货、调价、下架、改库存——只读分析

📐 **数据口径**
- GMV = 买家实付总额（含运费/税/优惠后），**不是**平台最终结算金额
- 已付款订单：paid_time 不为空，排除未付款 / 已取消
- 库存：每次同步后的当前快照，无历史趋势
- 数据来源：TikTok Shop 官方 Open API

🔄 **小命令**
- `/reset` — 清空上下文 + 重新加载我的指令（回答跑偏了就发这个）
- `/start` — 重新看一遍本指引

有问题直接发就行，我会按上面的范围、口径、限制如实回答。
```

## 字符规模

- 总长约 880 字（含 emoji），单条飞书私聊消息上限远超此，安全。
- 渲染：粗体 `**...**`、bullet `- `、emoji 在飞书私聊正常渲染；行内 `` ` `` 包裹的命令显示为等宽字体。

## 实现注意

- **必须主动调 IM API 发消息**——webhook 返回 200 只是给飞书的 ack，不会变成用户消息。调 `POST /open-apis/im/v1/messages?receive_id_type=chat_id`，body 类似：
  ```json
  {
    "receive_id": "<event.chat_id>",
    "msg_type": "text",
    "content": "{\"text\":\"👋 你好...（onboarding 文案）\"}"
  }
  ```
  注意 `content` 是**字符串化的 JSON**，不是对象。
- **必须做去重**——bot_p2p_chat_entered_v1 每次进入会话都触发，不去重客户每点开就被刷一次。最小落地：内存 / Redis / DB 表 `feishu_user_onboarded(open_id, first_seen_at)`，命中即 ack 不发；只有 `/start` 路径绕过去重。
- **不要**用 `### 标题` 或 `| col | col |` 表格——飞书私聊会显示原始 `#` 和 `|---|` 字符（已在 SKILL.md / SOUL.md 写明）。
- 如果 openclaw 在 bot_p2p_chat_entered_v1 事件路径上**会再走一次 LLM**（把事件当用户消息塞进 agent），那是错的——必须配置成"事件直接触发固定文案发送"，不要让模型基于事件 payload 重新生成回复（模型很可能去查数据 API、跑 SKILL.md 决策树，毁掉欢迎语）。
- 推荐用 `msg_type=text` 直接发，避免 card JSON 2.0 + cardkit 权限纠缠（之前 cardkit:card:write 缺失就栽过）。
- 如果想换成卡片版（带按钮"印尼 / 全部 / 有哪些范围 / 库存 / 本周 GMV"），点击按钮发对应短语——比纯文字 onboarding 转化率更高，但要先确认 cardkit 权限到位。
- 鉴权用应用的 `tenant_access_token`（非 user token）；建议加 token 缓存 + 自动续期（飞书 token 有效期 2 小时）。
- 验证 webhook 请求时检查 `header.token` 是否等于配置的 verification token，防伪造。

## 后续可加

- 用户发 `/help` 时回简化版（只列 5 类查询 + /reset）
- 多语言（如果团队里有非中文用户）
- 按 scope 自动切换问候语（默认范围已是印尼时，开场改"我现在默认看你的**印尼 TikTok 全部店**"）

## 已知限制（2026-06-07）

1. **`/start` 失败**：模型对 `/start` 有强烈的 telegram bot welcome 训练偏好，看到这个 trigger 会自由生成英文 welcome 模板，prompt 强化无效。主推入口用 `操作指引` / `指引`。
2. **LLM verbatim 不稳定**：即使 SKILL.md 写明"逐字复制 + 多条硬约束 + 错误示范"，模型仍有概率不听话——把 8 行文案重新组织成 30 行自由发挥版，把不可用的"告警"等功能当能力宣传。**两次同样的输入可能得到不同结果**。模型上限问题，不可能 100% 修复。
3. **要真正稳定**只能绕开 LLM：
   - 短期：openclaw 加 "static reply per skill" 配置（trigger 字符串 → 直接发某段固定文本，跳过 LLM）
   - 中期：独立飞书 SDK 长连接 client 订阅 `p2p_chat_create`，靠 IM API 发固定文本（但要先验证两个 SDK 不会互抢消息）
4. **`/reset` 不重载 SKILL.md**——只清上下文。要拿到新 SKILL.md 必须 `/new` 开新会话。
5. **openclaw 拒绝软链 skill**：log 关键字 `Skipping escaped skill path outside its configured root: reason=symlink-escape`。skill 必须是 workspace root 内的实文件副本，通过 `./scripts/sync-skill.sh` 同步。
