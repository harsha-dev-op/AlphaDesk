from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    backtests,
    data_sources,
    features,
    health,
    indices,
    portfolio,
    quality,
    research,
    scanner,
    securities,
    strategies,
)
from app.core.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()
configure_logging(settings.log_level)
logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("application_startup", environment=settings.app_env, version=settings.app_version)
    yield
    logger.info("application_shutdown")


app = FastAPI(title="AlphaDesk API", version=settings.app_version, description="Point-in-time market-data, technical-feature, scanner, strategy, composition, backtest, and portfolio research foundation. No order execution.", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=["*"])
app.include_router(health.router)
app.include_router(data_sources.router)
app.include_router(securities.router)
app.include_router(features.router)
app.include_router(scanner.router)
app.include_router(strategies.router)
app.include_router(backtests.router)
app.include_router(portfolio.router)
app.include_router(research.router)
app.include_router(indices.router)
app.include_router(quality.router)
