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


class FeishuOAuthConfig(BaseModel):
    """独立运营看板的飞书 OAuth v2 网页免登 + 登录态配置（方案 B，见 plan/14）。

    浏览器跳飞书授权（飞书客户端内自动免登）→ 回调拿一次性 token 取 `open_id` 即丢弃，
    登录态由独立无状态 HMAC 签名 cookie 承载（同构 web/signed_link.py，不建 session 表）。
    `open_id` 是 per-app 的：看板 OAuth 与运营对话必须同一飞书 app（建议 ecom-app），
    user_roles 存的 open_id 才能三处串起来。
    """
    app_id: str = ""  # 选定统一飞书 app 的 client_id（需用户提供，见 Phase 0）
    app_secret: str = ""  # 对应 client_secret
    redirect_uri: str = ""  # 回调地址，须在飞书后台 redirect_uri 白名单中
    session_secret: str = ""  # 登录态 cookie 的 HMAC-SHA256 签名密钥（独立于 dashboard.link_secret）
    session_ttl_seconds: int = 604800  # 登录态有效期，默认 7 天
    cookie_name: str = "board_session"  # 登录态 cookie 名
    cookie_secure: bool = True  # Set-Cookie 是否带 Secure（生产 HTTPS 必须 True，本机调试可 False）
    # 对话侧 fail-closed 硬闸灰度开关（防自锁，见 plan/14 Phase 6）：
    # 先 False 部署 → CLI 登记 boss/operator → 确认无误再置 True。
    # False 时 web/routes/data.py::_resolve_scope 维持旧行为（未登记 open_id 不拒）。
    enforce_dialog_authz: bool = False


class Settings(BaseSettings):
    """全局配置"""
    db: DatabaseConfig = DatabaseConfig()
    tiktok: TikTokConfig
    api: APIConfig = APIConfig()
    dashboard: DashboardConfig = DashboardConfig()
    feishu_oauth: FeishuOAuthConfig = FeishuOAuthConfig()
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

    class Config:
        env_file = ".env"
        env_nested_delimiter = "__"


# 全局配置实例
settings = Settings()
