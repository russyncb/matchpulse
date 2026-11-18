"""The initial rule catalogue (proposal section 11.2) as plain data.

Reactivation policy (document of record):
  reactivate = true   the situation can recur, so a new alert is created each time the condition
                      becomes true again after it resolved (R001, R002, R004, R008).
  reactivate = false  at most ONE alert per match (R003, R005, R006, R007). R005-R007 describe
                      things that, once recorded, stay true until the match ends.
Alerts of a finished match are always resolved with the reason "match_ended".
"""
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import Rule
from app.rules.schema import RuleDefinition, stored_condition

CATALOGUE: list[dict] = [
    {"code": "R001", "name": "Two-goal lead", "reactivate": True,
     "description": "One team leads by two or more goals from minute 55.",
     "all": [{"field": "goal_difference", "operator": "gte", "value": 2},
             {"field": "minute", "operator": "gte", "value": 55}]},
    {"code": "R002", "name": "Late one-goal lead", "reactivate": True,
     "description": "One team leads by exactly one goal from minute 70.",
     "all": [{"field": "goal_difference", "operator": "eq", "value": 1},
             {"field": "minute", "operator": "gte", "value": 70}]},
    {"code": "R003", "name": "Goalless match", "reactivate": False,
     "description": "No goals scored by minute 60.",
     "all": [{"field": "total_goals", "operator": "eq", "value": 0},
             {"field": "minute", "operator": "gte", "value": 60}]},
    {"code": "R004", "name": "Late level score", "reactivate": True,
     "description": "The score is level from minute 75 (goal difference 0).",
     "all": [{"field": "goal_difference", "operator": "eq", "value": 0},
             {"field": "minute", "operator": "gte", "value": 75}]},
    {"code": "R005", "name": "Red card", "severity": "warning", "reactivate": False,
     "description": "A red card (or second yellow) has been shown.",
     "all": [{"field": "red_cards", "operator": "gte", "value": 1}]},
    {"code": "R006", "name": "Late goal", "reactivate": False,
     "description": "A goal was scored after minute 80.",
     "all": [{"field": "last_goal_minute", "operator": "gt", "value": 80}]},
    {"code": "R007", "name": "Three-goal match", "reactivate": False,
     "description": "Three or more goals in total.",
     "all": [{"field": "total_goals", "operator": "gte", "value": 3}]},
    {"code": "R008", "name": "Score changed", "reactivate": True,
     "description": "The score changed within the last two minutes.",
     "all": [{"field": "total_goals", "operator": "gte", "value": 1},
             {"field": "score_age_seconds", "operator": "lte", "value": 120}]},
]


def seed_catalogue(db: Session) -> int:
    """Insert catalogue rules that do not exist yet. Never overwrites an existing rule (so edits and
    enable/disable choices survive restarts). Returns how many were inserted."""
    inserted = 0
    for raw in CATALOGUE:
        defn = RuleDefinition.model_validate(raw)  # the catalogue obeys the same validation as users
        stmt = pg_insert(Rule).values(
            code=defn.code, name=defn.name, enabled=defn.enabled, severity=defn.severity,
            condition=stored_condition(defn),
        ).on_conflict_do_nothing().returning(Rule.id)
        if db.execute(stmt).first() is not None:
            inserted += 1
    db.commit()
    return inserted
