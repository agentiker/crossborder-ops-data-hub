"""
数据库初始化模块
SQLAlchemy 2.0 引擎与会话工厂
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from core.config import settings


# 构建数据库连接URL
DATABASE_URL = (
    f"mysql+pymysql://{settings.db.user}:{settings.db.password}"
    f"@{settings.db.host}:{settings.db.port}/{settings.db.database}"
)

# 创建引擎（连接池配置）
engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,  # 连接健康检查
    echo=False,
)

# 会话工厂
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    """ORM基类"""
    pass


def get_session():
    """获取数据库会话（上下文管理器）"""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db():
    """初始化数据库表"""
    from models import base_models  # noqa: F401

    Base.metadata.create_all(bind=engine)
