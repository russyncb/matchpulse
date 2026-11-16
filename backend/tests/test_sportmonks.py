import copy
import json
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models import Match, MatchEvent, MatchStatus
from app.providers.errors import AuthError, BadResponse, ProviderDown, RateLimited
from app.providers.gateway import ProviderGateway
from app.providers.sportmonks import STATE_MAP, SportmonksProvider, parse_fixture
from app.services.sync import sync_fixtures
from tests.test_gateway import Clock

FIXTURES = Path(__file__).parent / "fixtures" / "sportmonks"
TOKEN = "secret-token-123"


def load(name):
    return json.loads((FIXTURES / name).read_text())


def cph():
    return copy.deepcopy(load("fixture_19713866_full.json")["data"])


def cel():
    return copy.deepcopy(load("fixture_19722750_full.json")["data"])


def make_event(**kw):
    base = {"id": 999001, "fixture_id": 19713866, "period_id": 7222789, "participant_id": 293, "type_id": 20,
            "player_name": "Test Player", "minute": 90, "extra_minute": None, "rescinded": None}
    return {**base, **kw}


# ---------------------------------------------------------------- parsing real samples
def test_parse_copenhagen_match():
    f = parse_fixture(cph())
    assert (f.provider, f.provider_id) == ("sportmonks", "19713866")
    assert (f.competition.provider_id, f.competition.name) == ("271", "Superliga")
    assert (f.home_team.name, f.away_team.name) == ("FC København", "Brøndby IF")
    assert f.kickoff == datetime(2026, 11, 8, 11, 0, tzinfo=timezone.utc)
    assert (f.status, f.provider_status) == (MatchStatus.FINISHED, "FT")
    assert (f.home_score, f.away_score) == (1, 0)
    assert f.elapsed_minute == 97  # 90 + 7 added minutes, from the periods include
    goal = next(e for e in f.events if e.event_type == "goal")
    assert (goal.provider_event_id, goal.elapsed_minute, goal.team_side) == ("158598430", 54, "home")
    assert goal.payload["player_name"] == "William Clem" and goal.payload["result"] == "1-0"
    assert sorted({e.event_type for e in f.events}) == ["goal", "substitution", "yellow_card"]


def test_parse_celtic_match_maps_var_and_penalty_events():
    f = parse_fixture(cel())
    assert (f.home_team.name, f.away_team.name, f.home_score, f.away_score) == ("Celtic", "Kilmarnock", 1, 0)
    types = [e.event_type for e in f.events]
    assert types.count("yellow_card") == 6 and "var" in types and "missed_penalty" in types
    cards = [e for e in f.events if e.event_type == "yellow_card"]
    assert sum(e.team_side == "away" for e in cards) == 5  # five Kilmarnock cards, one Celtic
    var = [e for e in f.events if e.event_type == "var"]
    assert {e.payload["addition"] for e in var} == {"Goal disallowed", "Penalty awarded"}


# ---------------------------------------------------------------- synthetic cases the samples lack
def test_red_card_and_stoppage_time():
    raw = cph()
    raw["events"] += [make_event(id=1, type_id=20, minute=90, extra_minute=3),
                      make_event(id=2, type_id=21, minute=45, extra_minute=2),
                      make_event(id=3, type_id=9999, minute=5)]
    events = {e.provider_event_id: e for e in parse_fixture(raw).events}
    assert events["1"].event_type == "red_card" and events["1"].elapsed_minute == 93
    assert events["1"].payload["extra_minute"] == 3 and events["1"].team_side == "away"
    assert events["2"].event_type == "yellow_red_card" and events["2"].elapsed_minute == 47
    assert events["3"].event_type == "other"


def test_rescinded_events_are_dropped():
    raw = cph()
    raw["events"].append(make_event(id=7, type_id=20, rescinded=True))
    assert "7" not in {e.provider_event_id for e in parse_fixture(raw).events}


@pytest.mark.parametrize("developer_name,expected", [
    ("NS", MatchStatus.NOT_STARTED), ("INPLAY_1ST_HALF", MatchStatus.IN_PLAY), ("HT", MatchStatus.HALF_TIME),
    ("INPLAY_2ND_HALF", MatchStatus.IN_PLAY), ("INPLAY_ET", MatchStatus.EXTRA_TIME),
    ("INPLAY_ET_SECOND_HALF", MatchStatus.EXTRA_TIME), ("INPLAY_PENALTIES", MatchStatus.PENALTIES),
    ("AET", MatchStatus.FINISHED), ("FT_PEN", MatchStatus.FINISHED), ("POSTPONED", MatchStatus.POSTPONED),
    ("CANCELLED", MatchStatus.CANCELLED), ("SUSPENDED", MatchStatus.SUSPENDED),
])
def test_state_mapping(developer_name, expected):
    raw = cph()
    raw["state"] = {"id": 0, "developer_name": developer_name}
    assert parse_fixture(raw).status == expected
    assert STATE_MAP[developer_name] == expected


def test_not_started_fixture_has_no_minute_and_zero_score():
    raw = cph()
    raw.update(periods=[], scores=[], events=[], state={"id": 1, "developer_name": "NS"})
    f = parse_fixture(raw)
    assert (f.status, f.elapsed_minute, f.home_score, f.away_score, f.events) == (MatchStatus.NOT_STARTED, None, 0, 0, ())


@pytest.mark.parametrize("breakage", ["participants", "state", "league"])
def test_missing_include_is_rejected(breakage):
    raw = cph()
    del raw[breakage]
    with pytest.raises(ValueError):
        parse_fixture(raw)


def test_unknown_state_is_rejected_not_guessed():
    raw = cph()
    raw["state"] = {"id": 99, "developer_name": "SOMETHING_NEW"}
    with pytest.raises(ValueError):
        parse_fixture(raw)


# ---------------------------------------------------------------- the HTTP adapter (no network)
def provider(handler, **kw):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return SportmonksProvider(TOKEN, client=client, **kw)


def ok(data, **extra):
    return httpx.Response(200, json={"data": data, "rate_limit": {"remaining": 150, "resets_in_seconds": 600,
                                                                   "requested_entity": "Fixture"}, **extra})


def test_get_competitions():
    p = provider(lambda req: httpx.Response(200, json=load("leagues.json")))
    comps = p.get_competitions()
    assert [c.provider_id for c in comps] == ["271", "501", "513", "1659"]
    assert comps[1].name == "Premiership" and p.last_rate_limit["remaining"] == 178


def test_get_fixtures_filters_by_league_and_keeps_token_out_of_url():
    seen = []

    def handler(req):
        seen.append(req)
        return ok([cph(), cel()])

    fixtures = provider(handler).get_fixtures("271", date(2026, 11, 8), date(2026, 11, 8))
    assert [f.provider_id for f in fixtures] == ["19713866"]
    req = seen[0]
    assert req.url.path.endswith("/fixtures/between/2026-11-08/2026-11-08")
    assert "events" in req.url.params["include"] and TOKEN not in str(req.url)
    assert req.headers["Authorization"] == TOKEN


def test_live_fixtures_use_livescores_endpoint():
    seen = []
    provider(lambda req: (seen.append(req), ok([cel()]))[1]).get_live_fixtures()
    assert seen[0].url.path.endswith("/livescores")


def test_pagination_follows_has_more():
    def handler(req):
        page = int(req.url.params["page"])
        return ok([cph()] if page == 1 else [cel()],
                  pagination={"has_more": page == 1, "current_page": page})

    p = provider(handler)
    assert {f.provider_id for f in p.get_live_fixtures()} == {"19713866", "19722750"}


def test_empty_live_poll_is_normal():
    assert provider(lambda req: ok([])).get_live_fixtures() == []
    no_result = httpx.Response(200, json={"message": "No result(s) found matching your request."})
    assert provider(lambda req: no_result).get_live_fixtures() == []


def test_one_bad_fixture_is_skipped_but_all_bad_is_an_error():
    bad = {"id": 1, "league_id": 271}
    assert [f.provider_id for f in provider(lambda r: ok([bad, cph()])).get_live_fixtures()] == ["19713866"]
    with pytest.raises(BadResponse):
        provider(lambda r: ok([bad])).get_live_fixtures()


@pytest.mark.parametrize("response,error", [
    (httpx.Response(429, headers={"Retry-After": "30"}, json={}), RateLimited),
    (httpx.Response(401, json={"message": "Unauthenticated"}), AuthError),
    (httpx.Response(403, json={"message": "Not in your plan"}), AuthError),
    (httpx.Response(500, text="oops"), ProviderDown),
    (httpx.Response(503, text="oops"), ProviderDown),
    (httpx.Response(400, json={"message": "bad include"}), BadResponse),
    (httpx.Response(200, text="<html>not json</html>"), BadResponse),
    (httpx.Response(200, json={"message": "something odd"}), BadResponse),
])
def test_http_failures_become_typed_errors_without_leaking_the_token(response, error):
    with pytest.raises(error) as exc:
        provider(lambda req: response).get_live_fixtures()
    assert TOKEN not in str(exc.value)


def test_rate_limit_retry_after_comes_from_header_or_body():
    with pytest.raises(RateLimited) as exc:
        provider(lambda r: httpx.Response(429, headers={"Retry-After": "30"}, json={})).get_live_fixtures()
    assert exc.value.retry_after == 30
    with pytest.raises(RateLimited) as exc:
        provider(lambda r: httpx.Response(429, json={"rate_limit": {"resets_in_seconds": 1234}})).get_live_fixtures()
    assert exc.value.retry_after == 1234


def test_timeouts_and_connection_errors_become_provider_down():
    def timeout(req): raise httpx.ReadTimeout("slow", request=req)
    def refused(req): raise httpx.ConnectError("refused", request=req)
    for handler in (timeout, refused):
        with pytest.raises(ProviderDown):
            provider(handler).get_live_fixtures()


def test_missing_token_is_a_clear_error():
    with pytest.raises(ValueError):
        SportmonksProvider(None)


# ---------------------------------------------------------------- end to end: adapter -> gateway -> database
def test_end_to_end_sync_into_database(db):
    p = provider(lambda req: ok([cph(), cel()]))
    gateway = ProviderGateway(p, session_factory=SessionLocal, clock=Clock(), sleep=lambda s: None)

    result = sync_fixtures(gateway, db, "271", date(2026, 11, 8), date(2026, 11, 8))
    assert result.ok and result.ingest.matches_created == 1 and result.ingest.events_created == 7

    match = db.scalars(select(Match)).one()
    assert (match.provider, match.provider_id, match.status) == ("sportmonks", "19713866", MatchStatus.FINISHED)
    assert (match.home_team.name, match.home_score, match.away_score, match.elapsed_minute) == ("FC København", 1, 0, 97)
    goal = db.scalars(select(MatchEvent).where(MatchEvent.event_type == "goal")).one()
    assert goal.team_id == match.home_team_id and goal.payload["player_name"] == "William Clem"

    again = sync_fixtures(gateway, db, "271", date(2026, 11, 8), date(2026, 11, 8))  # idempotent
    assert again.ingest.matches_created == 0 and again.ingest.events_created == 0
    assert db.scalar(select(func.count()).select_from(MatchEvent)) == 7
