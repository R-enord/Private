"""Runtime settings stored in the DB (owner-editable, audited). Env config stays the source of invariants."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from hub.models import AuditLog, Setting

DEFAULTS: dict[str, Any] = {
    "global_pause": False,
    "paused_job_types": [],
    "ai_enabled": True,  # local AI enrichment (Ollama) — cloud remains blocked regardless
    "ai_backfill_batch": 5,
    "embeddings_enabled": True,
    "score_weights": {},
    "reports_enabled": True,
    "report_hour_utc": 6,
    "low_resource_mode": False,
    "allow_private_content_to_cloud": False,  # informational mirror of the env invariant (always False here)
    "scheduler_state": {},
}


def get_setting(session: Session, key: str, default: Any = None) -> Any:
    row = session.get(Setting, key)
    if row is None:
        return DEFAULTS.get(key, default) if default is None else default
    return row.value


def set_setting(session: Session, key: str, value: Any, user_id: int | None = None, actor: str = "",
                audit: bool = True) -> None:
    row = session.get(Setting, key)
    old = row.value if row else DEFAULTS.get(key)
    if row is None:
        session.add(Setting(key=key, value=value, updated_by=user_id, updated_at=dt.datetime.now(dt.UTC)))
    else:
        row.value = value
        row.updated_by = user_id
        row.updated_at = dt.datetime.now(dt.UTC)
    if audit:
        session.add(AuditLog(actor_user_id=user_id, actor_label=actor or ("user" if user_id else "system"),
                             action="settings.update", target_type="setting", target_id=key,
                             details={"old": old, "new": value}))


def all_settings(session: Session) -> dict[str, Any]:
    out = dict(DEFAULTS)
    for row in session.scalars(select(Setting)):
        out[row.key] = row.value
    return out


def audit(session: Session, action: str, *, user_id: int | None = None, actor: str = "", target_type: str = "",
          target_id: str | int = "", **details: Any) -> None:
    session.add(AuditLog(actor_user_id=user_id, actor_label=actor or ("user" if user_id else "system"), action=action,
                         target_type=target_type, target_id=str(target_id), details=details))
