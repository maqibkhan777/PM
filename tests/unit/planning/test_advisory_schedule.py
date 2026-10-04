"""Unit tests for Milestone 4B: Advisory Schedule Generation."""

import pytest
from app.core.planning.schedule_models import (
    AdvisoryScheduleProposal,
    ResourceScheduleAudit,
    ScheduleFeasibilityStatus,
    ScheduledTaskProposal,
)
from app.core.planning.schedule_service import AdvisoryScheduleService
from app.core.planning.schedule_formatter import AdvisoryScheduleFormatter
from app.core.planning.validation_models import (
    CapacityDataSource,
    DependencyValidationFinding,
    EstimateType,
    PlanningDataValidationReport,
    ResourceCapacityAudit,
    ValidatedTaskWorkload,
    ValidationOutcome,
)


def _build_mock_validation_report(
    tasks=None,
    resources=None,
    findings=None,
    configured_projects=None,
    resolved_projects=None,
    unresolved_projects=None,
    overall_status=ValidationOutcome.VALID,
):
    configured = configured_projects or ["SMTPSUPORT", "GF"]
    resolved = resolved_projects or ["SMTPSUPORT", "GF"]
    unresolved = unresolved_projects or []

    return PlanningDataValidationReport(
        report_id="val-test-123",
        generated_at="2026-10-01T12:00:00Z",
        anchor_date="2026-10-01",
        planning_horizon_working_days=10,
        configured_projects=configured,
        resolved_projects=resolved,
        unresolved_projects=unresolved,
        total_active_tasks=len(tasks or []),
        tasks_with_explicit_remaining=len([t for t in (tasks or []) if t.estimate_type == EstimateType.EXPLICIT_REMAINING]),
        tasks_with_benchmark_estimates=len([t for t in (tasks or []) if t.estimate_type == EstimateType.HISTORICAL_BENCHMARK]),
        tasks_with_missing_estimates=len([t for t in (tasks or []) if t.estimate_type == EstimateType.MISSING_UNESTIMATED]),
        tasks_missing_assignee=len([t for t in (tasks or []) if not t.assignee_name]),
        resources_audited=resources or [],
        overallocated_resources=[r.display_name for r in (resources or []) if r.is_overallocated],
        cross_project_contention_resources=[r.display_name for r in (resources or []) if r.cross_project_conflict_detected],
        blocked_tasks_count=len([t for t in (tasks or []) if t.is_blocked]),
        dependency_findings=findings or [],
        validated_tasks=tasks or [],
        deduplicated_issue_keys=[t.issue_key for t in (tasks or [])],
        exclusions_and_reasons={},
        overall_status=overall_status,
        executive_summary="Mock validation report for testing",
        capacity_limitations_summary=[
            "Authoritative employee leave, holiday calendars, and part-time schedules are UNKNOWN; nominal 6.5h/day baseline applied."
        ],
    )


def test_advisory_schedule_generation_basic():
    """Test generating a feasible schedule with explicit and benchmark proxy tasks."""
    t1 = ValidatedTaskWorkload(
        issue_key="SMTPSUPORT-101",
        project_key="SMTPSUPORT",
        summary="Fix auth token expiration",
        status="In Progress",
        status_category="In Progress",
        assignee_name="Alice Developer",
        priority="High",
        remaining_estimate_hours=13.0,
        estimate_type=EstimateType.EXPLICIT_REMAINING,
        is_proxy_estimate=False,
    )
    t2 = ValidatedTaskWorkload(
        issue_key="GF-202",
        project_key="GF",
        summary="Improve form validation UX",
        status="To Do",
        status_category="To Do",
        assignee_name="Alice Developer",
        priority="Medium",
        remaining_estimate_hours=6.5,
        estimate_type=EstimateType.HISTORICAL_BENCHMARK,
        is_proxy_estimate=True,
    )

    r1 = ResourceCapacityAudit(
        account_id="acc-alice",
        display_name="Alice Developer",
        role="Senior Fullstack Engineer",
        projects_involved=["GF", "SMTPSUPORT"],
        assigned_tasks_count=2,
        total_assigned_remaining_hours=19.5,
        available_capacity_hours=65.0,
        workload_pressure_ratio=0.3,
        is_overallocated=False,
        cross_project_conflict_detected=True,
    )

    report = _build_mock_validation_report(tasks=[t1, t2], resources=[r1])
    svc = AdvisoryScheduleService()
    proposal = svc.generate_advisory_schedule(report, anchor_date="2026-10-01")

    assert proposal.proposal_id.startswith("sch-")
    assert proposal.validation_status == ValidationOutcome.VALID
    assert proposal.total_tasks_considered == 2
    assert proposal.tasks_explicit_estimates == 1
    assert proposal.tasks_benchmark_proxies == 1
    assert proposal.tasks_beyond_horizon_count == 0
    assert len(proposal.resource_schedules) == 1

    alice_sched = proposal.resource_schedules[0]
    assert alice_sched.display_name == "Alice Developer"
    assert alice_sched.is_cross_project is True
    assert len(alice_sched.scheduled_tasks) == 2

    # Task 1: In Progress, High priority -> first in queue
    task1_prop = alice_sched.scheduled_tasks[0]
    assert task1_prop.issue_key == "SMTPSUPORT-101"
    assert task1_prop.queue_sequence == 1
    assert task1_prop.working_days_needed == 2.0  # 13.0 / 6.5
    assert task1_prop.tentative_start_date == "2026-10-01"
    assert task1_prop.dates_available is True

    # Task 2: To Do -> second in queue
    task2_prop = alice_sched.scheduled_tasks[1]
    assert task2_prop.issue_key == "GF-202"
    assert task2_prop.queue_sequence == 2
    assert task2_prop.is_proxy_estimate is True


def test_blocked_tasks_and_unresolved_predecessors():
    """Test that blocked tasks retain blocker warnings and are distinguished."""
    t_blocker = ValidatedTaskWorkload(
        issue_key="GF-393",
        project_key="GF",
        summary="Core form engine upgrade",
        status="To Do",
        status_category="To Do",
        assignee_name="Syed ali",
        priority="Highest",
        remaining_estimate_hours=13.0,
        estimate_type=EstimateType.EXPLICIT_REMAINING,
    )
    t_blocked = ValidatedTaskWorkload(
        issue_key="GF-474",
        project_key="GF",
        summary="Regression testing of form engine",
        status="To Do",
        status_category="To Do",
        assignee_name="Muhammad Bilal Khan",
        priority="High",
        remaining_estimate_hours=41.4,
        estimate_type=EstimateType.HISTORICAL_BENCHMARK,
        is_proxy_estimate=True,
        is_blocked=True,
        hard_blocker_keys=["GF-393"],
    )

    dep_finding = DependencyValidationFinding(
        source_issue_key="GF-393",
        target_issue_key="GF-474",
        link_type="Blocks",
        is_hard_block=True,
        is_target_resolved=False,
    )

    report = _build_mock_validation_report(tasks=[t_blocker, t_blocked], findings=[dep_finding])
    svc = AdvisoryScheduleService()
    proposal = svc.generate_advisory_schedule(report, anchor_date="2026-10-01")

    assert proposal.blocked_tasks_count == 1
    assert proposal.feasibility_status == ScheduleFeasibilityStatus.PARTIALLY_CONSTRAINED

    bilal_sched = [r for r in proposal.resource_schedules if r.display_name == "Muhammad Bilal Khan"][0]
    bilal_task = bilal_sched.scheduled_tasks[0]
    assert bilal_task.issue_key == "GF-474"
    assert bilal_task.is_blocked is True
    assert "GF-393" in bilal_task.unresolved_predecessor_keys
    assert any("Blocked by" in w for w in bilal_task.warnings)


def test_missing_estimates_cannot_have_dates():
    """Test that missing estimates do not invent duration and cannot have tentative completion dates."""
    t_unestimated = ValidatedTaskWorkload(
        issue_key="SMTPSUPORT-999",
        project_key="SMTPSUPORT",
        summary="Unclear bug report",
        status="To Do",
        status_category="To Do",
        assignee_name="Nauman Sadiq",
        priority="Medium",
        remaining_estimate_hours=None,
        estimate_type=EstimateType.MISSING_UNESTIMATED,
        is_proxy_estimate=False,
    )

    report = _build_mock_validation_report(tasks=[t_unestimated])
    svc = AdvisoryScheduleService()
    proposal = svc.generate_advisory_schedule(report, anchor_date="2026-10-01")

    assert proposal.tasks_missing_estimates == 1
    assert proposal.feasibility_status == ScheduleFeasibilityStatus.PARTIALLY_CONSTRAINED

    nauman_sched = proposal.resource_schedules[0]
    task_prop = nauman_sched.scheduled_tasks[0]
    assert task_prop.dates_available is False
    assert task_prop.tentative_start_date is None
    assert task_prop.tentative_completion_date is None
    assert any("Missing estimate" in w for w in task_prop.warnings)


def test_unassigned_tasks_handling():
    """Test that unassigned tasks are separated and clearly flagged."""
    t_unassigned = ValidatedTaskWorkload(
        issue_key="GF-500",
        project_key="GF",
        summary="Unassigned triage task",
        status="To Do",
        status_category="To Do",
        assignee_name=None,
        priority="Low",
        remaining_estimate_hours=5.0,
        estimate_type=EstimateType.HISTORICAL_BENCHMARK,
    )

    report = _build_mock_validation_report(tasks=[t_unassigned])
    svc = AdvisoryScheduleService()
    proposal = svc.generate_advisory_schedule(report, anchor_date="2026-10-01")

    assert proposal.unassigned_tasks_count == 1
    assert len(proposal.unassigned_tasks) == 1
    assert proposal.unassigned_tasks[0].issue_key == "GF-500"
    assert proposal.unassigned_tasks[0].dates_available is False


def test_dependency_cycle_disables_dates_and_marks_unreliable():
    """Test that dependency cycles trigger UNRELIABLE feasibility status and disable date calculation."""
    t1 = ValidatedTaskWorkload(
        issue_key="TASK-A",
        project_key="GF",
        summary="Task A",
        status="To Do",
        assignee_name="Alice",
        remaining_estimate_hours=6.5,
        estimate_type=EstimateType.EXPLICIT_REMAINING,
        is_blocked=True,
    )
    t2 = ValidatedTaskWorkload(
        issue_key="TASK-B",
        project_key="GF",
        summary="Task B",
        status="To Do",
        assignee_name="Alice",
        remaining_estimate_hours=6.5,
        estimate_type=EstimateType.EXPLICIT_REMAINING,
        is_blocked=True,
    )

    f1 = DependencyValidationFinding(
        source_issue_key="TASK-A",
        target_issue_key="TASK-B",
        link_type="Blocks",
        is_hard_block=True,
        is_target_resolved=False,
    )
    f2 = DependencyValidationFinding(
        source_issue_key="TASK-B",
        target_issue_key="TASK-A",
        link_type="Blocks",
        is_hard_block=True,
        is_target_resolved=False,
    )

    report = _build_mock_validation_report(tasks=[t1, t2], findings=[f1, f2])
    svc = AdvisoryScheduleService()
    proposal = svc.generate_advisory_schedule(report, anchor_date="2026-10-01")

    assert proposal.feasibility_status == ScheduleFeasibilityStatus.UNRELIABLE
    assert any("CRITICAL: Dependency cycle detected" in r for r in proposal.risks_and_assumptions)
    for res_sched in proposal.resource_schedules:
        for t in res_sched.scheduled_tasks:
            assert t.dates_available is False


def test_schedule_formatter_markdown_and_discord():
    """Test formatting proposal into Markdown report and Discord summary."""
    t1 = ValidatedTaskWorkload(
        issue_key="GF-101",
        project_key="GF",
        summary="Setup environment",
        status="In Progress",
        assignee_name="Bob",
        remaining_estimate_hours=6.5,
        estimate_type=EstimateType.EXPLICIT_REMAINING,
    )
    r1 = ResourceCapacityAudit(
        account_id="acc-bob",
        display_name="Bob",
        role="Developer",
        projects_involved=["GF"],
        assigned_tasks_count=1,
        total_assigned_remaining_hours=6.5,
        available_capacity_hours=65.0,
    )

    report = _build_mock_validation_report(tasks=[t1], resources=[r1])
    svc = AdvisoryScheduleService()
    proposal = svc.generate_advisory_schedule(report, anchor_date="2026-10-01")

    md_report = AdvisoryScheduleFormatter.format_markdown_report(proposal)
    assert "# Advisory Schedule Proposal Report" in md_report
    assert "GF-101" in md_report
    assert "Bob" in md_report
    assert "Human Review Required" in md_report

    discord_summary = AdvisoryScheduleFormatter.format_discord_summary(proposal)
    assert "📅 **Advisory Schedule Proposal**" in discord_summary
    assert "GF-101" in discord_summary
    assert "Human review required" in discord_summary


def test_benchmark_lookup_preserves_issue_type():
    """Test that benchmark quantiles query preserves task issue_type rather than defaulting to Task."""
    t_epic = ValidatedTaskWorkload(
        issue_key="GF-229",
        project_key="GF",
        summary="Gutena Forms Epic",
        status="To Do",
        assignee_name="Aqib Khan",
        priority="Medium",
        issue_type="Epic",
        remaining_estimate_hours=2.67,
        estimate_type=EstimateType.HISTORICAL_BENCHMARK,
        is_proxy_estimate=True,
    )
    r1 = ResourceCapacityAudit(
        account_id="acc-aqib",
        display_name="Aqib Khan",
        role="Team Member",
        projects_involved=["GF"],
        assigned_tasks_count=1,
        total_assigned_remaining_hours=2.67,
        available_capacity_hours=65.0,
    )
    report = _build_mock_validation_report(tasks=[t_epic], resources=[r1])
    svc = AdvisoryScheduleService()
    proposal = svc.generate_advisory_schedule(report, anchor_date="2026-10-01")

    task_prop = proposal.resource_schedules[0].scheduled_tasks[0]
    # For GF:Epic:Medium (n=1, sparse), fallback to GF:all (n=96, p50=2.67)
    assert task_prop.benchmark_sample_count == 96
    assert task_prop.estimated_effort_hours == 2.67


def test_blocked_task_status_notes_formatting():
    """Test that blocked tasks are formatted with conditional predecessor completion and not marked as plain Executable."""
    t_blocked = ValidatedTaskWorkload(
        issue_key="GF-474",
        project_key="GF",
        summary="QA Regression",
        status="To Do",
        assignee_name="Muhammad Bilal Khan",
        priority="Medium",
        remaining_estimate_hours=36.0,
        estimate_type=EstimateType.EXPLICIT_REMAINING,
        is_blocked=True,
        hard_blocker_keys=["GF-393"],
    )
    r1 = ResourceCapacityAudit(
        account_id="acc-bilal",
        display_name="Muhammad Bilal Khan",
        role="Mid-level QA",
        projects_involved=["GF"],
        assigned_tasks_count=1,
        total_assigned_remaining_hours=36.0,
        available_capacity_hours=65.0,
    )
    dep_finding = DependencyValidationFinding(
        source_issue_key="GF-393",
        target_issue_key="GF-474",
        link_type="Blocks",
        is_hard_block=True,
        is_target_resolved=False,
    )
    report = _build_mock_validation_report(tasks=[t_blocked], resources=[r1], findings=[dep_finding])
    svc = AdvisoryScheduleService()
    proposal = svc.generate_advisory_schedule(report, anchor_date="2026-10-01")

    md = AdvisoryScheduleFormatter.format_markdown_report(proposal)
    assert "⛔ Blocked by GF-393 (Conditional on predecessor completion)" in md
    assert "⛔ Blocked by GF-393; Executable" not in md

