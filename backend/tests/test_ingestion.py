from sqlalchemy import func, select

from app.models import Competition, Match, MatchEvent, MatchStatus, Team
from app.providers.models import synthetic_event_id
from app.providers.replay import ReplayProvider
from app.services.ingestion import IngestionService


def _count(db, model):
    return db.scalar(select(func.count()).select_from(model))


def test_first_ingest_creates_everything(db):
    provider = ReplayProvider()
    result = IngestionService(db).ingest_fixtures(provider.get_live_fixtures())
    assert (result.competitions_created, result.teams_created, result.matches_created) == (1, 2, 1)
    match = db.scalars(select(Match)).one()
    assert match.provider == "replay" and match.status == MatchStatus.IN_PLAY
    assert match.home_team.name == "Replay Reds"


def test_full_replay_updates_one_match_without_duplicates(db):
    provider = ReplayProvider()
    for _ in range(7):  # every step of the scenario
        IngestionService(db).ingest_fixtures(provider.get_live_fixtures())
    assert _count(db, Match) == 1 and _count(db, Competition) == 1 and _count(db, Team) == 2
    match = db.scalars(select(Match)).one()
    assert (match.status, match.home_score, match.away_score) == (MatchStatus.FINISHED, 2, 1)
    assert _count(db, MatchEvent) == 4  # cumulative events were not re-inserted


def test_repeated_ingestion_is_idempotent(db):
    fixtures = ReplayProvider().get_live_fixtures()
    IngestionService(db).ingest_fixtures(fixtures)
    again = IngestionService(db).ingest_fixtures(fixtures)
    assert again.matches_created == 0 and again.matches_updated == 1
    assert again.competitions_created == 0 and again.teams_created == 0 and again.events_created == 0


def test_events_are_linked_to_the_right_team(db):
    provider = ReplayProvider()
    for _ in range(5):
        IngestionService(db).ingest_fixtures(provider.get_live_fixtures())
    red_card = db.scalars(select(MatchEvent).where(MatchEvent.event_type == "red_card")).one()
    match = db.scalars(select(Match)).one()
    assert red_card.team_id == match.away_team_id and red_card.elapsed_minute == 58


def test_synthetic_event_id_is_stable_and_distinguishes_events():
    a = synthetic_event_id("m1", "goal", 23, "Smith")
    assert a == synthetic_event_id("m1", "goal", 23, "Smith")
    assert a != synthetic_event_id("m1", "goal", 24, "Smith")


def test_competitions_only_ingest(db):
    result = IngestionService(db).ingest_competitions(ReplayProvider().get_competitions())
    assert result.competitions_created == 1 and _count(db, Match) == 0
