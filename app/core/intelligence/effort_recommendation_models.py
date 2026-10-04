"""Domain models and schemas for Empirical Historical Effort Benchmarking Integration (Milestone 3).

Advisory recommendation structures and retrieval contracts strictly separated per Jira project.
"""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.core.intelligence.effort_benchmark_models import BenchmarkReliability, EffortDistributionQuantiles


class BenchmarkRecommendationStatus(str, Enum):
    """Status of an empirical effort benchmark recommendation."""
    USABLE = "USABLE"                      # >= 10 samples (P50 and P90 available)
    LOW_CONFIDENCE = "LOW_CONFIDENCE"      # 5-9 samples (Tentative recommendation, P50 reported with warning)
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"  # < 5 samples (No manufactured estimates, explicit insufficient-data result)
    NOT_FOUND = "NOT_FOUND"                # No benchmark record exists for the project


class TaskEffortBenchmarkRecommendation(BaseModel):
    """Deterministic, evidence-backed effort recommendation for a single task."""
    issue_key: str
    project_key: str
    issue_type: str = "Task"
    priority: str = "Medium"

    # Recommended effort values (in hours)
    recommended_effort_hours: Optional[float] = None
    p50_effort_hours: Optional[float] = None
    p90_effort_hours: Optional[float] = None

    # Grouping and tier details
    grouping_used: str = "none"  # e.g. "issue_type_priority:Support:Medium", "issue_type:Support", "overall:all"
    dimension_type: str = "none"
    dimension_key: str = "none"
    is_fallback_grouping: bool = False
    fallback_disclosure: Optional[str] = None

    # Statistical metadata
    sample_count: int = 0
    reliability_status: BenchmarkRecommendationStatus = BenchmarkRecommendationStatus.INSUFFICIENT_DATA
    calculation_version: str = "v1"
    analysis_run_id: Optional[str] = None
    date_window: Optional[str] = None

    # Disclaimers and data quality notes
    explanation: str = ""
    is_tentative: bool = False
    data_quality_warning: Optional[str] = None
    lifecycle_distinction_note: str = (
        "Logged developer effort reflects active time spent and must NEVER be conflated with elapsed wall-clock lifecycle lead time."
    )

    class Config:
        populate_by_name = True
        extra = "forbid"
