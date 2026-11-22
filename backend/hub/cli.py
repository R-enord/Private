"""`hub` command-line interface.

    hub migrate            apply database migrations (alembic upgrade head) and seed the registry
    hub create-owner       create the single owner account (prompts for password)
    hub serve              run the web server (127.0.0.1:8000 by default)
    hub worker             run the background worker (scheduler + jobs); keeps collecting with the browser closed
    hub start              run server + worker together (simple supervisor; restarts crashed children)
    hub run-source KEY     run one source now (foreground) — useful for debugging
    hub status             show sources, queue and workers
    hub doctor             inspect this machine (OS/CPU/RAM/GPU/disk/runtimes/Ollama) and recommend settings
    hub backup             create a pg_dump backup in data/backups
    hub restore FILE --yes restore a backup (destructive)
"""

from __future__ import annotations

import argparse
import getpass
import logging
import os
import subprocess  # noqa: S404
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def _setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(level=getattr(logging, level), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)


def cmd_migrate(_: argparse.Namespace) -> int:
    from alembic import command
    from alembic.config import Config

    from hub.db import session_scope
    from hub.seed import seed_all

    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    command.upgrade(cfg, "head")
    from hub.pipeline.ingest import backfill_media

    with session_scope() as s:
        seed_all(s)
    with session_scope() as s:
        n = backfill_media(s)
    print(f"database migrated and source registry seeded; {n} existing item(s) re-classified as media")
    return 0


def cmd_create_owner(args: argparse.Namespace) -> int:
    from sqlalchemy import func, select

    from hub.db import session_scope
    from hub.models import User
    from hub.security.auth import create_user
    from hub.settings_store import audit

    email = args.email or input("Owner email: ").strip()
    pw = os.environ.get("HUB_OWNER_PASSWORD") or getpass.getpass("Password (min 12 chars): ")
    if not os.environ.get("HUB_OWNER_PASSWORD") and getpass.getpass("Repeat password: ") != pw:
        print("passwords do not match", file=sys.stderr)
        return 1
    with session_scope() as s:
        if s.scalar(select(func.count()).select_from(User).where(User.role == "owner")):
            print("an owner already exists; refusing to create another", file=sys.stderr)
            return 1
        u = create_user(s, email, pw, role="owner", display_name=args.name or "")
        audit(s, "user.create_owner", actor="cli", target_type="user", target_id=u.id)
    print(f"owner {email} created")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from hub.config import get_settings

    s = get_settings()
    s.check_exposure()
    if s.network_mode != "local":
        from hub.net.lanaddr import phone_urls

        print(f"phone access ({s.network_mode} mode): " + ", ".join(phone_urls(s.network_mode, s.extra_hosts, s.scheme,
                                                                                args.port or s.port)))
    uvicorn.run("hub.api.app:create_app", factory=True, host=s.bind_host, port=args.port or s.port,
                log_level="info", proxy_headers=False, server_header=False,
                ssl_certfile=str(s.tls_cert_file) if s.tls_cert_file else None,
                ssl_keyfile=str(s.tls_key_file) if s.tls_key_file else None)
    return 0


def cmd_worker(args: argparse.Namespace) -> int:
    from hub.worker.runner import Worker

    _setup_logging()
    Worker(concurrency=args.concurrency, run_scheduler=not args.no_scheduler).run_forever()
    return 0


def cmd_start(_: argparse.Namespace) -> int:
    """Tiny supervisor: one server + one worker, restarted with backoff if they crash."""
    _setup_logging()
    log = logging.getLogger("hub.start")
    exe = [sys.executable, "-m", "hub.cli"]
    specs = {"server": exe + ["serve"], "worker": exe + ["worker"]}
    procs: dict[str, subprocess.Popen[bytes]] = {}
    backoff = {k: 1.0 for k in specs}
    try:
        while True:
            for name, cmd in specs.items():
                p = procs.get(name)
                if p is None or p.poll() is not None:
                    if p is not None:
                        log.warning("%s exited with %s; restarting in %.0fs", name, p.returncode, backoff[name])
                        time.sleep(backoff[name])
                        backoff[name] = min(60.0, backoff[name] * 2)
                    procs[name] = subprocess.Popen(cmd, cwd=BACKEND)  # noqa: S603
                    log.info("started %s (pid %s)", name, procs[name].pid)
            time.sleep(2)
    except KeyboardInterrupt:
        log.info("stopping…")
    finally:
        for p in procs.values():
            p.terminate()
        for p in procs.values():
            try:
                p.wait(30)
            except subprocess.TimeoutExpired:
                p.kill()
    return 0


def cmd_run_source(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from hub.db import session_scope
    from hub.models import Source
    from hub.pipeline.ingest import run_source

    _setup_logging()
    with session_scope() as s:
        sid = s.scalar(select(Source.id).where(Source.key == args.key))
    if sid is None:
        print(f"unknown source {args.key}", file=sys.stderr)
        return 1
    r = run_source(sid, force=args.force)
    print(f"{r.source_key}: {r.status} records={r.records} new={r.new} updated={r.updated} merged={r.merged} "
          f"filtered={r.filtered} http={r.http_requests} {r.error}")
    for w in r.warnings:
        print("  warning:", w)
    return 0 if r.status in ("ok", "not_modified", "skipped") else 2


def cmd_status(_: argparse.Namespace) -> int:
    from sqlalchemy import select

    from hub.db import session_scope
    from hub.models import Source, WorkerHeartbeat
    from hub.worker.queue import queue_counts

    with session_scope() as s:
        print(f"{'source':26} {'en':3} {'health':12} {'last success':20} next run")
        for src in s.scalars(select(Source).order_by(Source.enabled.desc(), Source.key)):
            ls = src.last_success_at.strftime("%Y-%m-%d %H:%M") if src.last_success_at else "-"
            nr = src.next_run_at.strftime("%Y-%m-%d %H:%M") if src.next_run_at else "-"
            print(f"{src.key:26} {'yes' if src.enabled else 'no':3} {src.health:12} {ls:20} {nr}")
        print("\nqueue:", queue_counts(s))
        for w in s.scalars(select(WorkerHeartbeat).order_by(WorkerHeartbeat.last_heartbeat_at.desc()).limit(5)):
            print(f"worker {w.worker_id} leader={w.is_scheduler_leader} last={w.last_heartbeat_at:%H:%M:%S} "
                  f"done={w.jobs_done} stopped={w.stopped_at is not None}")
    return 0


def cmd_doctor(_: argparse.Namespace) -> int:
    from hub.doctor import run_doctor

    return run_doctor()


def cmd_backup(_: argparse.Namespace) -> int:
    from hub.backup import create_backup

    p = create_backup()
    print(f"backup written: {p} ({p.stat().st_size} bytes)")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    from hub.backup import restore_backup

    if not args.yes:
        print("restore replaces data in the configured database; re-run with --yes to confirm", file=sys.stderr)
        return 1
    restore_backup(Path(args.file))
    print("restore complete")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="hub", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate").set_defaults(fn=cmd_migrate)
    co = sub.add_parser("create-owner")
    co.add_argument("--email")
    co.add_argument("--name")
    co.set_defaults(fn=cmd_create_owner)
    sv = sub.add_parser("serve")
    sv.add_argument("--port", type=int)
    sv.set_defaults(fn=cmd_serve)
    wk = sub.add_parser("worker")
    wk.add_argument("--concurrency", type=int)
    wk.add_argument("--no-scheduler", action="store_true")
    wk.set_defaults(fn=cmd_worker)
    sub.add_parser("start").set_defaults(fn=cmd_start)
    rs = sub.add_parser("run-source")
    rs.add_argument("key")
    rs.add_argument("--force", action="store_true", help="run even if disabled/paused")
    rs.set_defaults(fn=cmd_run_source)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    sub.add_parser("backup").set_defaults(fn=cmd_backup)
    rr = sub.add_parser("restore")
    rr.add_argument("file")
    rr.add_argument("--yes", action="store_true")
    rr.set_defaults(fn=cmd_restore)
    args = p.parse_args(argv)
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
