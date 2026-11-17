from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "MatchPulse"
    app_version: str = "0.1.0"
    environment: str = "development"
    log_level: str = "INFO"
    sportmonks_api_token: str | None = None
    # Free plan allows 180 calls/hour per entity; stay a little under it.
    sportmonks_hourly_limit: int = 170
    active_provider: str = "sportmonks"  # "sportmonks" or "replay" (demo data, no API calls)
    sportmonks_tracked_leagues: str = "271,501"  # comma-separated provider league ids
    live_poll_seconds: int = 60
    snapshot_interval_seconds: int = 300
    stale_after_seconds: int = 600
    fixtures_days_back: int = 1
    fixtures_days_ahead: int = 7
    worker_tick_seconds: int = 5
    database_url: str = "postgresql+psycopg://matchpulse:matchpulse@db:5432/matchpulse"

    # Read from environment variables or a local .env file (never committed).
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


    @property
    def tracked_leagues(self) -> list[str]:
        return [x.strip() for x in self.sportmonks_tracked_leagues.split(",") if x.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
