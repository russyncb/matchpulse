from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import ApiUsage, ProviderState


def hour_start(now: datetime) -> datetime:
    return now.replace(minute=0, second=0, microsecond=0)


def day_start(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def next_hour(now: datetime) -> datetime:
    return hour_start(now) + timedelta(hours=1)


def next_day(now: datetime) -> datetime:
    return day_start(now) + timedelta(days=1)


def record_request(db: Session, provider: str, now: datetime, failed: bool) -> None:
    stmt = pg_insert(ApiUsage).values(
        provider=provider, window_start=hour_start(now), requests=1, failures=int(failed)
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["provider", "window_start"],
        set_={"requests": ApiUsage.requests + 1, "failures": ApiUsage.failures + int(failed)},
    )
    db.execute(stmt)
    db.commit()


def requests_since(db: Session, provider: str, since: datetime) -> int:
    total = db.scalar(
        select(func.coalesce(func.sum(ApiUsage.requests), 0))
        .where(ApiUsage.provider == provider, ApiUsage.window_start >= since)
    )
    return int(total)


def set_state(db: Session, provider: str, status: str, *, blocked_until: datetime | None = None,
              error: str | None = None, now: datetime) -> None:
    values = dict(provider=provider, status=status, blocked_until=blocked_until, last_error=error)
    set_ = dict(status=status, blocked_until=blocked_until, updated_at=now)
    if status == "ok":
        values["last_success_at"] = now
        set_["last_success_at"] = now
        set_["last_error"] = None
    else:
        set_["last_error"] = error
    stmt = pg_insert(ProviderState).values(**values).on_conflict_do_update(
        index_elements=["provider"], set_=set_
    )
    db.execute(stmt)
    db.commit()
