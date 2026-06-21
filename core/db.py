"""
数据库初始化模块
SQLAlchemy 2.0 引擎与会话工厂

自动租户过滤：注册 do_orm_execute 事件，当 contextvar 被显式设定时，
对所有带 account_id 列的 ORM SELECT 自动注入 WHERE account_id = ?。
未设定 contextvar 的入口行为与改造前完全一致（opt-in，零静默回归）。
"""
from sqlalchemy import create_engine, event
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


def _inject_tenant_filter(execute_context):
    """ORM SELECT 自动注入 WHERE account_id = ?（opt-in：仅在 contextvar 显式设定时生效）。

    - current_account_or_none() 返回 None → 不过滤（未设定 / TENANT_BYPASS）
    - with_loader_criteria(Base, ...) 自动跳过无 account_id 列的 mapper
    """
    from core.tenancy import current_account_or_none

    account_id = current_account_or_none()
    if account_id is None:
        return
    if not execute_context.is_select:
        return
    from models.base_models import Base
    from sqlalchemy.orm import with_loader_criteria

    criteria = lambda cls: cls.account_id == account_id if hasattr(cls, "account_id") else True
    execute_context.statement = execute_context.statement.options(
        with_loader_criteria(
            Base,
            criteria,
            include_aliases=True,
            track_closure_variables=False,
        )
    )


event.listen(SessionLocal, "do_orm_execute", _inject_tenant_filter)


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
