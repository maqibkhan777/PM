"""Jira manual polling and operational endpoints."""

from typing import Any, Dict
from fastapi import APIRouter
from app.services.orchestrator import orchestrator

router = APIRouter(prefix="/jira", tags=["Jira"])


@router.post("/poll")
async def trigger_jira_poll() -> Dict[str, Any]:
    """Manually trigger a single Jira polling cycle for testing and on-demand sync."""
    result = await orchestrator.jira_poller.poll()
    return {
        "status": result.get("status", "completed"),
        "issues_scanned": result.get("issues_scanned", 0),
        "events_generated": result.get("events_generated", 0),
        "duplicates_skipped": result.get("duplicates_skipped", 0),
    }
