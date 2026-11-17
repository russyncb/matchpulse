from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MatchSnapshot(Base):
    """Append-only history. Normal match updates never modify these rows."""

    __tablename__ = "match_snapshots"
    __table_args__ = (
        Index("ix_match_snapshots_match_id_recorded_at", "match_id", "recorded_at"),
        Index("ix_match_snapshots_recorded_at", "recorded_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"))
    status: Mapped[str | None] = mapped_column(String(20))
    elapsed_minute: Mapped[int | None]
    home_score: Mapped[int] = mapped_column(server_default=text("0"))
    away_score: Mapped[int] = mapped_column(server_default=text("0"))
    statistics: Mapped[dict | None] = mapped_column(JSONB)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
