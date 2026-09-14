"""PM Operations Agent - Local Server Entry Point."""

import uvicorn
from app.config.settings import settings
from app.utils.logger import logger
from app.database.schema import init_db


def main():
    """Launch the PM Operations Agent FastAPI server with uvicorn."""
    init_db()

    logger.info(f"Starting server on {settings.HOST}:{settings.PORT} (Dry Run: {settings.DRY_RUN})...")
    uvicorn.run(
        "app.api.app:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        log_level="info" if not settings.DEBUG else "debug"
    )


if __name__ == "__main__":
    main()
