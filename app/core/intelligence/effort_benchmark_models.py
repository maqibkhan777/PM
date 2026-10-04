"""Domain models and schemas for Empirical Historical Effort Benchmarking (Milestone 2).

Strictly separates project benchmarks (e.g. SMTPSUPORT vs GF).
Implements explicit reliability thresholds:
- < 5 samples: INSUFFICIENT_DATA
- 5-9 samples: LOW_CONFIDENCE
- 10+ samples: USABLE (with P25, P50, P75, P90 quartiles)
"""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class BenchmarkReliability(str, Enum):
    """Statistical reliability rating for a benchmark segment based on sample size."""
    USABLE = "USABLE"                  # >= 10 samples (Full P25/P50/P75/P90 quartiles supported)
    LOW_CONFIDENCE = "LOW_CONFIDENCE"  # 5-9 samples (P50/Mean usable with caution)
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"  # < 5 samples (Cannot reliably formulate benchmark)


class EffortDistributionQuantiles(BaseModel):
    """Percentile distribution for logged developer effort hours."""
    sample_count: int = 0
    mean_hours: float = 0.0
    median_hours: float = 0.0  # P50
    p25_hours: Optional[float] = None
    p75_hours: Optional[float] = None
    p90_hours: Optional[float] = None
    min_hours: float = 0.0
    max_hours: float = 0.0
    stddev_hours: float = 0.0


class SegmentedProjectBenchmark(BaseModel):
    """An empirical effort benchmark for a specific grouping within a single project."""
    project_key: str
    dimension_type: str  # 'overall', 'issue_type', 'priority', 'issue_type_priority', 'label'
    dimension_key: str   # e.g., 'Bug', 'High', 'Bug:High', 'oauth'
    sample_count: int = 0
    missing_effort_count: int = 0
    total_logged_hours: float = 0.0
    distribution: EffortDistributionQuantiles = Field(default_factory=EffortDistributionQuantiles)
    reliability: BenchmarkReliability = BenchmarkReliability.INSUFFICIENT_DATA
    data_quality_warning: Optional[str] = None


class ProjectEffortBenchmarkReport(BaseModel):
    """Complete empirical effort benchmarking report for a single Jira project."""
    project_key: str
    date_range_start: str
    date_range_end: str
    total_completed_issues: int = 0
    issues_with_logged_effort: int = 0
    issues_missing_effort: int = 0
    missing_effort_percentage: float = 0.0
    total_logged_hours: float = 0.0

    # Overall project benchmark
    overall_benchmark: SegmentedProjectBenchmark = Field(default_factory=lambda: SegmentedProjectBenchmark(
        project_key="", dimension_type="overall", dimension_key="all"
    ))

    # Segmented benchmarks strictly separated by dimension
    by_issue_type: Dict[str, SegmentedProjectBenchmark] = Field(default_factory=dict)
    by_priority: Dict[str, SegmentedProjectBenchmark] = Field(default_factory=dict)
    by_issue_type_priority: Dict[str, SegmentedProjectBenchmark] = Field(default_factory=dict)
    by_label: Dict[str, SegmentedProjectBenchmark] = Field(default_factory=dict)

    data_quality_warnings: List[str] = Field(default_factory=list)
    supported_groupings: List[str] = Field(default_factory=list)
    unsupported_groupings: List[str] = Field(default_factory=list)


class MultiProjectBenchmarkSummary(BaseModel):
    """Multi-project benchmark collection container maintaining strict project separation."""
    summary_id: str
    generated_at: str
    lookback_days: int
    projects: List[str]
    reports_by_project: Dict[str, ProjectEffortBenchmarkReport] = Field(default_factory=dict)
    cross_project_isolation_verified: bool = True
