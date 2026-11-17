import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import Competition
from app.providers.gateway import ProviderGateway
from app.services.activity import has_active_matches, stale_matches
from app.services.snapshots import record_snapshots
from app.services.sync import SyncResult, sync_competitions, sync_fixtures, sync_live
from app.worker.scheduler import Job, JobResult

logger = logging.getLogger("matchpulse.jobs")


@dataclass
class WorkerContext:
    gateway: ProviderGateway
    session_factory: Callable
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    tracked_competitions: list[str] = field(default_factory=list)  # empty = all stored ones
    days_back: int = 1
    days_ahead: int = 7
    snapshot_interval: timedelta = timedelta(minutes=5)
    stale_after: timedelta = timedelta(minutes=10)
    always_poll: bool = False  # demo/replay mode: skip the "is anything live?" check


def _from_sync(sync: SyncResult, note: str) -> JobResult:
    if sync.ok:
        return JobResult("ok", note)
    return JobResult("skipped" if sync.skipped else "failed", sync.error or "")


def _combine(results: list[JobResult]) -> JobResult:
    for status in ("failed", "skipped"):
        bad = [r for r in results if r.status == status]
        if bad:
            return JobResult(status, bad[0].note)
    return JobResult("ok", "; ".join(r.note for r in results))


def competitions_job(ctx: WorkerContext) -> JobResult:
    with ctx.session_factory() as db:
        sync = sync_competitions(ctx.gateway, db)
    return _from_sync(sync, f"{sync.ingest.competitions_created} new competitions" if sync.ok else "")


def fixtures_job(ctx: WorkerContext) -> JobResult:
    today = ctx.clock().date()
    with ctx.session_factory() as db:
        ids = ctx.tracked_competitions or list(db.scalars(
            select(Competition.provider_id).where(
                Competition.provider == ctx.gateway.name, Competition.active.is_(True))))
        results = []
        for comp_id in ids:
            sync = sync_fixtures(ctx.gateway, db, comp_id, today - timedelta(days=ctx.days_back),
                                 today + timedelta(days=ctx.days_ahead))
            results.append(_from_sync(sync, f"{comp_id}: {sync.ingest.matches_created} new, "
                                      f"{sync.ingest.matches_updated} updated" if sync.ok else ""))
    return _combine(results) if results else JobResult("skipped", "no competitions to sync yet")


def live_job(ctx: WorkerContext) -> JobResult:
    now = ctx.clock()
    with ctx.session_factory() as db:
        if not ctx.always_poll and not has_active_matches(db, now, provider=ctx.gateway.name):
            return JobResult("skipped", "no active matches (no API request made)")
        sync = sync_live(ctx.gateway, db)
        if not sync.ok:
            return _from_sync(sync, "")
        r = sync.ingest
        snaps = record_snapshots(db, r.match_ids, now, ctx.snapshot_interval)
        return JobResult("ok", f"{len(r.match_ids)} matches, {snaps} snapshots, "
                               f"{r.events_created} new events, {r.events_deleted} removed")


def reconcile_job(ctx: WorkerContext) -> JobResult:
    """Re-fetch matches that look frozen in a live state (e.g. after a provider outage)."""
    now = ctx.clock()
    with ctx.session_factory() as db:
        rows = stale_matches(db, now, ctx.stale_after, provider=ctx.gateway.name)
        if not rows:
            return JobResult("skipped", "nothing stale")
        by_comp: dict[str, list[datetime]] = {}
        for match, comp_id in rows:
            by_comp.setdefault(comp_id, []).append(match.kickoff)
        results = [
            _from_sync(sync_fixtures(ctx.gateway, db, comp_id, min(k).date(), max(k).date()),
                       f"re-checked {len(k)} stale matches in {comp_id}")
            for comp_id, k in by_comp.items()
        ]
    return _combine(results)


def build_jobs(ctx: WorkerContext, *, live_seconds: int = 60) -> list[Job]:
    return [
        Job("competitions", timedelta(hours=6), lambda: competitions_job(ctx)),
        Job("fixtures", timedelta(hours=6), lambda: fixtures_job(ctx)),
        Job("live", timedelta(seconds=live_seconds), lambda: live_job(ctx)),
        Job("reconcile", timedelta(minutes=10), lambda: reconcile_job(ctx)),
    ]
