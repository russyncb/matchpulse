"""Rule evaluation and the alert lifecycle (proposal sections 11.4 and 11.5).

For every live match, every enabled rule is evaluated against the match's facts:
  condition becomes true        -> create an ACTIVE alert (subject to the rule's reactivate flag)
  condition stays true          -> nothing (no duplicates, no churn)
  condition becomes false       -> resolve the alert (history is kept)
  match ends / rule disabled    -> resolve the alert with that reason
The database also forbids two ACTIVE alerts for the same match and rule.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Alert, AlertState, Match, Rule
from app.models.enums import LIVE_STATUSES, TERMINAL_STATUSES
from app.rules.facts import build_facts
from app.rules.operators import compare
from app.rules.schema import RuleDefinition

logger = logging.getLogger("matchpulse.rules")


@dataclass
class Evaluation:
    matched: bool
    observed: dict[str, Any]  # the fact values this rule looked at


@dataclass
class EvalSummary:
    matches: int = 0
    created: int = 0
    resolved: int = 0
    skipped_stale: int = 0
    skipped_not_live: int = 0
    invalid_rules: int = 0

    def describe(self) -> str:
        return (f"{self.matches} matches evaluated, {self.created} alerts created, {self.resolved} resolved, "
                f"{self.skipped_stale} stale skipped, {self.invalid_rules} invalid rules")


def evaluate(defn: RuleDefinition, facts: dict[str, Any]) -> Evaluation:
    matched = all(compare(facts.get(c.field), c.operator, c.value) for c in defn.all)
    return Evaluation(matched, {c.field: facts.get(c.field) for c in defn.all})


def definition_from_row(rule: Rule) -> RuleDefinition:
    """Raises pydantic.ValidationError if the stored rule is not valid."""
    cond = rule.condition or {}
    return RuleDefinition.model_validate({
        "code": rule.code, "name": rule.name, "enabled": rule.enabled, "severity": rule.severity,
        "reactivate": cond.get("reactivate", False), "description": cond.get("description"),
        "all": cond.get("all", []),
    })


class RuleEngine:
    def __init__(self, db: Session, *, fresh_within: timedelta = timedelta(minutes=3)):
        self.db = db
        self.fresh_within = fresh_within

    def load_rules(self, summary: EvalSummary | None = None) -> list[tuple[Rule, RuleDefinition]]:
        rules = []
        for rule in self.db.scalars(select(Rule).where(Rule.enabled.is_(True)).order_by(Rule.code)):
            try:
                rules.append((rule, definition_from_row(rule)))
            except ValidationError as exc:  # a broken stored rule must not stop the other rules
                logger.error("skipping invalid rule %s: %s", rule.code, exc.errors()[0]["msg"])
                if summary:
                    summary.invalid_rules += 1
        return rules

    def evaluate_active(self, now: datetime) -> EvalSummary:
        """Evaluate every live match, plus any match that still holds ACTIVE alerts (so they resolve)."""
        summary = EvalSummary()
        rules = self.load_rules(summary)
        with_alerts = select(Alert.match_id).where(Alert.state == AlertState.ACTIVE)
        matches = self.db.scalars(
            select(Match).where((Match.status.in_(LIVE_STATUSES)) | (Match.id.in_(with_alerts)))
            .order_by(Match.id)
        ).all()
        for match in matches:
            self.evaluate_match(match, rules, now, summary)
        return summary

    def evaluate_match(self, match: Match, rules: list[tuple[Rule, RuleDefinition]], now: datetime,
                       summary: EvalSummary) -> None:
        active = {a.rule_id: a for a in self.db.scalars(
            select(Alert).where(Alert.match_id == match.id, Alert.state == AlertState.ACTIVE))}

        if match.status in TERMINAL_STATUSES:  # finished/cancelled/...: close everything, create nothing
            for alert in active.values():
                self._resolve(alert, now, {"reason": "match_ended", "status": match.status.value}, summary)
            summary.matches += 1
            self.db.commit()
            return
        if match.status not in LIVE_STATUSES:
            summary.skipped_not_live += 1
            logger.info("match %s skipped: status %s is not live", match.id, match.status.value)
            return
        if match.last_updated < now - self.fresh_within:
            summary.skipped_stale += 1
            logger.warning("match %s skipped: data not refreshed since %s", match.id, match.last_updated)
            return

        facts = build_facts(self.db, match, now)
        ever_alerted = set(self.db.scalars(select(Alert.rule_id).where(Alert.match_id == match.id)))
        evaluated = set()
        for rule, defn in rules:
            evaluated.add(rule.id)
            result = evaluate(defn, facts)
            alert = active.get(rule.id)
            if result.matched and alert is None:
                if defn.reactivate or rule.id not in ever_alerted:
                    self._create(match, rule, result.observed, now, summary)
            elif not result.matched and alert is not None:
                self._resolve(alert, now, {"reason": "condition_false", "values": result.observed}, summary)
        for rule_id, alert in active.items():  # rule was disabled or deleted while its alert was active
            if rule_id not in evaluated:
                self._resolve(alert, now, {"reason": "rule_disabled"}, summary)
        summary.matches += 1
        self.db.commit()

    # ---- helpers
    def _create(self, match: Match, rule: Rule, observed: dict, now: datetime, summary: EvalSummary) -> None:
        try:
            with self.db.begin_nested():  # savepoint: a lost race must not poison the whole batch
                self.db.add(Alert(match_id=match.id, rule_id=rule.id, state=AlertState.ACTIVE,
                                  observed_values=observed, triggered_at=now))
                self.db.flush()
            summary.created += 1
        except IntegrityError:  # another worker created the same active alert first: fine
            logger.info("alert for match %s rule %s already active", match.id, rule.code)

    def _resolve(self, alert: Alert, now: datetime, details: dict, summary: EvalSummary) -> None:
        alert.state = AlertState.RESOLVED
        alert.resolved_at = now
        alert.resolved_values = details
        summary.resolved += 1
