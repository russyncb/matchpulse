from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import Competition, Match, Team
from app.schemas import (
    CompetitionCreate, CompetitionRead, MatchCreate, MatchRead, TeamCreate, TeamRead,
)

router = APIRouter(tags=["catalog"])


def _save(db: Session, obj):
    db.add(obj)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Record violates a database constraint "
                            "(duplicate provider id, or invalid values).") from exc
    db.refresh(obj)
    return obj


def _get_or_404(db: Session, model, obj_id: int):
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"{model.__name__} {obj_id} not found")
    return obj


@router.post("/competitions", response_model=CompetitionRead, status_code=201)
def create_competition(body: CompetitionCreate, db: Session = Depends(get_db)):
    return _save(db, Competition(**body.model_dump()))


@router.get("/competitions", response_model=list[CompetitionRead])
def list_competitions(db: Session = Depends(get_db)):
    return db.scalars(select(Competition).order_by(Competition.id)).all()


@router.get("/competitions/{competition_id}", response_model=CompetitionRead)
def get_competition(competition_id: int, db: Session = Depends(get_db)):
    return _get_or_404(db, Competition, competition_id)


@router.post("/teams", response_model=TeamRead, status_code=201)
def create_team(body: TeamCreate, db: Session = Depends(get_db)):
    return _save(db, Team(**body.model_dump()))


@router.get("/teams", response_model=list[TeamRead])
def list_teams(db: Session = Depends(get_db)):
    return db.scalars(select(Team).order_by(Team.id)).all()


@router.get("/teams/{team_id}", response_model=TeamRead)
def get_team(team_id: int, db: Session = Depends(get_db)):
    return _get_or_404(db, Team, team_id)


@router.post("/matches", response_model=MatchRead, status_code=201)
def create_match(body: MatchCreate, db: Session = Depends(get_db)):
    for model, ref_id in ((Competition, body.competition_id),
                          (Team, body.home_team_id), (Team, body.away_team_id)):
        if db.get(model, ref_id) is None:
            raise HTTPException(status_code=422, detail=f"{model.__name__} {ref_id} does not exist")
    return _save(db, Match(**body.model_dump()))


@router.get("/matches", response_model=list[MatchRead])
def list_matches(db: Session = Depends(get_db)):
    return db.scalars(select(Match).order_by(Match.kickoff)).all()


@router.get("/matches/{match_id}", response_model=MatchRead)
def get_match(match_id: int, db: Session = Depends(get_db)):
    return _get_or_404(db, Match, match_id)
