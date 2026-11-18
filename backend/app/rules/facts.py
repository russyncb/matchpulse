"""Turns a stored match into the flat set of facts rules are written against.

Only the database is read here; nothing in the rules package knows which provider supplied the data.
"""
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Match, MatchEvent, MatchSnapshot

RED_CARD_TYPES = ("red_card", "yellow_red_card")
GOAL_TYPES = ("goal", "penalty_goal", "own_goal")


def build_facts(db: Session, match: Match, now: datetime) -> dict[str, Any]:
    rows = db.execute(
        select(MatchEvent.event_type, func.count(), func.max(MatchEvent.elapsed_minute))
        .where(MatchEvent.match_id == match.id).group_by(MatchEvent.event_type)
    ).all()
    red_cards = sum(count for kind, count, _ in rows if kind in RED_CARD_TYPES)
    goal_minutes = [m for kind, _, m in rows if kind in GOAL_TYPES and m is not None]

    return {
        "status": match.status.value,
        "minute": match.elapsed_minute,
        "home_score": match.home_score,
        "away_score": match.away_score,
        "total_goals": match.home_score + match.away_score,
        "goal_difference": abs(match.home_score - match.away_score),
        "red_cards": red_cards,
        "last_goal_minute": max(goal_minutes, default=None),
        "score_age_seconds": _score_age_seconds(db, match, now),
    }


def _score_age_seconds(db: Session, match: Match, now: datetime) -> int | None:
    snapshots = db.execute(
        select(MatchSnapshot.home_score, MatchSnapshot.away_score, MatchSnapshot.recorded_at)
        .where(MatchSnapshot.match_id == match.id)
        .order_by(MatchSnapshot.recorded_at, MatchSnapshot.id)
    ).all()
    if not snapshots:
        return None
    current = (match.home_score, match.away_score)
    since = None
    for home, away, recorded_at in reversed(snapshots):  # walk back through the current score's run
        if (home, away) != current:
            break
        since = recorded_at
    if since is None:  # the match moved on after the last snapshot: the score is brand new
        return 0
    return max(0, int((now - since).total_seconds()))
