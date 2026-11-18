from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models import Alert, Match, MatchEvent, MatchSnapshot, MatchStatus, Rule, SyncStatus
from app.providers.gateway import ProviderGateway
from app.providers.replay import ReplayProvider
from app.rules.catalogue import seed_catalogue
from app.worker.jobs import WorkerContext, build_jobs
from app.worker.scheduler import Job, JobResult, Scheduler
from tests.test_gateway import Clock, ScriptedProvider

T0 = datetime(2026, 11, 14, 15, 0, tzinfo=timezone.utc)


class FixedClock(Clock):
    def __init__(self, start=T0):
        self.now = start


def replay_scheduler(clock, provider=None, **ctx_kw):
    with SessionLocal() as seed_db:
        seed_catalogue(seed_db)  # the worker does this at startup
    gateway = ProviderGateway(provider or ReplayProvider(), session_factory=SessionLocal, clock=clock,
                              sleep=lambda s: None)
    ctx = WorkerContext(gateway=gateway, session_factory=SessionLocal, clock=clock, always_poll=True, **ctx_kw)
    return Scheduler(build_jobs(ctx, live_seconds=60), session_factory=SessionLocal, clock=clock)


# ------------------------------------------------ the whole pipeline, end to end
def test_worker_plays_a_full_match_and_survives_restart(db):
    clock = FixedClock()
    provider = ReplayProvider()  # stands in for the external API; it keeps its own state across restarts
    scheduler = replay_scheduler(clock, provider)

    first = scheduler.run_once()  # fresh database: every job is due
    assert first == ["competitions", "fixtures", "live", "reconcile", "rules"]
    for _ in range(6):  # the remaining steps of the scripted match, one poll per minute
        clock.advance(seconds=60)
        assert "live" in scheduler.run_once()

    match = db.scalars(select(Match)).one()
    assert (match.status, match.home_score, match.away_score) == (MatchStatus.FINISHED, 2, 1)
    assert db.scalar(select(func.count()).select_from(MatchEvent)) == 4

    history = db.execute(select(MatchSnapshot.status, MatchSnapshot.home_score, MatchSnapshot.away_score)
                         .order_by(MatchSnapshot.recorded_at, MatchSnapshot.id)).all()
    assert history == [("IN_PLAY", 0, 0), ("IN_PLAY", 1, 0), ("IN_PLAY", 2, 0), ("HALF_TIME", 2, 0),
                       ("IN_PLAY", 2, 0), ("IN_PLAY", 2, 1), ("FINISHED", 2, 1)]  # score changes recorded

    # the rules engine saw the whole match: alerts were raised as conditions became true...
    rows = db.execute(select(Alert.id)).all()
    by_code = {code: (state.value, (resolved or {}).get("reason")) for code, state, resolved in db.execute(
        select(Rule.code, Alert.state, Alert.resolved_values).join(Rule, Rule.id == Alert.rule_id))}
    assert by_code == {
        "R001": ("RESOLVED", "condition_false"),  # lead of two shrank to one at 82'
        "R002": ("RESOLVED", "match_ended"), "R005": ("RESOLVED", "match_ended"),
        "R006": ("RESOLVED", "match_ended"), "R007": ("RESOLVED", "match_ended"),
        "R008": ("RESOLVED", "match_ended"),
    }
    assert len(rows) == 6 and not db.scalars(select(Alert).where(Alert.state == "ACTIVE")).all()

    # restart: a brand new scheduler must not re-run jobs that are not due
    assert replay_scheduler(clock, provider).run_once() == []
    snapshots_before = db.scalar(select(func.count()).select_from(MatchSnapshot))
    clock.advance(seconds=60)
    replay_scheduler(clock, provider).run_once()  # only the live job is due again
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(MatchSnapshot)) == snapshots_before  # nothing changed
    assert db.scalar(select(func.count()).select_from(MatchEvent)) == 4  # events stored idempotently


def test_job_status_is_tracked(db):
    clock = FixedClock()
    replay_scheduler(clock).run_once()
    rows = {r.job_name: r for r in db.scalars(select(SyncStatus))}
    assert set(rows) == {"worker_heartbeat", "competitions", "fixtures", "live", "reconcile", "rules"}
    assert rows["live"].last_success_at == T0 and "1 matches" in rows["live"].last_result
    assert rows["reconcile"].last_result == "nothing stale" and rows["reconcile"].last_success_at is None


# ------------------------------------------------ scheduler behaviour
def make_scheduler(jobs, clock):
    return Scheduler(jobs, session_factory=SessionLocal, clock=clock, retry_interval=timedelta(minutes=5))


def test_jobs_run_only_when_due():
    clock, calls = FixedClock(), []
    job = Job("tick", timedelta(minutes=10), lambda: (calls.append(1), JobResult("ok", "x"))[1])
    sched = make_scheduler([job], clock)
    assert sched.run_once() == ["tick"]
    clock.advance(minutes=9)
    assert sched.run_once() == []
    clock.advance(minutes=1)
    assert sched.run_once() == ["tick"] and len(calls) == 2


def test_crashing_job_is_recorded_and_does_not_stop_others(db):
    clock, ran = FixedClock(), []

    def boom(): raise RuntimeError("kaput")

    sched = make_scheduler([Job("bad", timedelta(hours=6), boom),
                            Job("good", timedelta(hours=6), lambda: (ran.append(1), JobResult("ok", "fine"))[1])], clock)
    assert sched.run_once() == ["bad", "good"] and ran == [1]
    bad = db.get(SyncStatus, "bad")
    assert bad.consecutive_failures == 1 and "kaput" in bad.last_error and bad.last_success_at is None


def test_failed_job_retries_sooner_than_its_normal_interval(db):
    clock, outcomes = FixedClock(), [JobResult("failed", "down"), JobResult("failed", "down"), JobResult("ok", "back")]
    sched = make_scheduler([Job("sync", timedelta(hours=6), lambda: outcomes.pop(0))], clock)
    sched.run_once()
    clock.advance(minutes=4)
    assert sched.run_once() == []  # still waiting for the 5 minute retry
    clock.advance(minutes=1)
    assert sched.run_once() == ["sync"]  # second failure
    assert db.get(SyncStatus, "sync").consecutive_failures == 2
    clock.advance(minutes=5)
    sched.run_once()
    db.expire_all()
    row = db.get(SyncStatus, "sync")
    assert row.consecutive_failures == 0 and row.last_result == "back"
    clock.advance(minutes=30)
    assert sched.run_once() == []  # healthy again: back to the 6 hour rhythm


def test_heartbeat_even_without_jobs(db):
    clock = FixedClock()
    make_scheduler([], clock).run_once()
    assert db.get(SyncStatus, "worker_heartbeat").last_attempt_at == T0


# ------------------------------------------------ quota protection
def seed_match(db, *, status, kickoff, provider="replay"):
    from app.providers.models import CompetitionData, FixtureData, TeamData
    from app.services.ingestion import IngestionService
    f = FixtureData(provider, "M1", CompetitionData(provider, "C1", "League"), TeamData(provider, "T1", "A"),
                    TeamData(provider, "T2", "B"), kickoff, status=status)
    IngestionService(db).ingest_fixtures([f])
    return db.scalars(select(Match)).one()


@pytest.mark.parametrize("status,kickoff_offset,expected", [
    (MatchStatus.IN_PLAY, timedelta(hours=-1), True),
    (MatchStatus.HALF_TIME, timedelta(minutes=-50), True),
    (MatchStatus.NOT_STARTED, timedelta(minutes=10), True),     # about to start
    (MatchStatus.NOT_STARTED, timedelta(minutes=-20), True),    # should have started
    (MatchStatus.NOT_STARTED, timedelta(hours=2), False),
    (MatchStatus.FINISHED, timedelta(hours=-2), False),
    (MatchStatus.POSTPONED, timedelta(minutes=5), False),
    (MatchStatus.IN_PLAY, timedelta(hours=-8), False),          # frozen leftover, not worth polling for
])
def test_active_match_detection(db, status, kickoff_offset, expected):
    from app.services.activity import has_active_matches
    seed_match(db, status=status, kickoff=T0 + kickoff_offset)
    assert has_active_matches(db, T0) is expected


def test_live_job_makes_no_request_when_nothing_is_active(db):
    from app.worker.jobs import live_job
    provider = ScriptedProvider()
    seed_match(db, status=MatchStatus.NOT_STARTED, kickoff=T0 + timedelta(hours=5), provider="fake")
    clock = FixedClock()
    gateway = ProviderGateway(provider, session_factory=SessionLocal, clock=clock)
    result = live_job(WorkerContext(gateway=gateway, session_factory=SessionLocal, clock=clock))
    assert result.status == "skipped" and provider.calls == 0


def test_provider_outage_is_reported_not_raised(db):
    from app.providers.errors import ProviderDown
    from app.worker.jobs import live_job
    clock = FixedClock()
    gateway = ProviderGateway(ScriptedProvider(*[ProviderDown("boom")] * 3), session_factory=SessionLocal,
                              clock=clock, sleep=lambda s: None)
    result = live_job(WorkerContext(gateway=gateway, session_factory=SessionLocal, clock=clock, always_poll=True))
    assert result.status == "failed" and "boom" in result.note


# ------------------------------------------------ snapshots
def test_periodic_snapshot_only_while_live(db):
    from app.services.snapshots import record_snapshots
    match = seed_match(db, status=MatchStatus.IN_PLAY, kickoff=T0)
    five = timedelta(minutes=5)
    assert record_snapshots(db, [match.id], T0, five) == 1                       # first sighting
    assert record_snapshots(db, [match.id], T0 + timedelta(minutes=3), five) == 0  # nothing changed
    assert record_snapshots(db, [match.id], T0 + timedelta(minutes=5), five) == 1  # periodic
    match.status = MatchStatus.FINISHED
    db.commit()
    assert record_snapshots(db, [match.id], T0 + timedelta(minutes=6), five) == 1  # status change
    assert record_snapshots(db, [match.id], T0 + timedelta(minutes=30), five) == 0  # finished: no more


def test_not_started_matches_get_no_snapshots(db):
    from app.services.snapshots import record_snapshots
    match = seed_match(db, status=MatchStatus.NOT_STARTED, kickoff=T0)
    assert record_snapshots(db, [match.id], T0, timedelta(minutes=5)) == 0
