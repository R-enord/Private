from __future__ import annotations

import datetime as dt
import threading
import time

from sqlalchemy import func, select, text

from hub.db import get_engine, session_scope
from hub.models import Item, Job, Source, WorkerHeartbeat
from hub.settings_store import set_setting
from hub.worker import queue, scheduler
from hub.worker.jobs import set_test_overrides
from hub.worker.runner import Worker
from tests.conftest import no_sleep
from tests.fixture_transport import TRANSPORT


def test_enqueue_dedupes_active_jobs(db) -> None:
    with session_scope() as s:
        a = queue.enqueue(s, "rescore", dedupe_key="x")
        b = queue.enqueue(s, "rescore", dedupe_key="x")
        assert a is not None and b is None
    with session_scope() as s:
        job = queue.claim(s, "w1")
        queue.complete(s, job.id)
    with session_scope() as s:
        assert queue.enqueue(s, "rescore", dedupe_key="x") is not None, "can re-enqueue after completion"


def test_concurrent_claims_never_double_claim(db) -> None:
    with session_scope() as s:
        for i in range(40):
            queue.enqueue(s, "rescore", {"i": i})
    claimed: list[int] = []
    lock = threading.Lock()

    def worker(name: str) -> None:
        while True:
            with session_scope() as s:
                j = queue.claim(s, name)
                if j is None:
                    return
                with lock:
                    claimed.append(j.id)
                queue.complete(s, j.id)

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(claimed) == 40 and len(set(claimed)) == 40


def test_fail_backoff_and_dead_letter(db) -> None:
    with session_scope() as s:
        jid = queue.enqueue(s, "rescore", max_attempts=2)
    for expected in ("queued", "dead"):
        with session_scope() as s:
            s.execute(text("UPDATE jobs SET run_after = now() WHERE id = :i"), {"i": jid})
        with session_scope() as s:
            j = queue.claim(s, "w")
            assert j is not None and j.id == jid
            assert queue.fail(s, jid, "boom") == expected
        with session_scope() as s:
            j = s.get(Job, jid)
            if expected == "queued":
                assert j.run_after > dt.datetime.now(dt.UTC), "retry is delayed (backoff)"


def test_defer_does_not_consume_attempts(db) -> None:
    with session_scope() as s:
        jid = queue.enqueue(s, "ai_enrich", {"item_id": 1}, max_attempts=1)
    with session_scope() as s:
        queue.claim(s, "w")
        queue.defer(s, jid, dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1), "AI unavailable")
    with session_scope() as s:
        j = s.get(Job, jid)
        assert j.status == "queued" and j.attempts == 0


def test_stale_jobs_recovered_after_worker_crash(db) -> None:
    with session_scope() as s:
        jid = queue.enqueue(s, "rescore")
        s.add(WorkerHeartbeat(worker_id="dead-worker", hostname="h", pid=1, roles=["jobs"],
                              last_heartbeat_at=dt.datetime.now(dt.UTC) - dt.timedelta(minutes=10)))
    with session_scope() as s:
        queue.claim(s, "dead-worker")
    with session_scope() as s:
        assert queue.recover_stale(s, lock_timeout_s=900, heartbeat_stale_s=60) == 1
    with session_scope() as s:
        j = s.get(Job, jid)
        assert j.status == "queued" and "recovered" in j.last_error


def test_scheduler_enqueues_due_sources_once_and_respects_pause(db) -> None:
    now = dt.datetime.now(dt.UTC)
    with session_scope() as s:
        res1 = scheduler.tick(s, now)
    with session_scope() as s:
        res2 = scheduler.tick(s, now)
        enabled = s.scalar(select(func.count()).select_from(Source).where(Source.enabled.is_(True)))
        n_fetch = s.scalar(select(func.count()).select_from(Job).where(Job.type == "fetch_source"))
    assert len(res1["sources"]) == enabled and n_fetch == enabled
    assert res2["sources"] == [], "not re-enqueued while queued / not yet due"
    assert "rescore" in res1["periodic"] and "rescore" not in res2["periodic"]
    with session_scope() as s:
        s.execute(text("DELETE FROM jobs"))
        s.execute(text("UPDATE sources SET next_run_at = NULL"))
        set_setting(s, "global_pause", True)
    with session_scope() as s:
        assert scheduler.tick(s, now) == {"paused": True}
        assert s.scalar(select(func.count()).select_from(Job)) == 0
    with session_scope() as s:
        set_setting(s, "global_pause", False)
        src = s.scalar(select(Source).where(Source.key == "arxiv-ai"))
        src.paused = True
        other = s.scalar(select(Source).where(Source.key == "hf-trending-models"))
        other.backoff_until = now + dt.timedelta(hours=1)
    with session_scope() as s:
        res = scheduler.tick(s, now + dt.timedelta(seconds=1))
        assert "arxiv-ai" not in res["sources"] and "hf-trending-models" not in res["sources"]


def test_single_scheduler_leader() -> None:
    eng = get_engine()
    c1, c2 = eng.connect(), eng.connect()
    try:
        assert scheduler.try_acquire_leadership(c1) is True
        assert scheduler.try_acquire_leadership(c2) is False
        scheduler.release_leadership(c1)
        assert scheduler.try_acquire_leadership(c2) is True
        scheduler.release_leadership(c2)
    finally:
        c1.close()
        c2.close()


def test_worker_collects_with_no_web_server_and_survives_restart(db) -> None:
    """The worker alone (no API, no browser) runs the scheduler and ingests sources; a second worker
    instance picks up where a stopped one left off."""
    set_test_overrides(TRANSPORT, no_sleep)
    try:
        with session_scope() as s:
            set_setting(s, "ai_enabled", False)  # AI path tested separately
            s.execute(text("UPDATE sources SET host_min_spacing_s = 0"))
        w1 = Worker(concurrency=2, poll_s=0.1, heartbeat_s=0.5)
        w1.settings.scheduler_tick_s = 0.2  # type: ignore[misc]
        w1.start()
        deadline = time.monotonic() + 60
        items = 0
        while time.monotonic() < deadline:
            with session_scope() as s:
                items = s.scalar(select(func.count()).select_from(Item)) or 0
                done = s.scalar(select(func.count()).select_from(Job).where(
                    Job.type == "fetch_source", Job.status.in_(("succeeded",)))) or 0
            if done >= 5:
                break
            time.sleep(0.3)
        w1.stop()
        assert items > 0 and done >= 5
        with session_scope() as s:
            hb = s.get(WorkerHeartbeat, w1.worker_id)
            assert hb.stopped_at is not None and hb.jobs_done >= 5
            # simulate more work arriving after the first worker stopped
            queue.enqueue(s, "rescore", dedupe_key="after-restart")
        w2 = Worker(concurrency=1, poll_s=0.1, heartbeat_s=0.5, run_scheduler=False)
        w2.start()
        deadline = time.monotonic() + 30
        status = None
        while time.monotonic() < deadline:
            with session_scope() as s:
                status = s.scalar(select(Job.status).where(Job.dedupe_key == "after-restart"))
            if status == "succeeded":
                break
            time.sleep(0.2)
        w2.stop()
        assert status == "succeeded"
    finally:
        set_test_overrides(None, None)
        with session_scope() as s:
            s.execute(text("UPDATE sources SET host_min_spacing_s = 1"))
