from abc import ABC, abstractmethod
from datetime import date

from app.providers.models import CompetitionData, FixtureData


class FootballProvider(ABC):
    """What every provider adapter must implement. Methods return INTERNAL data only."""

    name: str

    @abstractmethod
    def get_competitions(self) -> list[CompetitionData]: ...

    @abstractmethod
    def get_fixtures(self, competition_provider_id: str, date_from: date,
                     date_to: date) -> list[FixtureData]: ...

    @abstractmethod
    def get_live_fixtures(self) -> list[FixtureData]: ...
