"""Deterministic Dependency Graph (DAG) Engine for Phase 3A.

Provides:
- In-memory directed dependency graph
- Addition and querying of dependency edges (predecessors, successors)
- Distinction between HARD_BLOCK dependencies and other relationship types
- Cycle detection with exact cycle path reporting
- Stable, deterministic topological sorting with tie-breaking
- Hard blocker check for any given task
"""

from collections import defaultdict, deque
from typing import Any, Dict, List, Optional, Set, Tuple

from app.core.models.planning import (
    DependencyClassification,
    DependencyEdge,
    CycleDetectionResult,
    TopologicalSortResult,
)
from app.utils.logger import logger


class DependencyGraph:
    """In-memory deterministic Directed Acyclic Graph (DAG) for issue dependencies.

    Convention:
      Edge (A -> B) means A is a predecessor of B (e.g. A blocks B).
      B depends on A; A must complete before B can proceed.
    """

    def __init__(self, include_only_hard_blocks: bool = True):
        """Initialize empty graph.

        Args:
            include_only_hard_blocks: If True, only edges with classification HARD_BLOCK
                                     are considered scheduling dependencies in the graph.
        """
        self.include_only_hard_blocks = include_only_hard_blocks
        # Adjacency lists:
        # successors[u] = list of (v, edge) where u -> v
        self._successors: Dict[str, List[Tuple[str, DependencyEdge]]] = defaultdict(list)
        # predecessors[v] = list of (u, edge) where u -> v
        self._predecessors: Dict[str, List[Tuple[str, DependencyEdge]]] = defaultdict(list)
        # All known nodes
        self._nodes: Set[str] = set()

    def add_node(self, node_key: str) -> None:
        """Register a node in the graph."""
        clean_key = str(node_key).strip().upper()
        self._nodes.add(clean_key)
        if clean_key not in self._successors:
            self._successors[clean_key] = []
        if clean_key not in self._predecessors:
            self._predecessors[clean_key] = []

    def add_edge(
        self,
        source_key: str,
        target_key: str,
        link_type: str,
        classification: DependencyClassification,
    ) -> bool:
        """Add a directed edge from source to target.

        source_key: Predecessor issue key (e.g. blocking issue).
        target_key: Successor issue key (e.g. blocked issue).

        Returns:
            True if edge was added to the graph, False if skipped (e.g. non-hard block when filtered).
        """
        src = str(source_key).strip().upper()
        tgt = str(target_key).strip().upper()

        is_hard = (classification == DependencyClassification.HARD_BLOCK)

        if self.include_only_hard_blocks and not is_hard:
            # Informational, non-dependency, or causal/verification without hard block semantics
            return False

        self.add_node(src)
        self.add_node(tgt)

        edge = DependencyEdge(
            source_key=src,
            target_key=tgt,
            link_type=link_type,
            classification=classification,
            is_hard_block=is_hard,
        )

        # Avoid duplicate parallel edges
        if not any(v == tgt and e.link_type == link_type for v, e in self._successors[src]):
            self._successors[src].append((tgt, edge))
            self._predecessors[tgt].append((src, edge))
            return True

        return False

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    @property
    def edge_count(self) -> int:
        return sum(len(edges) for edges in self._successors.values())

    @property
    def all_nodes(self) -> List[str]:
        return sorted(list(self._nodes))

    def get_predecessors(self, node_key: str) -> List[str]:
        """Return all immediate predecessor keys (tasks that must complete before node_key)."""
        clean_key = str(node_key).strip().upper()
        return sorted([src for src, _ in self._predecessors.get(clean_key, [])])

    def get_successors(self, node_key: str) -> List[str]:
        """Return all immediate successor keys (tasks waiting on node_key)."""
        clean_key = str(node_key).strip().upper()
        return sorted([tgt for tgt, _ in self._successors.get(clean_key, [])])

    def has_hard_blockers(self, node_key: str) -> bool:
        """Check whether the given task has any hard blocking predecessors."""
        clean_key = str(node_key).strip().upper()
        preds = self._predecessors.get(clean_key, [])
        return any(edge.is_hard_block for _, edge in preds)

    def detect_cycles(self) -> CycleDetectionResult:
        """Deterministic cycle detection using depth-first search (DFS) with 3-color marking.

        Colors:
          0 = unvisited (WHITE)
          1 = currently in recursion stack (GRAY)
          2 = completely explored (BLACK)
        """
        color: Dict[str, int] = {node: 0 for node in self._nodes}
        parent: Dict[str, Optional[str]] = {node: None for node in self._nodes}

        # Iterate deterministically over sorted nodes
        for start_node in sorted(list(self._nodes)):
            if color[start_node] != 0:
                continue

            # Run iterative DFS to prevent recursion limit on deep graphs
            stack: List[Tuple[str, int]] = [(start_node, 0)]
            color[start_node] = 1

            while stack:
                curr, edge_idx = stack[-1]
                succs = sorted([tgt for tgt, _ in self._successors.get(curr, [])])

                if edge_idx < len(succs):
                    nbr = succs[edge_idx]
                    # Update stack frame edge index
                    stack[-1] = (curr, edge_idx + 1)

                    if color[nbr] == 1:
                        # Cycle found! Reconstruct cycle path
                        cycle_path = [nbr, curr]
                        # Trace back through stack
                        for s_node, _ in reversed(stack[:-1]):
                            cycle_path.append(s_node)
                            if s_node == nbr:
                                break
                        cycle_path.reverse()
                        logger.warning(f"Dependency cycle detected in graph: {' -> '.join(cycle_path)}")
                        return CycleDetectionResult(
                            has_cycle=True,
                            cycle_nodes=cycle_path,
                            error_message=f"Dependency cycle detected: {' -> '.join(cycle_path)}",
                        )
                    elif color[nbr] == 0:
                        color[nbr] = 1
                        parent[nbr] = curr
                        stack.append((nbr, 0))
                else:
                    color[curr] = 2
                    stack.pop()

        return CycleDetectionResult(has_cycle=False, cycle_nodes=[])

    def topological_sort(self) -> TopologicalSortResult:
        """Deterministic Kahn's algorithm for topological ordering.

        If multiple nodes have in-degree 0 simultaneously, ties are broken deterministically
        by alphabetical sorting of issue keys.

        Returns:
            TopologicalSortResult indicating whether graph is acyclic and the ordered keys.
        """
        cycle_res = self.detect_cycles()
        if cycle_res.has_cycle:
            return TopologicalSortResult(
                is_acyclic=False,
                ordered_keys=[],
                cycle_details=cycle_res,
            )

        # In-degree computation
        in_degree: Dict[str, int] = {node: 0 for node in self._nodes}
        for u in self._nodes:
            for v, _ in self._successors[u]:
                in_degree[v] += 1

        # Queue of available nodes with in-degree 0 (maintained as sorted list for deterministic tie-break)
        ready_queue = [node for node, deg in in_degree.items() if deg == 0]
        ready_queue.sort()

        ordered: List[str] = []

        while ready_queue:
            # Deterministically pop the smallest tie-breaker key
            curr = ready_queue.pop(0)
            ordered.append(curr)

            new_ready = []
            for nbr, _ in self._successors[curr]:
                in_degree[nbr] -= 1
                if in_degree[nbr] == 0:
                    new_ready.append(nbr)

            if new_ready:
                ready_queue.extend(new_ready)
                ready_queue.sort()

        if len(ordered) != len(self._nodes):
            # Fallback safeguard in case cycle was not caught
            return TopologicalSortResult(
                is_acyclic=False,
                ordered_keys=[],
                cycle_details=CycleDetectionResult(
                    has_cycle=True,
                    cycle_nodes=[],
                    error_message="Topological sort encountered unresolved dependencies (unreported cycle).",
                ),
            )

        return TopologicalSortResult(
            is_acyclic=True,
            ordered_keys=ordered,
            cycle_details=None,
        )

    @classmethod
    def from_link_records(
        cls,
        links: List[Dict[str, Any]],
        include_only_hard_blocks: bool = True,
    ) -> "DependencyGraph":
        """Factory method to construct a graph from a list of normalized link records."""
        from app.core.models.planning import classify_jira_link_type

        graph = cls(include_only_hard_blocks=include_only_hard_blocks)
        for link in links:
            src = link.get("source_issue_key")
            tgt = link.get("target_issue_key")
            l_type = link.get("link_type_name", "")
            classification_val = link.get("classification")

            if not src or not tgt:
                continue

            if classification_val:
                try:
                    classification = DependencyClassification(classification_val)
                except ValueError:
                    classification = classify_jira_link_type(l_type)
            else:
                classification = classify_jira_link_type(l_type)

            graph.add_edge(
                source_key=src,
                target_key=tgt,
                link_type=l_type,
                classification=classification,
            )
        return graph
