import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import health
from app.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger("matchpulse")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("MatchPulse API started (env=%s)", settings.environment)
    yield
    logger.info("MatchPulse API stopped")


app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)
app.include_router(health.router, prefix="/api/v1")
