from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import ApiUsage, ProviderState
from app.services import usage

router = APIRouter(tags=["providers"])


@router.get("/providers/status")
def providers_status(db: Session = Depends(get_db)) -> list[dict]:
    now = datetime.now(timezone.utc)
    states = {s.provider: s for s in db.scalars(select(ProviderState))}
    names = sorted(set(states) | set(db.scalars(select(ApiUsage.provider).distinct())))
    out = []
    for name in names:
        s = states.get(name)
        out.append({
            "provider": name,
            "status": s.status if s else "ok",
            "blocked_until": s.blocked_until if s else None,
            "last_error": s.last_error if s else None,
            "last_success_at": s.last_success_at if s else None,
            "requests_this_hour": usage.requests_since(db, name, usage.hour_start(now)),
            "requests_today": usage.requests_since(db, name, usage.day_start(now)),
        })
    return out
