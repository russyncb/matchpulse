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
    database_url: str = "postgresql+psycopg://matchpulse:matchpulse@db:5432/matchpulse"

    # Read from environment variables or a local .env file (never committed).
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
