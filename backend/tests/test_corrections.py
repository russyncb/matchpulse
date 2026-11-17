import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models import Match, MatchEvent, MatchStatus
from app.providers.gateway import ProviderGateway
from app.providers.sportmonks import SportmonksProvider, parse_fixture
from app.services.ingestion import IngestionService
from app.worker.jobs import WorkerContext, reconcile_job
from tests.test_gateway import Clock
from tests.test_sportmonks import cph, make_event


def n_events(db):
    return db.scalar(select(func.count()).select_from(MatchEvent))


def test_cancelled_event_is_removed_on_next_sync(db):
    raw = cph()
    raw["events"].append(make_event(id=777, type_id=20, minute=88))  # a red card
    first = IngestionService(db).ingest_fixtures([parse_fixture(raw)])
    assert first.events_created == 8 and n_events(db) == 8

    raw["events"][-1]["rescinded"] = True  # VAR overturned it: provider now flags it
    second = IngestionService(db).ingest_fixtures([parse_fixture(raw)])
    assert second.events_deleted == 1 and n_events(db) == 7
    assert db.scalars(select(MatchEvent).where(MatchEvent.event_type == "red_card")).first() is None


def test_event_missing_from_provider_list_is_removed(db):
    raw = cph()
    IngestionService(db).ingest_fixtures([parse_fixture(raw)])
    del raw["events"][0]  # the goal vanished from the provider's list
    result = IngestionService(db).ingest_fixtures([parse_fixture(raw)])
    assert result.events_deleted == 1
    assert db.scalars(select(MatchEvent).where(MatchEvent.event_type == "goal")).first() is None


def test_events_not_fetched_never_delete_anything(db):
    raw = cph()
    IngestionService(db).ingest_fixtures([parse_fixture(raw)])
    del raw["events"]  # include missing from the response
    fixture = parse_fixture(raw)
    assert fixture.events is None
    result = IngestionService(db).ingest_fixtures([fixture])
    assert result.events_deleted == 0 and n_events(db) == 7


def test_empty_event_list_from_provider_clears_events(db):
    raw = cph()
    IngestionService(db).ingest_fixtures([parse_fixture(raw)])
    raw["events"] = []
    IngestionService(db).ingest_fixtures([parse_fixture(raw)])
    assert n_events(db) == 0


def test_score_correction_updates_the_match_in_place(db):
    raw = cph()
    raw["state"] = {"id": 22, "developer_name": "INPLAY_2ND_HALF"}
    IngestionService(db).ingest_fixtures([parse_fixture(raw)])
    for s in raw["scores"]:  # goal disallowed: 1-0 becomes 0-0
        if s["description"] == "CURRENT":
            s["score"]["goals"] = 0
    result = IngestionService(db).ingest_fixtures([parse_fixture(raw)])
    assert result.matches_updated == 1 and result.matches_created == 0
    match = db.scalars(select(Match)).one()
    assert (match.home_score, match.away_score) == (0, 0)


def test_postponed_and_suspended_statuses_are_stored(db):
    for state, expected in (("POSTPONED", MatchStatus.POSTPONED), ("SUSPENDED", MatchStatus.SUSPENDED)):
        raw = cph()
        raw["state"] = {"id": 0, "developer_name": state}
        IngestionService(db).ingest_fixtures([parse_fixture(raw)])
        db.expire_all()
        assert db.scalars(select(Match)).one().status == expected


def test_frozen_live_match_is_refetched_and_finished(db):
    """After an outage a match can stay 'in play' forever; the reconcile job re-fetches it."""
    raw = cph()
    live = copy.deepcopy(raw)
    live["state"] = {"id": 22, "developer_name": "INPLAY_2ND_HALF"}
    IngestionService(db).ingest_fixtures([parse_fixture(live)])
    now = datetime(2026, 11, 8, 14, 0, tzinfo=timezone.utc)
    db.execute(Match.__table__.update().values(last_updated=now - timedelta(hours=1)))
    db.commit()

    provider = SportmonksProvider("tok", client=httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json={"data": [raw]}))))  # provider now says FT
    clock = Clock(); clock.now = now
    ctx = WorkerContext(gateway=ProviderGateway(provider, session_factory=SessionLocal, clock=clock,
                                                sleep=lambda s: None), session_factory=SessionLocal, clock=clock)
    result = reconcile_job(ctx)
    assert result.status == "ok" and "1 stale" in result.note
    db.expire_all()
    assert db.scalars(select(Match)).one().status == MatchStatus.FINISHED
