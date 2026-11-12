"""Tests run against a real PostgreSQL database named <dev db>_test.

The dev database is never touched. The test database is created automatically
and built by running the real Alembic migrations, so every test run also proves
that the migrations work.
"""
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

DEV_URL = make_url(
    os.environ.get("DATABASE_URL", "postgresql+psycopg://matchpulse:matchpulse@db:5432/matchpulse")
)
TEST_URL = DEV_URL.set(database=f"{DEV_URL.database}_test")
os.environ["DATABASE_URL"] = TEST_URL.render_as_string(hide_password=False)

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402


def _ensure_test_database() -> None:
    admin = create_engine(DEV_URL.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": TEST_URL.database}
        ).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{TEST_URL.database}"'))
    admin.dispose()


@pytest.fixture(scope="session", autouse=True)
def migrated_database():
    _ensure_test_database()
    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    command.downgrade(cfg, "base")  # start from a clean slate
    command.upgrade(cfg, "head")
    yield
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_tables(migrated_database):
    with engine.begin() as conn:
        conn.execute(text(
            "TRUNCATE alerts, match_snapshots, match_events, matches, rules, teams, competitions "
            "RESTART IDENTITY CASCADE"
        ))


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    return TestClient(app)
