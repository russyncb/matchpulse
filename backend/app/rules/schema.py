"""The rule definition format (proposal section 11.3) and its validation.

A rule is data, not code: a list of clauses that must ALL hold. Every clause compares one match
"fact" with a constant. Invalid definitions are rejected here, before they can be stored or enabled.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

from app.models.enums import MatchStatus

OPERATORS = ("eq", "gt", "gte", "lt", "lte")

# fact name -> "int" or "status". Facts are built from the database in app/rules/facts.py.
FIELDS: dict[str, str] = {
    "status": "status",
    "minute": "int",             # elapsed match minute, stoppage time included (45+2 = 47)
    "home_score": "int",
    "away_score": "int",
    "total_goals": "int",
    "goal_difference": "int",    # absolute difference between the two teams' scores
    "red_cards": "int",          # red cards + second yellows, both teams
    "last_goal_minute": "int",   # minute of the latest goal event (absent if no goals)
    "score_age_seconds": "int",  # seconds since the current score was first recorded
}


class Clause(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    operator: Literal["eq", "gt", "gte", "lt", "lte"]
    value: StrictInt | StrictStr

    @model_validator(mode="after")
    def _check(self):
        kind = FIELDS.get(self.field)
        if kind is None:
            raise ValueError(f"unknown field {self.field!r}; allowed: {', '.join(FIELDS)}")
        if kind == "status":
            if self.operator != "eq":
                raise ValueError("status only supports the 'eq' operator")
            if self.value not in {s.value for s in MatchStatus}:
                raise ValueError(f"unknown status {self.value!r}")
        else:
            if isinstance(self.value, str):
                raise ValueError(f"field {self.field!r} needs a whole number, got text")
            if not 0 <= self.value <= 10_000:
                raise ValueError("number must be between 0 and 10000")
        return self


class RuleDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{1,19}$")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    enabled: bool = True
    severity: Literal["info", "warning", "critical"] = "info"
    # Can the alert fire again after it resolved and the condition became true later?
    # False = at most one alert per match; True = a new alert each time the condition becomes true.
    reactivate: bool = False
    all: list[Clause] = Field(min_length=1, max_length=10)


def stored_condition(defn: RuleDefinition) -> dict:
    """What goes into the rules.condition column (code/name/enabled/severity have their own columns)."""
    return {"all": [c.model_dump() for c in defn.all], "reactivate": defn.reactivate,
            "description": defn.description}
