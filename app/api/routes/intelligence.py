"""FastAPI route endpoints for Phase B v1.1 Historical Intelligence & Evidence Layer."""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Body, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.core.intelligence.engine import HistoricalIntelligenceEngine
from app.core.intelligence.models import (
    HistoricalEffortBenchmark,
    HistoricalEvidenceRecord,
    HistoricalIntelligenceProfile,
    TaskMixProfile,
)
from app.database.repositories import (
    EmployeeRoleRepository,
    HistoricalIntelligenceRepository,
)
from app.utils.logger import logger

router = APIRouter(prefix="/performance/intelligence", tags=["Historical Intelligence & Evidence"])
engine = HistoricalIntelligenceEngine()
intel_repo = HistoricalIntelligenceRepository()
role_repo = EmployeeRoleRepository()


class RunAnalysisRequest(BaseModel):
    run_id: Optional[str] = Field(None, description="Optional custom analysis run ID")
    history_days: int = Field(365, description="Historical analysis window in days")
    team_group: Optional[str] = Field(None, description="Optional team group filter")
    account_id: Optional[str] = Field(None, description="Optional single employee account ID filter")


@router.post("/analyze", status_code=status.HTTP_200_OK)
async def run_intelligence_analysis(req: RunAnalysisRequest = Body(default_factory=RunAnalysisRequest)):
    """Trigger a complete Phase B Historical Intelligence and Evidence analysis run."""
    try:
        result = engine.run_analysis(
            run_id=req.run_id,
            history_days=req.history_days,
            team_group=req.team_group,
            account_id_filter=req.account_id,
            persist=True,
        )
        # Convert pydantic models to dicts for clean serialization
        profiles_dict = {
            acc: p.model_dump() if hasattr(p, "model_dump") else p
            for acc, p in result["profiles"].items()
        }
        return {
            "status": "SUCCESS",
            "analysis_run_id": result["analysis_run_id"],
            "calculated_at": result["calculated_at"],
            "requested_history_days": result["requested_history_days"],
            "actual_available_history_days": result["actual_available_history_days"],
            "earliest_record_date": result["earliest_record_date"],
            "latest_record_date": result["latest_record_date"],
            "authoritative_count": result["authoritative_count"],
            "excluded_count": result["excluded_count"],
            "evidence_count": result["evidence_count"],
            "ai_readiness_status": result["ai_readiness_status"],
            "profiles_count": len(profiles_dict),
        }
    except Exception as e:
        logger.error(f"Error running intelligence analysis: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Historical intelligence analysis failed: {str(e)}",
        )


@router.get("", response_model=List[Dict[str, Any]])
async def list_intelligence_profiles(
    run_id: Optional[str] = Query(None, description="Optional analysis run ID"),
    team_group: Optional[str] = Query(None, description="Optional team group filter"),
    limit: int = Query(50, ge=1, le=100, description="Max profiles to return"),
):
    """List historical intelligence profiles for the latest or specified run."""
    profiles = intel_repo.list_profiles(run_id=run_id, team_group=team_group, limit=limit)
    return profiles


@router.get("/{account_id}", response_model=Dict[str, Any])
async def get_employee_intelligence_profile(
    account_id: str,
    run_id: Optional[str] = Query(None, description="Optional analysis run ID"),
):
    """Retrieve full AI-ready HistoricalIntelligenceProfile contract for a specific employee."""
    profile = intel_repo.get_profile(account_id=account_id, run_id=run_id)
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Historical intelligence profile not found for account_id '{account_id}'.",
        )
    return profile


@router.get("/{account_id}/task-mix", response_model=Dict[str, Any])
async def get_employee_task_mix(
    account_id: str,
    run_id: Optional[str] = Query(None, description="Optional analysis run ID"),
):
    """Retrieve task mix distribution profile for a specific employee."""
    task_mix = intel_repo.get_task_mix(account_id=account_id, run_id=run_id)
    if not task_mix:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task mix profile not found for account_id '{account_id}'.",
        )
    return task_mix


@router.get("/{account_id}/effort", response_model=List[Dict[str, Any]])
async def get_employee_effort_benchmarks(
    account_id: str,
    run_id: Optional[str] = Query(None, description="Optional analysis run ID"),
    segment_type: Optional[str] = Query(None, description="Optional segment type filter"),
):
    """Retrieve 11-tier segmented historical effort benchmarks for an employee."""
    benchmarks = intel_repo.get_effort_benchmarks(
        account_id=account_id,
        run_id=run_id,
        segment_type=segment_type,
    )
    return benchmarks


@router.get("/{account_id}/trends", response_model=List[Dict[str, Any]])
async def get_employee_trends(
    account_id: str,
    run_id: Optional[str] = Query(None, description="Optional analysis run ID"),
):
    """Retrieve rolling trend metrics (30d/90d/180d/365d) for an employee."""
    trends = intel_repo.get_trends(account_id=account_id, run_id=run_id)
    return trends


@router.get("/{account_id}/evidence", response_model=List[Dict[str, Any]])
async def get_employee_evidence_records(
    account_id: str,
    run_id: Optional[str] = Query(None, description="Optional analysis run ID"),
    evidence_type: Optional[str] = Query(None, description="Optional evidence type filter"),
    issue_key: Optional[str] = Query(None, description="Optional issue key filter"),
    limit: int = Query(100, ge=1, le=500, description="Max evidence records to return"),
):
    """Retrieve immutable, traceable evidence records for an employee."""
    evidence = intel_repo.get_evidence_records(
        account_id=account_id,
        run_id=run_id,
        evidence_type=evidence_type,
        issue_key=issue_key,
        limit=limit,
    )
    return evidence
