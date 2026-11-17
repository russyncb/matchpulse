"""Decides whether polling the provider is worthwhile right now (protects the API quota)."""
from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models import Competition, Match
from app.models.enums import LIVE_STATUSES, MatchStatus


def has_active_matches(db: Session, now: datetime, *, provider: str | None = None) -> bool:
    """True if a match is live, or about to start / should already have started."""
    live = and_(Match.status.in_(LIVE_STATUSES), Match.kickoff > now - timedelta(hours=6))
    starting = and_(Match.status == MatchStatus.NOT_STARTED,
                    Match.kickoff <= now + timedelta(minutes=15),
                    Match.kickoff >= now - timedelta(hours=3))
    query = select(Match.id).where(or_(live, starting)).limit(1)
    if provider:
        query = query.where(Match.provider == provider)
    return db.scalar(query) is not None


def stale_matches(db: Session, now: datetime, stale_after: timedelta, *, provider: str):
    """Matches still marked live whose data the provider has not refreshed for a while."""
    return db.execute(
        select(Match, Competition.provider_id)
        .join(Competition, Competition.id == Match.competition_id)
        .where(Match.provider == provider, Match.status.in_(LIVE_STATUSES),
               Match.last_updated < now - stale_after, Match.kickoff > now - timedelta(hours=48))
    ).all()
