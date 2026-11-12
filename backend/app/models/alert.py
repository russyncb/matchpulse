from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import AlertState


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        # At most ONE active alert per match + rule, enforced by the database.
        Index(
            "uq_alerts_one_active_per_match_rule",
            "match_id",
            "rule_id",
            unique=True,
            postgresql_where=text("state = 'ACTIVE'"),
        ),
        CheckConstraint(
            "(state = 'ACTIVE' AND resolved_at IS NULL) OR (state = 'RESOLVED' AND resolved_at IS NOT NULL)",
            name="state_matches_resolved_at",
        ),
        Index("ix_alerts_state", "state"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"))
    rule_id: Mapped[int] = mapped_column(ForeignKey("rules.id", ondelete="RESTRICT"))
    state: Mapped[AlertState] = mapped_column(
        Enum(AlertState, native_enum=False, length=20, create_constraint=True, name="alert_state"),
        default=AlertState.ACTIVE,
        server_default=AlertState.ACTIVE.value,
    )
    observed_values: Mapped[dict | None] = mapped_column(JSONB)
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
