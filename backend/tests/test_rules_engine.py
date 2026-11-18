"""Pure rule evaluation (no database) plus the alert lifecycle."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import Alert, AlertState, Match, MatchEvent, MatchSnapshot, MatchStatus, Rule
from app.providers.models import CompetitionData, EventData, FixtureData, TeamData
from app.rules.catalogue import CATALOGUE, seed_catalogue
from app.rules.engine import RuleEngine, evaluate
from app.rules.facts import build_facts
from app.rules.operators import compare
from app.rules.schema import RuleDefinition
from app.services.ingestion import IngestionService

T0 = datetime(2026, 11, 14, 16, 0, tzinfo=timezone.utc)
DEFS = {r["code"]: RuleDefinition.model_validate(r) for r in CATALOGUE}


# ---------------------------------------------------------------- operators and the catalogue, no database
@pytest.mark.parametrize("op,fact,value,expected", [
    ("eq", 2, 2, True), ("eq", 2, 3, False), ("gt", 3, 2, True), ("gt", 2, 2, False),
    ("gte", 2, 2, True), ("gte", 1, 2, False), ("lt", 1, 2, True), ("lt", 2, 2, False),
    ("lte", 2, 2, True), ("lte", 3, 2, False), ("eq", "IN_PLAY", "IN_PLAY", True),
    ("gte", None, 0, False), ("eq", None, 0, False), ("lt", None, 5, False),  # unknown never matches
])
def test_operators(op, fact, value, expected):
    assert compare(fact, op, value) is expected


def facts(**kw):
    base = dict(status="IN_PLAY", minute=60, home_score=0, away_score=0, total_goals=0, goal_difference=0,
                red_cards=0, last_goal_minute=None, score_age_seconds=None)
    return {**base, **kw}


@pytest.mark.parametrize("code,hit,miss", [
    ("R001", facts(goal_difference=2, minute=55), facts(goal_difference=2, minute=54)),
    ("R002", facts(goal_difference=1, minute=70), facts(goal_difference=2, minute=80)),
    ("R003", facts(total_goals=0, minute=60), facts(total_goals=0, minute=59)),
    ("R004", facts(goal_difference=0, minute=75), facts(goal_difference=1, minute=80)),
    ("R005", facts(red_cards=1), facts(red_cards=0)),
    ("R006", facts(last_goal_minute=81), facts(last_goal_minute=80)),
    ("R007", facts(total_goals=3), facts(total_goals=2)),
    ("R008", facts(total_goals=1, score_age_seconds=120), facts(total_goals=1, score_age_seconds=121)),
])
def test_catalogue_rules_fire_exactly_at_their_thresholds(code, hit, miss):
    assert evaluate(DEFS[code], hit).matched is True
    assert evaluate(DEFS[code], miss).matched is False


def test_unknown_facts_do_not_fire_rules():
    assert evaluate(DEFS["R001"], facts(goal_difference=3, minute=None)).matched is False
    assert evaluate(DEFS["R006"], facts(last_goal_minute=None)).matched is False
    assert evaluate(DEFS["R008"], facts(total_goals=2, score_age_seconds=None)).matched is False


def test_evaluation_reports_the_values_it_looked_at():
    ev = evaluate(DEFS["R001"], facts(goal_difference=2, minute=61, red_cards=4))
    assert ev.observed == {"goal_difference": 2, "minute": 61}


# ---------------------------------------------------------------- database helpers
def make_match(db, *, status=MatchStatus.IN_PLAY, home=0, away=0, minute=60, events=(), age=timedelta(0)):
    fixture = FixtureData("t", "M1", CompetitionData("t", "C1", "League"), TeamData("t", "A", "Alpha"),
                          TeamData("t", "B", "Beta"), T0 - timedelta(hours=1), status=status,
                          elapsed_minute=minute, home_score=home, away_score=away,
                          events=tuple(EventData(*e) if not isinstance(e, EventData) else e for e in events))
    IngestionService(db).ingest_fixtures([fixture])
    match = db.scalars(select(Match)).one()
    db.execute(Match.__table__.update().values(last_updated=T0 - age))
    db.commit()
    db.refresh(match)
    return match


def set_state(db, match, **kw):
    for k, v in kw.items():
        setattr(match, k, v)
    match.last_updated = T0
    db.commit()


def run(db, now=T0, **kw):
    return RuleEngine(db, **kw).evaluate_active(now)


def alerts(db, code=None):
    q = select(Alert).join(Rule, Rule.id == Alert.rule_id).order_by(Alert.id)
    if code:
        q = q.where(Rule.code == code)
    return db.scalars(q).all()


def codes(db, state=None):
    """Rule codes of the stored alerts (optionally only those in one state)."""
    q = select(Rule.code).join(Alert, Alert.rule_id == Rule.id)
    if state:
        q = q.where(Alert.state == state)
    return set(db.scalars(q))


@pytest.fixture
def seeded(db):
    seed_catalogue(db)


# ---------------------------------------------------------------- facts
def test_facts_from_a_real_looking_match(db, seeded):
    match = make_match(db, home=2, away=1, minute=82, events=[
        ("g1", "goal", 23, "home"), ("g2", "own_goal", 82, "home"), ("c1", "red_card", 58, "away"),
        ("c2", "yellow_red_card", 70, "home"), ("c3", "yellow_card", 10, "home")])
    f = build_facts(db, match, T0)
    assert (f["total_goals"], f["goal_difference"], f["minute"], f["status"]) == (3, 1, 82, "IN_PLAY")
    assert f["red_cards"] == 2 and f["last_goal_minute"] == 82  # yellow cards do not count
    assert f["score_age_seconds"] is None  # no snapshots yet


def test_score_age_counts_from_when_the_current_score_first_appeared(db, seeded):
    match = make_match(db, home=1, away=0)
    for minutes, (h, a) in [(0, (0, 0)), (10, (1, 0)), (20, (1, 0)), (30, (1, 0))]:
        db.add(MatchSnapshot(match_id=match.id, status="IN_PLAY", home_score=h, away_score=a,
                             recorded_at=T0 - timedelta(minutes=30 - minutes)))
    db.commit()
    assert build_facts(db, match, T0)["score_age_seconds"] == 20 * 60  # 1-0 first seen 20 min ago
    match.home_score = 2  # moved on since the last snapshot
    assert build_facts(db, match, T0)["score_age_seconds"] == 0


# ---------------------------------------------------------------- the alert lifecycle
def test_condition_becoming_true_creates_one_alert_with_observed_values(db, seeded):
    make_match(db, home=2, away=0, minute=60)
    summary = run(db)
    r001 = alerts(db, "R001")
    assert len(r001) == 1 and r001[0].state == AlertState.ACTIVE
    assert r001[0].observed_values == {"goal_difference": 2, "minute": 60} and r001[0].triggered_at == T0
    assert summary.created == 1 and codes(db) == {"R001"}


def test_condition_staying_true_never_creates_duplicates(db, seeded):
    make_match(db, home=2, away=0, minute=60)
    for i in range(5):
        summary = run(db, T0 + timedelta(seconds=30 * i))
    assert len(alerts(db)) == 1 and summary.created == 0 and summary.resolved == 0


def test_condition_becoming_false_resolves_and_keeps_history(db, seeded):
    match = make_match(db, home=2, away=0, minute=60)
    run(db)
    set_state(db, match, home_score=2, away_score=1)  # lead shrinks to one goal
    run(db, T0 + timedelta(minutes=1))
    a = alerts(db, "R001")[0]
    assert a.state == AlertState.RESOLVED and a.resolved_at == T0 + timedelta(minutes=1)
    assert a.resolved_values == {"reason": "condition_false", "values": {"goal_difference": 1, "minute": 60}}


def test_reactivating_rule_fires_again_when_condition_returns(db, seeded):
    match = make_match(db, home=2, away=0, minute=60)
    run(db)
    set_state(db, match, away_score=1); run(db, T0 + timedelta(minutes=1))   # false
    set_state(db, match, away_score=0); run(db, T0 + timedelta(minutes=2))   # true again
    r001 = alerts(db, "R001")
    assert [a.state for a in r001] == [AlertState.RESOLVED, AlertState.ACTIVE]  # R001 reactivates


def test_once_per_match_rule_does_not_fire_a_second_time(db, seeded):
    match = make_match(db, home=1, away=0, minute=61)
    set_state(db, match, home_score=0)  # goalless at 61'
    run(db)
    assert len(alerts(db, "R003")) == 1
    set_state(db, match, home_score=1); run(db, T0 + timedelta(minutes=1))   # goal: resolved
    set_state(db, match, home_score=0); run(db, T0 + timedelta(minutes=2))   # goal disallowed: true again
    r003 = alerts(db, "R003")
    assert len(r003) == 1 and r003[0].state == AlertState.RESOLVED  # R003 is once per match


def test_finished_match_resolves_everything_and_creates_nothing(db, seeded):
    match = make_match(db, home=3, away=0, minute=88, events=[("g", "goal", 85, "home")])
    run(db)
    assert codes(db, AlertState.ACTIVE) >= {"R001", "R006", "R007"}
    set_state(db, match, status=MatchStatus.FINISHED)
    summary = run(db, T0 + timedelta(minutes=1))
    assert all(a.state == AlertState.RESOLVED and a.resolved_values["reason"] == "match_ended" for a in alerts(db))
    assert summary.created == 0
    run(db, T0 + timedelta(minutes=2))  # nothing more to do, and no churn
    assert not db.scalars(select(Alert).where(Alert.state == AlertState.ACTIVE)).all()


def test_stale_data_is_skipped_not_evaluated(db, seeded):
    make_match(db, home=2, away=0, minute=60, age=timedelta(minutes=10))
    summary = run(db, fresh_within=timedelta(minutes=3))
    assert summary.skipped_stale == 1 and alerts(db) == []


@pytest.mark.parametrize("status", [MatchStatus.NOT_STARTED, MatchStatus.SUSPENDED])
def test_matches_that_are_not_live_are_left_alone(db, seeded, status):
    make_match(db, status=status, home=2, away=0, minute=60)
    assert alerts(db) == [] and run(db).created == 0


def test_suspended_match_keeps_its_alerts_until_play_resumes(db, seeded):
    match = make_match(db, home=2, away=0, minute=60)
    run(db)
    set_state(db, match, status=MatchStatus.SUSPENDED)
    run(db, T0 + timedelta(minutes=1))
    assert alerts(db, "R001")[0].state == AlertState.ACTIVE


def test_disabling_a_rule_resolves_its_active_alert(db, seeded):
    make_match(db, home=2, away=0, minute=60)
    run(db)
    db.execute(Rule.__table__.update().where(Rule.code == "R001").values(enabled=False))
    db.commit()
    run(db, T0 + timedelta(minutes=1))
    a = alerts(db, "R001")[0]
    assert a.state == AlertState.RESOLVED and a.resolved_values == {"reason": "rule_disabled"}


def test_invalid_stored_rule_is_skipped_without_stopping_other_rules(db, seeded):
    db.add(Rule(code="BROKEN", name="Broken", condition={"all": [{"field": "nonsense", "operator": "eq", "value": 1}]}))
    db.commit()
    make_match(db, home=2, away=0, minute=60)
    summary = run(db)
    assert summary.invalid_rules == 1 and len(alerts(db, "R001")) == 1


def test_existing_active_alert_blocks_a_duplicate_even_if_engine_state_is_lost(db, seeded):
    """The database constraint is the last line of defence (e.g. two workers racing)."""
    match = make_match(db, home=2, away=0, minute=60)
    run(db)
    engine = RuleEngine(db)
    rules = engine.load_rules()
    r001 = next(r for r, _ in rules if r.code == "R001")
    from app.rules.engine import EvalSummary
    s = EvalSummary()
    engine._create(match, r001, {}, T0, s)  # simulates a racing duplicate insert
    assert s.created == 0 and len([a for a in alerts(db, "R001")]) == 1


def test_seeding_is_idempotent_and_never_overwrites_edits(db):
    assert seed_catalogue(db) == 8
    db.execute(Rule.__table__.update().where(Rule.code == "R001").values(enabled=False, name="Edited"))
    db.commit()
    assert seed_catalogue(db) == 0
    r001 = db.scalars(select(Rule).where(Rule.code == "R001")).one()
    assert r001.enabled is False and r001.name == "Edited"
