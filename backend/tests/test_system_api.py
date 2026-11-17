from datetime import datetime, timedelta, timezone

from app.db.session import SessionLocal
from app.models import MatchStatus
from app.worker.scheduler import Job, JobResult, Scheduler
from tests.test_worker import replay_scheduler, FixedClock


def test_system_status_reports_worker_and_jobs(client):
    assert client.get("/api/v1/system/status").json()["worker_alive"] is False  # nothing ran yet
    now = datetime.now(timezone.utc)
    Scheduler([Job("demo", timedelta(hours=1), lambda: JobResult("ok", "did it"))],
              session_factory=SessionLocal, clock=lambda: now).run_once()
    body = client.get("/api/v1/system/status").json()
    assert body["worker_alive"] is True
    assert body["jobs"][0]["job"] == "demo" and body["jobs"][0]["last_result"] == "did it"


def test_match_history_endpoints_after_a_replayed_match(client):
    clock = FixedClock()
    scheduler = replay_scheduler(clock)
    scheduler.run_once()
    for _ in range(6):
        clock.advance(seconds=60)
        scheduler.run_once()

    match = client.get("/api/v1/matches").json()[0]
    assert match["status"] == MatchStatus.FINISHED.value
    snaps = client.get(f"/api/v1/matches/{match['id']}/snapshots").json()
    assert [(s["home_score"], s["away_score"]) for s in snaps][:3] == [(0, 0), (1, 0), (2, 0)]
    events = client.get(f"/api/v1/matches/{match['id']}/events").json()
    assert [e["event_type"] for e in events] == ["goal", "goal", "red_card", "goal"]
    assert len(client.get("/api/v1/matches", params={"status": "FINISHED"}).json()) == 1
    assert client.get("/api/v1/matches", params={"status": "IN_PLAY"}).json() == []
    assert client.get("/api/v1/matches/9999/snapshots").status_code == 404
