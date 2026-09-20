"""Deterministic tests for Phase 3A: Jira Issue Link Normalization and Dependency DAG.

Covers all Phase 3A requirements A through U:
A. Blocks link normalization
B. Problem/Incident normalization
C. Test link normalization
D. Informational Relates relationship
E. Duplicate non-dependency
F. Cloners non-dependency
G. Merge relationship non-dependency
H. Discovery relationship non-dependency
I. Defect contextual relationship
J. Unknown link type
K. Duplicate poll/upsert idempotency
L. A -> B graph
M. A -> B -> C graph
N. Multiple independent chains
O. Cycle detection
P. Deterministic topological ordering
Q. Hard blocker detection
R. Cross-resource dependency
S. Empty graph
T. Self-loop cycle
U. Malformed link payload handling
"""

import pytest
import sqlite3
from typing import Dict, Any

from app.core.models.planning import (
    DependencyClassification,
    classify_jira_link_type,
    JIRA_LINK_TYPE_MAP,
)
from app.core.planning.dag import DependencyGraph
from app.database.connection import DatabaseManager
from app.database.repositories import JiraIssueLinkRepository
from app.database.schema import init_db


@pytest.fixture
def test_db_manager(tmp_path):
    """Provide isolated in-memory or temp-file database for Phase 3A tests."""
    db_file = str(tmp_path / "test_pm_phase3a.db")
    mgr = DatabaseManager(db_path=db_file)
    init_db(mgr)
    return mgr


class TestLinkClassification:
    """Tests A through J: Verification of deterministic link classifications."""

    def test_blocks_classification(self):
        """A. Blocks link normalization -> HARD_BLOCK."""
        assert classify_jira_link_type("Blocks") == DependencyClassification.HARD_BLOCK
        assert classify_jira_link_type("blocks") == DependencyClassification.HARD_BLOCK

    def test_problem_incident_classification(self):
        """B. Problem/Incident normalization -> CAUSAL_DEPENDENCY."""
        assert classify_jira_link_type("Problem/Incident") == DependencyClassification.CAUSAL_DEPENDENCY
        assert classify_jira_link_type("problem/incident") == DependencyClassification.CAUSAL_DEPENDENCY

    def test_test_link_classification(self):
        """C. Test link normalization -> VERIFICATION_DEPENDENCY."""
        assert classify_jira_link_type("Test") == DependencyClassification.VERIFICATION_DEPENDENCY
        assert classify_jira_link_type("test") == DependencyClassification.VERIFICATION_DEPENDENCY

    def test_relates_classification(self):
        """D. Informational Relates relationship -> INFORMATIONAL."""
        assert classify_jira_link_type("Relates") == DependencyClassification.INFORMATIONAL
        assert classify_jira_link_type("relates") == DependencyClassification.INFORMATIONAL

    def test_duplicate_classification(self):
        """E. Duplicate non-dependency -> NON_DEPENDENCY."""
        assert classify_jira_link_type("Duplicate") == DependencyClassification.NON_DEPENDENCY

    def test_cloners_classification(self):
        """F. Cloners non-dependency -> NON_DEPENDENCY."""
        assert classify_jira_link_type("Cloners") == DependencyClassification.NON_DEPENDENCY

    def test_polaris_merge_classification(self):
        """G. Merge relationship non-dependency -> NON_DEPENDENCY."""
        assert classify_jira_link_type("Polaris merge work item link") == DependencyClassification.NON_DEPENDENCY

    def test_discovery_connected_classification(self):
        """H. Discovery relationship non-dependency -> NON_DEPENDENCY."""
        assert classify_jira_link_type("Discovery - Connected") == DependencyClassification.NON_DEPENDENCY

    def test_defect_classification(self):
        """I. Defect contextual relationship -> CONTEXTUAL."""
        assert classify_jira_link_type("Defect") == DependencyClassification.CONTEXTUAL

    def test_unknown_link_type_classification(self):
        """J. Unknown link type -> UNKNOWN (never assumes HARD_BLOCK)."""
        assert classify_jira_link_type("CustomRandomLink") == DependencyClassification.UNKNOWN
        assert classify_jira_link_type(None) == DependencyClassification.UNKNOWN
        assert classify_jira_link_type("") == DependencyClassification.UNKNOWN


class TestLinkRepository:
    """Tests K and persistence/idempotency requirements."""

    def test_idempotent_upsert(self, test_db_manager):
        """K. Duplicate poll/upsert results in single record with updated timestamp."""
        repo = JiraIssueLinkRepository(test_db_manager)

        # First observation
        id1 = repo.upsert_link(
            source_issue_key="PROJ-101",
            target_issue_key="PROJ-102",
            link_type_name="Blocks",
            inward_description="is blocked by",
            outward_description="blocks",
            observed_at="2026-09-20T10:00:00Z",
        )
        assert repo.count() == 1

        rec1 = repo.get_by_id(id1)
        assert rec1 is not None
        assert rec1["classification"] == "HARD_BLOCK"
        assert rec1["first_seen_at"] == "2026-09-20T10:00:00Z"
        assert rec1["last_seen_at"] == "2026-09-20T10:00:00Z"

        # Repeated poll observation
        id2 = repo.upsert_link(
            source_issue_key="PROJ-101",
            target_issue_key="PROJ-102",
            link_type_name="Blocks",
            inward_description="is blocked by",
            outward_description="blocks",
            observed_at="2026-09-20T10:05:00Z",
        )
        assert id1 == id2
        assert repo.count() == 1

        rec2 = repo.get_by_id(id2)
        assert rec2["first_seen_at"] == "2026-09-20T10:00:00Z"
        assert rec2["last_seen_at"] == "2026-09-20T10:05:00Z"

    def test_query_source_and_target_links(self, test_db_manager):
        repo = JiraIssueLinkRepository(test_db_manager)
        repo.upsert_link(
            source_issue_key="ALPHA-1",
            target_issue_key="BETA-2",
            link_type_name="Blocks",
        )
        repo.upsert_link(
            source_issue_key="ALPHA-1",
            target_issue_key="GAMMA-3",
            link_type_name="Relates",
        )

        sources = repo.list_links_for_source("ALPHA-1")
        assert len(sources) == 2

        targets = repo.list_links_for_target("BETA-2")
        assert len(targets) == 1
        assert targets[0]["source_issue_key"] == "ALPHA-1"
        assert targets[0]["classification"] == "HARD_BLOCK"


class TestDependencyGraph:
    """Tests L through U: Graph algorithms, DAGs, cycle detection, and topological sorting."""

    def test_a_to_b_graph(self):
        """L. A -> B graph."""
        graph = DependencyGraph(include_only_hard_blocks=True)
        added = graph.add_edge("TASK-A", "TASK-B", "Blocks", DependencyClassification.HARD_BLOCK)
        assert added is True
        assert graph.node_count == 2
        assert graph.edge_count == 1
        assert graph.get_predecessors("TASK-B") == ["TASK-A"]
        assert graph.get_successors("TASK-A") == ["TASK-B"]

        sort_res = graph.topological_sort()
        assert sort_res.is_acyclic is True
        assert sort_res.ordered_keys == ["TASK-A", "TASK-B"]

    def test_a_to_b_to_c_graph(self):
        """M. A -> B -> C graph."""
        graph = DependencyGraph(include_only_hard_blocks=True)
        graph.add_edge("TASK-A", "TASK-B", "Blocks", DependencyClassification.HARD_BLOCK)
        graph.add_edge("TASK-B", "TASK-C", "Blocks", DependencyClassification.HARD_BLOCK)

        assert graph.node_count == 3
        assert graph.edge_count == 2
        sort_res = graph.topological_sort()
        assert sort_res.is_acyclic is True
        assert sort_res.ordered_keys == ["TASK-A", "TASK-B", "TASK-C"]

    def test_multiple_independent_chains(self):
        """N. Multiple independent chains."""
        # Chain 1: A -> B
        # Chain 2: X -> Y
        graph = DependencyGraph(include_only_hard_blocks=True)
        graph.add_edge("TASK-A", "TASK-B", "Blocks", DependencyClassification.HARD_BLOCK)
        graph.add_edge("TASK-X", "TASK-Y", "Blocks", DependencyClassification.HARD_BLOCK)

        assert graph.node_count == 4
        sort_res = graph.topological_sort()
        assert sort_res.is_acyclic is True
        # Deterministic tie-breaking orders TASK-A before TASK-X
        assert sort_res.ordered_keys == ["TASK-A", "TASK-B", "TASK-X", "TASK-Y"]

    def test_cycle_detection_3_nodes(self):
        """O. Cycle detection: A -> B -> C -> A."""
        graph = DependencyGraph(include_only_hard_blocks=True)
        graph.add_edge("TASK-A", "TASK-B", "Blocks", DependencyClassification.HARD_BLOCK)
        graph.add_edge("TASK-B", "TASK-C", "Blocks", DependencyClassification.HARD_BLOCK)
        graph.add_edge("TASK-C", "TASK-A", "Blocks", DependencyClassification.HARD_BLOCK)

        cycle_res = graph.detect_cycles()
        assert cycle_res.has_cycle is True
        assert len(cycle_res.cycle_nodes) >= 3

        sort_res = graph.topological_sort()
        assert sort_res.is_acyclic is False
        assert sort_res.ordered_keys == []
        assert sort_res.cycle_details is not None
        assert sort_res.cycle_details.has_cycle is True

    def test_deterministic_topological_ordering_tie_break(self):
        """P. Deterministic topological ordering with tie-breaking."""
        # Root A and Root B both point to C.
        # Both A and B have in-degree 0 initially.
        # Deterministic alphabetical tie-breaking must yield [A, B, C], never [B, A, C].
        graph = DependencyGraph(include_only_hard_blocks=True)
        graph.add_edge("ROOT-Z", "LEAF-1", "Blocks", DependencyClassification.HARD_BLOCK)
        graph.add_edge("ROOT-A", "LEAF-1", "Blocks", DependencyClassification.HARD_BLOCK)

        sort_res = graph.topological_sort()
        assert sort_res.is_acyclic is True
        assert sort_res.ordered_keys == ["ROOT-A", "ROOT-Z", "LEAF-1"]

    def test_hard_blocker_detection(self):
        """Q. Hard blocker detection."""
        graph = DependencyGraph(include_only_hard_blocks=True)
        graph.add_edge("BLOCKER-1", "BLOCKED-2", "Blocks", DependencyClassification.HARD_BLOCK)
        graph.add_node("FREE-3")

        assert graph.has_hard_blockers("BLOCKED-2") is True
        assert graph.has_hard_blockers("BLOCKER-1") is False
        assert graph.has_hard_blockers("FREE-3") is False

    def test_cross_resource_dependency(self):
        """R. Cross-resource dependency operates strictly on issue keys."""
        # Alice owns DEV-10, Bob owns QA-20. The graph does not care who owns what;
        # it strictly establishes DEV-10 -> QA-20 precedence.
        graph = DependencyGraph(include_only_hard_blocks=True)
        graph.add_edge("DEV-10", "QA-20", "Blocks", DependencyClassification.HARD_BLOCK)

        assert graph.get_predecessors("QA-20") == ["DEV-10"]
        assert graph.get_successors("DEV-10") == ["QA-20"]
        sort_res = graph.topological_sort()
        assert sort_res.ordered_keys == ["DEV-10", "QA-20"]

    def test_empty_graph(self):
        """S. Empty graph."""
        graph = DependencyGraph(include_only_hard_blocks=True)
        assert graph.node_count == 0
        assert graph.edge_count == 0
        cycle_res = graph.detect_cycles()
        assert cycle_res.has_cycle is False
        sort_res = graph.topological_sort()
        assert sort_res.is_acyclic is True
        assert sort_res.ordered_keys == []

    def test_self_loop(self):
        """T. Self-loop: A -> A."""
        graph = DependencyGraph(include_only_hard_blocks=True)
        graph.add_edge("TASK-LOOP", "TASK-LOOP", "Blocks", DependencyClassification.HARD_BLOCK)

        cycle_res = graph.detect_cycles()
        assert cycle_res.has_cycle is True
        assert "TASK-LOOP" in cycle_res.cycle_nodes

        sort_res = graph.topological_sort()
        assert sort_res.is_acyclic is False

    def test_malformed_link_payload(self):
        """U. Malformed link payload handling in from_link_records factory."""
        malformed_links = [
            {"source_issue_key": None, "target_issue_key": "B"},
            {"source_issue_key": "A", "target_issue_key": ""},
            {"source_issue_key": "VALID-1", "target_issue_key": "VALID-2", "link_type_name": "Blocks"},
            {"corrupted": True},
        ]
        graph = DependencyGraph.from_link_records(malformed_links)
        assert graph.node_count == 2
        assert graph.edge_count == 1
        assert graph.get_successors("VALID-1") == ["VALID-2"]

    def test_graph_filters_non_hard_blockers_by_default(self):
        """Verify that Relates and Duplicate do not become scheduling edges by default."""
        links = [
            {"source_issue_key": "T-1", "target_issue_key": "T-2", "link_type_name": "Blocks"},
            {"source_issue_key": "T-3", "target_issue_key": "T-4", "link_type_name": "Relates"},
            {"source_issue_key": "T-5", "target_issue_key": "T-6", "link_type_name": "Duplicate"},
        ]
        graph = DependencyGraph.from_link_records(links, include_only_hard_blocks=True)
        assert graph.node_count == 2
        assert graph.edge_count == 1
        assert graph.all_nodes == ["T-1", "T-2"]
