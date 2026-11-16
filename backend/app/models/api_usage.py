from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ApiUsage(Base):
    """Requests made to each provider, in hourly buckets (UTC). Daily totals are sums of buckets."""

    __tablename__ = "api_usage"
    __table_args__ = (UniqueConstraint("provider", "window_start"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(50))
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    requests: Mapped[int] = mapped_column(server_default=text("0"))
    failures: Mapped[int] = mapped_column(server_default=text("0"))
