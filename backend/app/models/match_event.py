from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MatchEvent(Base):
    __tablename__ = "match_events"
    __table_args__ = (
        # Idempotent ingestion: the same provider event can only be stored once.
        UniqueConstraint("provider", "provider_event_id"),
        Index("ix_match_events_match_id_elapsed_minute", "match_id", "elapsed_minute"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(50))
    provider_event_id: Mapped[str] = mapped_column(String(150))
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"))
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id", ondelete="SET NULL"))
    event_type: Mapped[str] = mapped_column(String(30))
    elapsed_minute: Mapped[int | None]
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict | None] = mapped_column(JSONB)  # raw provider detail
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
