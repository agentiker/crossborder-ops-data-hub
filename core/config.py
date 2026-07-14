"""
配置管理模块
使用 Pydantic Settings 自动加载 .env 文件，支持嵌套配置
"""
from pydantic_settings import BaseSettings
from pydantic import BaseModel


class DatabaseConfig(BaseModel):
    """数据库配置"""
    host: str = "localhost"
    port: int = 3306
    user: str = "root"
    password: str = ""
    database: str = "crossborder_ops_data_hub"


class TikTokConfig(BaseModel):
    """TikTok Shop API配置"""
    app_key: str = ""
    app_secret: str = ""
    base_url: str = "https://open-api.tiktokglobalshop.com"
    auth_base_url: str = "https://auth.tiktok-shops.com"


class TikTokBusinessCredential(BaseModel):
    """单个 TikTok for Business（Marketing API）app 的 OAuth 凭据（多租户，每 account_id 一组）。"""
    app_id: str = ""      # Marketing API 的 App ID（≠ Shop 的 app_key）
    app_secret: str = ""  # 对应的 App secret


class TikTokBusinessConfig(BaseModel):
    """TikTok for Business / Marketing API 配置（拉 GMV Max 广告花费，见 plan/tiktok-marketing-api-gmv-max.md）。

    这是与 TikTok Shop **完全独立**的另一套系统：不同 base_url、不同 App、不同 OAuth、
    不同 token 与签名模型。GMV Max 花费不在 Shop Finance 结算里（Shop 侧 gmv_max_ad_fee_amount 恒 0），
    只能从这套 Marketing API 的 /gmv_max/report/get/ 拿。授权主体是 advertiser_id（广告账户），
    而非 shop_id。token 走 `Access-Token` 请求头、不做 Shop 那套 sign 拼接。

    多租户：`apps` 按 account_id 索引多组凭据（.env 用 JSON 串）；未配 `apps` 时回落顶层
    app_id/app_secret 单 app 垫片（与 feishu_oauth 同构），让单客户部署零改动。
    """
    # account_id → 凭据。例：{"ecom-app": {...}, "ecom-app-gtl": {...}}
    apps: dict[str, TikTokBusinessCredential] = {}
    base_url: str = "https://business-api.tiktok.com"
    # OAuth 回调路径（站内全自动回调，仿 Shop 的 /callback/tiktok）。各租户各自子域名在
    # TikTok for Business 后台把完整 URL 登记进 Advertiser Redirect URL 白名单。
    redirect_path: str = "/auth/callback/tiktok_business"
    # —— 单 app 兼容垫片（apps 未配时回落）——
    app_id: str = ""
    app_secret: str = ""

    def credential(self, account_id: str) -> "TikTokBusinessCredential":
        """取某租户（account_id）的 Marketing API app 凭据；未配 apps 时回落顶层单 app 字段。"""
        if account_id in self.apps:
            return self.apps[account_id]
        return TikTokBusinessCredential(app_id=self.app_id, app_secret=self.app_secret)


class MabangConfig(BaseModel):
    """马帮 ERP 成本抓取配置（无头浏览器登录，抓统一成本价入 product_costs）。

    见 flows/sync_mabang_costs。凭证仅用于登录 POST，不落库、不进日志。account 为空时
    回落 current_account()（单客户部署即 DEFAULT_ACCOUNT）。此马帮账号对应 TikTok 店的租户。
    """
    user: str = ""       # 马帮登录账号（手机号）
    password: str = ""   # 马帮登录密码（明文，仅登录用）
    account: str = ""    # 成本归属租户 account_id；空则回落 current_account()


class APIConfig(BaseModel):
    """对外 HTTP 接口配置（供 openclaw skill 本机调用）"""
    host: str = "127.0.0.1"  # 默认仅监听回环地址，不对公网开放
    port: int = 8000
    internal_token: str = ""  # skill 调用 /api/data 时需在 X-Internal-Token 头携带


class DashboardConfig(BaseModel):
    """飞书内嵌 H5 看板配置（路线 A：签名链接 + cloudflared 临时隧道）。

    看板靠 HMAC 签名 token 承担鉴权（不碰飞书 OAuth/JSSDK）：bot 发带 token 的链接，
    本服务验签拿 open_id，再按 open_id 的 binding scope 强制软隔离。详见 plan/13。
    """
    link_secret: str = ""  # HMAC-SHA256 签名密钥；未配置时拒签 token、验签一律失败
    public_base_url: str = ""  # 隧道公网根地址（如 https://xxx.trycloudflare.com）；签发链接用
    token_ttl_seconds: int = 1800  # token 默认有效期（30 分钟）


class FeishuAppCredential(BaseModel):
    """单个飞书 app 的 OAuth 凭据（多租户：每个 account_id 对应一组）。"""
    app_id: str = ""      # client_id（如 cli_aaa9302… = ecom-app，cli_aaaf… = ecom-app-gtl）
    app_secret: str = ""  # client_secret


class FeishuOAuthConfig(BaseModel):
    """独立运营看板的飞书 OAuth v2 网页免登 + 登录态配置（方案 B，见 plan/14）。

    浏览器跳飞书授权（飞书客户端内自动免登）→ 回调拿一次性 token 取 `open_id` 即丢弃，
    登录态由独立无状态 HMAC 签名 cookie 承载（同构 web/signed_link.py，不建 session 表）。

    多租户（多飞书 app）：`open_id` 是 per-app 的，不同飞书租户用各自的 app。`apps` 按
    account_id（= 租户主键）索引多组凭据；冷登录用哪套由子域名 Host 决定（core/tenancy）。
    顶层 `app_id/app_secret/redirect_uri` 保留为**单 app 兼容垫片**：未配 `apps` 时回落它
    （= 旧单租户行为，等价 ecom-app），让旧 .env 与旧部署零改动仍可跑。
    """
    # account_id → 凭据。例：{"ecom-app": {...}, "ecom-app-gtl": {...}}（.env 用 JSON 串）
    apps: dict[str, FeishuAppCredential] = {}
    # 回调路径，所有 app 共用同一 path、各自子域名各自在飞书后台白名单登记完整 URL。
    redirect_path: str = "/board/auth/feishu/callback"
    # —— 单 app 兼容垫片（旧字段，apps 未配时回落）——
    app_id: str = ""  # 选定统一飞书 app 的 client_id（需用户提供，见 Phase 0）
    app_secret: str = ""  # 对应 client_secret
    redirect_uri: str = ""  # 回调地址，须在飞书后台 redirect_uri 白名单中
    session_secret: str = ""  # 登录态 cookie 的 HMAC-SHA256 签名密钥（独立于 dashboard.link_secret）
    session_ttl_seconds: int = 604800  # 登录态有效期，默认 7 天
    cookie_name: str = "board_session"  # 登录态 cookie 名
    cookie_secure: bool = True  # Set-Cookie 是否带 Secure（生产 HTTPS 必须 True，本机调试可 False）
    # authorize 请求的 scope：必须带一个稳定 scope，否则飞书不落"已授权记录"→ 每次登录都弹
    # 同意页（实测：空 scope 连"历史已授予"都不显示）。带上 contact:user.base:readonly
    # （= "获取用户基本信息"，返回 open_id+姓名，登录够用）后，用户首次点一次授权即被记住，
    # 之后静默发码不再弹。该权限须在飞书后台「权限管理」开通并发版，否则 authorize 报 20027。
    # 注意：authorize 链接刻意不带 prompt=consent（它会强制每次确认）。可经 env 覆盖。
    oauth_scope: str = "contact:user.base:readonly"
    # 对话侧登记闸的 fail-closed 灰度开关（防自锁）：由 _resolve_scope /
    # set_scope_binding 的 _assert_dialog_registered 读取（plan/09 Phase 7 接通）。
    # 先 False 部署 → 自助申请/CLI 登记 boss/operator → 看灰度日志确认登记齐再置 True。
    # False 时维持旧行为（未登记 open_id 仅记 warning、不拒）；True 时未登记/无 open_id → 403。
    enforce_dialog_authz: bool = False

    def credential(self, account_id: str) -> "FeishuAppCredential":
        """取某租户（account_id）的飞书 app 凭据。

        未在 `apps` 配置时回落顶层单 app 字段（= 旧单租户行为，等价 ecom-app），
        保证旧 .env 仍可用。回落出的凭据 app_id 可能为空 → 上层据此判未配置并拒绝。
        """
        if account_id in self.apps:
            return self.apps[account_id]
        return FeishuAppCredential(app_id=self.app_id, app_secret=self.app_secret)


class TenancyConfig(BaseModel):
    """多租户拓扑映射（env 驱动，属部署拓扑，与 cloudflared/飞书凭据同处管理）。

    .env 用 JSON 串（pydantic 原生支持 dict env）：
      TENANCY__HOST_TO_ACCOUNT='{"board.agenticker.cc":"ecom-app","gtl.board.agenticker.cc":"ecom-app-gtl"}'
      TENANCY__PUBLIC_BASE_URL='{"ecom-app":"https://board.agenticker.cc","ecom-app-gtl":"https://gtl.board.agenticker.cc"}'
    未配时 core/tenancy 回落 DEFAULT_ACCOUNT / dashboard.public_base_url。
    """
    host_to_account: dict[str, str] = {}   # 子域名 host → account_id
    public_base_url: dict[str, str] = {}   # account_id → 该租户公网根
    # 回落锚点。默认 ecom-app（hp 存量主租户，零行为变更）；单客户独立部署（如 prod）
    # 用 TENANCY__DEFAULT_ACCOUNT 覆盖成本机租户，让 board/token/数据/飞书 agent 全对齐。
    default_account: str = "ecom-app"


class LLMConfig(BaseModel):
    """Web 对话端自建 agent 的大模型 Provider 配置（可配置，国外/国内通用，见 plan/15）。

    抽象一层 Provider，按 `provider` 分发到两类适配器（services/llm）：
    - "openai"：OpenAI 兼容族——国外 OpenAI，国内 DeepSeek / 通义千问(Qwen) /
      智谱 GLM / Kimi(Moonshot) / 豆包(火山方舟) / 百度千帆 / 硅基流动等，只换
      base_url + api_key + model 即可切换（它们都提供 /chat/completions 兼容端点）。
    - "anthropic"：Claude，独立协议（/v1/messages）。

    网络现实：国内模型从国内服务器直连无需代理；Anthropic/OpenAI 需出口可达
    （见记忆 tiktok-api-direct-connect 的网关坑）。可配置正好按部署环境切。
    零新依赖：用已有 requests 直接打 HTTP（含 SSE 流式），不引入 SDK。
    """
    provider: str = "openai"  # openai（兼容族）/ anthropic
    base_url: str = ""  # API 根地址；openai 兼容族如 https://api.deepseek.com/v1
    api_key: str = ""  # 对应 provider 的密钥
    model: str = ""  # 模型名，如 deepseek-chat / qwen-plus / claude-... / gpt-...
    temperature: float = 0.3  # 问数场景偏确定性，默认低温
    max_tool_steps: int = 6  # agent loop 单轮最多连续工具调用步数（防失控）
    request_timeout_seconds: int = 120  # 单次 LLM 请求超时


class Settings(BaseSettings):
    """全局配置"""
    db: DatabaseConfig = DatabaseConfig()
    tiktok: TikTokConfig
    tiktok_business: TikTokBusinessConfig = TikTokBusinessConfig()
    api: APIConfig = APIConfig()
    dashboard: DashboardConfig = DashboardConfig()
    feishu_oauth: FeishuOAuthConfig = FeishuOAuthConfig()
    tenancy: TenancyConfig = TenancyConfig()
    llm: LLMConfig = LLMConfig()
    mabang: MabangConfig = MabangConfig()
    scheduler_interval_minutes: int = 60
    # 业务归日时区偏移（小时）。印尼 WIB 固定 UTC+7（无夏令时）。
    # 订单 paid_time 存 naive UTC，GMV/趋势/单品按此偏移归到当地"自然日"。
    # 多店未来若跨时区，应改为按 shop 所在国时区，本期单店固定印尼。
    business_tz_offset_hours: int = 7
    # 待发货超时预警阈值（小时）：距平台发货截止不足此值记为"临界"。
    # 可经 .env 的 FULFILLMENT_WARNING_HOURS 覆盖。
    fulfillment_warning_hours: int = 24
    # 告警静默时段（监控巡检在此窗口内不推送，避免夜间打扰）。按 alert_quiet_tz 解读。
    # 跨午夜：start > end 时表示 [start, 次日 end) 为静默（如 23:00~次日 08:30）。
    alert_quiet_start: str = "23:00"
    alert_quiet_end: str = "08:30"
    alert_quiet_tz: str = "Asia/Shanghai"
    # openclaw CLI 可执行文件（监控用它直投飞书，0 经 LLM）。生产环境用绝对路径覆盖，
    # 避免 flow 进程 PATH 找不到 nvm node 下的 openclaw（见 plan「部署注意」）。
    openclaw_bin: str = "openclaw"
    # 低库存/断货预警阈值（可售天数 = 可用库存 ÷ 日均销速）。可经 .env 覆盖。
    # 可售 < critical_days 记"告急"；critical_days ~ warning_days 记"预警"；库存 0 且有销量记"断货"。
    # 销速按近 velocity_window_days 天的已付款销量折算日均；销速为 0（无销量）的 SKU 不计入预警。
    stock_cover_critical_days: int = 3
    stock_cover_warning_days: int = 7
    stock_velocity_window_days: int = 7

    # 扣点率异常告警（结算口径）。费率 = Σ总扣费(fee_tax_amount) ÷ Σ已结算订单 GMV(total_amount)，
    # 按订单 join、按 currency 分组（同币种内比，跨币种不混算）。仅纳入已付款且已有结算交易的订单。
    # 结算滞后：近 fee_rate_settle_lag_days 天订单结算未完成 → 评估/基准窗口都从更早处取，避免虚低。
    #   评估窗口 = [今天 − lag − eval_window_days + 1, 今天 − lag]（最近一段已结算完的天）
    #   基准窗口 = 其前 fee_rate_baseline_days 天的同口径费率
    # 仅当费率「上升」且 相对偏移 > rel_pct 且 绝对偏移 > abs_pct（pct 点）才告警（下降是好事不报）。
    # 任一窗口 GMV < min_gmv（同币种）或历史不足 → 优雅跳过（低基数/冷启动护栏，不误报）。
    fee_rate_settle_lag_days: int = 21
    fee_rate_eval_window_days: int = 7
    fee_rate_baseline_days: int = 28
    fee_rate_alert_rel_pct: float = 0.15  # 相对升幅阈值（0.15 = 比基准高 15%）
    fee_rate_alert_abs_pct: float = 0.03  # 绝对升幅阈值（0.03 = 费率高 3 个百分点）
    fee_rate_min_gmv: float = 10_000_000.0  # 窗口 GMV 护栏（默认按 IDR；多币种各自比较）
    # 及时费率告警（unsettled 预估口径，无结算滞后）：评估窗口 = 最近 N 天未结算预估费率，
    #   基准 = 已结算历史费率（避开滞后段，作稳基准检测"政策刚变尚未结算"）；阈值/护栏复用上方。
    #   N 不超过 unsettled_lookback_days（unsettled 全量替换只留近几天）。
    fee_rate_realtime_eval_days: int = 3

    # 爆单提醒：某商品当日（印尼业务日，截至 now 的 intraday）已付款销量 ≥ 阈值即提醒。
    # 当日去重：同一商品同一天只报一次；跨天自动重置（见 services/hotsell_alerts）。
    hotsell_daily_units_threshold: int = 50

    # 「新品」窗口：source_create_time 落近 N 天即算新品（看板新品卡 + 爆单告警🌟标注同口径，
    # 见 docs/business-rules §4.4）。看板卡、/board/new-products 端点、scan flow 告警标注三处共用。
    new_product_lookback_days: int = 60

    # 广告费结算滞后天数：广告费来自已结算 statement，且 fact_ad_spend_daily 按 order_create_time
    # 归日 → 近 N 天下单的单多未结算、广告费持续填充中。窗口结束日落在「今天−N」之内即判「不完整」，
    # 前端标注「结算中」，避免把"结算未回"误读成"广告变高效/ROAS 暴涨"（见 docs §6）。与扣点费率
    # 的 fee_rate_settle_lag_days 同源同量级（都来自 statement 结算）。
    ad_settle_lag_days: int = 21

    # 补货公式默认参数（运营可经 replenishment_config 表按范围覆盖，见 services/replenishment_config）。
    # 目标备货 = 近 velocity_days 天销量 × 系数；普通 SKU 用 normal、超级爆品(人工标记)用 superhot。
    # 补货量 = 目标备货 − 可用库存 − 在途，≤0 剔除。
    replenish_velocity_days: int = 30
    replenish_normal_multiplier: float = 1.5
    replenish_superhot_multiplier: float = 2.0

    # 阶段3a 预估利润（利润 = GMV − 扣点 − 广告费 − 产品成本(含运费) − 预估退货，统一折 CNY 展示）。
    # idr_to_rmb：IDR→CNY 乘数 **fail-safe 回落值**。优先查 fact_exchange_rate（中行牌价日表，
    # flows/sync_exchange_rate 每日抓，见 services/fx_rate.get_idr_to_rmb）；查表失败才用此固定值。1e7 IDR≈4500 CNY。
    # estimated_return_rate_default：全店默认预估退货率（无 return_rate_configs 配置时回落，运营可在 .env 调）。
    # unsettled_lookback_days：unsettled 采集按 order_create_time 回看天数（覆盖结算滞后边缘）。
    idr_to_rmb: float = 0.00045
    estimated_return_rate_default: float = 0.05
    profit_currency: str = "CNY"
    unsettled_lookback_days: int = 3

    # ── 上线前审计与合规（plan 审计合规：API/操作/权限/授权日志 + token 加密 + 备份）──
    # token_encryption_key：platform_tokens.access_token/refresh_token 透明加密的 Fernet 密钥
    #   （urlsafe base64 32B，`python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"` 生成）。
    #   未配置时：写非空 token 直接 raise（fail closed 防裸明文落库）、读存量明文放行（迁移兼容）。
    #   生产与 hp 各用独立 key，丢 key = 现存 token 不可解需重授权，须随 DB 一起备份保管。
    # backup_gpg_passphrase：每日 mysqldump 加密备份的 GPG 对称口令（scripts/backup_db.py 读）。
    #   它和 token_encryption_key 都须存在**备份之外**（异地/密管）：备份口令不能只躺在它加密的备份里，
    #   token key 也不能只躺在被它加密的 DB 备份里——否则丢机即双双不可恢复。
    # audit_anchor_enabled：哈希链尾每日锚定（发飞书运维群留痕）开关。
    # audit_anchor_account/open_id：锚定消息的飞书收件人（运维），同 notify-failure.sh 约定。
    #   两者任一为空 → 锚定只写 journald（同机可被同管理员篡改，无外部留痕）；生产必须配，
    #   否则"不可抵赖"失效——anchor flow 会 print 警告提示未配置外部锚点。
    token_encryption_key: str = ""
    backup_gpg_passphrase: str = ""
    audit_anchor_enabled: bool = True
    audit_anchor_account: str = ""
    audit_anchor_open_id: str = ""

    class Config:
        env_file = ".env"
        env_nested_delimiter = "__"


# 全局配置实例
settings = Settings()
