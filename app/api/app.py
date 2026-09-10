"""FastAPI Application factory and lifecycle setup."""

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from app.database.schema import init_db
from app.services.orchestrator import orchestrator
from app.api.routes import (
    health_router,
    webhooks_router,
    events_router,
    actions_router,
    rules_router,
    reports_router,
    mappings_router,
    jira_poll_router,
    test_notifications_router,
)
from app.config.settings import settings
from app.utils.logger import logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle manager."""
    logger.info("Starting PM Operations Agent application...")
    # 1. Initialize SQLite Database Schema
    init_db()

    # 2. Initialize System Orchestrator (connectors, event bus, scheduler)
    await orchestrator.initialize()

    yield

    # Shutdown
    logger.info("Stopping PM Operations Agent application...")
    await orchestrator.shutdown()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    app = FastAPI(
        title="PM Operations Agent",
        description="Local-first connector-based automation platform for Project Managers",
        version="0.1.0",
        lifespan=lifespan
    )

    # Global Exception Handler
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.error(f"Unhandled error processing {request.method} {request.url.path}: {exc}", exc_info=True)
        # Never expose internal stack traces to clients
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": "An internal server error occurred."}
        )

    # Root status endpoint
    @app.get("/", tags=["General"])
    async def root():
        return {
            "name": "PM Operations Agent",
            "version": "0.1.0",
            "status": "online",
            "dry_run": settings.DRY_RUN,
            "documentation": "/docs"
        }

    # Mount Route Blueprints
    app.include_router(health_router)
    app.include_router(webhooks_router)
    app.include_router(events_router)
    app.include_router(actions_router)
    app.include_router(rules_router)
    app.include_router(reports_router)
    app.include_router(mappings_router)
    app.include_router(jira_poll_router)
    app.include_router(test_notifications_router)

    return app


# Application singleton
app = create_app()
