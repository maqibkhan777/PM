"""FastAPI route endpoints for Phase A Performance Data Foundation."""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Body, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.core.models.performance import (
    EffortStatistics,
    EmployeeRoleAssignment,
    PerformanceAnalysisRun,
    PerformanceEvidence,
    PerformanceSignal,
    ResourcePerformanceProfile,
    TaskDeliveryForecast,
    TeamPerformanceSummary,
)
from app.core.performance.engine import PerformanceAnalysisEngine
from app.database.repositories import EmployeeRoleRepository, PerformanceRepository
from app.utils.logger import logger

router = APIRouter(prefix="/performance", tags=["Performance Data Foundation"])
engine = PerformanceAnalysisEngine()
perf_repo = PerformanceRepository()
role_repo = EmployeeRoleRepository()


class CreateRoleAssignmentRequest(BaseModel):
    account_id: str = Field(..., description="Jira Atlassian Account ID")
    display_name: str = Field(..., description="Employee full display name")
    designation: str = Field(..., description="Exact employee designation")
    role_category: str = Field(..., description="Normalized role category")
    effective_from: Optional[str] = None
    effective_to: Optional[str] = None


@router.get("/runs/latest", response_model=Optional[Dict[str, Any]])
async def get_latest_analysis_run(
    team_group: Optional[str] = Query(None, description="Optional team group filter")
):
    """Retrieve the metadata for the most recent completed performance analysis run."""
    run = perf_repo.get_latest_analysis_run(team_group=team_group)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No completed performance analysis run found."
        )
    return run


@router.get("/roles", response_model=List[Dict[str, Any]])
async def list_role_assignments():
    """List all authoritative employee designation and role category assignments."""
    return role_repo.list_assignments()


@router.get("/roles/unresolved", response_model=List[Dict[str, Any]])
async def get_unresolved_employees(
    team_group: Optional[str] = Query(None, description="Optional team group filter")
):
    """List active discovered employees who lack an authoritative role assignment without guessing."""
    discovered = engine._discover_resources(team_group=team_group)
    unresolved = role_repo.get_unresolved_employees(list(discovered.values()))
    return unresolved


@router.post("/roles", status_code=status.HTTP_201_CREATED)
async def upsert_role_assignment(req: CreateRoleAssignmentRequest = Body(...)):
    """Add or update an authoritative employee designation and role category assignment."""
    role_repo.upsert_assignment(
        account_id=req.account_id,
        display_name=req.display_name,
        designation=req.designation,
        role_category=req.role_category,
        effective_from=req.effective_from,
        effective_to=req.effective_to,
        source="manual_admin_api",
    )
    return {"status": "SUCCESS", "account_id": req.account_id, "designation": req.designation}


@router.get("/resources", response_model=List[Dict[str, Any]])
async def list_resource_profiles(
    team_group: Optional[str] = Query(None, description="Filter profiles by team group"),
    run_id: Optional[str] = Query(None, description="Filter profiles by specific analysis run ID"),
):
    """List resource performance profiles."""
    profiles = perf_repo.list_profiles(team_group=team_group, run_id=run_id)
    return profiles


@router.get("/resources/{account_id}", response_model=Dict[str, Any])
async def get_resource_profile(
    account_id: str,
    run_id: Optional[str] = Query(None, description="Optional analysis run ID (defaults to latest)"),
):
    """Retrieve full deterministic performance profile for a specific resource."""
    profile = perf_repo.get_profile(account_id=account_id, run_id=run_id)
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Resource profile for account_id '{account_id}' not found."
        )
    return profile


@router.get("/resources/{account_id}/effort-stats", response_model=List[Dict[str, Any]])
async def get_resource_effort_statistics(
    account_id: str,
    run_id: Optional[str] = Query(None, description="Optional analysis run ID"),
):
    """Retrieve segmented effort percentiles (P25, median, mean, P75) for a resource."""
    stats = perf_repo.get_effort_statistics(account_id=account_id, run_id=run_id)
    return stats


@router.get("/resources/{account_id}/forecasts", response_model=List[Dict[str, Any]])
async def get_resource_task_forecasts(
    account_id: str,
    run_id: Optional[str] = Query(None, description="Optional analysis run ID"),
):
    """Retrieve active task delivery forecasts and due date risk assessments for a resource."""
    forecasts = perf_repo.get_task_forecasts(account_id=account_id, run_id=run_id)
    return forecasts


@router.get("/resources/{account_id}/signals", response_model=List[Dict[str, Any]])
async def get_resource_signals(
    account_id: str,
    run_id: Optional[str] = Query(None, description="Optional analysis run ID"),
):
    """Retrieve deterministic performance signals for a resource."""
    signals = perf_repo.get_signals(account_id=account_id, run_id=run_id)
    return signals


@router.get("/resources/{account_id}/evidence", response_model=List[Dict[str, Any]])
async def get_resource_evidence(
    account_id: str,
    issue_key: Optional[str] = Query(None, description="Filter by Jira issue key"),
    run_id: Optional[str] = Query(None, description="Filter by analysis run ID"),
    limit: int = Query(100, ge=1, le=500, description="Max evidence records to return"),
):
    """Retrieve traceable evidence ledger records for a resource."""
    evidence = perf_repo.get_evidence(
        account_id=account_id,
        issue_key=issue_key,
        run_id=run_id,
        limit=limit,
    )
    return evidence


@router.get("/team/summary", response_model=TeamPerformanceSummary)
async def get_team_performance_summary(
    team_group: Optional[str] = Query(None, description="Filter by team group"),
    run_id: Optional[str] = Query(None, description="Filter by analysis run ID"),
):
    """Retrieve aggregated team performance foundation summary."""
    summary = engine.get_team_summary(team_group=team_group, run_id=run_id)
    return summary


@router.post("/analyze", response_model=PerformanceAnalysisRun)
async def trigger_performance_analysis(
    team_group: Optional[str] = Query(None, description="Optional team group to analyze"),
    history_days: Optional[int] = Query(None, ge=1, le=730, description="Analysis window length in days"),
):
    """Trigger an on-demand deterministic performance foundation analysis run."""
    logger.info(f"Manual trigger for performance analysis received (team_group={team_group}, history_days={history_days})")
    run_result = engine.run_analysis(team_group=team_group, history_days=history_days)
    return run_result
