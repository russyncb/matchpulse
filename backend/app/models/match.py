from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.competition import Competition
from app.models.enums import MatchStatus
from app.models.team import Team


class Match(Base):
    __tablename__ = "matches"
    __table_args__ = (
        UniqueConstraint("provider", "provider_id"),
        CheckConstraint("home_team_id <> away_team_id", name="different_teams"),
        CheckConstraint("home_score >= 0 AND away_score >= 0", name="scores_non_negative"),
        Index("ix_matches_status", "status"),
        Index("ix_matches_competition_id", "competition_id"),
        Index("ix_matches_kickoff", "kickoff"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(50))
    provider_id: Mapped[str] = mapped_column(String(100))
    competition_id: Mapped[int] = mapped_column(ForeignKey("competitions.id", ondelete="RESTRICT"))
    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="RESTRICT"))
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="RESTRICT"))
    kickoff: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[MatchStatus] = mapped_column(
        Enum(MatchStatus, native_enum=False, length=20, create_constraint=True, name="match_status"),
        default=MatchStatus.NOT_STARTED,
        server_default=MatchStatus.NOT_STARTED.value,
    )
    provider_status: Mapped[str | None] = mapped_column(String(50))  # raw provider value
    elapsed_minute: Mapped[int | None]
    home_score: Mapped[int] = mapped_column(server_default=text("0"))
    away_score: Mapped[int] = mapped_column(server_default=text("0"))
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    competition: Mapped[Competition] = relationship()
    home_team: Mapped[Team] = relationship(foreign_keys=[home_team_id])
    away_team: Mapped[Team] = relationship(foreign_keys=[away_team_id])
