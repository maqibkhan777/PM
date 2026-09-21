"""Deterministic Artifact Engine for Phase 3B.

Extracts explicit artifact definitions from Jira labels (e.g., 'artifact:api-spec'),
distinguishes explicit producer/consumer relationships, provides deterministic
task-nature inference, and serves deterministic artifact queries.

CRITICAL ARCHITECTURAL RULE:
Explicit artifact relationships are authoritative.
Inferred artifact relationships are advisory and carry provenance and confidence.
An inferred artifact MUST NOT automatically become a HARD_BLOCK scheduling dependency.
"""

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from app.core.models.planning import (
    ArtifactType,
    ArtifactProvenance,
    ArtifactStatus,
    ArtifactRecord,
    ArtifactRelationshipRecord,
)
from app.database.connection import db_manager, DatabaseManager
from app.database.repositories import ArtifactRepository
from app.utils.logger import logger
from app.utils.time import utc_now_iso


# Regex to match opt-in artifact label conventions:
# 1. artifact:<name> (e.g. 'artifact:api-spec')
# 2. produces:artifact:<name> or produces:<name>
# 3. consumes:artifact:<name> or consumes:<name>
EXPLICIT_ARTIFACT_LABEL_PATTERN = re.compile(
    r"^(?:(produces|consumes):)?(?:artifact:)?([a-z0-9_\-\.]+)(?::(produces|consumes))?$",
    re.IGNORECASE,
)

# Heuristic category mapping from artifact name keywords to standard ArtifactType
ARTIFACT_NAME_TYPE_KEYWORDS = [
    ("spec", ArtifactType.SPECIFICATION),
    ("design", ArtifactType.DESIGN_ASSET),
    ("figma", ArtifactType.DESIGN_ASSET),
    ("ui", ArtifactType.DESIGN_ASSET),
    ("ux", ArtifactType.DESIGN_ASSET),
    ("api", ArtifactType.API_CONTRACT),
    ("schema", ArtifactType.API_CONTRACT),
    ("migration", ArtifactType.DATABASE_MIGRATION),
    ("db", ArtifactType.DATABASE_MIGRATION),
    ("package", ArtifactType.BUILD_PACKAGE),
    ("build", ArtifactType.BUILD_PACKAGE),
    ("release", ArtifactType.BUILD_PACKAGE),
    ("test", ArtifactType.TEST_SUITE),
    ("qa", ArtifactType.TEST_SUITE),
    ("doc", ArtifactType.DOCUMENTATION),
]


def parse_artifact_label(raw_label: str) -> Optional[Tuple[str, Optional[str]]]:
    """Parse an opt-in artifact label deterministically.

    Supported patterns:
    - 'artifact:api-spec' -> ('api-spec', None)
    - 'produces:api-spec' or 'produces:artifact:api-spec' -> ('api-spec', 'PRODUCES')
    - 'consumes:api-spec' or 'consumes:artifact:api-spec' -> ('api-spec', 'CONSUMES')

    Returns:
        (normalized_artifact_name, role) where role in ('PRODUCES', 'CONSUMES', None),
        or None if the label is not an artifact label or is malformed.
    """
    if not raw_label or not isinstance(raw_label, str):
        return None

    cleaned = raw_label.strip()
    if not cleaned:
        return None

    # Strict check: must start with 'artifact:', 'produces:', or 'consumes:'
    lower = cleaned.lower()
    if not (lower.startswith("artifact:") or lower.startswith("produces:") or lower.startswith("consumes:")):
        return None

    prefix_role = None
    if lower.startswith("produces:"):
        prefix_role = "PRODUCES"
        cleaned_body = cleaned[9:]
    elif lower.startswith("consumes:"):
        prefix_role = "CONSUMES"
        cleaned_body = cleaned[9:]
    else:
        cleaned_body = cleaned[9:]

    # Remove optional nested 'artifact:' prefix e.g. 'produces:artifact:xyz'
    if cleaned_body.lower().startswith("artifact:"):
        cleaned_body = cleaned_body[9:]

    # Validate artifact name characters: alphanumeric, hyphens, underscores, dots
    art_name = cleaned_body.strip().lower()
    if not art_name or not re.match(r"^[a-z0-9][a-z0-9_\-\.]*$", art_name):
        return None

    return (art_name, prefix_role)


def infer_artifact_type_from_name(artifact_name: str) -> ArtifactType:
    """Deterministically categorize an artifact name into a standard ArtifactType."""
    lower = artifact_name.lower()
    for kw, atype in ARTIFACT_NAME_TYPE_KEYWORDS:
        if kw in lower:
            return atype
    return ArtifactType.GENERIC


class ArtifactEngine:
    """Deterministic engine for parsing, recording, querying, and synthesizing work products."""

    def __init__(self, manager: Optional[DatabaseManager] = None):
        self.mgr = manager or db_manager
        self.repo = ArtifactRepository(self.mgr)

    def extract_artifacts_from_labels(
        self,
        issue_key: str,
        project_key: str,
        labels: List[str],
        observed_at: Optional[str] = None,
    ) -> List[Tuple[str, Optional[str]]]:
        """Extract all valid artifact labels for a Jira issue and record them.

        Returns:
            List of (artifact_id, role) tuples processed for this issue.
        """
        now_str = observed_at or utc_now_iso()
        processed: List[Tuple[str, Optional[str]]] = []

        if not labels or not isinstance(labels, list):
            return processed

        for label in labels:
            parsed = parse_artifact_label(str(label))
            if not parsed:
                continue

            art_name, explicit_role = parsed
            art_type = infer_artifact_type_from_name(art_name)

            # 1. Upsert project artifact record
            art_id = self.repo.upsert_artifact(
                name=art_name,
                project_key=project_key,
                artifact_type=art_type.value,
                status=ArtifactStatus.PLANNED.value,
                producer_issue_key=issue_key if explicit_role == "PRODUCES" else None,
                provenance=ArtifactProvenance.EXPLICIT_JIRA_LABEL.value,
                confidence="HIGH",
                observed_at=now_str,
                is_active=True,
            )

            # 2. If explicit producer or consumer role was declared, record relationship
            if explicit_role:
                self.repo.upsert_relationship(
                    artifact_id=art_id,
                    issue_key=issue_key,
                    relationship_type=explicit_role,
                    provenance=ArtifactProvenance.EXPLICIT_JIRA_LABEL.value,
                    confidence="HIGH",
                    is_inferred=False,
                    observed_at=now_str,
                    is_active=True,
                )

            processed.append((art_id, explicit_role))

        return processed

    def infer_handoff_artifact(
        self,
        predecessor_key: str,
        successor_key: str,
        predecessor_nature: str,
        successor_nature: str,
        project_key: str,
        observed_at: Optional[str] = None,
    ) -> Optional[str]:
        """Synthesize an advisory handoff artifact between two tasks based on TaskNature.

        CRITICAL ARCHITECTURAL RULES:
        1. Inferred provenance: TASK_NATURE_INFERENCE.
        2. Confidence: MEDIUM or LOW.
        3. Never marked as confirmed.
        4. Inferred artifacts MUST NOT become HARD_BLOCK scheduling dependencies.

        Supported inference transitions:
        - DESIGN -> DEVELOPMENT => 'design-asset' (DESIGN_ASSET)
        - DEVELOPMENT -> QA_TESTING => 'build-package' (BUILD_PACKAGE)
        - RESEARCH -> DEVELOPMENT => 'research-spec' (SPECIFICATION)
        - BUSINESS_ANALYSIS -> DEVELOPMENT => 'requirements-spec' (SPECIFICATION)
        """
        now_str = observed_at or utc_now_iso()
        p_nat = str(predecessor_nature).strip().upper()
        s_nat = str(successor_nature).strip().upper()

        art_name = None
        art_type = None

        if p_nat == "DESIGN" and s_nat in ("DEVELOPMENT", "ENHANCEMENT", "BUG_FIX"):
            art_name = "design-asset"
            art_type = ArtifactType.DESIGN_ASSET
        elif p_nat in ("DEVELOPMENT", "ENHANCEMENT", "BUG_FIX") and s_nat == "QA_TESTING":
            art_name = "build-package"
            art_type = ArtifactType.BUILD_PACKAGE
        elif p_nat == "RESEARCH" and s_nat in ("DEVELOPMENT", "ENHANCEMENT"):
            art_name = "research-spec"
            art_type = ArtifactType.SPECIFICATION
        elif p_nat == "BUSINESS_ANALYSIS" and s_nat in ("DEVELOPMENT", "ENHANCEMENT"):
            art_name = "requirements-spec"
            art_type = ArtifactType.SPECIFICATION

        if not art_name or not art_type:
            return None

        # 1. Upsert inferred artifact record
        art_id = self.repo.upsert_artifact(
            name=art_name,
            project_key=project_key,
            artifact_type=art_type.value,
            status=ArtifactStatus.PLANNED.value,
            producer_issue_key=predecessor_key,
            provenance=ArtifactProvenance.TASK_NATURE_INFERENCE.value,
            confidence="MEDIUM",
            observed_at=now_str,
            is_active=True,
        )

        # 2. Upsert inferred producer relationship
        self.repo.upsert_relationship(
            artifact_id=art_id,
            issue_key=predecessor_key,
            relationship_type="PRODUCES",
            provenance=ArtifactProvenance.TASK_NATURE_INFERENCE.value,
            confidence="MEDIUM",
            is_inferred=True,
            observed_at=now_str,
            is_active=True,
        )

        # 3. Upsert inferred consumer relationship
        self.repo.upsert_relationship(
            artifact_id=art_id,
            issue_key=successor_key,
            relationship_type="CONSUMES",
            provenance=ArtifactProvenance.TASK_NATURE_INFERENCE.value,
            confidence="MEDIUM",
            is_inferred=True,
            observed_at=now_str,
            is_active=True,
        )

        return art_id

    def get_artifact_details(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve full details of an artifact including producers and consumers."""
        art = self.repo.get_artifact(artifact_id)
        if not art:
            return None

        rels = self.repo.list_relationships_for_artifact(artifact_id, active_only=True)
        producers = [r["issue_key"] for r in rels if r["relationship_type"] == "PRODUCES"]
        consumers = [r["issue_key"] for r in rels if r["relationship_type"] == "CONSUMES"]

        res = dict(art)
        res["producers"] = producers
        res["consumers"] = consumers
        res["relationships"] = rels
        return res

    def list_artifacts_for_issue(self, issue_key: str) -> Dict[str, List[Dict[str, Any]]]:
        """List all artifacts produced or consumed by a specific Jira issue."""
        rels = self.repo.list_relationships_for_issue(issue_key, active_only=True)
        produced = []
        consumed = []
        for r in rels:
            art = self.repo.get_artifact(r["artifact_id"])
            if not art:
                continue
            item = dict(art)
            item["relationship_provenance"] = r["provenance"]
            item["relationship_confidence"] = r["confidence"]
            item["is_inferred"] = bool(r.get("is_inferred", 0))
            if r["relationship_type"] == "PRODUCES":
                produced.append(item)
            elif r["relationship_type"] == "CONSUMES":
                consumed.append(item)

        return {"produced": produced, "consumed": consumed}
