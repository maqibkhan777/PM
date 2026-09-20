"""Planning domain models, enums, and typed dependency representations.

Phase 3A: Jira Issue Link Normalization and Dependency DAG Foundation.
Deterministic, offline, and provider-agnostic.
"""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class DependencyClassification(str, Enum):
    """Deterministic classification of Jira issue link types for planning and DAGs.

    Strictly maps the 9 confirmed Jira link types present in production:
    - HARD_BLOCK: Target task cannot proceed until source task is resolved (Blocks).
    - CAUSAL_DEPENDENCY: Causal defect/incident link (Problem/Incident).
    - VERIFICATION_DEPENDENCY: Verification link between QA/test and task (Test).
    - INFORMATIONAL: Symmetric informational association, non-blocking (Relates).
    - NON_DEPENDENCY: Provenance or workflow relation, non-blocking (Duplicate, Cloners, Polaris merge, Discovery).
    - CONTEXTUAL: Originating defect relationship (Defect).
    - UNKNOWN: Unrecognized link type, preserved safely without assuming hard blocking.
    """
    HARD_BLOCK = "HARD_BLOCK"
    CAUSAL_DEPENDENCY = "CAUSAL_DEPENDENCY"
    VERIFICATION_DEPENDENCY = "VERIFICATION_DEPENDENCY"
    INFORMATIONAL = "INFORMATIONAL"
    NON_DEPENDENCY = "NON_DEPENDENCY"
    CONTEXTUAL = "CONTEXTUAL"
    UNKNOWN = "UNKNOWN"


# Mapping from Jira link type name (case-insensitive) to deterministic classification
JIRA_LINK_TYPE_MAP: Dict[str, DependencyClassification] = {
    "blocks": DependencyClassification.HARD_BLOCK,
    "problem/incident": DependencyClassification.CAUSAL_DEPENDENCY,
    "test": DependencyClassification.VERIFICATION_DEPENDENCY,
    "relates": DependencyClassification.INFORMATIONAL,
    "duplicate": DependencyClassification.NON_DEPENDENCY,
    "cloners": DependencyClassification.NON_DEPENDENCY,
    "polaris merge work item link": DependencyClassification.NON_DEPENDENCY,
    "discovery - connected": DependencyClassification.NON_DEPENDENCY,
    "defect": DependencyClassification.CONTEXTUAL,
}


def classify_jira_link_type(link_type_name: Optional[str]) -> DependencyClassification:
    """Classify a Jira issue link type name safely and deterministically.

    Unknown link types default to DependencyClassification.UNKNOWN, never to HARD_BLOCK.
    """
    if not link_type_name or not isinstance(link_type_name, str):
        return DependencyClassification.UNKNOWN
    cleaned = link_type_name.strip().lower()
    return JIRA_LINK_TYPE_MAP.get(cleaned, DependencyClassification.UNKNOWN)


class JiraIssueLinkRecord(BaseModel):
    """Normalized representation of a directed Jira issue link relationship."""
    id: str = Field(..., description="Deterministic unique ID: source_key:target_key:link_type_name")
    source_issue_key: str = Field(..., description="Source/predecessor issue key (e.g., blocking issue)")
    target_issue_key: str = Field(..., description="Target/successor issue key (e.g., blocked issue)")
    link_type_name: str = Field(..., description="Raw Jira link type name (e.g. 'Blocks', 'Relates')")
    inward_description: Optional[str] = Field(None, description="e.g. 'is blocked by'")
    outward_description: Optional[str] = Field(None, description="e.g. 'blocks'")
    classification: DependencyClassification = Field(default=DependencyClassification.UNKNOWN)
    source_issue_id: Optional[str] = None
    target_issue_id: Optional[str] = None
    first_seen_at: str
    last_seen_at: str
    is_active: bool = True

    class Config:
        populate_by_name = True
        extra = "allow"


class DependencyEdge(BaseModel):
    """An edge in the dependency DAG from predecessor (source) to successor (target)."""
    source_key: str = Field(..., description="Predecessor issue key that must complete first")
    target_key: str = Field(..., description="Successor issue key that is blocked or dependent")
    link_type: str = Field(..., description="Raw Jira link type name")
    classification: DependencyClassification = Field(..., description="Dependency classification")
    is_hard_block: bool = Field(default=True, description="True if this edge represents a hard scheduling block")


class CycleDetectionResult(BaseModel):
    """Result of cycle detection on a dependency graph."""
    has_cycle: bool
    cycle_nodes: List[str] = Field(default_factory=list, description="Cycle path if detected e.g. ['A', 'B', 'C', 'A']")
    error_message: Optional[str] = None


class TopologicalSortResult(BaseModel):
    """Result of topological sorting on a dependency graph."""
    is_acyclic: bool
    ordered_keys: List[str] = Field(default_factory=list, description="Deterministically ordered issue keys")
    cycle_details: Optional[CycleDetectionResult] = None
