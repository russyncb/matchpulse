"""Every provider call goes through here.

The gateway counts requests per provider, refuses to call when over budget or cooling down,
retries transient failures, and turns every failure into a typed ProviderError.
Callers catch ProviderError, so one failing provider never crashes the application.
"""
import logging
import time
from datetime import datetime, timedelta, timezone

from app.db.session import SessionLocal
from app.models import ProviderState
from app.providers.base import FootballProvider
from app.providers.errors import (
    AuthError, BadResponse, ProviderBlocked, ProviderDown, ProviderError, QuotaExhausted, RateLimited,
)
from app.services import usage

logger = logging.getLogger("matchpulse.providers")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProviderGateway:
    def __init__(self, provider: FootballProvider, *, hourly_limit: int | None = None,
                 daily_limit: int | None = None, max_attempts: int = 3, backoff_seconds: float = 1.0,
                 down_cooldown_seconds: float = 120, default_block_seconds: float = 3600,
                 auth_block_seconds: float = 3600,
                 session_factory=SessionLocal, clock=_utcnow, sleep=time.sleep):
        self.provider = provider
        self.name = provider.name
        self.hourly_limit = hourly_limit
        self.daily_limit = daily_limit
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds
        self.down_cooldown_seconds = down_cooldown_seconds
        self.default_block_seconds = default_block_seconds
        self.auth_block_seconds = auth_block_seconds
        self._session_factory = session_factory
        self._clock = clock
        self._sleep = sleep

    # ---- the one public method ----
    def fetch(self, method: str, *args, **kwargs):
        self._raise_if_blocked()
        attempt = 0
        while True:
            attempt += 1
            self._raise_if_over_budget()
            try:
                result = getattr(self.provider, method)(*args, **kwargs)
            except RateLimited as exc:
                self._failed()
                self._block("rate_limited", exc.retry_after or 60, str(exc))
                raise
            except QuotaExhausted as exc:
                self._failed()
                self._block("quota_exhausted", exc.retry_after or self.default_block_seconds, str(exc))
                raise
            except AuthError as exc:
                self._failed()
                self._block("down", self.auth_block_seconds, str(exc))
                raise
            except ProviderDown as exc:
                self._failed()
                if attempt >= self.max_attempts:
                    self._block("down", self.down_cooldown_seconds, str(exc))
                    raise
                logger.warning("%s attempt %d/%d failed: %s", self.name, attempt, self.max_attempts, exc)
                self._sleep(self.backoff_seconds * 2 ** (attempt - 1))
                continue
            except ProviderError:
                self._failed()
                raise
            except Exception as exc:  # a bug or surprise inside an adapter must not crash the app
                self._failed()
                logger.exception("%s adapter raised an unexpected error", self.name)
                raise BadResponse(f"unexpected adapter error: {exc!r}") from exc
            else:
                with self._session_factory() as db:
                    usage.record_request(db, self.name, self._clock(), failed=False)
                    usage.set_state(db, self.name, "ok", now=self._clock())
                return result

    # ---- internals ----
    def _failed(self) -> None:
        with self._session_factory() as db:
            usage.record_request(db, self.name, self._clock(), failed=True)

    def _block(self, status: str, seconds: float, error: str, until: datetime | None = None) -> None:
        now = self._clock()
        blocked_until = until or now + timedelta(seconds=seconds)
        logger.error("%s -> %s until %s (%s)", self.name, status, blocked_until.isoformat(), error)
        with self._session_factory() as db:
            usage.set_state(db, self.name, status, blocked_until=blocked_until, error=error, now=now)

    def _raise_if_blocked(self) -> None:
        with self._session_factory() as db:
            state = db.get(ProviderState, self.name)
            until = state.blocked_until if state else None
        if until and until > self._clock():
            raise ProviderBlocked(f"{self.name} is {state.status} until {until.isoformat()}")

    def _raise_if_over_budget(self) -> None:
        now = self._clock()
        with self._session_factory() as db:
            if self.hourly_limit is not None:
                used = usage.requests_since(db, self.name, usage.hour_start(now))
                if used >= self.hourly_limit:
                    self._block("quota_exhausted", 0, f"hourly budget {self.hourly_limit} used",
                                until=usage.next_hour(now))
                    raise QuotaExhausted(f"{self.name} hourly budget of {self.hourly_limit} used")
            if self.daily_limit is not None:
                used = usage.requests_since(db, self.name, usage.day_start(now))
                if used >= self.daily_limit:
                    self._block("quota_exhausted", 0, f"daily budget {self.daily_limit} used",
                                until=usage.next_day(now))
                    raise QuotaExhausted(f"{self.name} daily budget of {self.daily_limit} used")
