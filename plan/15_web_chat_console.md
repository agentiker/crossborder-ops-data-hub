# Plan 15 — Web 对话式控制台（仿 StoreClaw）+ 运营看板 + 角色权限三阶段

> 状态：**Phase A 已实现 + 单测/冒烟通过（2026-06-17）**，待真实 LLM 凭证端到端验证 + 部署。详见末尾「Phase A 实现记录」。
> 关联：plan/13（飞书 H5 看板）、plan/14（独立看板站 + 统一权限闸 user_authz）、记忆 `feishu-h5-dashboard`、`agent-runtime-and-skill-mcp-architecture`、`scope-foundation`、`roi-roas-alert-data-source`。
> 决策（已与用户确认 2026-06-17）：**先做 Phase A 对话外壳**；**前端走独立 SPA（开发形态）+ 先同源托管（部署形态）**；**LLM 走可配置 Provider 层（国外/国内均可，换 base_url+api_key+model）**；接受新建会话表；B/C 后续迭代。

---

## 一、调研结论（storeclaw.ai，已对抗式核验）

调研规模：5 角度 / 20 来源 / 93 声明 → 25 条核验 → **22 确认、3 证伪**。综合步因网关 503 失败，结论由本人据已确认事实补全。

### 产品本体
- StoreClaw Inc. 出品，自称 "The First AI Growth Engine"，定位"一组会卖货的 agent"而非聊天机器人，跨平台自主运营店铺。〔[storeclaw.ai](https://www.storeclaw.ai/)〕〔[producthunt](https://www.producthunt.com/products/storeclaw)〕
- 跨境定位明确：统一 Amazon / Shopify / TikTok Shop 运营。〔[pandaily](https://pandaily.com/storeclaw-ai-cross-border-ecommerce-platform)〕
- **四大模块**：LLM Chat（对话操作店铺）+ Skills（自动化专家）+ Connectors（全渠道同步）+ Schedule（自主运营）。单一工作台，宣称 20+ 连接器、50+ skills。〔[storeclaw.ai](https://www.storeclaw.ai/)〕〔[features](https://www.storeclaw.ai/features)〕

### 信息架构（要仿的重点）
- **5 大区**：Tasks、Skills、Connectors & Channels、Schedule、Accounts & Billing。〔[help-center](https://www.storeclaw.ai/help-center)〕
- **Tasks = 对话驱动执行** + 资产/文件库 + **Projects** 组织工作（会话挂在项目下、能产出文件资产，不是孤立气泡）。〔[help-center](https://www.storeclaw.ai/help-center)〕
- **Skill Hub**：浏览 / 激活 / 自建 / 安装第三方 Skill —— skills 是可被用户开关的能力单元。〔[help-center](https://www.storeclaw.ai/help-center)〕
- **plan → explain → approval-gate**：agent 给"带理由"的计划，用户审批后执行；细粒度权限——低风险自动跑、重大改动等人工复核。〔[producthunt](https://www.producthunt.com/products/storeclaw)〕

### 竞品 UI/功能模式（可借鉴）
- **TikTok Seller Assistant**：Seller Center 内右下角 sparkle 常驻 AI，"指引+数据+动作"合一。〔[socialmediatoday](https://www.socialmediatoday.com/news/tiktok-shop-rolls-out-new-tools-including-expanded-chatbot-access/812401/)〕
- **Conjura Owly / Polar Text-to-Dashboard / Nexscope**：自然语言问数 → 给答案+建议；NL prompt 直接生成看板；聊天里整合三方数据并可切模型。〔[conjura](https://www.conjura.com/blog/the-5-best-tiktok-shop-analytics-tools-for-ecommerce-brands)〕〔[nexscope](https://www.nexscope.ai/blog/ai-tools-for-tiktok-shop)〕
- **Dashboardly 三数据流**（对我们最有参考）：Shop API（订单/退款/结算/费用拆项）+ Ads API（广告花费按 SKU 关联、TACoS）+ 用户录入 ERP（COGS/头程/运营费）合一。印证 `roi-roas-alert-data-source` 的判断。〔[dashboardly](https://www.dashboardly.io/post/tiktok-shop-data-analytics-explained)〕

### 看板/权限设计参考
- 看板：arXiv 综述 144 个真实看板提炼 **8 组可复用设计模式**。〔[arxiv 2205.00757](https://arxiv.org/pdf/2205.00757)〕
- RBAC：WorkOS / Permit.io / Auth0 三篇多租户授权最佳实践（未逐条核验，设计读物）。

### ⚠️ 被证伪（勿采信）
1. "创始人 Steven Zhou / 总部洛杉矶"——3 票全否。
2. 首页"connection 即懂上下文 / never blank screen / 一句话触发多步工作流"——作首页硬声明时 2 票否，当**营销话术**看（features 页有列）。

---

## 二、我们的现状（不是从零）

| StoreClaw 模块 | 我们已有 | 差距 |
|---|---|---|
| LLM Chat | openclaw 对话端（飞书长连接） | **无独立 Web chat** |
| Skills | openclaw skill + `ops_*` MCP 工具 | 无可浏览/开关的 Skill Hub |
| Connectors | TikTok Shop 直连已通 | 单平台、未抽象 |
| Schedule | systemd timer（日报/告警 flow） | 无 UI |
| 运营看板 | `web/routes/board.py`：飞书 OAuth + boss/operator 权限闸 + `get_orders_trend` | 图表单一、HTML 内联 py |
| 角色权限 | `services/user_authz`（硬编码 boss/operator + allowed_scope） | 不可配置、无管理页 |

**现有后端取数端点 / MCP 工具**（`web/routes/data.py`，全部走 `_resolve_scope` 权限闸）：
`ops_overview` `/overview`、`ops_inventory` `/inventory`、`ops_low_stock` `/inventory/low-stock`、`ops_orders_summary` `/orders/summary`、`ops_top_skus` `/orders/top-skus`、`ops_orders_trend` `/orders/trend`、`ops_products` `/products`、`/fulfillments/pending`、`ops_scopes` `/scopes`。`profit/alerts` 仍 503（见 `mvp-data-api`）。

**关键工程现实**：`web/app.py` 把 HTML 内联在路由里返回，无独立前端。用户已定**走独立 SPA**。

**关键架构事实**（来自 plan/14）：`services/user_authz.py` 的统一权限闸已覆盖三处（看板 / 对话侧 ops_* / 主动推送）。Web chat 接进来即**第 4 处复用同一闸**，不重写权限判断。

---

## 三、总体架构（三阶段共用底座）

```
[独立 SPA 前端 (React+Vite+TS)]  ← 新建，与 FastAPI 同源或 CORS
   │  /api/chat (SSE 流式)        ← Phase A 新增：Web 端 agent loop
   │  /api/data/* (已存在)         ← Phase B 看板直接消费
   │  /api/admin/roles (新增)      ← Phase C 权限管理
   ▼
[FastAPI (web/app.py)]
   ├─ web/routes/data.py   →  _resolve_scope → services/scope_resolution
   ├─ services/user_authz  →  统一权限闸（boss/operator → allowed_scope）★第4处接入
   └─ 飞书 OAuth 登录 cookie (web/web_security.require_web_user, plan/14 已落)
```

**鉴权统一**：SPA 所有请求带 plan/14 的飞书 OAuth 登录 cookie；后端 `require_web_user` 拿 `open_id` → `user_authz` 夹紧范围。**Web chat 的 open_id 来自登录态（可信），不再由 agent 传参**（比 plan/13 签名链接更稳）。

**前端栈（独立 SPA 开发形态）**：Vite + React + TypeScript + Tailwind + shadcn/ui；图表用 ECharts（Phase B）；流式用原生 EventSource/fetch-stream。
**部署形态**：构建产物（dist）由 FastAPI `StaticFiles` 托管在 `/app/*`（**同源、免 CORS、复用 plan/14 飞书登录 cookie**）。将来要拆 CDN 再迁独立站（需配 CORS + cookie 跨域）。**先同源托管**。注：「独立 SPA」指前后端分离的开发形态，与「同源托管」不矛盾。

**LLM Provider 层（可配置，国外/国内通用）**：抽象统一接口（chat + 工具调用 + 流式），按 provider 分发：
- **OpenAI 兼容族**——一个适配器覆盖：国内 DeepSeek / 通义千问(Qwen) / 智谱 GLM / Kimi(Moonshot) / 豆包(火山方舟) / 百度千帆 / 硅基流动，国外 OpenAI。切换只换 `base_url + api_key + model`。
- **Anthropic Claude**——独立协议，单独适配器。
- 配置：`core/config.py` 新增 `LLMConfig`（provider / base_url / api_key / model / 可选 temperature、max_tool_steps），走 env。
- 注意：① **工具调用是 agent loop 命脉**，各家 tool-calling 成熟度不一，适配器层统一成内部 tool schema；② **网络现实**——国内模型从国内服务器直连无需代理，Anthropic/OpenAI 需出口可达（呼应 `tiktok-api-direct-connect` 网关坑），可配置正好按环境切；③ 默认 provider/model 实现时再定。

---

## 四、Phase A — Web 对话外壳（MVP，先做）

> 目标：浏览器打开 → 飞书登录 → 看到 StoreClaw 式三栏：左会话列表 / 中消息流 / 底输入框；输入"今天 GMV 多少"→ agent 调 `ops_*` 工具 → 流式回文字+表格。**范围按登录身份自动夹紧**（operator 越界查不到）。

**先不做（避免一步到位）**：Skill Hub、Projects/资产库、plan-approval 审批门、Schedule UI、Connectors 管理、对话内切换模型的 UI（后端 Provider 可配，但前端不做切换器）、写操作工具（只读问数）。

### A 后端：`/api/chat` agent loop
- 新增 `web/routes/chat.py`：`POST /api/chat`，入参 `{conversation_id?, message}`，SSE 流式回 token。
- **agent loop**：通过可配置 LLM Provider 层（见上节，默认值实现时定）跑"工具调用"循环：把现有 `ops_*` 端点包装成 tool schema（overview/inventory/low_stock/orders_summary/top_skus/orders_trend/products/fulfillments_pending）。工具执行 = **进程内直接调对应 `data.py` 端点函数**（仿 FastApiMCP 的 ASGITransport 思路，无额外 HTTP 跳），范围参数由 `user_authz` 夹紧后注入，**不暴露 scope/shop_id 给模型自由传**。
  - 复用点：与 openclaw 侧 MCP 工具同一批底层端点 + 同一权限闸 → 对话两端口径一致。
  - 系统提示词：先复用/裁剪 openclaw ecom skill 的策略层文案（记忆 `agent-runtime-and-skill-mcp-architecture` 主张 skill=策略层），避免两套人格漂移。
- **会话存储**：新增表 `web_conversations` / `web_messages`（id, open_id, title, created_at / conv_id, role, content, tool_calls_json, created_at）。MVP 用现有 MySQL。
- 鉴权：`Depends(require_web_user)` 复用 plan/14 登录态；`open_id` 全程从 cookie 取。

### A 前端：SPA 三栏
- 新建 `frontend/`（Vite+React+TS）。路由 `/app/chat`（仿 storeclaw `/app/chat`）。
- 布局：左侧会话列表（新建/切换/重命名）、中间消息流（markdown 渲染 + 表格 + 流式打字）、底部输入框 + 发送。空态给"引导起点"快捷问句（呼应 storeclaw never-blank-screen，但只是预置按钮，非真上下文感知）。
- 登录拦截：未登录 → 跳 plan/14 的 `/board/auth/feishu` OAuth；登录后回 `/app/chat`。
- 顶栏显示当前身份 + 范围（boss=全部 / operator=锁定范围），呼应"范围按账号锁定"。

### A 验收
- 登录后能发问、流式收到答案；问"GMV/库存/待发货/Top SKU"等能正确调工具回数。
- operator 账号问超出 allowed_scope 的店铺 → 拿不到越界数据（与看板/推送一致）。
- 会话可新建/切换/持久化（刷新不丢）。
- 全量 `uv run pytest` 无回归；新增 chat 端点单测（鉴权、工具调用、越权夹紧）。

### A 主要风险
- agent loop 自建 vs 复用 openclaw：本阶段**自建轻量 loop**（Web 原生、不抢飞书长连接），代价是与 openclaw 两套 runtime——用"共用底层端点+权限闸+尽量共用提示词"控制漂移，写进记忆。
- LLM 直连/代理与白名单 IP（记忆 `tiktok-api-direct-connect` 的网关坑）：Anthropic API 出口需确认可达。
- 模型成本/延迟：MVP 限制单轮工具调用步数 + 选型可配置。

---

## 五、Phase B — 运营看板多图表（后续）

- 把 `web/routes/board.py` 内联 HTML 迁到 SPA 路由 `/app/board`；后端退为 `/api/data/*`（已存在）+ 必要聚合端点。
- 引 ECharts，按 arXiv 8 模式组织多卡片：GMV 趋势（已 `ops_orders_trend`）、订单/销量趋势、库存健康（`ops_inventory`+`ops_low_stock`）、待发货分桶（`/fulfillments/pending`，按记忆 `fulfillment-sla-field-semantics` 用 `tts_sla_time`）、Top SKU 榜（`ops_top_skus`）。
- 日期/范围切换复用 board 现有逻辑；operator 锁定范围。
- 远期接 Dashboardly 式利润/ROAS 卡片（依赖 Finance scope 授权，见 `roi-roas-alert-data-source`），本阶段不做。

## 六、Phase C — 角色权限可配置（后续）

- `services/user_authz` 的硬编码 boss/operator → DB 配置：`user_roles(open_id, role, allowed_scope_key)` 已是 plan/14 的真相源 → 加管理端点 `/api/admin/roles`（boss only）+ SPA 管理页（增删改用户-角色-范围绑定）。
- 仅 boss 可访问；改动即时影响三处闸 + Web chat（第 4 处）。
- 远期：细粒度能力开关（呼应 storeclaw fine-grained permission / Skill 开关）、审批门（plan-approval）。

---

## 七、落地顺序与产物

1. **Phase A** 先行：`frontend/` 脚手架 + `web/routes/chat.py` + 会话表 + agent loop + 三栏 UI。可独立上线验证。
2. Phase B、C 各自独立发布，不互相阻塞。
3. 每阶段完成后更新本文件状态 + 写记忆。

> 决策已定（2026-06-17）：✅ LLM 可配置 Provider 层（国外/国内，换 base_url+api_key+model）；✅ SPA 同源托管（开发独立、部署同源）；✅ 接受新建会话表 `web_conversations`/`web_messages`（schema 见 Phase A 后端节）。

---

## 八、Phase A 实现记录（2026-06-17）

**新增文件**
- `services/llm/`：Provider 层。`types.py`（ChatMessage/ToolCall/ToolSpec/TextDelta/TurnComplete/LLMError）、`base.py`（LLMProvider ABC）、`openai_compat.py`（OpenAI 兼容族，含 SSE 流式 + 工具调用分片拼装）、`anthropic.py`（Claude /v1/messages，system 顶层 + content block + tool_result 回灌 + 连续 tool 结果合并）、`__init__.py`（`get_provider()` 工厂，按 config 分发，未配 key/model → LLMError fail-closed）。**零新依赖**（用 requests 直打 HTTP SSE）。
- `services/web_conversation_store.py`：会话 DAO，所有读写按 open_id 夹归属。
- `web/agent_tools.py`：6 个工具（ops_overview/orders_summary/orders_trend/top_skus/low_stock/fulfillments_pending），**范围不暴露给模型**——`resolve_authorized_scope(perm)` 夹紧后进程内直调 data.py 端点（仿 board.py::_collect）。
- `web/routes/chat.py`：agent loop（同步生成器跑在 Starlette 线程池，限 `max_tool_steps`）+ SSE（meta/delta/tool/done/error）+ 会话管理 API（/api/chat、/api/conversations[/{id}][/rename]、/api/me）。
- `frontend/`：Vite+React+TS SPA（精简依赖：react/react-dom/marked，未引 Tailwind/shadcn，留 Phase B）。三栏（Sidebar 会话列表 / Chat 消息流+流式+工具进度 / Composer），`api.ts` fetch+SSE 解析，401 跳飞书登录。`base=/app/`，dev proxy /api→8000。
- 测试：`tests/test_llm_providers.py`（两适配器 SSE 解析/工具拼装/非200）、`tests/test_web_conversation_store.py`（CRUD+归属隔离）、`tests/test_chat_routes.py`（鉴权401/SSE事件/落库/perm 传递）。

**改动文件**
- `core/config.py`：加 `LLMConfig`（provider/base_url/api_key/model/temperature/max_tool_steps/timeout），挂 `Settings.llm`。
- `models/base_models.py`：加 `WebConversation`/`WebMessage`。
- `web/web_security.py`：加 `require_web_user_api`（未登录返 401 JSON，区别于看板的 302）。
- `web/app.py`：挂 chat_router（cookie 鉴权、不入 MCP）；`/app` StaticFiles 同源托管 frontend/dist（dist 不存在则跳过）。
- `.env.example`：加 LLM 段。`.gitignore`：加 node_modules/ 与 frontend/dist/。

**验证**：全量 `uv run pytest` 189 passed 无回归；起服务 curl 冒烟——`/app/` 出 SPA（资源在 /app/assets/*）、`/health` 200、未登录 `/api/me`+`/api/chat` 均 401。

**坑记录**：sqlite `:memory:` 默认 SingletonThreadPool 每线程独立库；chat 流式生成器跑在线程池 → 测试需 StaticPool+check_same_thread=False 共享单连接（否则 `no such table`）。

**部署/运行前置（待用户）**
1. **建表**：新表 `web_conversations`/`web_messages` 走 `init_db()`（Base.metadata.create_all），上线手动执行（同 plan/14 建表惯例）。
2. **配 LLM**：`.env` 设 `LLM__PROVIDER`/`LLM__BASE_URL`/`LLM__API_KEY`/`LLM__MODEL`（国内模型直连最省；Claude/OpenAI 需出口可达）。未配则 /api/chat 流式首个事件返 error。
3. **构建前端**：`cd frontend && npm install && npm run build` 产出 dist，后端 `/app` 自动托管。
4. **端到端验证**（需 1-3 完成 + 飞书登录态 + user_roles 已登记）：浏览器开 `/app/`，问"近7天经营情况"，应看到工具进度 + 流式回答；operator 账号问到的数据被夹在其 allowed_scope 内。

**Phase A 明确未做**（留后续）：Skill Hub、Projects/资产库、plan-approval 审批门、Schedule UI、Connectors 管理、对话内切模型 UI、写操作工具、Markdown XSS 强净化（内部工具+自家 LLM 暂可接受）。
