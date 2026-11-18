import pytest
from pydantic import ValidationError

from app.rules.catalogue import CATALOGUE
from app.rules.schema import RuleDefinition


def rule(**overrides):
    base = {"code": "T001", "name": "Test", "all": [{"field": "minute", "operator": "gte", "value": 55}]}
    return {**base, **overrides}


def test_example_rule_from_the_proposal_is_valid():
    defn = RuleDefinition.model_validate({
        "code": "R001", "name": "Two-goal lead", "enabled": True, "severity": "info",
        "all": [{"field": "goal_difference", "operator": "gte", "value": 2},
                {"field": "minute", "operator": "gte", "value": 55},
                {"field": "status", "operator": "eq", "value": "IN_PLAY"}]})
    assert len(defn.all) == 3 and defn.reactivate is False


@pytest.mark.parametrize("raw", CATALOGUE, ids=lambda r: r["code"])
def test_whole_catalogue_is_valid(raw):
    assert RuleDefinition.model_validate(raw).code == raw["code"]


def test_catalogue_covers_the_eight_rules_of_the_proposal():
    assert [r["code"] for r in CATALOGUE] == [f"R00{i}" for i in range(1, 9)]


@pytest.mark.parametrize("bad,why", [
    (rule(all=[{"field": "possession", "operator": "gte", "value": 50}]), "unknown field"),
    (rule(all=[{"field": "minute", "operator": "between", "value": 5}]), "unknown operator"),
    (rule(all=[{"field": "minute", "operator": "ne", "value": 5}]), "operator not supported"),
    (rule(all=[{"field": "minute", "operator": "gte", "value": "fifty"}]), "text for a number"),
    (rule(all=[{"field": "minute", "operator": "gte", "value": 55.5}]), "float"),
    (rule(all=[{"field": "minute", "operator": "gte", "value": True}]), "bool"),
    (rule(all=[{"field": "minute", "operator": "gte", "value": -1}]), "negative"),
    (rule(all=[{"field": "minute", "operator": "gte", "value": 99999}]), "absurd"),
    (rule(all=[{"field": "status", "operator": "gte", "value": "IN_PLAY"}]), "status needs eq"),
    (rule(all=[{"field": "status", "operator": "eq", "value": "HALF_BAKED"}]), "unknown status"),
    (rule(all=[{"field": "status", "operator": "eq", "value": 3}]), "number for status"),
    (rule(all=[]), "no clauses"),
    (rule(all=[{"field": "minute", "operator": "gte", "value": 1}] * 11), "too many clauses"),
    (rule(all=[{"field": "minute", "operator": "gte", "value": 1, "extra": 1}]), "typo/extra key"),
    (rule(severity="apocalyptic"), "bad severity"),
    (rule(code="bad code!"), "bad code"),
    (rule(name=""), "empty name"),
    (rule(colour="red"), "unknown top-level key"),
    ({"code": "T001", "name": "x"}, "missing all"),
])
def test_invalid_rules_are_rejected(bad, why):
    with pytest.raises(ValidationError):
        RuleDefinition.model_validate(bad)


def test_error_message_names_the_problem():
    with pytest.raises(ValidationError) as exc:
        RuleDefinition.model_validate(rule(all=[{"field": "possession", "operator": "gte", "value": 5}]))
    assert "unknown field 'possession'" in str(exc.value)
