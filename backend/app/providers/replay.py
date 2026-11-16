"""A fake provider that replays a scripted match, one step per live poll.

Lets us test ingestion, rules and the dashboard deterministically, with no network
and no waiting for real matches. Step events are cumulative, like real providers.
"""
import json
from datetime import date, datetime
from pathlib import Path

from app.models.enums import MatchStatus
from app.providers.base import FootballProvider
from app.providers.errors import BadResponse
from app.providers.models import CompetitionData, EventData, FixtureData, TeamData

DATA_DIR = Path(__file__).parent / "replay_data"


class ReplayProvider(FootballProvider):
    def __init__(self, scenario: str | Path = "two_goal_lead_and_red_card.json"):
        path = Path(scenario)
        if not path.is_absolute():
            path = DATA_DIR / path
        try:
            self._data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise BadResponse(f"cannot load replay scenario {path}: {exc}") from exc
        self.name = self._data["provider"]
        self._i = 0  # number of live polls made so far

    def reset(self) -> None:
        self._i = 0

    def _competition(self) -> CompetitionData:
        c = self._data["competition"]
        return CompetitionData(self.name, c["provider_id"], c["name"], c.get("country"))

    def _team(self, side: str) -> TeamData:
        t = self._data["teams"][side]
        return TeamData(self.name, t["provider_id"], t["name"])

    def _fixture(self, step: dict | None) -> FixtureData:
        m = self._data["match"]
        step = step or {}
        events = tuple(
            EventData(
                provider_event_id=e["id"], event_type=e["type"], elapsed_minute=e.get("minute"),
                team_side=e.get("team"), payload={k: v for k, v in e.items() if k == "player"} or None,
            )
            for e in step.get("events", [])
        )
        return FixtureData(
            provider=self.name, provider_id=m["provider_id"], competition=self._competition(),
            home_team=self._team("home"), away_team=self._team("away"),
            kickoff=datetime.fromisoformat(m["kickoff"]),
            status=MatchStatus(step.get("status", "NOT_STARTED")),
            provider_status=step.get("status"), elapsed_minute=step.get("elapsed_minute"),
            home_score=step.get("home_score", 0), away_score=step.get("away_score", 0), events=events,
        )

    def _current(self) -> FixtureData:
        steps = self._data["steps"]
        return self._fixture(None if self._i == 0 else steps[min(self._i, len(steps)) - 1])

    def get_competitions(self) -> list[CompetitionData]:
        return [self._competition()]

    def get_fixtures(self, competition_provider_id: str, date_from: date, date_to: date) -> list[FixtureData]:
        if competition_provider_id != self._competition().provider_id:
            return []
        return [self._current()]

    def get_live_fixtures(self) -> list[FixtureData]:
        self._i = min(self._i + 1, len(self._data["steps"]))
        return [self._current()]
