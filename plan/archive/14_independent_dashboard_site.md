> **状态：✅ 已完成、已归档（2026-07-04）**。OAuth 登录 + UserRole/user_authz 权限闸 + /board 独立看板 + 对话侧权限夹紧 + 告警/推送按 account_id 过滤，全 Phase 落地、测试全绿、hp+prod 上线。

╭───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ plan/14 — 独立运营看板网站 + 数据层统一权限闸（方案 B）                                                                                               │
│                                                                                                                                                       │
│ ▎ 对 plan/13（飞书内嵌临时 H5）的架构级升级。本文件为待批计划。                                                                                       │
│                                                                                                                                                       │
│ Context（为什么做）                                                                                                                                   │
│                                                                                                                                                       │
│ plan/13 看板靠「bot 发 30 分钟签名链接」进入，身份由 agent 传参的 open_id 决定（可越权签发），托管是每次重启变域名的临时隧道。                        │
│                                                                                                                                                       │
│ 客户新需求：浏览器可直接访问的独立网站，可视化数据/趋势/告警，要真正的登录鉴权，能在飞书内预览/跳转登录。深入沟通后确认：                             │
│                                                                                                                                                       │
│ - 租户内分角色：老板(boss)看全部；运营(operator)只能看授予范围，且是不可越界硬上限（区别于现有 scope_binding 的"默认范围记忆"可越界）。               │
│ - 运营既看看板、也在飞书里直接问 bot，且会订阅推送 → 仅堵看板不够：运营直接问 bot 就能看全部、给运营推全范围日报也泄漏。因此权限必须下沉成覆盖三处的统一闸（方案 B）。                                                                  │
│                                                                                                                                                       │
│ openclaw 现状（已上 hp 核实 ~/.openclaw/openclaw.json）                                                                                               │
│                                                                                                                                                       │
│ - 三个飞书自建 app：main-app→main、ecom-app→ecom、ecom-app-gtl→ecom-gtl；connectionMode=websocket（长连接）；dmPolicy=allowlist（每 app 白名单现各放行 1 人）。                                                                                                                               │
│ - bindings 匹配维度只有 channel+accountId，无 user/open_id 维度 → openclaw 原生做不到"同 app 不同用户走不同 agent"。结论：scope 权限不靠 agent 表达，按 open_id 在我们数据层控制。agent 是人格/workspace 维度，与数据权限正交。                                                                      │
│                                                                                                                                                       │
│ 两条铁律                                                                                                                                              │
│                                                                                                                                                       │
│ 1. 单 app 统一：上线统一用一个飞书 app（建议 ecom-app）。飞书 open_id 是 per-app 的——看板 OAuth 与运营对话必须同一个 app，user_roles 存的 open_id 以该 app 为准，三处才串得起来。                                                                                                                       │
│ 2. 不碰 openclaw 代码：只在飞书后台开「网页」能力 + 配 redirect_uri + 把运营 open_id 加进 allowFrom（用户自行审核/改 json）。OAuth 网页授权与长连接事件订阅互不冲突（已查证）。                                                                                                          │
│                                                                                                                                                       │
│ 不做：plan/09 的 tenant_id 全表硬隔离（仍单租户）。                                                                                                   │
│                                                                                                                                                       │
│ 统一权限闸：覆盖三处                                                                                                                                  │
│                                                                                                                                                       │
│ user_roles(open_id→role+allowed_scope) + services/user_authz.py 是唯一真相，三处复用：                                                                │
│                                                                                                                                                       │
│ 1. 看板网站（新 /board，飞书 OAuth 登录态）。                                                                                                         │
│ 2. 对话侧（web/routes/data.py 的 _resolve_scope，所有 ops_* MCP 工具）——open_id 来自 openclaw 注入的 trusted metadata（可信），operator 任何查询强制夹进 allowed_scope，越界拒。                                                                                                              │
│ 3. 主动推送（日报 + 告警）——按收件人 open_id 的 allowed_scope 过滤推送内容。                                                                          │
│                                                                                                                                                       │
│ 核心拦截复用：resolve_filters(scope_key=allowed, shop_ids=展开(requested)) 的「交集+越界抛 ScopeError」语义（scope_resolution.py:166-189，已核实），operator 越界天然被拒，不重写判断。                                                          │
│                                                                                                                                                       │
│ 未登记 open_id = fail closed（拒绝）。→ 上线迁移顺序关键（见 Phase 6）：先把老板登记成 boss，再开闸，否则自己被锁。                                   │
│                                                                                                                                                       │
│ ---                                                                                                                                                   │
│ 飞书 OAuth v2 接口（已查最新文档）                                                                                                                    │
│                                                                                                                                                       │
│ - 授权页 GET https://accounts.feishu.cn/open-apis/authen/v1/authorize，query client_id/redirect_uri/response_type=code/state/scope（拿 open_id 最小权限如 contact:user.id:readonly）。飞书客户端内自动免登。                                                                                         │
│ - 换 token POST https://open.feishu.cn/open-apis/authen/v2/oauth/token，JSON {grant_type:"authorization_code",client_id,client_secret,code,redirect_uri}。code 5 分钟单次。                                                        │
│ - 取用户信息 GET https://open.feishu.cn/open-apis/authen/v1/user_info，头 Authorization: Bearer <token> → {open_id,name}。                            │
│ - 登录后只用一次 token 取 open_id 即丢弃，不持有/不刷新，省 offline_access；登录态由独立签名 cookie 承载。                                            │
│                                                                                                                                                       │
│ Session = 无状态签名 cookie：复用 web/signed_link.py 的 HMAC 思路，零新增依赖（HTTP 用已有 requests），不建 session 表（YAGNI）。Set-Cookie 带 HttpOnly;Secure;SameSite=Lax，TTL 默认 7 天，独立密钥 feishu_oauth.session_secret。                                                                   │
│                                                                                                                                                       │
│ ---                                                                                                                                                   │
│ Phase 0 — 飞书后台 + 部署前置（人工，阻塞）                                                                                                           │
│                                                                                                                                                       │
│ 1. 选定统一 app（建议 ecom-app），拿其 app_id/app_secret（需用户提供）。                                                                              │
│ 2. 飞书后台：开「网页」能力，加 redirect_uri 白名单 https://<域名>/board/auth/feishu/callback；申请拿 open_id 的最小权限。                            │
│ 3. 正式域名 + certbot --nginx。                                                                                                                       │
│ 4. allowFrom 加运营 open_id（用户自行改 openclaw.json）。                                                                                             │
│ 5. 上线后验证 openclaw 长连接不掉。                                                                                                                   │
│                                                                                                                                                       │
│ Phase 1 — 配置与数据模型                                                                                                                              │
│                                                                                                                                                       │
│ - core/config.py 新增 FeishuOAuthConfig：app_id/app_secret/redirect_uri/session_secret/session_ttl_seconds=604800/cookie_name/cookie_secure=True，挂 Settings.feishu_oauth（复用 env_nested_delimiter="__"）。                                                                                             │
│ - models/base_models.py 新增 UserRole（仿 ConversationScopeBinding，base_models.py:118）：channel/account_id/open_id/role(boss|operator)/allowed_scope_key/note/is_active/时间戳，唯一约束 (channel,account_id,open_id)。init_db 幂等建表。先单 allowed_scope_key（YAGNI）。                                                                     │
│                                                                                                                                                       │
│ Phase 2 — 统一权限解析服务（核心，数据层闸）                                                                                                          │
│                                                                                                                                                       │
│ services/user_authz.py（新建）：                                                                                                                      │
│ - get_user_permission(open_id, *, channel="feishu", account_id=<默认>) -> UserPermission | None：读 user_roles，无行/停用→None。                      │
│ - resolve_authorized_scope(perm, *, requested_scope_key=None, requested_shop_ids=None) -> ScopeFilters：                                              │
│   - boss：requested 有→resolve_filters(scope_key=requested)，否则全范围 resolve_filters()。                                                           │
│   - operator：base 钉 allowed_scope_key；requested 非空→借 resolve_filters 交集夹紧（越界 ScopeError）；allowed=None→权限错。                         │
│ - assert_authorized(open_id, ...)：对话侧/推送侧用的便捷封装，返回夹紧后的 ScopeFilters 或抛权限错（上层转 403 / 拒答文案）。                         │
│                                                                                                                                                       │
│ Phase 3 — 飞书 OAuth + 登录态                                                                                                                         │
│                                                                                                                                                       │
│ - web/feishu_oauth.py：build_authorize_url(state) / exchange_code_for_token(code) / fetch_open_id(token)（requests）。                                │
│ - web/web_session.py：make_session_cookie/verify_session_cookie——逐行同构 web/signed_link.py，换密钥源与 TTL。                                        │
│ - web/web_security.py：dependency require_web_user(request)->UserPermission：cookie 无效→302 跳 authorize；有效但无授权→403 页。                      │
│ - web/routes/auth_feishu.py（/board/auth/feishu）：/login（签名 state 含 next+nonce+exp）//callback（校 state→换 token→取 open_id→签 cookie→302）//logout。                                                                                                                                │
│                                                                                                                                                       │
│ Phase 4 — 看板页面（新建 web/routes/board.py，与旧 /dashboard?t= 共存）                                                                               │
│                                                                                                                                                       │
│ - GET /board、GET /board/data，均 Depends(require_web_user)。                                                                                         │
│ - _collect(perm,period,requested_scope_key)：先 resolve_authorized_scope（捕错→403），再直接调 services 层传 scope.platform/country/shop_ids（绕开 Query 包装）。                                                                                                                                        │
│ - 范围切换：list_scopes() 来源；boss 全部+「全部范围」，operator 锁其子集；?scope= 切换服务端兜底夹紧。                                               │
│ - 告警板块（只读）：调 get_pending_fulfillments + get_stock_risk（与 scan flow 同源纯函数），渲染待发货超时分桶 + 断货/告急/预警。不碰游标/不推送。   │
│ - 复用 plan/13 _PAGE（Chart.js），改数据源+切换控件+告警 DOM，页脚「飞书登录 · 范围按角色锁定」。                                                     │
│                                                                                                                                                       │
│ Phase 5 — 对话侧接入 + 推送过滤（方案 B 新增）                                                                                                        │
│                                                                                                                                                       │
│ - 对话侧硬闸：改 web/routes/data.py::_resolve_scope——带 open_id 的请求先 get_user_permission(open_id)：                                               │
│   - 未登记→拒绝（HTTP 403 + 明确文案，让 agent 转述"无看板/数据权限，请联系管理员"）。                                                                │
│   - operator→无论 agent 传什么 scope_id/platform/country/shop_ids，一律经 resolve_authorized_scope 夹进 allowed（越界→403/夹紧）。硬上限先于 binding；binding 仅在上限内当默认。                                                                                                                   │
│   - boss→维持现状（可查全部 + binding 默认）。                                                                                                        │
│   - 影响全部 ops_* 工具（统一在 _resolve_scope 一处生效，不逐个改）。                                                                                 │
│ - 推送过滤：flows/scan_fulfillment_alerts.py 与日报推送，按收件人 open_id 的 allowed_scope 算/裁内容（operator 收件人只算其范围；boss 全范围）。收件人映射处接 user_authz。                                                                                                                 │
│ - SKILL/文案：openclaw-skills/crossborder-ops-data/SKILL.md 注明 operator 被夹范围（避免误判数据缺失）；越界时 agent 复述"你的权限只覆盖 X"。         │
│                                                                                                                                                       │
│ Phase 6 — 接线 / 部署 / 上线迁移顺序                                                                                                                  │
│                                                                                                                                                       │
│ - web/app.py：挂 auth_feishu_router + board_router（不加 internal token）。/api/data 与 MCP 配置不动。                                                │
│ - deploy/nginx.conf：server_name 换正式域名；加 location /board/（+ location = /board）反代到 127.0.0.1:8000；/api/data/* 不加 location，继续 return 404。                                                                                                                                                 │
│ - .env/.env.example 加 FEISHU_OAUTH__*（SESSION_SECRET=secrets.token_urlsafe(32)、COOKIE_SECURE）。                                                   │
│ - plan/13 去留：保留 /dashboard?t=+signed_link+ops_dashboard_link 不动（零回归），bot 可改发 /board；稳定后续 plan 删旧入口。                         │
│ - ⚠️ 上线迁移顺序（关键，防自锁）：① 部署代码（含建表）；② 先用 CLI 把老板们登记成 boss、运营登记成 operator+scope；③ 确认登记无误后，再启用 Phase 5 的对话侧 fail-closed 硬闸（建议用配置开关 feishu_oauth.enforce_dialog_authz 控制，先 false 灰度、登记齐后置 true）；④ 验证老板对话正常、运营被夹。    │
│                                                                                                                                                       │
│ Phase 7 — 运维 CLI                                                                                                                                    │
│                                                                                                                                                       │
│ scripts/user_admin.py（仿 scope_admin.py）：list / set --open-id --role {boss|operator} [--scope-key --note] / deactivate --open-id。operator 必须给 --scope-key 且校验存在+active（复用 expand_scope）；boss 忽略。                                                                                       │
│                                                                                                                                                       │
│ Phase 8 — 测试（仿 tests/ monkeypatch SessionLocal + 内存 sqlite）                                                                                    │
│                                                                                                                                                       │
│ - tests/test_web_session.py（仿 test_signed_link.py）：round-trip/过期/篡改/错密钥/无密钥 fail-closed/垃圾输入。                                      │
│ - tests/test_user_authz.py（权限矩阵）：boss 不传→全、boss 传任意→该 scope；operator allowed=A 不传→A、传子集→收窄、传 B 越界→ScopeError、allowed=None→权限错；未登记→None。                                                                                                   │
│ - tests/test_feishu_oauth.py（mock requests）：authorize URL 必需 query；换 token/取 open_id 解析；state 签名/校验防 CSRF。                           │
│ - tests/test_board_routes.py（TestClient，mock cookie/perm）：无 cookie→302；boss→200；operator 改 ?scope=越界→403；未授权→403。                      │
│ - tests/test_data_authz.py（方案 B 新增）：对话侧 _resolve_scope 在 enforce 开启下——未登记 open_id→403；operator 传越界 scope_id/shop_ids→403/夹紧；boss→放行；enforce 关闭→维持旧行为（灰度兼容）。                                                                          │
│ - 推送过滤：scan flow 给 operator 收件人只算其 scope 的单测。                                                                                         │
│                                                                                                                                                       │
│ 前置依赖与风险                                                                                                                                        │
│                                                                                                                                                       │
│ ┌────────────────────────┬─────────────────────────────────────────────────────────────────────────────────┐                                          │
│ │           项           │                                    动作/风险                                    │                                          │
│ ├────────────────────────┼─────────────────────────────────────────────────────────────────────────────────┤                                          │
│ │ 统一 app 的 app_secret │ 阻塞前置：需用户提供选定 app 的 app_id/app_secret                               │                                          │
│ ├────────────────────────┼─────────────────────────────────────────────────────────────────────────────────┤                                          │
│ │ open_id per-app        │ 看板 OAuth 与运营对话必须同一 app，否则 open_id 串不起来                        │                                          │
│ ├────────────────────────┼─────────────────────────────────────────────────────────────────────────────────┤                                          │
│ │ 自锁风险               │ 对话侧 fail-closed 上线前必须先登记 boss；用 enforce_dialog_authz 开关灰度      │                                          │
│ ├────────────────────────┼─────────────────────────────────────────────────────────────────────────────────┤                                          │
│ │ 推送泄漏               │ 运营推送内容必须按 allowed_scope 裁，否则全范围日报泄漏                         │                                          │
│ ├────────────────────────┼─────────────────────────────────────────────────────────────────────────────────┤                                          │
│ │ CSRF/cookie            │ state 签名校验；cookie HttpOnly+Secure+SameSite=Lax                             │                                          │
│ ├────────────────────────┼─────────────────────────────────────────────────────────────────────────────────┤                                          │
│ │ 回归面                 │ 方案 B 动了 _resolve_scope 核心路径（所有 ops_* 工具）→ 灰度开关 + 全量回归必跑 │                                          │
│ └────────────────────────┴─────────────────────────────────────────────────────────────────────────────────┘                                          │
│                                                                                                                                                       │
│ 验证（端到端）                                                                                                                                        │
│                                                                                                                                                       │
│ 1. uv run pytest tests/test_web_session.py tests/test_user_authz.py tests/test_feishu_oauth.py tests/test_board_routes.py tests/test_data_authz.py，再全量 uv run pytest 无回归。                                                                                               │
│ 2. 本机：CLI 造 boss/operator；enforce 关→旧行为；开→未登记 403、operator 对话越界被夹、boss 放行；看板 boss 全范围/operator 锁定/改 URL 越界 403；告警板有数据。                                                                                                                                   │
│ 3. hp：真实飞书账号走 OAuth（浏览器跳授权 + 飞书内免登）；运营对话只看自己范围、推送只含自己范围；老板全范围；确认 openclaw 长连接未掉。              │
╰───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯