"""Test harness.

* Uses a dedicated database (``hub_test``) that is dropped and re-created from migrations at session start,
  so every run exercises a clean ``alembic upgrade head``.
* Network is never touched by default: tests use ``httpx.MockTransport`` with recorded fixtures in
  ``tests/fixtures`` (clearly labeled as fixtures). Live tests are opt-in (HUB_LIVE_TESTS=1).
"""

from __future__ import annotations

import os

TEST_DB = os.environ.get("HUB_TEST_DATABASE_URL", "postgresql+psycopg://hub:hub_local_dev@127.0.0.1:5432/hub_test")
os.environ["HUB_ENV"] = "test"
os.environ["HUB_DATABASE_URL"] = TEST_DB
os.environ.setdefault("HUB_OLLAMA_URL", "http://127.0.0.1:11434")

import json  # noqa: E402
from collections.abc import Callable, Iterator  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from hub.config import reset_settings_cache  # noqa: E402
from hub.db import reset_engine, session_scope  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _recreate_database(url: str) -> None:
    u = make_url(url)
    admin = create_engine(u.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :d"), {"d": u.database})
        c.execute(text(f'DROP DATABASE IF EXISTS "{u.database}"'))
        c.execute(text(f'CREATE DATABASE "{u.database}"'))
    admin.dispose()


def alembic_config() -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", TEST_DB)
    return cfg


@pytest.fixture(scope="session", autouse=True)
def _database() -> Iterator[None]:
    reset_settings_cache()
    reset_engine()
    _recreate_database(TEST_DB)
    command.upgrade(alembic_config(), "head")
    from hub.seed import seed_all

    with session_scope() as s:
        seed_all(s)
    yield
    reset_engine()


_KEEP = {"alembic_version", "categories", "sources"}


@pytest.fixture()
def db() -> Iterator[Any]:
    """Clean slate per test: truncate data tables (keeps seeded registry + categories, resets source state)."""
    from hub.db import get_engine

    eng = get_engine()
    with eng.begin() as c:
        tables = [r[0] for r in c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))]
        data_tables = [t for t in tables if t not in _KEEP]
        c.execute(text("TRUNCATE " + ", ".join(f'"{t}"' for t in data_tables) + " RESTART IDENTITY CASCADE"))
        c.execute(
            text(
                "UPDATE sources SET health='unknown', last_attempt_at=NULL, last_success_at=NULL, next_run_at=NULL, "
                "backoff_until=NULL, consecutive_failures=0, last_error='', paused=false, rate_limit_remaining=NULL, "
                "rate_limit_reset_at=NULL"
            )
        )
    with session_scope() as s:
        yield s


def load_fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def load_json_fixture(name: str) -> Any:
    return json.loads(load_fixture(name))


Handler = Callable[[httpx.Request], httpx.Response]


class Recorder:
    """MockTransport wrapper that records every request (to assert what did / did not hit the network)."""

    def __init__(self, handler: Handler):
        self.handler = handler
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)


@pytest.fixture()
def recorder_factory() -> Callable[[Handler], Recorder]:
    return Recorder


def no_sleep(_s: float) -> None:
    return None
