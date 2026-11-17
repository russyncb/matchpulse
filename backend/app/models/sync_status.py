from datetime import datetime

from sqlalchemy import DateTime, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SyncStatus(Base):
    """When each background job last ran. Stored in the database so a restart resumes the schedule
    instead of re-running everything (and spending API quota)."""

    __tablename__ = "sync_status"

    job_name: Mapped[str] = mapped_column(String(50), primary_key=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(server_default=text("0"))
    last_result: Mapped[str | None] = mapped_column(Text)
    last_error: Mapped[str | None] = mapped_column(Text)
