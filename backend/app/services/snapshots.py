from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Match, MatchSnapshot
from app.models.enums import LIVE_STATUSES, MatchStatus


def record_snapshots(db: Session, match_ids: list[int], now: datetime, interval: timedelta) -> int:
    """Append a history row when a match's status or score changed, and every `interval` while live.

    Snapshots are append-only: ordinary updates to the matches table never touch them.
    """
    created = 0
    for match in db.scalars(select(Match).where(Match.id.in_(set(match_ids)))):
        if match.status == MatchStatus.NOT_STARTED:
            continue
        last = db.scalars(
            select(MatchSnapshot).where(MatchSnapshot.match_id == match.id)
            .order_by(MatchSnapshot.recorded_at.desc(), MatchSnapshot.id.desc()).limit(1)
        ).first()
        changed = last is None or (last.status, last.home_score, last.away_score) != (
            match.status.value, match.home_score, match.away_score)
        periodic = (last is not None and match.status in LIVE_STATUSES
                    and now - last.recorded_at >= interval)
        if changed or periodic:
            db.add(MatchSnapshot(
                match_id=match.id, status=match.status.value, elapsed_minute=match.elapsed_minute,
                home_score=match.home_score, away_score=match.away_score, recorded_at=now))
            created += 1
    db.commit()
    return created
