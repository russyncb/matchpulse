from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Rule(Base):
    __tablename__ = "rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)  # e.g. "R001"
    name: Mapped[str] = mapped_column(String(200), unique=True)
    condition: Mapped[dict] = mapped_column(JSONB)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    severity: Mapped[str] = mapped_column(String(20), server_default="info")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
