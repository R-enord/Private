from __future__ import annotations

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url

from hub.backup import create_backup, list_backups, restore_backup
from hub.config import reset_settings_cache
from hub.db import session_scope
from hub.models import Item
from tests.conftest import TEST_DB


def test_backup_and_restore_roundtrip(db, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HUB_DATA_DIR", str(tmp_path))
    reset_settings_cache()
    try:
        with session_scope() as s:
            s.add(Item(kind="article", title="Backup canary item", url="https://example.org/canary",
                       canonical_url="https://example.org/canary", is_fixture=True))
        path = create_backup()
        assert path.exists() and path.stat().st_size > 0
        assert list_backups()[0]["file"] == path.name
        target = make_url(TEST_DB).set(database="hub_test_restore")
        admin = create_engine(make_url(TEST_DB).set(database="postgres"), isolation_level="AUTOCOMMIT")
        with admin.connect() as c:
            c.execute(text("DROP DATABASE IF EXISTS hub_test_restore"))
            c.execute(text("CREATE DATABASE hub_test_restore"))
        url = target.render_as_string(hide_password=False)
        restore_backup(path, url)
        restore_backup(path, url)  # idempotent over existing objects
        eng = create_engine(url)
        with eng.connect() as c:
            assert c.execute(select(func.count()).select_from(Item.__table__).where(Item.title == "Backup canary item")).scalar() == 1
            from alembic.script import ScriptDirectory

            from tests.conftest import alembic_config

            head = ScriptDirectory.from_config(alembic_config()).get_current_head()
            assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == head
        eng.dispose()
        with admin.connect() as c:
            c.execute(text("DROP DATABASE hub_test_restore"))
        admin.dispose()
    finally:
        monkeypatch.delenv("HUB_DATA_DIR")
        reset_settings_cache()
