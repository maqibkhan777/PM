"""Events query and manual retry routes."""

from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from app.services.orchestrator import orchestrator
from app.database.repositories import EventRepository

router = APIRouter(tags=["Events"])
event_repo = EventRepository()


@router.get("/events")
async def list_events(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    event_type: Optional[str] = None,
    task_id: Optional[str] = None,
    status: Optional[str] = None
):
    """Retrieve paginated list of normalized events."""
    events = event_repo.list_events(
        limit=limit,
        offset=offset,
        event_type=event_type,
        task_id=task_id,
        status=status
    )
    return {
        "count": len(events),
        "limit": limit,
        "offset": offset,
        "events": events
    }


@router.get("/events/{event_id}")
async def get_event(event_id: str):
    """Get single event details by ID."""
    event = event_repo.get_by_id(event_id)
    if not event:
        raise HTTPException(status_code=404, detail=f"Event '{event_id}' not found")
    return event


@router.post("/events/{event_id}/retry")
async def retry_failed_event(event_id: str):
    """Manually reprocess a failed or retry-pending event."""
    try:
        res = await orchestrator.reprocess_event(event_id)
        return res
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal error during event retry: {e}")
