import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.models import (
    Alert, AlertState, Competition, Match, MatchEvent, MatchSnapshot, Rule, Team,
)

KICKOFF = "2026-11-14T15:00:00+00:00"


@pytest.fixture
def match(db):
    comp = Competition(provider="test", provider_id="c1", name="Test League")
    home = Team(provider="test", provider_id="t1", name="Home FC")
    away = Team(provider="test", provider_id="t2", name="Away FC")
    db.add_all([comp, home, away])
    db.flush()
    m = Match(provider="test", provider_id="m1", competition_id=comp.id,
              home_team_id=home.id, away_team_id=away.id, kickoff=KICKOFF)
    db.add(m)
    db.commit()
    return m


@pytest.fixture
def rule(db):
    r = Rule(name="Two goal lead", condition={"field": "goal_difference", "operator": ">=", "value": 2})
    db.add(r)
    db.commit()
    return r


def test_relationships_load(db, match):
    assert match.competition.name == "Test League"
    assert match.home_team.name == "Home FC"
    assert match.away_team.name == "Away FC"
    assert match.status.value == "NOT_STARTED" and match.home_score == 0


def test_provider_id_unique_per_provider(db):
    db.add(Team(provider="a", provider_id="1", name="X"))
    db.commit()
    db.add(Team(provider="b", provider_id="1", name="Y"))  # other provider: allowed
    db.commit()
    db.add(Team(provider="a", provider_id="1", name="Z"))  # duplicate: rejected
    with pytest.raises(IntegrityError):
        db.commit()


def test_match_needs_two_different_teams(db, match):
    db.add(Match(provider="test", provider_id="m2", competition_id=match.competition_id,
                 home_team_id=match.home_team_id, away_team_id=match.home_team_id, kickoff=KICKOFF))
    with pytest.raises(IntegrityError):
        db.commit()


def test_negative_score_rejected(db, match):
    match.home_score = -1
    with pytest.raises(IntegrityError):
        db.commit()


def test_event_ingestion_is_idempotent(db, match):
    db.add(MatchEvent(provider="test", provider_event_id="e1", match_id=match.id, event_type="goal"))
    db.commit()
    db.add(MatchEvent(provider="test", provider_event_id="e1", match_id=match.id, event_type="goal"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_only_one_active_alert_per_match_and_rule(db, match, rule):
    db.add(Alert(match_id=match.id, rule_id=rule.id, observed_values={"goal_difference": 2}))
    db.commit()
    db.add(Alert(match_id=match.id, rule_id=rule.id))
    with pytest.raises(IntegrityError):
        db.commit()


def test_new_active_alert_allowed_after_previous_resolved(db, match, rule):
    first = Alert(match_id=match.id, rule_id=rule.id)
    db.add(first)
    db.commit()
    first.state = AlertState.RESOLVED
    first.resolved_at = func.now()
    db.commit()
    db.add(Alert(match_id=match.id, rule_id=rule.id))
    db.commit()
    assert db.scalar(select(func.count()).select_from(Alert)) == 2  # history retained


def test_resolved_alert_requires_resolved_at(db, match, rule):
    db.add(Alert(match_id=match.id, rule_id=rule.id, state=AlertState.RESOLVED))
    with pytest.raises(IntegrityError):
        db.commit()


def test_deleting_match_cascades_but_competition_is_protected(db, match, rule):
    db.add_all([
        MatchEvent(provider="test", provider_event_id="e1", match_id=match.id, event_type="goal"),
        MatchSnapshot(match_id=match.id, elapsed_minute=10, statistics={"shots": 3}),
        Alert(match_id=match.id, rule_id=rule.id),
    ])
    db.commit()
    db.delete(db.get(Competition, match.competition_id))  # still referenced by the match
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    db.delete(db.get(Match, match.id))
    db.commit()
    for table in ("match_events", "match_snapshots", "alerts"):
        assert db.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0
