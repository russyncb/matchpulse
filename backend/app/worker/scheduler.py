"""A tiny scheduler. Job timing lives in the database, so restarting the worker resumes the
schedule instead of re-running every job at once."""
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models import SyncStatus
from app.services import sync_status

logger = logging.getLogger("matchpulse.worker")
HEARTBEAT = "worker_heartbeat"


@dataclass
class JobResult:
    status: str  # "ok" | "skipped" | "failed"
    note: str = ""


@dataclass
class Job:
    name: str
    interval: timedelta
    run: Callable[[], JobResult]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Scheduler:
    def __init__(self, jobs: list[Job], *, session_factory=SessionLocal, clock=_utcnow,
                 tick_seconds: float = 5, retry_interval: timedelta = timedelta(minutes=5)):
        self.jobs = jobs
        self._session_factory = session_factory
        self._clock = clock
        self.tick_seconds = tick_seconds
        self.retry_interval = retry_interval

    def run_once(self) -> list[str]:
        """Run every job that is due. Returns the names of the jobs that ran."""
        now = self._clock()
        with self._session_factory() as db:
            sync_status.record(db, HEARTBEAT, "ok", "alive", now)
            rows = {r.job_name: r for r in db.query(SyncStatus).filter(
                SyncStatus.job_name.in_([j.name for j in self.jobs]))}
        ran = []
        for job in self.jobs:
            if self._is_due(job, rows.get(job.name), now):
                self._execute(job)
                ran.append(job.name)
        return ran

    def run_forever(self, stop: threading.Event) -> None:
        logger.info("worker started with jobs: %s", [j.name for j in self.jobs] or "none")
        while not stop.is_set():
            try:
                self.run_once()
            except Exception:  # even a database blip must not kill the worker
                logger.exception("scheduler tick failed")
            stop.wait(self.tick_seconds)
        logger.info("worker stopped")

    def _is_due(self, job: Job, row: SyncStatus | None, now: datetime) -> bool:
        if row is None or row.last_attempt_at is None:
            return True
        wait = job.interval if row.consecutive_failures == 0 else min(job.interval, self.retry_interval)
        return now >= row.last_attempt_at + wait

    def _execute(self, job: Job) -> None:
        try:
            result = job.run()
        except Exception as exc:  # one broken job must not stop the others
            logger.exception("job %s crashed", job.name)
            result = JobResult("failed", f"{type(exc).__name__}: {exc}")
        if result.status == "failed":
            logger.warning("job %s failed: %s", job.name, result.note)
        with self._session_factory() as db:
            sync_status.record(db, job.name, result.status, result.note, self._clock())
