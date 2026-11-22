"""Backups with pg_dump/pg_restore (custom format). Password passed via PGPASSWORD env, never argv."""

from __future__ import annotations

import datetime as dt
import os
import shutil
import subprocess  # noqa: S404 - fixed binaries, no shell, arguments fully controlled
from pathlib import Path
from typing import Any

from sqlalchemy.engine import make_url

from hub.config import get_settings


def backup_dir() -> Path:
    d = get_settings().data_dir / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _libpq(url: str) -> tuple[list[str], dict[str, str]]:
    u = make_url(url)
    args = ["-h", u.host or "127.0.0.1", "-p", str(u.port or 5432), "-U", u.username or "hub", "-d", u.database or "hub"]
    env = dict(os.environ)
    if u.password:
        env["PGPASSWORD"] = str(u.password)
    return args, env


def _bin(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        for cand in sorted(Path("/usr/lib/postgresql").glob(f"*/bin/{name}"), reverse=True):
            return str(cand)
        raise FileNotFoundError(f"{name} not found; install the PostgreSQL client tools")
    return path


def create_backup(keep: int = 14) -> Path:
    settings = get_settings()
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M%S")
    out = backup_dir() / f"hub-{stamp}.dump"
    args, env = _libpq(settings.database_url)
    subprocess.run([_bin("pg_dump"), "-Fc", "--no-owner", "-f", str(out), *args], check=True, env=env,  # noqa: S603
                   capture_output=True, timeout=3600)
    files = sorted(backup_dir().glob("hub-*.dump"))
    for old in files[:-keep]:
        old.unlink(missing_ok=True)
    return out


def restore_backup(file: Path, database_url: str | None = None) -> None:
    """Destructive: replaces objects in the target database with the backup's contents."""
    if not file.exists():
        raise FileNotFoundError(file)
    args, env = _libpq(database_url or get_settings().database_url)
    subprocess.run([_bin("pg_restore"), "--clean", "--if-exists", "--no-owner", "--single-transaction", *args, str(file)],  # noqa: S603
                   check=True, env=env, capture_output=True, timeout=3600)


def list_backups() -> list[dict[str, Any]]:
    d = get_settings().data_dir / "backups"
    if not d.exists():
        return []
    return [{"file": p.name, "size": p.stat().st_size,
             "created_at": dt.datetime.fromtimestamp(p.stat().st_mtime, dt.UTC).isoformat()}
            for p in sorted(d.glob("hub-*.dump"), reverse=True)[:30]]
