"""Actions management and approval routes."""

from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from app.core.actions.engine import action_engine
from app.core.actions.base import BaseAction
from app.core.models.enums import ActionType, ActionStatus
from app.database.repositories import ActionRepository

router = APIRouter(tags=["Actions"])
action_repo = ActionRepository()


@router.get("/actions")
async def list_actions(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: Optional[str] = None
):
    """List recorded actions with previews and execution status."""
    actions = action_repo.list_actions(limit=limit, offset=offset, status=status)
    return {
        "count": len(actions),
        "limit": limit,
        "offset": offset,
        "actions": actions
    }


@router.get("/actions/{action_id}")
async def get_action(action_id: str):
    """Fetch action details by ID."""
    action_rec = action_repo.get_by_action_id(action_id)
    if not action_rec:
        raise HTTPException(status_code=404, detail=f"Action '{action_id}' not found")
    return action_rec


@router.post("/actions/{action_id}/approve")
async def approve_action(action_id: str):
    """Approve and execute a pending approval action."""
    action_rec = action_repo.get_by_action_id(action_id)
    if not action_rec:
        raise HTTPException(status_code=404, detail=f"Action '{action_id}' not found")

    current_status = action_rec.get("status")
    if current_status != ActionStatus.PENDING_APPROVAL.value:
        raise HTTPException(
            status_code=400,
            detail=f"Action has status '{current_status}'. Only PENDING_APPROVAL actions can be approved."
        )

    # Reconstruct BaseAction
    act = BaseAction(
        action_id=action_rec["action_id"],
        idempotency_key=action_rec.get("idempotency_key"),
        action_type=ActionType(action_rec["action_type"]),
        target_system=action_rec["target_system"],
        target_id=action_rec["target_id"],
        parameters=action_rec.get("parameters", {}),
        requested_by=action_rec.get("parameters", {}).get("requested_by", "PM"),
        status=ActionStatus.APPROVED
    )

    result = await action_engine.execute(act, approved=True)
    return {
        "action_id": action_id,
        "status": result.status.value,
        "success": result.success,
        "dry_run": result.dry_run,
        "details": result.result_data,
        "error": result.error_message
    }
