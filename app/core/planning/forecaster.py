"""Deterministic Team Schedule and Bottleneck Forecaster for Phase 3D.

Composes:
1. ResourceQueueSnapshot (per-resource active queues, capacity, pace, workload)
2. DependencyGraph (directed DAG of HARD_BLOCK and other issue link dependencies)
3. ArtifactEngine / ArtifactRelationshipRecord (contextual work product handoffs)
4. Configurable planning horizon (settings.PLANNING_HORIZON_WORKING_DAYS)

Produces:
TeamScheduleProjection containing feasible, explainable timeline projections,
longest dependency chains, constraint mappings, and operational bottlenecks.

CRITICAL ARCHITECTURAL BOUNDARIES:
- ZERO AI or DeepSeek calls.
- ZERO Jira mutations, due-date writes, or comment creations.
- ZERO Action Engine executions.
- ZERO automatic reassignments or task rescheduling.
- ZERO employee scoring, ranking, or leaderboard evaluations.
- All projected dates are analytical evidence for Phase 3E/PlanningContext.
"""

from collections import defaultdict, deque
from datetime import datetime, date, timezone, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from app.config.settings import settings
from app.core.models.planning import (
    Bottleneck,
    BottleneckType,
    CapacityState,
    DependencyClassification,
    QueueTaskDetail,
    ResourceQueueSnapshot,
    ScheduleConstraint,
    TaskScheduleProjection,
    TeamScheduleProjection,
)
from app.core.performance.capacity import CapacityCalculator
from app.core.planning.dag import DependencyGraph
from app.database.connection import DatabaseManager, db_manager
from app.database.repositories import ArtifactRepository, JiraIssueLinkRepository
from app.utils.logger import logger
from app.utils.time import format_iso, parse_iso_datetime, utc_now, utc_now_iso


# Deterministic priority ranking for tie-breaking
PRIORITY_ORDER: Dict[str, int] = {
    "highest": 1,
    "high": 2,
    "medium": 3,
    "low": 4,
    "lowest": 5,
}


def _get_priority_rank(priority_str: Optional[str]) -> int:
    """Return numeric rank for priority string (1 is highest, 5 is lowest, 99 is unknown)."""
    if not priority_str:
        return 99
    return PRIORITY_ORDER.get(priority_str.strip().lower(), 99)


def _to_date(dt_val: Any) -> date:
    """Safely convert datetime, date, or ISO string to a date object."""
    if isinstance(dt_val, datetime):
        return dt_val.date()
    if isinstance(dt_val, date):
        return dt_val
    if isinstance(dt_val, str):
        parsed = parse_iso_datetime(dt_val)
        if parsed:
            return parsed.date()
        try:
            return datetime.strptime(dt_val[:10], "%Y-%m-%d").date()
        except Exception:
            pass
    return utc_now().date()


def _advance_to_working_day(d: date) -> date:
    """Advance date to nearest working day (Mon-Fri) if it falls on a weekend."""
    cur = d
    while cur.weekday() >= 5:  # 5 is Saturday, 6 is Sunday
        cur += timedelta(days=1)
    return cur


def _compute_calendar_projection(
    start_d: date,
    required_hours: float,
    daily_capacity_hours: float = 6.75,
) -> Tuple[date, float]:
    """Deterministically project completion date and working days needed on standard business days.
    
    Start date is advanced to next working day if it is on a weekend.
    Task duration is computed using business-day calculation consistent with CapacityCalculator.
    """
    effective_start = _advance_to_working_day(start_d)
    
    if required_hours <= 0.0:
        return effective_start, 0.0

    cap = max(daily_capacity_hours, 1.0)
    days_needed = required_hours / cap
    
    # Starting on effective_start (which is a working day):
    # If days_needed <= 1.0, finishes on effective_start itself.
    # If days_needed > 1.0, advances by ceil(days_needed) - 1 working days.
    import math
    steps = max(0, math.ceil(days_needed) - 1)
    
    curr = effective_start
    accumulated_steps = 0
    while accumulated_steps < steps:
        curr += timedelta(days=1)
        if curr.weekday() < 5:  # Mon-Fri
            accumulated_steps += 1
            
    return curr, round(days_needed, 2)



def _compute_horizon_end_date(anchor_d: date, horizon_working_days: int) -> date:
    """Calculate the calendar date ending the N working-day horizon (inclusive of anchor if weekday)."""
    cur = _advance_to_working_day(anchor_d)
    accumulated = 1
    while accumulated < horizon_working_days:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            accumulated += 1
    return cur


class TeamScheduleForecaster:
    """Deterministic team timeline and bottleneck forecaster."""

    def __init__(
        self,
        manager: Optional[DatabaseManager] = None,
        link_repo: Optional[JiraIssueLinkRepository] = None,
        artifact_repo: Optional[ArtifactRepository] = None,
    ):
        self.mgr = manager or db_manager
        self.link_repo = link_repo or JiraIssueLinkRepository(self.mgr)
        self.artifact_repo = artifact_repo or ArtifactRepository(self.mgr)

    def forecast_team_schedule(
        self,
        team_snapshots: List[ResourceQueueSnapshot],
        dependency_graph: Optional[DependencyGraph] = None,
        horizon_working_days: Optional[int] = None,
        anchor_date: Optional[Any] = None,
    ) -> TeamScheduleProjection:
        """Deterministically project team timeline and detect operational bottlenecks.

        Args:
            team_snapshots: List of ResourceQueueSnapshot instances (one per resource).
            dependency_graph: Optional DependencyGraph. If None, built from link repository.
            horizon_working_days: Explicit planning window in business days (overrides settings).
            anchor_date: Anchor starting date/datetime (defaults to UTC now).
        """
        # 1. Resolve Horizon and Anchor Date
        effective_horizon = horizon_working_days
        if effective_horizon is None:
            effective_horizon = getattr(settings, "PLANNING_HORIZON_WORKING_DAYS", 10)
        if effective_horizon <= 0:
            effective_horizon = 10

        anchor_d = _to_date(anchor_date) if anchor_date is not None else utc_now().date()
        anchor_d = _advance_to_working_day(anchor_d)
        anchor_str = anchor_d.strftime("%Y-%m-%d")
        horizon_end_d = _compute_horizon_end_date(anchor_d, effective_horizon)
        horizon_end_str = horizon_end_d.strftime("%Y-%m-%d")
        now_ts = utc_now_iso()

        # 2. Build or Reconcile Dependency Graph
        dag = dependency_graph
        if dag is None:
            # Build from link_repo for all active tasks
            all_issue_keys = [
                t.issue_key
                for s in team_snapshots
                for t in s.active_tasks
            ]
            all_links = self.link_repo.list_all_links(active_only=True)
            dag = DependencyGraph.from_link_records(all_links, include_only_hard_blocks=True)
            # Ensure all active issue keys exist in the DAG as nodes
            for k in all_issue_keys:
                dag.add_node(k)

        # 3. Check for Dependency Cycles
        cycle_res = dag.detect_cycles()
        bottlenecks: List[Bottleneck] = []
        schedule_valid = True
        error_msg = None

        if cycle_res.has_cycle:
            schedule_valid = False
            cycle_desc = " -> ".join(cycle_res.cycle_nodes) if cycle_res.cycle_nodes else "Circular dependency"
            error_msg = f"Dependency cycle detected in HARD_BLOCK links: {cycle_desc}"
            bottlenecks.append(
                Bottleneck(
                    type=BottleneckType.DEPENDENCY_CYCLE,
                    affected_issue_key=cycle_res.cycle_nodes[0] if cycle_res.cycle_nodes else None,
                    affected_resource_id=None,
                    severity="HIGH",
                    evidence=f"Cycle path: {cycle_desc}. Topological scheduling impossible without resolving circularity.",
                    data_quality="HIGH",
                )
            )

        # 4. Map Tasks and Normalization
        # Map: issue_key -> (QueueTaskDetail, ResourceQueueSnapshot)
        task_map: Dict[str, Tuple[QueueTaskDetail, ResourceQueueSnapshot]] = {}
        # Ensure input-order independence: sort resources by canonical resource_id
        sorted_snapshots = sorted(team_snapshots, key=lambda s: s.resource_id)

        for snap in sorted_snapshots:
            for task in snap.active_tasks:
                task_map[task.issue_key] = (task, snap)

        # 5. Extract Constraints and Predecessors
        dependency_constraints: List[ScheduleConstraint] = []
        hard_preds_map: Dict[str, List[str]] = defaultdict(list)
        hard_succs_map: Dict[str, List[str]] = defaultdict(list)

        # Query all links relevant to active tasks to record ScheduleConstraint list
        active_keys_set = set(task_map.keys())
        all_link_records = self.link_repo.list_all_links(active_only=True)
        # Sort links deterministically
        sorted_links = sorted(
            all_link_records,
            key=lambda l: (l.get("source_issue_key", ""), l.get("target_issue_key", ""), l.get("link_type_name", ""))
        )

        for l in sorted_links:
            src = l.get("source_issue_key", "").strip().upper()
            tgt = l.get("target_issue_key", "").strip().upper()
            classification_val = l.get("classification")
            is_hard = (classification_val == DependencyClassification.HARD_BLOCK.value)

            # Record constraint if either source or target is in active tasks
            if src in active_keys_set or tgt in active_keys_set:
                dependency_constraints.append(
                    ScheduleConstraint(
                        source_issue_key=src,
                        target_issue_key=tgt,
                        constraint_type=classification_val or "UNKNOWN",
                        is_hard_block=is_hard,
                        description=f"{src} {l.get('link_type_name', 'relates to')} {tgt}",
                    )
                )
                if is_hard:
                    hard_preds_map[tgt].append(src)
                    hard_succs_map[src].append(tgt)

        # In case DAG has hard edges not in link records directly (e.g. injected in test dag)
        for node in dag.all_nodes:
            preds = dag.get_predecessors(node)
            for p in preds:
                if p not in hard_preds_map[node]:
                    hard_preds_map[node].append(p)
                if node not in hard_succs_map[p]:
                    hard_succs_map[p].append(node)
                # Check if already added to dependency_constraints
                if not any(c.source_issue_key == p and c.target_issue_key == node and c.is_hard_block for c in dependency_constraints):
                    dependency_constraints.append(
                        ScheduleConstraint(
                            source_issue_key=p,
                            target_issue_key=node,
                            constraint_type=DependencyClassification.HARD_BLOCK.value,
                            is_hard_block=True,
                            description=f"{p} blocks {node}",
                        )
                    )

        # 6. Surface Artifact Handoffs Contextual Constraints
        for issue_key, (task, snap) in sorted(task_map.items()):
            # Produced artifacts
            produced_names = task.produced_artifact_names or []
            # Check consumer relationships in DB
            for p_name in produced_names:
                norm_p_name = p_name.strip().lower()
                all_arts = self.artifact_repo.list_all_artifacts(active_only=True)
                matching_arts = [a for a in all_arts if a.get("name", "").strip().lower() == norm_p_name]
                for art in matching_arts:
                    art_id = art["id"]
                    rels = self.artifact_repo.list_relationships_for_artifact(art_id, active_only=True)
                    for c_rel in rels:
                        if c_rel.get("relationship_type") == "CONSUMES":
                            c_key = c_rel.get("issue_key")
                            if c_key and c_key != issue_key:
                                dependency_constraints.append(
                                    ScheduleConstraint(
                                        source_issue_key=issue_key,
                                        target_issue_key=c_key,
                                        constraint_type="ARTIFACT_HANDOFF",
                                        is_hard_block=False,
                                        description=f"{issue_key} produces '{p_name}' consumed by {c_key}",
                                    )
                                )
                                # Add contextual advisory bottleneck
                                bottlenecks.append(
                                    Bottleneck(
                                        type=BottleneckType.ARTIFACT_HANDOFF,
                                        affected_issue_key=c_key,
                                        affected_resource_id=snap.resource_id,
                                        severity="LOW",
                                        evidence=f"Task {c_key} consumes artifact '{p_name}' produced by {issue_key}. Contextual work product handoff.",
                                        data_quality="HIGH" if not c_rel.get("is_inferred") else "MEDIUM",
                                    )
                                )

        # 7. Calculate Longest Dependency Chain in HARD_BLOCK DAG
        longest_chain: List[str] = []
        if schedule_valid and dag.node_count > 0:
            longest_chain = self._compute_longest_dependency_chain(dag, active_keys_set)

        # 8. Deterministic Timeline Simulation (if valid)
        task_projections: List[TaskScheduleProjection] = []
        tasks_beyond_horizon_count = 0

        if schedule_valid:
            task_projections, tasks_beyond_horizon_count = self._simulate_timeline(
                task_map=task_map,
                sorted_snapshots=sorted_snapshots,
                hard_preds_map=hard_preds_map,
                hard_succs_map=hard_succs_map,
                longest_chain=longest_chain,
                anchor_d=anchor_d,
                horizon_end_d=horizon_end_d,
            )
        else:
            # When circular or invalid, emit un-scheduled placeholder projections
            for snap in sorted_snapshots:
                for task in sorted(snap.active_tasks, key=lambda t: t.issue_key):
                    preds = sorted(hard_preds_map.get(task.issue_key, []))
                    succs = sorted(hard_succs_map.get(task.issue_key, []))
                    task_projections.append(
                        TaskScheduleProjection(
                            issue_key=task.issue_key,
                            resource_id=snap.resource_id,
                            resource_display_name=snap.display_name,
                            current_status=task.status,
                            estimated_effort_hours=task.expected_effort_hours,
                            duration_evidence_source=task.expected_effort_source,
                            duration_confidence=task.expected_effort_confidence,
                            dependency_predecessors=preds,
                            dependency_successors=succs,
                            earliest_feasible_start_date=anchor_str,
                            projected_start_date=anchor_str,
                            projected_completion_date=anchor_str,
                            working_days_needed=0.0,
                            is_blocked_by_dependency=len(preds) > 0,
                            is_beyond_horizon=False,
                            critical_chain_position=None,
                        )
                    )

        # 9. Detect Operational Bottlenecks
        detected_bottlenecks = self._detect_bottlenecks(
            sorted_snapshots=sorted_snapshots,
            task_projections=task_projections,
            longest_chain=longest_chain,
            effective_horizon=effective_horizon,
        )
        bottlenecks.extend(detected_bottlenecks)

        # Sort bottlenecks deterministically by severity (HIGH > MEDIUM > LOW), then type, then issue/resource
        severity_rank = {"HIGH": 1, "MEDIUM": 2, "LOW": 3}
        bottlenecks.sort(
            key=lambda b: (
                severity_rank.get(b.severity, 9),
                b.type.value,
                b.affected_issue_key or "",
                b.affected_resource_id or "",
            )
        )

        # Sort dependency constraints deterministically
        dependency_constraints.sort(
            key=lambda c: (c.source_issue_key, c.target_issue_key, c.constraint_type)
        )

        # 10. Compile Data Quality Summary
        data_quality_summary = self._compile_data_quality_summary(
            sorted_snapshots=sorted_snapshots,
            task_projections=task_projections,
            schedule_valid=schedule_valid,
        )

        return TeamScheduleProjection(
            forecast_timestamp=now_ts,
            anchor_date=anchor_str,
            planning_horizon_working_days=effective_horizon,
            horizon_end_date=horizon_end_str,
            schedule_valid=schedule_valid,
            tasks_projected_count=len(task_projections),
            tasks_beyond_horizon_count=tasks_beyond_horizon_count,
            task_projections=task_projections,
            longest_dependency_chain=longest_chain,
            dependency_constraints=dependency_constraints,
            bottlenecks=bottlenecks,
            data_quality_summary=data_quality_summary,
            error_message=error_msg,
        )

    def _simulate_timeline(
        self,
        task_map: Dict[str, Tuple[QueueTaskDetail, ResourceQueueSnapshot]],
        sorted_snapshots: List[ResourceQueueSnapshot],
        hard_preds_map: Dict[str, List[str]],
        hard_succs_map: Dict[str, List[str]],
        longest_chain: List[str],
        anchor_d: date,
        horizon_end_d: date,
    ) -> Tuple[List[TaskScheduleProjection], int]:
        """Simulate parallel multi-resource execution with single-resource serialization and DAG gates."""
        # Track resource next available date
        resource_available_date: Dict[str, date] = {
            snap.resource_id: anchor_d for snap in sorted_snapshots
        }
        # Track daily capacity per resource (nominal or forecast)
        resource_capacity: Dict[str, float] = {
            snap.resource_id: max(snap.capacity.forecast_daily_capacity_hours, 1.0)
            for snap in sorted_snapshots
        }
        # In-degree of remaining HARD_BLOCK predecessors that are part of active tasks
        # (External predecessors not in active tasks are treated as external gates)
        remaining_in_degree: Dict[str, int] = {}
        for key in task_map:
            # Active predecessors that must complete first
            active_preds = [p for p in hard_preds_map.get(key, []) if p in task_map]
            remaining_in_degree[key] = len(active_preds)

        # Completed completion dates
        completed_date: Dict[str, date] = {}
        projections_by_key: Dict[str, TaskScheduleProjection] = {}

        longest_chain_indices: Dict[str, int] = {
            k: idx + 1 for idx, k in enumerate(longest_chain)
        }

        # Ready queues per resource
        # A task is ready when remaining_in_degree == 0
        def _get_sort_tuple(key: str) -> Tuple[int, str, str]:
            tdetail, _ = task_map[key]
            p_rank = _get_priority_rank(tdetail.priority)
            due = tdetail.due_date or "9999-99-99"
            return (p_rank, str(due)[:10], tdetail.issue_key)

        ready_by_resource: Dict[str, List[str]] = defaultdict(list)
        for key, deg in remaining_in_degree.items():
            if deg == 0:
                _, snap = task_map[key]
                ready_by_resource[snap.resource_id].append(key)

        for r_id in ready_by_resource:
            ready_by_resource[r_id].sort(key=_get_sort_tuple)

        # Simulation loop: while any resource has ready tasks
        scheduled_count = 0
        total_tasks = len(task_map)
        scheduled_order: List[str] = []

        while scheduled_count < total_tasks:
            progress_made = False

            # Iterate over resources in deterministic sorted order
            for snap in sorted_snapshots:
                r_id = snap.resource_id
                r_queue = ready_by_resource[r_id]
                if not r_queue:
                    continue

                # Pop next task for this resource
                curr_key = r_queue.pop(0)
                scheduled_order.append(curr_key)
                progress_made = True
                scheduled_count += 1

                task_detail, _ = task_map[curr_key]

                daily_cap = resource_capacity[r_id]

                # Determine earliest feasible start:
                # 1. Resource must be available
                r_avail = resource_available_date[r_id]
                # 2. All hard predecessors must have completed
                preds = hard_preds_map.get(curr_key, [])
                earliest_feasible = anchor_d
                for p in preds:
                    if p in completed_date:
                        if completed_date[p] > earliest_feasible:
                            earliest_feasible = completed_date[p]
                    else:
                        # External predecessor not in active tasks (e.g. external ticket)
                        # Assumed already completed or active anchor
                        pass

                # Projected start date is max(resource_available, earliest_feasible)
                proj_start = max(r_avail, earliest_feasible)
                proj_start = _advance_to_working_day(proj_start)

                # Duration calculation
                effort_h = task_detail.expected_effort_hours
                if effort_h <= 0.0:
                    effort_h = task_detail.remaining_hours
                if effort_h <= 0.0:
                    effort_h = 1.0  # Safe deterministic 1h fallback

                proj_comp, days_needed = _compute_calendar_projection(
                    start_d=proj_start,
                    required_hours=effort_h,
                    daily_capacity_hours=daily_cap,
                )

                # Update completed date and resource availability
                completed_date[curr_key] = proj_comp
                # Resource will be available after completion date
                resource_available_date[r_id] = proj_comp

                is_beyond = proj_comp > horizon_end_d
                is_blocked = len(preds) > 0

                proj = TaskScheduleProjection(
                    issue_key=curr_key,
                    resource_id=r_id,
                    resource_display_name=snap.display_name,
                    current_status=task_detail.status,
                    estimated_effort_hours=round(effort_h, 2),
                    duration_evidence_source=task_detail.expected_effort_source,
                    duration_confidence=task_detail.expected_effort_confidence,
                    dependency_predecessors=sorted(preds),
                    dependency_successors=sorted(hard_succs_map.get(curr_key, [])),
                    earliest_feasible_start_date=earliest_feasible.strftime("%Y-%m-%d"),
                    projected_start_date=proj_start.strftime("%Y-%m-%d"),
                    projected_completion_date=proj_comp.strftime("%Y-%m-%d"),
                    working_days_needed=round(days_needed, 2),
                    is_blocked_by_dependency=is_blocked,
                    is_beyond_horizon=is_beyond,
                    critical_chain_position=longest_chain_indices.get(curr_key),
                )
                projections_by_key[curr_key] = proj

                # Unblock successors
                for succ in hard_succs_map.get(curr_key, []):
                    if succ in remaining_in_degree:
                        remaining_in_degree[succ] -= 1
                        if remaining_in_degree[succ] == 0:
                            _, succ_snap = task_map[succ]
                            ready_by_resource[succ_snap.resource_id].append(succ)
                            ready_by_resource[succ_snap.resource_id].sort(key=_get_sort_tuple)

            if not progress_made:
                # Deadlock or unresolvable dependency outside DAG cycle check
                break

        # If any tasks remain un-scheduled due to external deadlocks, place them at anchor with warning
        beyond_count = 0
        final_projections: List[TaskScheduleProjection] = []
        # Record simulated order per task for deterministic order preservation
        scheduled_order_index: Dict[str, int] = {}
        for idx, key in enumerate(scheduled_order):
            scheduled_order_index[key] = idx

        for snap in sorted_snapshots:

            for task in snap.active_tasks:
                k = task.issue_key
                if k in projections_by_key:
                    p = projections_by_key[k]
                else:
                    preds = sorted(hard_preds_map.get(k, []))
                    succs = sorted(hard_succs_map.get(k, []))
                    p = TaskScheduleProjection(
                        issue_key=k,
                        resource_id=snap.resource_id,
                        resource_display_name=snap.display_name,
                        current_status=task.status,
                        estimated_effort_hours=task.expected_effort_hours,
                        duration_evidence_source=task.expected_effort_source,
                        duration_confidence=task.expected_effort_confidence,
                        dependency_predecessors=preds,
                        dependency_successors=succs,
                        earliest_feasible_start_date=anchor_d.strftime("%Y-%m-%d"),
                        projected_start_date=anchor_d.strftime("%Y-%m-%d"),
                        projected_completion_date=horizon_end_d.strftime("%Y-%m-%d"),
                        working_days_needed=0.0,
                        is_blocked_by_dependency=len(preds) > 0,
                        is_beyond_horizon=True,
                        critical_chain_position=longest_chain_indices.get(k),
                    )
                if p.is_beyond_horizon:
                    beyond_count += 1
                final_projections.append(p)

        # Sort task projections deterministically: by resource_id, then scheduled order (or projected_start_date, issue_key)
        final_projections.sort(
            key=lambda p: (
                p.resource_id,
                scheduled_order_index.get(p.issue_key, 999999),
                p.projected_start_date,
                p.issue_key,
            )
        )

        return final_projections, beyond_count


    def _compute_longest_dependency_chain(
        self,
        dag: DependencyGraph,
        active_keys: Set[str],
    ) -> List[str]:
        """Compute the longest directed chain of HARD_BLOCK dependencies across active tasks."""
        # Filter nodes to active tasks present in DAG
        nodes = sorted([n for n in dag.all_nodes if n in active_keys])
        if not nodes:
            return []

        # Find in-degree within the active subgraph
        active_in_degree = {
            n: len([p for p in dag.get_predecessors(n) if p in active_keys])
            for n in nodes
        }
        # Longest path dynamically via topological DP
        # longest_path_to[u] = list of keys
        longest_path_to: Dict[str, List[str]] = {n: [n] for n in nodes}

        # Topological traversal using ready queue
        ready = [n for n in nodes if active_in_degree[n] == 0]
        ready.sort()

        visited_count = 0
        while ready:
            curr = ready.pop(0)
            visited_count += 1
            curr_path = longest_path_to[curr]

            for succ in dag.get_successors(curr):
                if succ in active_keys:
                    if len(curr_path) + 1 > len(longest_path_to[succ]):
                        longest_path_to[succ] = curr_path + [succ]
                    elif len(curr_path) + 1 == len(longest_path_to[succ]):
                        # Deterministic tie-breaker: compare paths lexicographically
                        cand_path = curr_path + [succ]
                        if cand_path < longest_path_to[succ]:
                            longest_path_to[succ] = cand_path

                    active_in_degree[succ] -= 1
                    if active_in_degree[succ] == 0:
                        ready.append(succ)
                        ready.sort()

        # Pick the longest path overall, tie-breaking lexicographically
        best_path: List[str] = []
        for n in nodes:
            path = longest_path_to[n]
            if len(path) > len(best_path):
                best_path = path
            elif len(path) == len(best_path) and len(path) > 1:
                if path < best_path:
                    best_path = path

        return best_path if len(best_path) > 1 else []

    def _detect_bottlenecks(
        self,
        sorted_snapshots: List[ResourceQueueSnapshot],
        task_projections: List[TaskScheduleProjection],
        longest_chain: List[str],
        effective_horizon: int,
    ) -> List[Bottleneck]:
        """Detect deterministic, rule-based operational bottlenecks."""
        bottlenecks: List[Bottleneck] = []

        # 1. Overloaded Resource Bottleneck
        for snap in sorted_snapshots:
            committed_h = snap.capacity.committed_workload_hours
            available_h = snap.capacity.available_capacity_hours
            cap_state = snap.capacity.capacity_state

            if cap_state in (CapacityState.OVERLOADED, CapacityState.SATURATED) or (available_h > 0 and committed_h > available_h):
                ratio = round(committed_h / max(available_h, 1.0), 2)
                severity = "HIGH" if (ratio >= 1.4 or cap_state == CapacityState.SATURATED) else "MEDIUM"
                bottlenecks.append(
                    Bottleneck(
                        type=BottleneckType.OVERLOADED_RESOURCE,
                        affected_issue_key=None,
                        affected_resource_id=snap.resource_id,
                        severity=severity,
                        evidence=(
                            f"Resource committed workload ({committed_h}h) exceeds available capacity "
                            f"({available_h}h) over {effective_horizon} working days (ratio: {ratio}x)."
                        ),
                        data_quality=snap.capacity_quality,
                    )
                )

            # 2. Insufficient Capacity Bottleneck
            if snap.capacity.forecast_daily_capacity_hours <= 0.0 or snap.capacity_quality == "CAPACITY_UNAVAILABLE":
                bottlenecks.append(
                    Bottleneck(
                        type=BottleneckType.INSUFFICIENT_CAPACITY,
                        affected_issue_key=None,
                        affected_resource_id=snap.resource_id,
                        severity="HIGH" if snap.active_task_count > 0 else "LOW",
                        evidence=(
                            f"Resource has {snap.capacity_quality} status with "
                            f"{snap.capacity.forecast_daily_capacity_hours}h daily forecast capacity."
                        ),
                        data_quality="LOW",
                    )
                )

        # 3. Long Dependency Chain Bottleneck
        if len(longest_chain) >= 3:
            bottlenecks.append(
                Bottleneck(
                    type=BottleneckType.DEPENDENCY_CHAIN,
                    affected_issue_key=longest_chain[-1],
                    affected_resource_id=None,
                    severity="HIGH" if len(longest_chain) >= 5 else "MEDIUM",
                    evidence=(
                        f"Long sequential HARD_BLOCK chain of {len(longest_chain)} tasks: "
                        f"{' -> '.join(longest_chain)}. Serializes execution across multiple tasks."
                    ),
                    data_quality="HIGH",
                )
            )

        # 4. Task-Level Bottlenecks (Blocked tasks, Long-duration tasks, Missing duration evidence)
        for p in task_projections:
            # Blocked task
            if p.is_blocked_by_dependency and len(p.dependency_predecessors) > 0:
                bottlenecks.append(
                    Bottleneck(
                        type=BottleneckType.BLOCKED_TASK,
                        affected_issue_key=p.issue_key,
                        affected_resource_id=p.resource_id,
                        severity="MEDIUM",
                        evidence=(
                            f"Task {p.issue_key} is blocked by hard predecessors: "
                            f"{', '.join(p.dependency_predecessors)}. Feasible start delayed."
                        ),
                        data_quality="HIGH",
                    )
                )

            # Long duration task (> 20 hours, consumes >= 3 standard working days)
            if p.estimated_effort_hours >= 20.0:
                bottlenecks.append(
                    Bottleneck(
                        type=BottleneckType.LONG_DURATION_TASK,
                        affected_issue_key=p.issue_key,
                        affected_resource_id=p.resource_id,
                        severity="MEDIUM",
                        evidence=(
                            f"Task {p.issue_key} has large estimated effort of {p.estimated_effort_hours}h "
                            f"({p.working_days_needed} working days), creating single-task scheduling delay."
                        ),
                        data_quality=p.duration_confidence,
                    )
                )

            # Missing duration evidence (unavailable or unestimated)
            if p.duration_evidence_source in ("unavailable", "insufficient_evidence", "deterministic_fallback") or p.duration_confidence in ("unavailable", "UNKNOWN"):
                bottlenecks.append(
                    Bottleneck(
                        type=BottleneckType.MISSING_DURATION_EVIDENCE,
                        affected_issue_key=p.issue_key,
                        affected_resource_id=p.resource_id,
                        severity="LOW",
                        evidence=(
                            f"Task {p.issue_key} lacks explicit estimate or historical evidence; "
                            f"relying on fallback ({p.duration_evidence_source})."
                        ),
                        data_quality="LOW",
                    )
                )

        return bottlenecks

    def _compile_data_quality_summary(
        self,
        sorted_snapshots: List[ResourceQueueSnapshot],
        task_projections: List[TaskScheduleProjection],
        schedule_valid: bool,
    ) -> Dict[str, Any]:
        """Compile a bounded, structured data quality summary for the team projection."""
        history_counts: Dict[str, int] = defaultdict(int)
        capacity_counts: Dict[str, int] = defaultdict(int)
        queue_counts: Dict[str, int] = defaultdict(int)

        for s in sorted_snapshots:
            history_counts[s.history_completeness] += 1
            capacity_counts[s.capacity_quality] += 1
            queue_counts[s.queue_completeness] += 1

        duration_confidence_counts: Dict[str, int] = defaultdict(int)
        for p in task_projections:
            duration_confidence_counts[p.duration_confidence] += 1

        # Overall quality level
        if not schedule_valid:
            overall_quality = "INVALID_CYCLE"
        elif history_counts.get("SUFFICIENT_HISTORY", 0) >= len(sorted_snapshots) and len(sorted_snapshots) > 0:
            overall_quality = "HIGH"
        elif history_counts.get("NO_HISTORY", 0) > 0 or capacity_counts.get("CAPACITY_UNAVAILABLE", 0) > 0:
            overall_quality = "LOW"
        else:
            overall_quality = "MEDIUM"

        return {
            "overall_data_quality": overall_quality,
            "schedule_valid": schedule_valid,
            "resources_count": len(sorted_snapshots),
            "total_tasks_count": len(task_projections),
            "history_completeness_distribution": dict(history_counts),
            "capacity_quality_distribution": dict(capacity_counts),
            "queue_completeness_distribution": dict(queue_counts),
            "duration_confidence_distribution": dict(duration_confidence_counts),
        }
