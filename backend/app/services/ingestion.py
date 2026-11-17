"""Writes provider-neutral data into PostgreSQL. Safe to run repeatedly (idempotent upserts)."""
from dataclasses import dataclass, field

from sqlalchemy import Boolean, delete, func, literal_column
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import Competition, Match, MatchEvent, Team
from app.providers.models import CompetitionData, FixtureData, TeamData


@dataclass
class IngestResult:
    competitions_created: int = 0
    teams_created: int = 0
    matches_created: int = 0
    matches_updated: int = 0
    events_created: int = 0
    events_deleted: int = 0
    match_ids: list[int] = field(default_factory=list)  # every match touched, in order


# In PostgreSQL, xmax = 0 on a returned row means it was freshly INSERTed, not updated.
_INSERTED = literal_column("(xmax = 0)", Boolean).label("inserted")


def _upsert(db: Session, model, values: dict, conflict: list[str], update: list[str], extra=None):
    stmt = pg_insert(model).values(**values)
    set_ = {c: stmt.excluded[c] for c in update}
    set_.update(extra or {})
    stmt = stmt.on_conflict_do_update(index_elements=conflict, set_=set_).returning(model.id, _INSERTED)
    row = db.execute(stmt).one()
    return row.id, row.inserted


class IngestionService:
    def __init__(self, db: Session):
        self.db = db
        self._competition_ids: dict[tuple[str, str], int] = {}
        self._team_ids: dict[tuple[str, str], int] = {}

    # ---- public API: each call is one transaction ----
    def ingest_competitions(self, items: list[CompetitionData]) -> IngestResult:
        return self._transaction(lambda r: [self._competition(c, r) for c in items])

    def ingest_fixtures(self, fixtures: list[FixtureData]) -> IngestResult:
        return self._transaction(lambda r: [self._fixture(f, r) for f in fixtures])

    def _transaction(self, work) -> IngestResult:
        result = IngestResult()
        try:
            work(result)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return result

    # ---- internals ----
    def _competition(self, c: CompetitionData, result: IngestResult) -> int:
        key = (c.provider, c.provider_id)
        if key not in self._competition_ids:
            cid, inserted = _upsert(
                self.db, Competition,
                dict(provider=c.provider, provider_id=c.provider_id, name=c.name, country=c.country),
                ["provider", "provider_id"], ["name", "country"],
            )
            self._competition_ids[key] = cid
            result.competitions_created += int(inserted)
        return self._competition_ids[key]

    def _team(self, t: TeamData, result: IngestResult) -> int:
        key = (t.provider, t.provider_id)
        if key not in self._team_ids:
            tid, inserted = _upsert(
                self.db, Team, dict(provider=t.provider, provider_id=t.provider_id, name=t.name),
                ["provider", "provider_id"], ["name"],
            )
            self._team_ids[key] = tid
            result.teams_created += int(inserted)
        return self._team_ids[key]

    def _fixture(self, f: FixtureData, result: IngestResult) -> None:
        competition_id = self._competition(f.competition, result)
        home_id = self._team(f.home_team, result)
        away_id = self._team(f.away_team, result)

        match_id, inserted = _upsert(
            self.db, Match,
            dict(provider=f.provider, provider_id=f.provider_id, competition_id=competition_id,
                 home_team_id=home_id, away_team_id=away_id, kickoff=f.kickoff, status=f.status,
                 provider_status=f.provider_status, elapsed_minute=f.elapsed_minute,
                 home_score=f.home_score, away_score=f.away_score),
            ["provider", "provider_id"],
            ["competition_id", "home_team_id", "away_team_id", "kickoff", "status",
             "provider_status", "elapsed_minute", "home_score", "away_score"],
            extra={"last_updated": func.now()},
        )
        if inserted:
            result.matches_created += 1
        else:
            result.matches_updated += 1

        result.match_ids.append(match_id)
        if f.events is None:  # events were not fetched; never delete what we cannot see
            return

        sides = {"home": home_id, "away": away_id}
        for ev in f.events:
            stmt = (
                pg_insert(MatchEvent)
                .values(provider=f.provider, provider_event_id=ev.provider_event_id, match_id=match_id,
                        team_id=sides.get(ev.team_side), event_type=ev.event_type,
                        elapsed_minute=ev.elapsed_minute, occurred_at=ev.occurred_at, payload=ev.payload)
                .on_conflict_do_nothing(index_elements=["provider", "provider_event_id"])
                .returning(MatchEvent.id)
            )
            if self.db.execute(stmt).first() is not None:
                result.events_created += 1

        # The provider's list is the full truth: events it no longer reports were cancelled
        # (e.g. a red card overturned by VAR), so they must not keep driving rules.
        gone = self.db.execute(
            delete(MatchEvent).where(
                MatchEvent.match_id == match_id,
                MatchEvent.provider == f.provider,
                MatchEvent.provider_event_id.not_in([e.provider_event_id for e in f.events]),
            )
        )
        result.events_deleted += gone.rowcount
