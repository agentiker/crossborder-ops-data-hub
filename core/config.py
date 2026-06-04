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
    redirect_uri: str = ""


class Settings(BaseSettings):
    """全局配置"""
    db: DatabaseConfig = DatabaseConfig()
    tiktok: TikTokConfig
    scheduler_interval_minutes: int = 60

    class Config:
        env_file = ".env"
        env_nested_delimiter = "__"


# 全局配置实例
settings = Settings()
