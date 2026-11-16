from datetime import datetime, timedelta, timezone

import pytest

from app.db.session import SessionLocal
from app.models import ProviderState
from app.providers.base import FootballProvider
from app.providers.errors import (
    BadResponse, ProviderBlocked, ProviderDown, QuotaExhausted, RateLimited,
)
from app.providers.gateway import ProviderGateway
from app.services import usage


class ScriptedProvider(FootballProvider):
    """Returns/raises whatever is queued, and counts how often it was really called."""
    name = "fake"

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def _next(self):
        self.calls += 1
        outcome = self.outcomes.pop(0) if self.outcomes else []
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def get_competitions(self): return self._next()
    def get_fixtures(self, *a): return self._next()
    def get_live_fixtures(self): return self._next()


class Clock:
    def __init__(self): self.now = datetime(2026, 11, 16, 12, 30, tzinfo=timezone.utc)
    def __call__(self): return self.now
    def advance(self, **kw): self.now += timedelta(**kw)


@pytest.fixture
def clock(): return Clock()


def gateway(provider, clock, **kw):
    kw.setdefault("sleep", lambda s: None)
    return ProviderGateway(provider, session_factory=SessionLocal, clock=clock, **kw)


def stats(clock):
    with SessionLocal() as db:
        return usage.requests_since(db, "fake", usage.day_start(clock.now)), db.get(ProviderState, "fake")


def test_success_is_counted_and_marks_provider_ok(clock):
    p = ScriptedProvider(["data"])
    assert gateway(p, clock).fetch("get_live_fixtures") == ["data"]
    used, state = stats(clock)
    assert used == 1 and state.status == "ok" and state.last_success_at is not None


def test_transient_failure_is_retried_and_every_attempt_counts(clock):
    p = ScriptedProvider(ProviderDown("timeout"), ProviderDown("timeout"), ["ok"])
    assert gateway(p, clock).fetch("get_live_fixtures") == ["ok"]
    assert p.calls == 3 and stats(clock)[0] == 3


def test_persistent_failure_puts_provider_in_cooldown(clock):
    p = ScriptedProvider(*[ProviderDown("down")] * 5)
    gw = gateway(p, clock, max_attempts=3)
    with pytest.raises(ProviderDown):
        gw.fetch("get_live_fixtures")
    assert p.calls == 3 and stats(clock)[1].status == "down"
    with pytest.raises(ProviderBlocked):  # refuses without touching the provider
        gw.fetch("get_live_fixtures")
    assert p.calls == 3
    clock.advance(minutes=3)  # cooldown over
    p.outcomes = [["back"]]
    assert gw.fetch("get_live_fixtures") == ["back"]


def test_hourly_budget_stops_calls_until_next_hour(clock):
    p = ScriptedProvider(["a"], ["b"], ["c"])
    gw = gateway(p, clock, hourly_limit=2)
    gw.fetch("get_live_fixtures"); gw.fetch("get_live_fixtures")
    with pytest.raises(QuotaExhausted):
        gw.fetch("get_live_fixtures")
    assert p.calls == 2
    assert stats(clock)[1].blocked_until == datetime(2026, 11, 16, 13, 0, tzinfo=timezone.utc)
    clock.advance(minutes=31)
    assert gw.fetch("get_live_fixtures") == ["c"]


def test_daily_budget_resets_at_midnight_utc(clock):
    p = ScriptedProvider()
    gw = gateway(p, clock, daily_limit=3)
    for _ in range(2):
        gw.fetch("get_live_fixtures")
    clock.advance(hours=1)
    gw.fetch("get_live_fixtures")
    with pytest.raises(QuotaExhausted):
        gw.fetch("get_live_fixtures")
    assert p.calls == 3
    clock.advance(hours=11)  # now 2026-11-17 00:30 UTC
    gw.fetch("get_live_fixtures")
    assert p.calls == 4


def test_provider_reported_quota_exhaustion_blocks_following_calls(clock):
    p = ScriptedProvider(QuotaExhausted("plan limit", retry_after=1800))
    gw = gateway(p, clock)
    with pytest.raises(QuotaExhausted):
        gw.fetch("get_live_fixtures")
    with pytest.raises(ProviderBlocked):
        gw.fetch("get_live_fixtures")
    assert p.calls == 1


def test_rate_limit_respects_retry_after(clock):
    p = ScriptedProvider(RateLimited("slow down", retry_after=90))
    gw = gateway(p, clock)
    with pytest.raises(RateLimited):
        gw.fetch("get_live_fixtures")
    clock.advance(seconds=60)
    with pytest.raises(ProviderBlocked):
        gw.fetch("get_live_fixtures")
    clock.advance(seconds=31)
    assert gw.fetch("get_live_fixtures") == []


def test_unexpected_adapter_bug_becomes_bad_response(clock):
    p = ScriptedProvider(KeyError("missing field"))
    with pytest.raises(BadResponse):
        gateway(p, clock).fetch("get_live_fixtures")
    assert stats(clock)[0] == 1


def test_auth_error_blocks_for_an_hour_and_stops_hammering(clock):
    from app.providers.errors import AuthError
    p = ScriptedProvider(AuthError("bad token"))
    gw = gateway(p, clock)
    with pytest.raises(AuthError):
        gw.fetch("get_live_fixtures")
    assert stats(clock)[1].status == "down"
    clock.advance(minutes=30)
    with pytest.raises(ProviderBlocked):
        gw.fetch("get_live_fixtures")
    assert p.calls == 1
