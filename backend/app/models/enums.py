import enum


class MatchStatus(str, enum.Enum):
    NOT_STARTED = "NOT_STARTED"
    IN_PLAY = "IN_PLAY"
    HALF_TIME = "HALF_TIME"
    EXTRA_TIME = "EXTRA_TIME"
    PENALTIES = "PENALTIES"
    FINISHED = "FINISHED"
    POSTPONED = "POSTPONED"
    SUSPENDED = "SUSPENDED"
    CANCELLED = "CANCELLED"
    ABANDONED = "ABANDONED"


class AlertState(str, enum.Enum):
    ACTIVE = "ACTIVE"
    RESOLVED = "RESOLVED"


LIVE_STATUSES = frozenset({
    MatchStatus.IN_PLAY, MatchStatus.HALF_TIME, MatchStatus.EXTRA_TIME, MatchStatus.PENALTIES,
})
# No further live updates expected (a postponed match is re-scheduled by the fixture sync).
TERMINAL_STATUSES = frozenset({
    MatchStatus.FINISHED, MatchStatus.CANCELLED, MatchStatus.POSTPONED, MatchStatus.ABANDONED,
})
