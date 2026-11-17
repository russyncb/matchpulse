"""MatchPulse's internal, provider-neutral data format.

Every adapter converts its provider's JSON into these classes. Nothing downstream
(ingestion, rules engine, dashboard) ever sees a provider-specific shape.

Internal event_type values: goal, own_goal, penalty_goal, missed_penalty, yellow_card,
red_card, yellow_red_card (second yellow), substitution, var, shootout_goal, shootout_miss,
other. Adapters map provider labels onto these. A rule about "red cards" should count both
red_card and yellow_red_card. elapsed_minute includes stoppage time (45+2 is stored as 47).
"""
import hashlib
from dataclasses import dataclass
from datetime import datetime

from app.models.enums import MatchStatus


def synthetic_event_id(match_provider_id: str, event_type: str, minute: int | None,
                       player: str | None = None, extra: str = "") -> str:
    """Stable id for providers that do not give events their own id.

    The same event seen on every poll hashes to the same value, so ingestion stays idempotent.
    """
    raw = "|".join([match_provider_id, event_type, str(minute), player or "", extra])
    return hashlib.sha1(raw.encode()).hexdigest()[:24]


@dataclass(frozen=True)
class CompetitionData:
    provider: str
    provider_id: str
    name: str
    country: str | None = None


@dataclass(frozen=True)
class TeamData:
    provider: str
    provider_id: str
    name: str


@dataclass(frozen=True)
class EventData:
    provider_event_id: str
    event_type: str
    elapsed_minute: int | None = None
    team_side: str | None = None  # "home", "away" or None
    occurred_at: datetime | None = None
    payload: dict | None = None  # raw provider detail, kept for debugging


@dataclass(frozen=True)
class FixtureData:
    provider: str
    provider_id: str
    competition: CompetitionData
    home_team: TeamData
    away_team: TeamData
    kickoff: datetime
    status: MatchStatus = MatchStatus.NOT_STARTED
    provider_status: str | None = None
    elapsed_minute: int | None = None
    home_score: int = 0
    away_score: int = 0
    # None = the provider call did not include events (leave stored events alone);
    # () = the provider says there are none (stored events not in this list are removed).
    events: tuple[EventData, ...] | None = None

    def __post_init__(self):
        if self.kickoff.tzinfo is None:
            raise ValueError("kickoff must be timezone-aware")
        if self.home_score < 0 or self.away_score < 0:
            raise ValueError("scores cannot be negative")
        if self.home_team.provider_id == self.away_team.provider_id:
            raise ValueError("home and away team must differ")
