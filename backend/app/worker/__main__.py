"""Entry point: python -m app.worker"""
import logging
import signal
import threading
from datetime import timedelta

from app.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.session import SessionLocal
from app.providers.gateway import ProviderGateway
from app.providers.replay import ReplayProvider
from app.providers.sportmonks import create_sportmonks_gateway
from app.worker.jobs import WorkerContext, build_jobs
from app.worker.scheduler import Scheduler

logger = logging.getLogger("matchpulse.worker")


def build_gateway(settings: Settings) -> ProviderGateway:
    if settings.active_provider == "replay":
        return ProviderGateway(ReplayProvider())
    if settings.active_provider == "sportmonks":
        return create_sportmonks_gateway(settings)
    raise ValueError(f"unknown ACTIVE_PROVIDER {settings.active_provider!r}")


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        gateway = build_gateway(settings)
        ctx = WorkerContext(
            gateway=gateway, session_factory=SessionLocal,
            tracked_competitions=[] if settings.active_provider == "replay" else settings.tracked_leagues,
            days_back=settings.fixtures_days_back, days_ahead=settings.fixtures_days_ahead,
            snapshot_interval=timedelta(seconds=settings.snapshot_interval_seconds),
            stale_after=timedelta(seconds=settings.stale_after_seconds),
            always_poll=settings.active_provider == "replay",
        )
        jobs = build_jobs(ctx, live_seconds=settings.live_poll_seconds)
    except ValueError as exc:  # e.g. missing token: stay alive and visible instead of crash-looping
        logger.error("worker cannot start its jobs: %s", exc)
        jobs = []

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    Scheduler(jobs, tick_seconds=settings.worker_tick_seconds).run_forever(stop)


if __name__ == "__main__":
    main()
