from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("TIKTOK__APP_KEY", "test_app_key")
os.environ.setdefault("TIKTOK__APP_SECRET", "test_app_secret")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.db import Base
from models import base_models  # noqa: F401


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db_session = Session()
    try:
        yield db_session
    finally:
        db_session.close()
        Base.metadata.drop_all(bind=engine)
