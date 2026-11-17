from datetime import datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import SyncStatus


def record(db: Session, job_name: str, status: str, note: str | None, now: datetime) -> None:
    """status: 'ok' | 'skipped' | 'failed'."""
    values = dict(job_name=job_name, last_attempt_at=now)
    set_ = dict(last_attempt_at=now)
    if status == "ok":
        values.update(last_success_at=now, consecutive_failures=0, last_result=note, last_error=None)
        set_.update(last_success_at=now, consecutive_failures=0, last_result=note, last_error=None)
    elif status == "skipped":
        values.update(last_result=note)
        set_.update(last_result=note)
    else:
        values.update(consecutive_failures=1, last_error=note)
        set_.update(consecutive_failures=SyncStatus.consecutive_failures + 1, last_error=note)
    stmt = pg_insert(SyncStatus).values(**values).on_conflict_do_update(
        index_elements=["job_name"], set_=set_)
    db.execute(stmt)
    db.commit()
