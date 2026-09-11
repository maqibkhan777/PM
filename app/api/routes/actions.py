"""Actions management, approval, rejection, and execution routes."""

from typing import Any, Dict, Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.actions.engine import action_engine
from app.core.actions.base import BaseAction
from app.core.models.enums import ActionType, ActionStatus
from app.database.repositories import ActionRepository

router = APIRouter(tags=["Actions"])
action_repo = ActionRepository()


class ActionCreateRequest(BaseModel):
    action_type: str = Field(..., description="Action type, e.g. TRANSITION_TASK, ADD_COMMENT")
    target_system: str = Field(..., description="Target system, e.g. jira, discord, mattermost")
    target_id: Optional[str] = Field("", description="Target ID, e.g. Jira issue key or channel name")
    parameters: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Action parameters")
    requested_by: Optional[str] = Field("PM", description="Requesting agent or user")
    dry_run: Optional[bool] = Field(None, description="Optional override for dry run mode")


class ActionApproveRequest(BaseModel):
    approved_by: Optional[str] = Field("PM", description="User or role approving the action")


class ActionRejectRequest(BaseModel):
    rejected_by: Optional[str] = Field("PM", description="User or role rejecting the action")
    reason: Optional[str] = Field(None, description="Reason for rejection")


@router.post("/actions")
async def create_action(req: ActionCreateRequest):
    """Create and evaluate a new action proposal through the Action Engine."""
    action_type = ActionType.from_str(req.action_type)
    if not action_type:
        valid_types = [t.name for t in ActionType] + [t.value for t in ActionType]
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported action type '{req.action_type}'. Valid types: {valid_types}"
        )


    act = BaseAction(
        action_type=action_type,
        target_system=req.target_system,
        target_id=req.target_id or "",
        parameters=req.parameters or {},
        requested_by=req.requested_by or "PM",
        dry_run=bool(req.dry_run) if req.dry_run is not None else False
    )

    result = await action_engine.execute(act)
    return {
        "action_id": act.action_id,
        "status": result.status.value,
        "success": result.success,
        "dry_run": result.dry_run,
        "details": result.result_data,
        "error": result.error_message
    }


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
async def approve_action(action_id: str, req: Optional[ActionApproveRequest] = None):
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

    approved_by = req.approved_by if req and req.approved_by else "PM"
    result = await action_engine.approve_action(action_id=action_id, approved_by=approved_by)

    return {
        "action_id": action_id,
        "status": result.status.value,
        "success": result.success,
        "dry_run": result.dry_run,
        "details": result.result_data,
        "error": result.error_message
    }


@router.post("/actions/{action_id}/reject")
async def reject_action(action_id: str, req: Optional[ActionRejectRequest] = None):
    """Reject a pending approval action."""
    action_rec = action_repo.get_by_action_id(action_id)
    if not action_rec:
        raise HTTPException(status_code=404, detail=f"Action '{action_id}' not found")

    current_status = action_rec.get("status")
    if current_status != ActionStatus.PENDING_APPROVAL.value:
        raise HTTPException(
            status_code=400,
            detail=f"Action has status '{current_status}'. Only PENDING_APPROVAL actions can be rejected."
        )

    rejected_by = req.rejected_by if req and req.rejected_by else "PM"
    reason = req.reason if req else None
    result = await action_engine.reject_action(action_id=action_id, rejected_by=rejected_by, reason=reason)

    return {
        "action_id": action_id,
        "status": result.status.value,
        "success": result.success,
        "details": result.result_data,
        "error": result.error_message
    }


@router.post("/actions/{action_id}/execute")
async def execute_action_endpoint(action_id: str):
    """Execute an approved action."""
    action_rec = action_repo.get_by_action_id(action_id)
    if not action_rec:
        raise HTTPException(status_code=404, detail=f"Action '{action_id}' not found")

    current_status = action_rec.get("status")
    if current_status == ActionStatus.PENDING_APPROVAL.value:
        raise HTTPException(
            status_code=400,
            detail=f"Action '{action_id}' is PENDING_APPROVAL and must be approved before execution."
        )
    if current_status in (ActionStatus.COMPLETED.value, ActionStatus.DRY_RUN_SIMULATED.value):
        # Return idempotent result
        return {
            "action_id": action_id,
            "status": current_status,
            "success": True,
            "details": action_rec.get("result_data"),
            "idempotent": True
        }

    # Reconstruct BaseAction
    act = BaseAction(
        action_id=action_rec["action_id"],
        idempotency_key=action_rec.get("idempotency_key"),
        action_type=ActionType(action_rec["action_type"]),
        target_system=action_rec["target_system"],
        target_id=action_rec["target_id"],
        parameters=action_rec.get("parameters", {}),
        requested_by=action_rec.get("requested_by") or "PM",
        status=ActionStatus(current_status),
        dry_run=bool(action_rec.get("dry_run", 0))
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
