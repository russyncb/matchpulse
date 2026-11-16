"""Fetch from a provider and store the result. This is the 'never crash' boundary:
provider problems come back as a SyncResult, not as an exception."""
import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app.providers.errors import ProviderBlocked, ProviderError
from app.providers.gateway import ProviderGateway
from app.services.ingestion import IngestionService, IngestResult

logger = logging.getLogger("matchpulse.sync")


@dataclass
class SyncResult:
    provider: str
    ok: bool
    skipped: bool = False  # True when the gateway refused to call (cooling down / over budget)
    error: str | None = None
    ingest: IngestResult | None = None


def _run(gateway: ProviderGateway, db: Session, method: str, ingest_method: str, *args) -> SyncResult:
    try:
        data = gateway.fetch(method, *args)
    except ProviderBlocked as exc:
        return SyncResult(gateway.name, ok=False, skipped=True, error=str(exc))
    except ProviderError as exc:
        logger.warning("%s.%s failed: %s", gateway.name, method, exc)
        return SyncResult(gateway.name, ok=False, error=str(exc))
    try:
        result = getattr(IngestionService(db), ingest_method)(data)
    except Exception as exc:  # storing failed; report it, keep the process alive
        logger.exception("ingestion failed for %s.%s", gateway.name, method)
        return SyncResult(gateway.name, ok=False, error=f"ingestion failed: {exc!r}")
    return SyncResult(gateway.name, ok=True, ingest=result)


def sync_competitions(gateway: ProviderGateway, db: Session) -> SyncResult:
    return _run(gateway, db, "get_competitions", "ingest_competitions")


def sync_fixtures(gateway: ProviderGateway, db: Session, competition_provider_id: str,
                  date_from: date, date_to: date) -> SyncResult:
    return _run(gateway, db, "get_fixtures", "ingest_fixtures", competition_provider_id, date_from, date_to)


def sync_live(gateway: ProviderGateway, db: Session) -> SyncResult:
    return _run(gateway, db, "get_live_fixtures", "ingest_fixtures")
