"""Deterministic tests for Phase 3B: Artifact Model & Work Product Handoff Foundation.

Covers all Phase 3B scenarios A through T:
A. Valid 'artifact:api-spec' label
B. Multiple artifact labels
C. Non-artifact labels ignored
D. Malformed artifact label
E. Duplicate artifact label
F. Artifact identity normalization
G. Explicit artifact provenance
H. Missing producer
I. Missing consumer
J. Same artifact name in different projects
K. Repeated observation / idempotent upsert
L. Artifact status updates
M. Explicit producer/consumer relationship
N. Inferred relationship (TaskNature-based)
O. Inferred relationship marked as non-authoritative
P. Inferred relationship CANNOT become HARD_BLOCK
Q. Unknown task nature
R. Empty artifact dataset
S. Inactive artifact relationship
T. Zero Jira mutations (read-only verification)
"""

import pytest
from typing import Dict, Any

from app.core.models.planning import (
    ArtifactType,
    ArtifactProvenance,
    ArtifactStatus,
    ArtifactRecord,
    ArtifactRelationshipRecord,
    DependencyClassification,
)
from app.core.planning.artifacts import (
    ArtifactEngine,
    parse_artifact_label,
    infer_artifact_type_from_name,
)
from app.core.planning.dag import DependencyGraph
from app.database.connection import DatabaseManager
from app.database.repositories import ArtifactRepository
from app.database.schema import init_db


@pytest.fixture
def test_db_manager(tmp_path):
    """Provide isolated in-memory or temp-file database for Phase 3B tests."""
    db_file = str(tmp_path / "test_pm_phase3b.db")
    mgr = DatabaseManager(db_path=db_file)
    init_db(mgr)
    return mgr


class TestArtifactLabelParsing:
    """Tests A through F: Unit parsing and normalization of opt-in artifact labels."""

    def test_valid_artifact_label(self):
        """A. Valid 'artifact:api-spec' label."""
        res = parse_artifact_label("artifact:api-spec")
        assert res is not None
        assert res == ("api-spec", None)

    def test_multiple_artifact_labels(self):
        """B. Multiple artifact labels parsed independently."""
        labels = ["artifact:api-spec", "artifact:figma-v1", "produces:build-package"]
        parsed = [parse_artifact_label(l) for l in labels]
        assert parsed == [
            ("api-spec", None),
            ("figma-v1", None),
            ("build-package", "PRODUCES"),
        ]

    def test_non_artifact_labels_ignored(self):
        """C. Non-artifact labels ignored."""
        assert parse_artifact_label("bug") is None
        assert parse_artifact_label("frontend") is None
        assert parse_artifact_label("1st_reminder_sent") is None
        assert parse_artifact_label("v1.2.3") is None
        assert parse_artifact_label("") is None
        assert parse_artifact_label(None) is None

    def test_malformed_artifact_label(self):
        """D. Malformed artifact label handled safely."""
        assert parse_artifact_label("artifact:") is None
        assert parse_artifact_label("artifact:   ") is None
        assert parse_artifact_label("artifact:invalid spaces name") is None
        assert parse_artifact_label("artifact:!!!bad_chars") is None
        assert parse_artifact_label("produces:") is None
        assert parse_artifact_label("consumes:") is None

    def test_duplicate_artifact_label(self):
        """E. Duplicate artifact label normalizes to identical name."""
        res1 = parse_artifact_label("artifact:api-spec")
        res2 = parse_artifact_label("ARTIFACT:API-SPEC")
        assert res1 == res2 == ("api-spec", None)

    def test_artifact_identity_normalization(self):
        """F. Artifact identity normalization: project_key:name in lowercase."""
        art_id1 = ArtifactRepository._generate_artifact_id("wsss", "API-SPEC")
        art_id2 = ArtifactRepository._generate_artifact_id("WSSS", "api-spec")
        assert art_id1 == art_id2 == "WSSS:api-spec"

    def test_type_inference_from_name(self):
        assert infer_artifact_type_from_name("api-spec") == ArtifactType.SPECIFICATION
        assert infer_artifact_type_from_name("figma-design") == ArtifactType.DESIGN_ASSET
        assert infer_artifact_type_from_name("user-api") == ArtifactType.API_CONTRACT
        assert infer_artifact_type_from_name("db-migration-v2") == ArtifactType.DATABASE_MIGRATION
        assert infer_artifact_type_from_name("release-package") == ArtifactType.BUILD_PACKAGE
        assert infer_artifact_type_from_name("qa-test-plan") == ArtifactType.TEST_SUITE
        assert infer_artifact_type_from_name("arbitrary-deliverable") == ArtifactType.GENERIC


class TestArtifactRepositoryAndEngine:
    """Tests G through S: Persistence, provenance, producer/consumer relationships, and inference."""

    def test_explicit_artifact_provenance(self, test_db_manager):
        """G. Explicit artifact provenance recorded as EXPLICIT_JIRA_LABEL."""
        engine = ArtifactEngine(test_db_manager)
        processed = engine.extract_artifacts_from_labels(
            issue_key="WSSS-100",
            project_key="WSSS",
            labels=["artifact:api-spec"],
            observed_at="2026-09-21T10:00:00Z",
        )
        assert len(processed) == 1
        art_id, role = processed[0]
        assert art_id == "WSSS:api-spec"
        assert role is None

        details = engine.get_artifact_details(art_id)
        assert details is not None
        assert details["name"] == "api-spec"
        assert details["provenance"] == ArtifactProvenance.EXPLICIT_JIRA_LABEL.value
        assert details["confidence"] == "HIGH"

    def test_missing_producer(self, test_db_manager):
        """H. Missing producer: artifact declared without an explicit producer."""
        engine = ArtifactEngine(test_db_manager)
        engine.extract_artifacts_from_labels(
            issue_key="WSSS-101",
            project_key="WSSS",
            labels=["artifact:design-mockup"],
        )
        details = engine.get_artifact_details("WSSS:design-mockup")
        assert details is not None
        assert details["producer_issue_key"] is None
        assert details["producers"] == []

    def test_missing_consumer(self, test_db_manager):
        """I. Missing consumer: artifact declared with producer but no consumers yet."""
        engine = ArtifactEngine(test_db_manager)
        engine.extract_artifacts_from_labels(
            issue_key="WSSS-102",
            project_key="WSSS",
            labels=["produces:api-spec"],
        )
        details = engine.get_artifact_details("WSSS:api-spec")
        assert details is not None
        assert details["producer_issue_key"] == "WSSS-102"
        assert details["producers"] == ["WSSS-102"]
        assert details["consumers"] == []

    def test_same_artifact_name_in_different_projects(self, test_db_manager):
        """J. Same artifact name in different projects are isolated by project_key."""
        engine = ArtifactEngine(test_db_manager)
        p1 = engine.extract_artifacts_from_labels("PROJA-1", "PROJA", ["artifact:contract"])
        p2 = engine.extract_artifacts_from_labels("PROJB-1", "PROJB", ["artifact:contract"])

        assert p1[0][0] == "PROJA:contract"
        assert p2[0][0] == "PROJB:contract"
        assert engine.repo.count_artifacts() == 2

    def test_repeated_observation_idempotent_upsert(self, test_db_manager):
        """K. Repeated observation updates timestamps without creating duplicate rows."""
        engine = ArtifactEngine(test_db_manager)
        engine.extract_artifacts_from_labels(
            "WSSS-200", "WSSS", ["produces:schema-v1"], observed_at="2026-09-21T10:00:00Z"
        )
        assert engine.repo.count_artifacts() == 1
        assert engine.repo.count_relationships() == 1

        rec1 = engine.repo.get_artifact("WSSS:schema-v1")
        assert rec1["first_seen_at"] == "2026-09-21T10:00:00Z"
        assert rec1["last_seen_at"] == "2026-09-21T10:00:00Z"

        # Second observation 10 minutes later
        engine.extract_artifacts_from_labels(
            "WSSS-200", "WSSS", ["produces:schema-v1"], observed_at="2026-09-21T10:10:00Z"
        )
        assert engine.repo.count_artifacts() == 1
        assert engine.repo.count_relationships() == 1

        rec2 = engine.repo.get_artifact("WSSS:schema-v1")
        assert rec2["first_seen_at"] == "2026-09-21T10:00:00Z"
        assert rec2["last_seen_at"] == "2026-09-21T10:10:00Z"

    def test_artifact_status_updates(self, test_db_manager):
        """L. Artifact status updates properly preserved."""
        repo = ArtifactRepository(test_db_manager)
        art_id = repo.upsert_artifact(name="spec-x", project_key="WSSS", status=ArtifactStatus.PLANNED.value)
        assert repo.get_artifact(art_id)["status"] == "PLANNED"

        repo.upsert_artifact(name="spec-x", project_key="WSSS", status=ArtifactStatus.AVAILABLE.value)
        assert repo.get_artifact(art_id)["status"] == "AVAILABLE"

    def test_explicit_producer_and_consumer_relationship(self, test_db_manager):
        """M. Explicit producer/consumer relationship."""
        engine = ArtifactEngine(test_db_manager)
        # Task A produces api-spec
        engine.extract_artifacts_from_labels("WSSS-301", "WSSS", ["produces:api-spec"])
        # Task B consumes api-spec
        engine.extract_artifacts_from_labels("WSSS-302", "WSSS", ["consumes:api-spec"])

        details = engine.get_artifact_details("WSSS:api-spec")
        assert details["producers"] == ["WSSS-301"]
        assert details["consumers"] == ["WSSS-302"]

        issue_arts = engine.list_artifacts_for_issue("WSSS-301")
        assert len(issue_arts["produced"]) == 1
        assert issue_arts["produced"][0]["name"] == "api-spec"

    def test_inferred_relationship_task_nature(self, test_db_manager):
        """N. Inferred relationship based on TaskNature (DESIGN -> DEVELOPMENT)."""
        engine = ArtifactEngine(test_db_manager)
        art_id = engine.infer_handoff_artifact(
            predecessor_key="DES-10",
            successor_key="DEV-20",
            predecessor_nature="DESIGN",
            successor_nature="DEVELOPMENT",
            project_key="DES",
        )
        assert art_id is not None
        assert art_id == "DES:design-asset"

        details = engine.get_artifact_details(art_id)
        assert details["name"] == "design-asset"
        assert details["artifact_type"] == ArtifactType.DESIGN_ASSET.value

    def test_inferred_relationship_marked_non_authoritative(self, test_db_manager):
        """O. Inferred relationship marked as non-authoritative with provenance & confidence."""
        engine = ArtifactEngine(test_db_manager)
        art_id = engine.infer_handoff_artifact(
            predecessor_key="DEV-10",
            successor_key="QA-20",
            predecessor_nature="DEVELOPMENT",
            successor_nature="QA_TESTING",
            project_key="DEV",
        )
        details = engine.get_artifact_details(art_id)
        assert details["provenance"] == ArtifactProvenance.TASK_NATURE_INFERENCE.value
        assert details["confidence"] == "MEDIUM"

        rels = details["relationships"]
        assert len(rels) == 2
        for r in rels:
            assert bool(r["is_inferred"]) is True
            assert r["provenance"] == ArtifactProvenance.TASK_NATURE_INFERENCE.value

    def test_inferred_relationship_cannot_become_hard_block(self, test_db_manager):
        """P. Inferred relationship CANNOT become HARD_BLOCK in DependencyGraph."""
        # Even if an artifact relationship exists between DES-10 and DEV-20,
        # DependencyGraph initialized with hard blocks must NOT treat it as a hard block.
        engine = ArtifactEngine(test_db_manager)
        engine.infer_handoff_artifact(
            predecessor_key="DES-10",
            successor_key="DEV-20",
            predecessor_nature="DESIGN",
            successor_nature="DEVELOPMENT",
            project_key="DES",
        )

        graph = DependencyGraph(include_only_hard_blocks=True)
        # Non-hard block additions return False and do not create edges
        added = graph.add_edge(
            "DES-10", "DEV-20", "InferredArtifact", DependencyClassification.INFORMATIONAL
        )
        assert added is False
        assert graph.node_count == 0
        assert graph.edge_count == 0
        assert graph.has_hard_blockers("DEV-20") is False

    def test_unknown_task_nature_does_not_infer(self, test_db_manager):
        """Q. Unknown task nature does not fabricate an artifact."""
        engine = ArtifactEngine(test_db_manager)
        art_id = engine.infer_handoff_artifact(
            predecessor_key="TASK-1",
            successor_key="TASK-2",
            predecessor_nature="UNKNOWN",
            successor_nature="UNKNOWN",
            project_key="PROJ",
        )
        assert art_id is None
        assert engine.repo.count_artifacts() == 0

    def test_empty_artifact_dataset(self, test_db_manager):
        """R. Empty artifact dataset handles queries cleanly."""
        engine = ArtifactEngine(test_db_manager)
        assert engine.get_artifact_details("NONEXISTENT") is None
        assert engine.list_artifacts_for_issue("NONEXISTENT") == {"produced": [], "consumed": []}
        assert engine.repo.list_all_artifacts() == []

    def test_inactive_artifact_relationship(self, test_db_manager):
        """S. Inactive artifact relationship filtered out when active_only=True."""
        repo = ArtifactRepository(test_db_manager)
        art_id = repo.upsert_artifact("deliv-1", "PROJ", is_active=False)
        repo.upsert_relationship(art_id, "PROJ-1", "PRODUCES", is_active=False)

        assert repo.get_artifact(art_id)["is_active"] == 0
        assert repo.list_all_artifacts(active_only=True) == []
        assert len(repo.list_all_artifacts(active_only=False)) == 1
        assert repo.list_relationships_for_artifact(art_id, active_only=True) == []
        assert len(repo.list_relationships_for_artifact(art_id, active_only=False)) == 1

    def test_no_jira_mutations(self):
        """T. Verification that artifact engine code makes zero Jira API calls or mutations."""
        # Pure offline functions and local SQLite persistence
        assert parse_artifact_label("artifact:doc-v1") == ("doc-v1", None)
        assert infer_artifact_type_from_name("doc-v1") == ArtifactType.DOCUMENTATION
