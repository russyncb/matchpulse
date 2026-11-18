from datetime import datetime, timedelta, timezone

from tests.test_rules_engine import T0, make_match, run, seeded  # noqa: F401

GOOD = {"code": "X001", "name": "Custom", "severity": "warning", "reactivate": True,
        "all": [{"field": "total_goals", "operator": "gte", "value": 5}]}


def test_catalogue_is_listed(client, db, seeded):
    rules = client.get("/api/v1/rules").json()
    assert [r["code"] for r in rules] == [f"R00{i}" for i in range(1, 9)]
    assert rules[0]["all"][0] == {"field": "goal_difference", "operator": "gte", "value": 2}
    assert rules[4]["severity"] == "warning" and rules[0]["reactivate"] is True


def test_valid_rule_can_be_created_then_duplicate_is_refused(client):
    created = client.post("/api/v1/rules", json=GOOD)
    assert created.status_code == 201 and created.json()["code"] == "X001"
    assert client.post("/api/v1/rules", json=GOOD).status_code == 409


def test_invalid_rules_are_rejected_with_a_clear_reason(client):
    bad = {**GOOD, "all": [{"field": "possession", "operator": "gte", "value": 50}]}
    response = client.post("/api/v1/rules", json=bad)
    assert response.status_code == 422 and "unknown field" in str(response.json())
    assert client.get("/api/v1/rules").json() == []  # nothing stored
    assert client.post("/api/v1/rules/validate", json=bad).status_code == 422
    assert client.post("/api/v1/rules/validate", json=GOOD).json() == {"valid": True, "code": "X001"}


def test_enable_and_disable(client):
    rid = client.post("/api/v1/rules", json=GOOD).json()["id"]
    assert client.patch(f"/api/v1/rules/{rid}", json={"enabled": False}).json()["enabled"] is False
    assert client.patch(f"/api/v1/rules/{rid}", json={"enabled": True}).json()["enabled"] is True
    assert client.patch("/api/v1/rules/999", json={"enabled": True}).status_code == 404


def test_a_broken_stored_rule_cannot_be_enabled(client, db):
    from app.models import Rule
    db.add(Rule(code="BAD", name="Bad", enabled=False, condition={"all": [{"field": "x", "operator": "eq", "value": 1}]}))
    db.commit()
    rid = db.query(Rule).filter_by(code="BAD").one().id
    assert client.patch(f"/api/v1/rules/{rid}", json={"enabled": True}).status_code == 422


def test_alert_history_and_explanation_endpoints(client, db, seeded):
    match = make_match(db, home=2, away=0, minute=60)
    run(db)
    alerts = client.get("/api/v1/alerts", params={"state": "ACTIVE"}).json()
    assert [a["rule_code"] for a in alerts] == ["R001"]
    assert alerts[0]["match"] == "Alpha vs Beta" and alerts[0]["observed_values"]["goal_difference"] == 2

    assert len(client.get(f"/api/v1/matches/{match.id}/alerts").json()) == 1
    assert client.get("/api/v1/alerts", params={"rule_code": "R005"}).json() == []
    assert client.get("/api/v1/alerts", params={"state": "RESOLVED"}).json() == []

    explain = client.get(f"/api/v1/matches/{match.id}/evaluation").json()
    assert explain["facts"]["goal_difference"] == 2
    r001 = next(r for r in explain["rules"] if r["code"] == "R001")
    assert r001["matched"] is True and r001["active_alert_id"] == alerts[0]["id"]
    assert next(r for r in explain["rules"] if r["code"] == "R005")["matched"] is False
    assert client.get("/api/v1/matches/999/evaluation").status_code == 404
    assert client.get("/api/v1/matches/999/alerts").status_code == 404
