from datetime import date, datetime, timezone

from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models import Match
from app.providers.errors import ProviderDown, QuotaExhausted
from app.providers.gateway import ProviderGateway
from app.providers.replay import ReplayProvider
from app.services.sync import sync_competitions, sync_fixtures, sync_live
from tests.test_gateway import Clock, ScriptedProvider


def make_gateway(provider, **kw):
    return ProviderGateway(provider, session_factory=SessionLocal, clock=Clock(), sleep=lambda s: None, **kw)


def test_sync_live_stores_matches(db):
    result = sync_live(make_gateway(ReplayProvider()), db)
    assert result.ok and result.ingest.matches_created == 1
    assert db.scalar(select(func.count()).select_from(Match)) == 1


def test_sync_competitions_and_fixtures(db):
    gw = make_gateway(ReplayProvider())
    assert sync_competitions(gw, db).ingest.competitions_created == 1
    result = sync_fixtures(gw, db, "REP-LG", date(2026, 11, 14), date(2026, 11, 15))
    assert result.ok and result.ingest.matches_created == 1


def test_provider_failure_returns_result_instead_of_crashing(db):
    broken = ScriptedProvider(*[ProviderDown("boom")] * 3)
    result = sync_live(make_gateway(broken), db)
    assert not result.ok and not result.skipped and "boom" in result.error
    again = sync_live(make_gateway(broken), db)  # now cooling down
    assert again.skipped and broken.calls == 3


def test_one_broken_provider_does_not_stop_another(db):
    broken = make_gateway(ScriptedProvider(QuotaExhausted("no quota")))
    good = make_gateway(ReplayProvider())
    results = [sync_live(g, db) for g in (broken, good)]
    assert [r.ok for r in results] == [False, True]


def test_bad_data_from_adapter_is_reported_not_raised(db):
    bad = ScriptedProvider(["this is not a FixtureData"])
    result = sync_live(make_gateway(bad), db)
    assert not result.ok and "ingestion failed" in result.error


def test_providers_status_endpoint(db, client):
    sync_live(make_gateway(ReplayProvider(), hourly_limit=100), db)
    # make_gateway uses a fixed 2026-11-16 12:30 clock, so check the shape only
    body = client.get("/api/v1/providers/status").json()
    assert body[0]["provider"] == "replay" and body[0]["status"] == "ok"
