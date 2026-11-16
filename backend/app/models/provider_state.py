from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProviderState(Base):
    """Is this provider usable right now? Survives restarts, so a worker doesn't retry blindly."""

    __tablename__ = "provider_state"
    __table_args__ = (
        CheckConstraint("status IN ('ok','quota_exhausted','rate_limited','down')", name="valid_status"),
    )

    provider: Mapped[str] = mapped_column(String(50), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), default="ok", server_default="ok")
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
