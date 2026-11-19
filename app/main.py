import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1.api import api_router
from app.core.config import settings
from app.core.logging import setup_logging
from app.db.session import init_db
from app.workers.asr_worker import start_worker, stop_worker

setup_logging(settings.LOG_LEVEL)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: init DB, start worker, stop worker on shutdown."""
    logger.info(f"Starting {settings.PROJECT_NAME}")
    await init_db()
    logger.info("Database initialized")

    # Lazy-load FUNASR model in the worker thread (heavy)
    start_worker()
    logger.info("ASR worker started")

    yield

    logger.info("Shutting down")
    stop_worker()


app = FastAPI(
    title=settings.PROJECT_NAME,
    version="1.0.0",
    description="Independent ASR service backed by FunASR.",
    lifespan=lifespan,
)

app.include_router(api_router, prefix=settings.API_V1_STR)


@app.get("/")
async def root():
    return {
        "service": settings.PROJECT_NAME,
        "version": "1.0.0",
        "docs": "/docs",
    }
