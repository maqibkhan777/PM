"""Deterministic Provider-Agnostic Planning Context Builder for Phase 3E.

Composes existing deterministic outputs:
1. ResourceQueueSnapshots (Phase 3C)
2. TeamScheduleProjection (Phase 3D)
3. DependencyGraph (Phase 3A)
4. ArtifactEngine / ArtifactRecords / ArtifactRelationshipRecords (Phase 3B)

CRITICAL INVARIANTS:
1. CONTEXT COMPOSITION ONLY: Does NOT calculate pace, does NOT calculate dependencies,
   does NOT calculate projected schedules, does NOT calculate capacity.
2. ZERO PLANNING DECISIONS: Does not reassign resources, does not reschedule tasks,
   does not set due dates, does not rank employees.
3. ZERO AI / DEEPSEEK CALLS: Pure local deterministic composition.
4. ZERO JIRA MUTATIONS: Read-only local projection composition.
5. BOUNDED & SANITIZED: Strictly enforces caps on resources, tasks, dependencies,
   artifacts, and bottlenecks. Scrubs any secret-like strings.
6. DETERMINISTIC & ORDER-INDEPENDENT: Sorted outputs ensure identical representations
   regardless of input structure ordering.
"""

from collections import defaultdict
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Set, Tuple

from app.core.models.planning import (
    ArtifactRecord,
    ArtifactRelationshipRecord,
    ArtifactStatus,
    ArtifactType,
    Bottleneck,
    CapacityState,
    ContextTruncationMetadata,
    DependencyClassification,
    PlanningArtifactContext,
    PlanningContext,
    PlanningDependencyContext,
    PlanningResourceContext,
    PlanningScheduleSummary,
    PlanningTaskContext,
    PlanningTeamSummary,
    QueueTaskDetail,
    ResourcePaceSummary,
    ResourceQueueSnapshot,
    TeamScheduleProjection,
)
from app.core.planning.dag import DependencyGraph
from app.utils.logger import redact_text, sanitize_dict
from app.utils.time import format_iso, utc_now, utc_now_iso


# Default bounds to ensure bounded context safety
DEFAULT_MAX_RESOURCES = 50
DEFAULT_MAX_TASKS_PER_RESOURCE = 50
DEFAULT_MAX_TOTAL_TASKS = 200
DEFAULT_MAX_DEPENDENCIES = 200
DEFAULT_MAX_ARTIFACTS = 100
DEFAULT_MAX_BOTTLENECKS = 50


class PlanningContextBuilder:
    """Pure deterministic composition builder for PlanningContext."""

    def __init__(
        self,
        max_resources: int = DEFAULT_MAX_RESOURCES,
        max_tasks_per_resource: int = DEFAULT_MAX_TASKS_PER_RESOURCE,
        max_total_tasks: int = DEFAULT_MAX_TOTAL_TASKS,
        max_dependencies: int = DEFAULT_MAX_DEPENDENCIES,
        max_artifacts: int = DEFAULT_MAX_ARTIFACTS,
        max_bottlenecks: int = DEFAULT_MAX_BOTTLENECKS,
    ):
        self.max_resources = max_resources
        self.max_tasks_per_resource = max_tasks_per_resource
        self.max_total_tasks = max_total_tasks
        self.max_dependencies = max_dependencies
        self.max_artifacts = max_artifacts
        self.max_bottlenecks = max_bottlenecks

    def build_context(
        self,
        team_snapshots: List[ResourceQueueSnapshot],
        schedule_projection: Optional[TeamScheduleProjection] = None,
        dependency_graph: Optional[DependencyGraph] = None,
        artifact_records: Optional[List[ArtifactRecord]] = None,
        artifact_relationships: Optional[List[ArtifactRelationshipRecord]] = None,
        team_group: Optional[str] = None,
        anchor_date: Optional[str] = None,
        horizon_working_days: int = 10,
        now: Optional[datetime] = None,
    ) -> PlanningContext:
        """Compose a bounded, typed PlanningContext from deterministic upstream sources.

        Args:
            team_snapshots: List of ResourceQueueSnapshot objects (Phase 3C).
            schedule_projection: Optional TeamScheduleProjection (Phase 3D).
            dependency_graph: Optional DependencyGraph (Phase 3A).
            artifact_records: Optional List of ArtifactRecord objects (Phase 3B).
            artifact_relationships: Optional List of ArtifactRelationshipRecord objects (Phase 3B).
            team_group: Scoping team/group identifier.
            anchor_date: Optional anchor date (YYYY-MM-DD). If omitted, inferred from schedule_projection or now.
            horizon_working_days: Planning horizon length in business days (default: 10).
            now: Optional timestamp override for deterministic testing.

        Returns:
            PlanningContext: Bounded, provider-neutral domain context.
        """
        now_dt = now or utc_now()
        generated_at = format_iso(now_dt)

        # 1. Resolve Anchor Date and Horizon End Date
        resolved_anchor_date = ""
        resolved_horizon_end = ""
        effective_horizon = horizon_working_days

        if schedule_projection:
            resolved_anchor_date = schedule_projection.anchor_date
            resolved_horizon_end = schedule_projection.horizon_end_date
            effective_horizon = schedule_projection.planning_horizon_working_days
        elif anchor_date:
            resolved_anchor_date = str(anchor_date)[:10]
            resolved_horizon_end = resolved_anchor_date
        else:
            resolved_anchor_date = generated_at[:10]
            resolved_horizon_end = resolved_anchor_date

        # Truncation tracking
        truncation_reasons: List[str] = []
        is_truncated = False

        # 2. Deterministic Resource Ordering & Bounding
        # Sort snapshots by canonical resource_id
        sorted_snapshots = sorted(
            team_snapshots,
            key=lambda s: str(s.resource_id).strip().lower(),
        )
        orig_resource_count = len(sorted_snapshots)
        if len(sorted_snapshots) > self.max_resources:
            truncation_reasons.append(
                f"Resource count truncated from {orig_resource_count} to {self.max_resources}"
            )
            is_truncated = True
            included_snapshots = sorted_snapshots[: self.max_resources]
        else:
            included_snapshots = sorted_snapshots

        included_resource_count = len(included_snapshots)

        # Build Resource Contexts
        planning_resources: List[PlanningResourceContext] = []
        for snap in included_snapshots:
            res_ctx = PlanningResourceContext(
                resource_id=snap.resource_id,
                display_name=redact_text(snap.display_name),
                role=redact_text(snap.designation) if snap.designation else None,
                role_category=snap.role_category,
                team_group=snap.team_group,
                active_task_count=snap.active_task_count,
                current_workload_hours=round(snap.total_inferred_remaining_hours, 2),
                remaining_effort_hours=round(snap.total_inferred_remaining_hours, 2),
                available_capacity_hours=round(snap.capacity.available_capacity_hours, 2),
                capacity_state=snap.capacity.capacity_state,
                workload_pressure=snap.workload_pressure_level,
                historical_pace=snap.historical_pace,
                personal_baseline=snap.personal_baseline_summary,
                history_completeness=snap.history_completeness,
                capacity_quality=snap.capacity_quality,
                queue_completeness=snap.queue_completeness,
                data_quality_notes=snap.data_quality_notes,
            )
            planning_resources.append(res_ctx)

        # 3. Deterministic Task Aggregation & Bounding
        # Extract tasks from snapshots, index schedule projections if available
        sched_map: Dict[str, Any] = {}
        if schedule_projection:
            for tp in schedule_projection.task_projections:
                sched_map[tp.issue_key.strip().upper()] = tp

        all_tasks_raw: List[Tuple[QueueTaskDetail, str, str]] = []
        for snap in included_snapshots:
            # Sort resource's tasks deterministically: priority, due date, issue_key
            res_tasks = sorted(
                snap.active_tasks,
                key=lambda t: (
                    t.priority or "Medium",
                    t.due_date or "9999-99-99",
                    t.issue_key,
                ),
            )
            if len(res_tasks) > self.max_tasks_per_resource:
                truncation_reasons.append(
                    f"Tasks for resource {snap.resource_id} truncated from {len(res_tasks)} to {self.max_tasks_per_resource}"
                )
                is_truncated = True
                res_tasks = res_tasks[: self.max_tasks_per_resource]

            for t in res_tasks:
                all_tasks_raw.append((t, snap.resource_id, snap.display_name))

        # Sort all tasks deterministically by issue_key
        all_tasks_raw.sort(key=lambda item: item[0].issue_key.strip().upper())
        orig_task_count = len(all_tasks_raw)

        if len(all_tasks_raw) > self.max_total_tasks:
            truncation_reasons.append(
                f"Total task count truncated from {orig_task_count} to {self.max_total_tasks}"
            )
            is_truncated = True
            included_tasks_raw = all_tasks_raw[: self.max_total_tasks]
        else:
            included_tasks_raw = all_tasks_raw

        included_task_count = len(included_tasks_raw)

        planning_tasks: List[PlanningTaskContext] = []
        for task_detail, res_id, res_name in included_tasks_raw:
            k = task_detail.issue_key.strip().upper()
            tp = sched_map.get(k)

            # Predecessors / Successors from DAG or schedule projection or task detail
            predecessors: List[str] = []
            successors: List[str] = []
            if tp:
                predecessors = sorted(list(tp.dependency_predecessors))
                successors = sorted(list(tp.dependency_successors))
            elif dependency_graph:
                predecessors = dependency_graph.get_predecessors(k)
                successors = dependency_graph.get_successors(k)
            else:
                predecessors = sorted(list(task_detail.hard_blocker_keys))

            p_start = tp.projected_start_date if tp else None
            p_comp = tp.projected_completion_date if tp else None
            is_beyond = tp.is_beyond_horizon if tp else False

            # Extract effort & confidence from task detail
            rem_effort = (
                task_detail.expected_effort_hours
                if task_detail.expected_effort_hours > 0
                else task_detail.remaining_hours
            )

            pt = PlanningTaskContext(
                issue_key=k,
                assigned_resource_id=res_id,
                assigned_resource_name=redact_text(res_name),
                summary=redact_text(task_detail.summary),
                status=task_detail.status,
                priority=task_detail.priority,
                issue_type=task_detail.issue_type,
                task_nature=task_detail.task_nature,
                project_key=task_detail.project_key,
                estimated_remaining_hours=round(rem_effort, 2),
                duration_evidence_source=task_detail.expected_effort_source,
                duration_confidence=task_detail.expected_effort_confidence,
                due_date=task_detail.due_date,
                is_overdue=task_detail.is_overdue,
                is_stale=task_detail.is_stale,
                is_blocked=task_detail.is_blocked,
                is_reopened=task_detail.is_reopened,
                projected_start_date=p_start,
                projected_completion_date=p_comp,
                is_beyond_horizon=is_beyond,
                predecessor_keys=sorted(predecessors),
                successor_keys=sorted(successors),
                produced_artifact_names=sorted(list(task_detail.produced_artifact_names)),
                consumed_artifact_names=sorted(list(task_detail.consumed_artifact_names)),
            )
            planning_tasks.append(pt)

        # 4. Deterministic Dependency Context
        raw_deps: List[PlanningDependencyContext] = []
        seen_dep_keys: Set[Tuple[str, str, str]] = set()

        # From ScheduleProjection constraints
        if schedule_projection:
            for sc in schedule_projection.dependency_constraints:
                dep_key = (sc.source_issue_key, sc.target_issue_key, sc.constraint_type)
                if dep_key not in seen_dep_keys:
                    seen_dep_keys.add(dep_key)
                    is_hard = sc.is_hard_block
                    # Classify if string
                    classification = DependencyClassification.HARD_BLOCK if is_hard else DependencyClassification.INFORMATIONAL
                    if sc.constraint_type == "CAUSAL_DEPENDENCY":
                        classification = DependencyClassification.CAUSAL_DEPENDENCY
                    elif sc.constraint_type == "VERIFICATION_DEPENDENCY":
                        classification = DependencyClassification.VERIFICATION_DEPENDENCY
                    elif sc.constraint_type == "ARTIFACT_HANDOFF":
                        classification = DependencyClassification.INFORMATIONAL

                    raw_deps.append(
                        PlanningDependencyContext(
                            source_issue_key=sc.source_issue_key,
                            target_issue_key=sc.target_issue_key,
                            link_type=sc.constraint_type,
                            classification=classification,
                            is_hard_block=is_hard,
                            is_advisory=not is_hard,
                            provenance="SCHEDULE_PROJECTION",
                            confidence="HIGH" if is_hard else "MEDIUM",
                        )
                    )

        # From DependencyGraph if not already present
        if dependency_graph:
            for u in dependency_graph.all_nodes:
                succ_edges = dependency_graph._successors.get(u, [])
                for v, edge in succ_edges:
                    dep_key = (edge.source_key, edge.target_key, edge.link_type)
                    if dep_key not in seen_dep_keys:
                        seen_dep_keys.add(dep_key)
                        raw_deps.append(
                            PlanningDependencyContext(
                                source_issue_key=edge.source_key,
                                target_issue_key=edge.target_key,
                                link_type=edge.link_type,
                                classification=edge.classification,
                                is_hard_block=edge.is_hard_block,
                                is_advisory=not edge.is_hard_block,
                                provenance="DEPENDENCY_DAG",
                                confidence="HIGH" if edge.is_hard_block else "MEDIUM",
                            )
                        )

        # Sort dependencies deterministically: source_key, target_key, link_type
        raw_deps.sort(
            key=lambda d: (d.source_issue_key, d.target_issue_key, d.link_type)
        )
        orig_dep_count = len(raw_deps)

        if len(raw_deps) > self.max_dependencies:
            truncation_reasons.append(
                f"Dependency relationships truncated from {orig_dep_count} to {self.max_dependencies}"
            )
            is_truncated = True
            included_deps = raw_deps[: self.max_dependencies]
        else:
            included_deps = raw_deps

        included_dep_count = len(included_deps)

        # 5. Deterministic Artifact Context
        raw_artifacts: List[PlanningArtifactContext] = []
        seen_arts: Set[str] = set()

        # Build lookup from task issue_key -> assigned resource
        task_res_map: Dict[str, str] = {
            t.issue_key: t.assigned_resource_id
            for t in planning_tasks
            if t.assigned_resource_id
        }

        # If explicit artifact records and relationships provided
        if artifact_records:
            # Map relationships
            prod_map: Dict[str, List[str]] = defaultdict(list)
            cons_map: Dict[str, List[str]] = defaultdict(list)
            rel_inferred_map: Dict[str, bool] = defaultdict(bool)
            rel_prov_map: Dict[str, str] = {}
            rel_conf_map: Dict[str, str] = {}

            if artifact_relationships:
                for rel in artifact_relationships:
                    if rel.relationship_type.upper() == "PRODUCES":
                        prod_map[rel.artifact_id].append(rel.issue_key)
                    elif rel.relationship_type.upper() == "CONSUMES":
                        cons_map[rel.artifact_id].append(rel.issue_key)
                    if rel.is_inferred:
                        rel_inferred_map[rel.artifact_id] = True
                    rel_prov_map[rel.artifact_id] = rel.provenance.value if hasattr(rel.provenance, "value") else str(rel.provenance)
                    rel_conf_map[rel.artifact_id] = rel.confidence

            for art in artifact_records:
                art_id = art.id
                seen_arts.add(art.name)
                prod_key = art.producer_issue_key or (prod_map[art_id][0] if prod_map[art_id] else None)
                prod_res = task_res_map.get(prod_key) if prod_key else None
                cons_keys = sorted(cons_map[art_id])
                cons_res = sorted(list({task_res_map[ck] for ck in cons_keys if ck in task_res_map}))

                is_inf = rel_inferred_map.get(art_id, False) or (art.provenance.value == "TASK_NATURE_INFERENCE" if hasattr(art.provenance, "value") else str(art.provenance) == "TASK_NATURE_INFERENCE")

                raw_artifacts.append(
                    PlanningArtifactContext(
                        artifact_name=art.name,
                        project_key=art.project_key,
                        artifact_type=art.artifact_type,
                        status=art.status,
                        producer_issue_key=prod_key,
                        producer_resource_id=prod_res,
                        consumer_issue_keys=cons_keys,
                        consumer_resource_ids=cons_res,
                        relationship_type="PRODUCES" if prod_key else "CONSUMES",
                        is_inferred=is_inf,
                        is_advisory=is_inf,
                        provenance=art.provenance.value if hasattr(art.provenance, "value") else str(art.provenance),
                        confidence=art.confidence,
                    )
                )

        # Also incorporate artifact mentions found in QueueTaskDetails if not already present
        for t in planning_tasks:
            for p_art in t.produced_artifact_names:
                if p_art not in seen_arts:
                    seen_arts.add(p_art)
                    raw_artifacts.append(
                        PlanningArtifactContext(
                            artifact_name=p_art,
                            project_key=t.project_key,
                            artifact_type=ArtifactType.GENERIC,
                            status=ArtifactStatus.PLANNED,
                            producer_issue_key=t.issue_key,
                            producer_resource_id=t.assigned_resource_id,
                            consumer_issue_keys=[],
                            consumer_resource_ids=[],
                            relationship_type="PRODUCES",
                            is_inferred=False,
                            is_advisory=False,
                            provenance="EXPLICIT_JIRA_LABEL",
                            confidence="HIGH",
                        )
                    )
            for c_art in t.consumed_artifact_names:
                if c_art not in seen_arts:
                    seen_arts.add(c_art)
                    raw_artifacts.append(
                        PlanningArtifactContext(
                            artifact_name=c_art,
                            project_key=t.project_key,
                            artifact_type=ArtifactType.GENERIC,
                            status=ArtifactStatus.PLANNED,
                            producer_issue_key=None,
                            producer_resource_id=None,
                            consumer_issue_keys=[t.issue_key],
                            consumer_resource_ids=[t.assigned_resource_id] if t.assigned_resource_id else [],
                            relationship_type="CONSUMES",
                            is_inferred=False,
                            is_advisory=False,
                            provenance="EXPLICIT_JIRA_LABEL",
                            confidence="HIGH",
                        )
                    )

        # Sort artifacts deterministically by project_key, artifact_name
        raw_artifacts.sort(key=lambda a: (a.project_key, a.artifact_name))
        orig_art_count = len(raw_artifacts)

        if len(raw_artifacts) > self.max_artifacts:
            truncation_reasons.append(
                f"Artifact entries truncated from {orig_art_count} to {self.max_artifacts}"
            )
            is_truncated = True
            included_arts = raw_artifacts[: self.max_artifacts]
        else:
            included_arts = raw_artifacts

        included_art_count = len(included_arts)

        # 6. Deterministic Schedule & Bottleneck Summary
        longest_chain: List[str] = []
        raw_bottlenecks: List[Bottleneck] = []
        schedule_valid = True
        error_msg = None
        data_quality_summary: Dict[str, Any] = {}

        if schedule_projection:
            schedule_valid = schedule_projection.schedule_valid
            error_msg = schedule_projection.error_message
            longest_chain = list(schedule_projection.longest_dependency_chain)
            raw_bottlenecks = list(schedule_projection.bottlenecks)
            data_quality_summary = sanitize_dict(schedule_projection.data_quality_summary)
        elif dependency_graph:
            cycle_res = dependency_graph.detect_cycles()
            if cycle_res.has_cycle:
                schedule_valid = False
                error_msg = f"Dependency cycle detected in HARD_BLOCK links: {' -> '.join(cycle_res.cycle_nodes)}"

        # Sort bottlenecks deterministically
        severity_rank = {"HIGH": 1, "MEDIUM": 2, "LOW": 3}
        raw_bottlenecks.sort(
            key=lambda b: (
                severity_rank.get(b.severity, 9),
                b.type.value if hasattr(b.type, "value") else str(b.type),
                b.affected_issue_key or "",
                b.affected_resource_id or "",
            )
        )
        orig_bottleneck_count = len(raw_bottlenecks)

        if len(raw_bottlenecks) > self.max_bottlenecks:
            truncation_reasons.append(
                f"Bottlenecks truncated from {orig_bottleneck_count} to {self.max_bottlenecks}"
            )
            is_truncated = True
            included_bottlenecks = raw_bottlenecks[: self.max_bottlenecks]
        else:
            included_bottlenecks = raw_bottlenecks

        included_bottleneck_count = len(included_bottlenecks)

        # Redact evidence in bottlenecks
        clean_bottlenecks: List[Bottleneck] = []
        for b in included_bottlenecks:
            clean_bottlenecks.append(
                Bottleneck(
                    type=b.type,
                    affected_issue_key=b.affected_issue_key,
                    affected_resource_id=b.affected_resource_id,
                    severity=b.severity,
                    evidence=redact_text(b.evidence),
                    data_quality=b.data_quality,
                )
            )

        planning_schedule = PlanningScheduleSummary(
            schedule_valid=schedule_valid,
            anchor_date=resolved_anchor_date,
            planning_horizon_working_days=effective_horizon,
            horizon_end_date=resolved_horizon_end,
            tasks_projected_count=len(planning_tasks),
            tasks_beyond_horizon_count=sum(1 for t in planning_tasks if t.is_beyond_horizon),
            longest_dependency_chain=longest_chain,
            bottlenecks=clean_bottlenecks,
            data_quality_summary=data_quality_summary,
            error_message=error_msg,
        )

        # 7. Team Summary Aggregations
        overloaded_res_count = sum(
            1
            for r in planning_resources
            if r.capacity_state in (CapacityState.OVERLOADED, CapacityState.SATURATED)
        )
        blocked_tasks_count = sum(1 for t in planning_tasks if t.is_blocked)
        overdue_tasks_count = sum(1 for t in planning_tasks if t.is_overdue)
        beyond_horizon_tasks_count = sum(1 for t in planning_tasks if t.is_beyond_horizon)
        total_remaining_hours = sum(t.estimated_remaining_hours for t in planning_tasks)
        total_available_hours = sum(r.available_capacity_hours for r in planning_resources)

        team_summary = PlanningTeamSummary(
            resource_count=len(planning_resources),
            active_task_count=len(planning_tasks),
            overloaded_resource_count=overloaded_res_count,
            blocked_task_count=blocked_tasks_count,
            overdue_task_count=overdue_tasks_count,
            tasks_beyond_horizon_count=beyond_horizon_tasks_count,
            total_remaining_effort_hours=round(total_remaining_hours, 2),
            total_available_capacity_hours=round(total_available_hours, 2),
            schedule_valid=schedule_valid,
        )

        # 8. Truncation Metadata
        truncation_meta = ContextTruncationMetadata(
            is_truncated=is_truncated,
            original_resource_count=orig_resource_count,
            included_resource_count=included_resource_count,
            original_task_count=orig_task_count,
            included_task_count=included_task_count,
            original_dependency_count=orig_dep_count,
            included_dependency_count=included_dep_count,
            original_artifact_count=orig_art_count,
            included_artifact_count=included_art_count,
            original_bottleneck_count=orig_bottleneck_count,
            included_bottleneck_count=included_bottleneck_count,
            truncation_reasons=truncation_reasons,
        )

        return PlanningContext(
            context_version="planning-v1",
            generated_at=generated_at,
            anchor_date=resolved_anchor_date,
            planning_horizon_working_days=effective_horizon,
            horizon_end_date=resolved_horizon_end,
            team_group=team_group,
            team_summary=team_summary,
            resources=planning_resources,
            tasks=planning_tasks,
            dependencies=included_deps,
            artifacts=included_arts,
            schedule=planning_schedule,
            truncation=truncation_meta,
        )
