from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import SyncStatus
from app.worker.scheduler import HEARTBEAT

router = APIRouter(tags=["system"])
WORKER_STALE_AFTER = timedelta(seconds=60)


@router.get("/system/status")
def system_status(db: Session = Depends(get_db)) -> dict:
    now = datetime.now(timezone.utc)
    rows = {r.job_name: r for r in db.scalars(select(SyncStatus))}
    beat = rows.pop(HEARTBEAT, None)
    seen = beat.last_attempt_at if beat else None
    return {
        "database": "ok",
        "worker_alive": bool(seen and now - seen < WORKER_STALE_AFTER),
        "worker_last_seen": seen,
        "jobs": [
            {"job": r.job_name, "last_attempt_at": r.last_attempt_at, "last_success_at": r.last_success_at,
             "consecutive_failures": r.consecutive_failures, "last_result": r.last_result,
             "last_error": r.last_error}
            for r in sorted(rows.values(), key=lambda r: r.job_name)
        ],
    }
