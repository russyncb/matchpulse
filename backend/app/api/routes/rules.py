from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.db.session import get_db
from app.models import Alert, AlertState, Match, Rule, Team
from app.rules.engine import definition_from_row, evaluate
from app.rules.facts import build_facts
from app.rules.schema import RuleDefinition, stored_condition
from app.schemas import AlertRead, RuleEnable, RuleRead

router = APIRouter(tags=["rules"])


def _rule_read(rule: Rule) -> dict:
    cond = rule.condition or {}
    return {"id": rule.id, "code": rule.code, "name": rule.name, "description": cond.get("description"),
            "enabled": rule.enabled, "severity": rule.severity, "reactivate": cond.get("reactivate", False),
            "all": cond.get("all", [])}


@router.get("/rules", response_model=list[RuleRead])
def list_rules(db: Session = Depends(get_db)):
    return [_rule_read(r) for r in db.scalars(select(Rule).order_by(Rule.code))]


@router.post("/rules/validate")
def validate_rule(definition: RuleDefinition) -> dict:
    """Dry run: FastAPI answers 422 with the exact problems if the definition is invalid."""
    return {"valid": True, "code": definition.code}


@router.post("/rules", response_model=RuleRead, status_code=201)
def create_rule(definition: RuleDefinition, db: Session = Depends(get_db)):
    rule = Rule(code=definition.code, name=definition.name, enabled=definition.enabled,
                severity=definition.severity, condition=stored_condition(definition))
    db.add(rule)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "A rule with this code or name already exists") from exc
    return _rule_read(rule)


@router.patch("/rules/{rule_id}", response_model=RuleRead)
def set_rule_enabled(rule_id: int, body: RuleEnable, db: Session = Depends(get_db)):
    rule = db.get(Rule, rule_id)
    if rule is None:
        raise HTTPException(404, f"Rule {rule_id} not found")
    if body.enabled:  # a rule is validated again before it can be switched on
        try:
            definition_from_row(rule)
        except ValidationError as exc:
            raise HTTPException(422, [e["msg"] for e in exc.errors()]) from exc
    rule.enabled = body.enabled
    db.commit()
    return _rule_read(rule)


def _alert_rows(db: Session, match_id: int | None, state: str | None, rule_code: str | None, limit: int):
    home, away = aliased(Team), aliased(Team)
    query = (select(Alert, Rule, home.name, away.name)
             .join(Rule, Rule.id == Alert.rule_id).join(Match, Match.id == Alert.match_id)
             .join(home, home.id == Match.home_team_id).join(away, away.id == Match.away_team_id)
             .order_by(Alert.triggered_at.desc(), Alert.id.desc()).limit(limit))
    if match_id is not None:
        query = query.where(Alert.match_id == match_id)
    if state:
        query = query.where(Alert.state == AlertState(state))
    if rule_code:
        query = query.where(Rule.code == rule_code)
    return [{
        "id": a.id, "match_id": a.match_id, "match": f"{h} vs {w}", "rule_code": r.code,
        "rule_name": r.name, "severity": r.severity, "state": a.state.value,
        "observed_values": a.observed_values, "triggered_at": a.triggered_at,
        "resolved_at": a.resolved_at, "resolved_values": a.resolved_values,
    } for a, r, h, w in db.execute(query)]


@router.get("/alerts", response_model=list[AlertRead])
def list_alerts(state: AlertState | None = None, match_id: int | None = None,
                rule_code: str | None = None, limit: int = 200, db: Session = Depends(get_db)):
    return _alert_rows(db, match_id, state.value if state else None, rule_code, min(limit, 1000))


@router.get("/matches/{match_id}/alerts", response_model=list[AlertRead])
def match_alerts(match_id: int, state: AlertState | None = None, db: Session = Depends(get_db)):
    if db.get(Match, match_id) is None:
        raise HTTPException(404, f"Match {match_id} not found")
    return _alert_rows(db, match_id, state.value if state else None, None, 1000)


@router.get("/matches/{match_id}/evaluation")
def explain_match(match_id: int, db: Session = Depends(get_db)) -> dict:
    """Why would (or wouldn't) each rule fire right now? Read-only: creates no alerts."""
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(404, f"Match {match_id} not found")
    now = datetime.now(timezone.utc)
    facts = build_facts(db, match, now)
    active = {a.rule_id: a.id for a in db.scalars(
        select(Alert).where(Alert.match_id == match_id, Alert.state == AlertState.ACTIVE))}
    results = []
    for rule in db.scalars(select(Rule).order_by(Rule.code)):
        entry = {"code": rule.code, "name": rule.name, "enabled": rule.enabled,
                 "active_alert_id": active.get(rule.id)}
        try:
            ev = evaluate(definition_from_row(rule), facts)
            entry.update(matched=ev.matched, observed=ev.observed)
        except ValidationError:
            entry.update(matched=None, error="stored rule definition is invalid")
        results.append(entry)
    return {"match_id": match_id, "status": match.status.value, "evaluated_at": now, "facts": facts,
            "rules": results}
