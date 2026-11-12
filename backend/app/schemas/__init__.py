from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import MatchStatus


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CompetitionCreate(BaseModel):
    provider: str = Field(min_length=1, max_length=50)
    provider_id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    country: str | None = None
    active: bool = True


class CompetitionRead(CompetitionCreate, ORMModel):
    id: int


class TeamCreate(BaseModel):
    provider: str = Field(min_length=1, max_length=50)
    provider_id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)


class TeamRead(TeamCreate, ORMModel):
    id: int


class MatchCreate(BaseModel):
    provider: str = Field(min_length=1, max_length=50)
    provider_id: str = Field(min_length=1, max_length=100)
    competition_id: int
    home_team_id: int
    away_team_id: int
    kickoff: datetime
    status: MatchStatus = MatchStatus.NOT_STARTED
    elapsed_minute: int | None = Field(default=None, ge=0)
    home_score: int = Field(default=0, ge=0)
    away_score: int = Field(default=0, ge=0)


class MatchRead(MatchCreate, ORMModel):
    id: int
    last_updated: datetime
    home_team: TeamRead
    away_team: TeamRead
    competition: CompetitionRead
