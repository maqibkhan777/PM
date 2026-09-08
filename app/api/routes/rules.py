"""Rules configuration and management routes."""

from typing import Dict, Any
from fastapi import APIRouter, HTTPException, Body
from app.core.rules.engine import rules_engine

router = APIRouter(tags=["Rules"])


@router.get("/rules")
async def list_rules():
    """List all registered rules, their enabled status, and configurations."""
    return {"rules": rules_engine.list_rules()}


@router.post("/rules/{rule_name}/toggle")
async def toggle_rule(rule_name: str, payload: Dict[str, bool] = Body(...)):
    """Enable or disable a specific workflow rule."""
    enabled = payload.get("enabled", True)
    success = rules_engine.set_rule_enabled(rule_name, enabled)
    if not success:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_name}' not found")
    return {
        "rule_name": rule_name,
        "enabled": enabled,
        "message": f"Rule '{rule_name}' is now {'enabled' if enabled else 'disabled'}"
    }
