"""Webhook ingestion endpoints."""

from typing import Any, Dict
from fastapi import APIRouter, Request, status, Response, BackgroundTasks
from app.services.orchestrator import orchestrator
from app.utils.logger import logger

router = APIRouter(tags=["Webhooks"])


@router.post("/webhooks/jira", status_code=status.HTTP_202_ACCEPTED)
async def receive_jira_webhook(request: Request, response: Response):
    """Receive and rapidly ingest Jira Cloud webhook payloads.

    Flow:
    1. Parse payload
    2. Persist with status 'RECEIVED'
    3. Return HTTP 202 Accepted immediately
    4. Process through Event Bus & Rules in background task
    """
    try:
        raw_payload = await request.json()
    except Exception as e:
        logger.error(f"Invalid JSON received on Jira webhook endpoint: {e}")
        response.status_code = status.HTTP_400_BAD_REQUEST
        return {"status": "error", "message": "Invalid JSON body"}

    headers = dict(request.headers)
    event_id = await orchestrator.process_raw_webhook(
        source="jira",
        raw_payload=raw_payload,
        headers=headers
    )

    if event_id == "duplicate":
        response.status_code = status.HTTP_200_OK
        return {"status": "ignored", "message": "Duplicate event"}

    return {
        "status": "received",
        "event_id": event_id,
        "message": "Webhook accepted for background processing"
    }
