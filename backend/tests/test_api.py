COMP = {"provider": "test", "provider_id": "c1", "name": "Test League", "country": "England"}
HOME = {"provider": "test", "provider_id": "t1", "name": "Home FC"}
AWAY = {"provider": "test", "provider_id": "t2", "name": "Away FC"}


def _setup(client):
    comp = client.post("/api/v1/competitions", json=COMP).json()
    home = client.post("/api/v1/teams", json=HOME).json()
    away = client.post("/api/v1/teams", json=AWAY).json()
    return comp, home, away


def _match_body(comp, home, away, **extra):
    return {"provider": "test", "provider_id": "m1", "competition_id": comp["id"],
            "home_team_id": home["id"], "away_team_id": away["id"],
            "kickoff": "2026-11-14T15:00:00Z", **extra}


def test_create_and_retrieve_records(client):
    comp, home, away = _setup(client)
    created = client.post("/api/v1/matches", json=_match_body(comp, home, away))
    assert created.status_code == 201
    got = client.get(f"/api/v1/matches/{created.json()['id']}").json()
    assert got["home_team"]["name"] == "Home FC"
    assert got["competition"]["name"] == "Test League"
    assert got["status"] == "NOT_STARTED"
    assert len(client.get("/api/v1/matches").json()) == 1


def test_duplicate_provider_id_returns_409(client):
    client.post("/api/v1/competitions", json=COMP)
    assert client.post("/api/v1/competitions", json=COMP).status_code == 409


def test_match_with_unknown_team_returns_422(client):
    comp, home, _ = _setup(client)
    body = _match_body(comp, home, {"id": 9999})
    assert client.post("/api/v1/matches", json=body).status_code == 422


def test_invalid_status_rejected(client):
    comp, home, away = _setup(client)
    body = _match_body(comp, home, away, status="HALF_BAKED")
    assert client.post("/api/v1/matches", json=body).status_code == 422


def test_missing_record_returns_404(client):
    assert client.get("/api/v1/matches/12345").status_code == 404
