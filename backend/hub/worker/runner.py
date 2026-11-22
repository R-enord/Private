"""Worker process: scheduler (if leader) + job runner threads + heartbeat.

Start with ``hub worker``. It is independent of the web server and the browser: as long as this process and
PostgreSQL run, sources keep being collected. Safe to run several workers; SIGTERM/SIGINT stop it gracefully
(no new claims; in-flight jobs finish or are recovered by another worker after a crash).
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import signal
import socket
import threading
import time
import traceback
import uuid
from typing import Any

from sqlalchemy import text

from hub.config import get_settings
from hub.db import get_engine, session_scope
from hub.models import WorkerHeartbeat
from hub.settings_store import get_setting
from hub.worker import queue, scheduler
from hub.worker.jobs import AI_JOB_TYPES, HANDLERS, Defer

log = logging.getLogger("hub.worker")


class Worker:
    def __init__(self, concurrency: int | None = None, *, run_scheduler: bool = True, poll_s: float = 1.0,
                 heartbeat_s: float = 10.0, worker_id: str | None = None):
        settings = get_settings()
        self.settings = settings
        low = settings.low_resource_mode
        self.concurrency = 1 if low else max(1, concurrency or settings.worker_concurrency)
        self.run_scheduler = run_scheduler
        self.poll_s = poll_s
        self.heartbeat_s = heartbeat_s
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
        self.stop_event = threading.Event()
        self.ai_slots = threading.Semaphore(max(1, settings.ai_max_concurrency))
        self.is_leader = False
        self.jobs_done = 0
        self.jobs_failed = 0
        self._current: dict[int, int | None] = {}
        self._threads: list[threading.Thread] = []
        self._leader_conn: Any = None

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self._heartbeat(initial=True)
        self._threads.append(threading.Thread(target=self._heartbeat_loop, name="heartbeat", daemon=True))
        if self.run_scheduler:
            self._threads.append(threading.Thread(target=self._scheduler_loop, name="scheduler", daemon=True))
        for n in range(self.concurrency):
            self._threads.append(threading.Thread(target=self._job_loop, args=(n,), name=f"jobs-{n}", daemon=True))
        for t in self._threads:
            t.start()
        log.info("worker %s started (concurrency=%d, scheduler=%s)", self.worker_id, self.concurrency,
                 self.run_scheduler)

    def stop(self, timeout: float = 30.0) -> None:
        self.stop_event.set()
        deadline = time.monotonic() + timeout
        for t in self._threads:
            t.join(max(0.1, deadline - time.monotonic()))
        self._release_leader()
        with session_scope() as s:
            hb = s.get(WorkerHeartbeat, self.worker_id)
            if hb:
                hb.stopped_at = dt.datetime.now(dt.UTC)
                hb.last_heartbeat_at = hb.stopped_at
                hb.is_scheduler_leader = False
                hb.current_job_id = None
                hb.jobs_done = self.jobs_done
                hb.jobs_failed = self.jobs_failed
        log.info("worker %s stopped", self.worker_id)

    def run_forever(self) -> None:
        def _sig(signum: int, _frame: Any) -> None:
            log.info("signal %s received; shutting down", signum)
            self.stop_event.set()

        signal.signal(signal.SIGTERM, _sig)
        signal.signal(signal.SIGINT, _sig)
        self.start()
        while not self.stop_event.is_set():
            time.sleep(0.5)
        self.stop()

    # ------------------------------------------------------------------ heartbeat
    def _heartbeat(self, initial: bool = False) -> None:
        now = dt.datetime.now(dt.UTC)
        with session_scope() as s:
            hb = s.get(WorkerHeartbeat, self.worker_id)
            if hb is None:
                hb = WorkerHeartbeat(worker_id=self.worker_id, hostname=socket.gethostname(), pid=os.getpid(),
                                     roles=["scheduler", "jobs"] if self.run_scheduler else ["jobs"], started_at=now)
                s.add(hb)
            hb.last_heartbeat_at = now
            hb.is_scheduler_leader = self.is_leader
            hb.jobs_done = self.jobs_done
            hb.jobs_failed = self.jobs_failed
            hb.current_job_id = next((j for j in self._current.values() if j), None)
            hb.stopped_at = None
            if not initial:
                n = queue.recover_stale(s, self.settings.job_lock_timeout_s, heartbeat_stale_s=int(self.heartbeat_s * 6))
                if n:
                    log.warning("recovered %d stale job(s)", n)

    def _heartbeat_loop(self) -> None:
        while not self.stop_event.wait(self.heartbeat_s):
            try:
                self._heartbeat()
            except Exception:  # noqa: BLE001
                log.exception("heartbeat failed")

    # ------------------------------------------------------------------ scheduler
    def _acquire_leader(self) -> bool:
        if self._leader_conn is None:
            self._leader_conn = get_engine().connect()
        ok = scheduler.try_acquire_leadership(self._leader_conn)
        self._leader_conn.commit()
        return ok

    def _release_leader(self) -> None:
        if self._leader_conn is not None:
            try:
                if self.is_leader:
                    scheduler.release_leadership(self._leader_conn)
                    self._leader_conn.commit()
            finally:
                self._leader_conn.close()
                self._leader_conn = None
                self.is_leader = False

    def _scheduler_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                if not self.is_leader:
                    self.is_leader = self._acquire_leader()
                else:
                    # make sure our session-level lock connection is alive
                    self._leader_conn.execute(text("SELECT 1"))
                if self.is_leader:
                    with session_scope() as s:
                        res = scheduler.tick(s)
                    if res.get("sources") or res.get("periodic"):
                        log.info("scheduled: %s", res)
            except Exception:  # noqa: BLE001
                log.exception("scheduler tick failed; dropping leadership")
                self.is_leader = False
                if self._leader_conn is not None:
                    try:
                        self._leader_conn.close()
                    except Exception:  # noqa: BLE001
                        log.debug("leader connection close failed", exc_info=True)
                    self._leader_conn = None
            self.stop_event.wait(self.settings.scheduler_tick_s)

    # ------------------------------------------------------------------ jobs
    def run_one(self, slot: int = 0) -> bool:
        """Claim and execute at most one job. Returns True if a job ran."""
        with session_scope() as s:
            if get_setting(s, "global_pause", False):
                return False
            paused_types = list(get_setting(s, "paused_job_types", []) or [])
        ai_acquired = self.ai_slots.acquire(blocking=False)
        exclude = paused_types + ([] if ai_acquired else AI_JOB_TYPES)
        try:
            with session_scope() as s:
                job = queue.claim(s, self.worker_id, exclude_types=exclude)
                if job is None:
                    return False
                job_id, job_type, payload = job.id, job.type, dict(job.payload or {})
            if ai_acquired and job_type not in AI_JOB_TYPES:
                self.ai_slots.release()  # don't hold the AI slot during non-AI work
                ai_acquired = False
            self._current[slot] = job_id
            handler = HANDLERS.get(job_type)
            try:
                if handler is None:
                    raise RuntimeError(f"no handler for job type {job_type!r}")
                result = handler(payload, job_id)
            except Defer as d:
                with session_scope() as s:
                    queue.defer(s, job_id, dt.datetime.now(dt.UTC) + dt.timedelta(seconds=d.seconds), d.reason)
            except Exception as e:  # noqa: BLE001
                self.jobs_failed += 1
                with session_scope() as s:
                    status = queue.fail(s, job_id, f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=5)}")
                log.warning("job %s (%s) failed -> %s: %s", job_id, job_type, status, e)
            else:
                self.jobs_done += 1
                with session_scope() as s:
                    queue.complete(s, job_id, result)
            finally:
                self._current[slot] = None
            return True
        finally:
            if ai_acquired:
                self.ai_slots.release()

    def _job_loop(self, slot: int) -> None:
        while not self.stop_event.is_set():
            try:
                ran = self.run_one(slot)
            except Exception:  # noqa: BLE001
                log.exception("job loop error")
                ran = False
            if not ran:
                self.stop_event.wait(self.poll_s)
