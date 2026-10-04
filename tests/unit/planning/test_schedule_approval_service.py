"""Comprehensive unit and integration tests for Milestone 4C: Schedule Human Approval and Pre-Execution Conflict Validation."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.core.actions.base import ActionResult
from app.core.models.enums import ActionStatus
from app.core.planning.approval_models import (
    AllowedScheduleActionType,
    ApprovalStatus,
    ApprovedScheduleAction,
    ConflictCategory,
    ScheduleApprovalRequest,
    ScheduleReviewerIdentity,
)
from app.core.planning.approval_service import (
    ScheduleApprovalService,
    ScheduleAuthorizationError,
    ScheduleExecutionError,
)
from app.core.planning.schedule_models import (
    AdvisoryScheduleProposal,
    ResourceScheduleAudit,
    ScheduleFeasibilityStatus,
    ScheduledTaskProposal,
)
from app.core.planning.validation_models import (
    PlanningDataValidationReport,
    ValidatedTaskWorkload,
    ValidationOutcome,
)
from app.database.connection import DatabaseManager


@pytest.fixture
def test_approval_db(tmp_path):
    """Isolated database manager with required tables."""
    db_file = str(tmp_path / "test_approval.db")
    db = DatabaseManager(db_path=db_file)
    with db.session() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS planning_approval_requests (
                id TEXT PRIMARY KEY,
                approval_request_id TEXT NOT NULL UNIQUE,
                proposal_id TEXT NOT NULL,
                proposal_version TEXT NOT NULL,
                context_version TEXT NOT NULL,
                anchor_date TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'PENDING',
                proposal_summary TEXT NOT NULL,
                validation_status TEXT NOT NULL,
                validation_result_json TEXT NOT NULL,
                request_payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS planning_approval_decisions (
                id TEXT PRIMARY KEY,
                decision_id TEXT NOT NULL UNIQUE,
                approval_request_id TEXT NOT NULL UNIQUE,
                proposal_id TEXT NOT NULL,
                proposal_version TEXT NOT NULL,
                context_version TEXT NOT NULL,
                decision TEXT NOT NULL,
                reviewer_user_id TEXT NOT NULL,
                reviewer_display_name TEXT NOT NULL,
                reviewer_roles_json TEXT NOT NULL,
                acknowledged_issue_codes_json TEXT NOT NULL,
                comments TEXT,
                decided_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audit_logs (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                target TEXT NOT NULL,
                result TEXT NOT NULL,
                details TEXT
            );

            CREATE TABLE IF NOT EXISTS actions (
                id TEXT PRIMARY KEY,
                action_id TEXT NOT NULL,
                idempotency_key TEXT UNIQUE,
                action_type TEXT NOT NULL,
                target_system TEXT NOT NULL,
                target_id TEXT NOT NULL,
                parameters TEXT NOT NULL,
                status TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                dry_run INTEGER NOT NULL DEFAULT 0,
                preview TEXT,
                last_error TEXT,
                requested_by TEXT,
                requires_approval INTEGER NOT NULL DEFAULT 0,
                approved_by TEXT,
                approved_at TEXT,
                rejected_by TEXT,
                rejected_at TEXT,
                rejection_reason TEXT,
                result_data TEXT,
                created_at TEXT NOT NULL,
                executed_at TEXT
            );
            """
        )
    return db


def _build_test_proposal_and_validation():
    """Helper to build consistent proposal and validation report."""
    task1 = ValidatedTaskWorkload(
        issue_key="GF-483",
        project_key="GF",
        summary="2nd Cycle QA",
        status="To Do",
        assignee_name="Muhammad Bilal Khan",
        priority="Medium",
        due_date="2026-10-01",
        remaining_estimate_hours=12.33,
    )
    task2 = ValidatedTaskWorkload(
        issue_key="SMTPSUPORT-397",
        project_key="SMTPSUPORT",
        summary="Chrome Web store",
        status="To Do",
        assignee_name="Ahsan Iftikhar",
        priority="Medium",
        due_date=None,
        remaining_estimate_hours=4.58,
    )
    val_rep = PlanningDataValidationReport(
        report_id="val-123",
        generated_at="2026-10-01T12:00:00Z",
        anchor_date="2026-10-01",
        configured_projects=["GF", "SMTPSUPORT"],
        resolved_projects=["GF", "SMTPSUPORT"],
        validated_tasks=[task1, task2],
        overall_status=ValidationOutcome.VALID,
    )

    sched_task1 = ScheduledTaskProposal(
        action_id="act-sch-001-GF-483-duedate",
        issue_key="GF-483",
        project_key="GF",
        summary="2nd Cycle QA",
        assignee_name="Muhammad Bilal Khan",
        priority="Medium",
        estimated_effort_hours=12.33,
        dates_available=True,
        tentative_start_date="2026-10-01",
        tentative_completion_date="2026-10-07",
    )
    sched_task2 = ScheduledTaskProposal(
        action_id="act-sch-001-SMTPSUPORT-397-duedate",
        issue_key="SMTPSUPORT-397",
        project_key="SMTPSUPORT",
        summary="Chrome Web store",
        assignee_name="Ahsan Iftikhar",
        priority="Medium",
        estimated_effort_hours=4.58,
        dates_available=True,
        tentative_start_date="2026-10-01",
        tentative_completion_date="2026-10-02",
    )

    r1 = ResourceScheduleAudit(
        account_id="acc-bilal",
        display_name="Muhammad Bilal Khan",
        scheduled_tasks=[sched_task1],
    )
    r2 = ResourceScheduleAudit(
        account_id="acc-ahsan",
        display_name="Ahsan Iftikhar",
        scheduled_tasks=[sched_task2],
    )

    proposal = AdvisoryScheduleProposal(
        proposal_id="sch-001",
        proposal_version=1,
        generated_at="2026-10-01T12:00:00Z",
        anchor_date="2026-10-01",
        horizon_end_date="2026-10-14",
        validation_report_id="val-123",
        validation_status=ValidationOutcome.VALID,
        feasibility_status=ScheduleFeasibilityStatus.FEASIBLE,
        configured_projects=["GF", "SMTPSUPORT"],
        resolved_projects=["GF", "SMTPSUPORT"],
        resource_schedules=[r1, r2],
    )

    return proposal, val_rep


def test_create_approval_request_structure(test_approval_db):
    """Test creating an explicit approval request from proposal and validation report."""
    proposal, val_rep = _build_test_proposal_and_validation()
    svc = ScheduleApprovalService(manager=test_approval_db)

    req = svc.create_approval_request(proposal, val_rep)

    assert req.approval_id.startswith("app-")
    assert req.proposal_id == "sch-001"
    assert req.proposal_version == 1
    assert req.status == ApprovalStatus.PENDING
    assert req.authorization_scope == [AllowedScheduleActionType.UPDATE_DUE_DATE]
    assert len(req.actions) == 2

    # Check GF-483 action details
    gf_act = next(a for a in req.actions if a.issue_key == "GF-483")
    assert gf_act.proposed_due_date == "2026-10-07"
    assert gf_act.expected_pre_execution_due_date == "2026-10-01"
    assert gf_act.baseline_assignee == "Muhammad Bilal Khan"
    assert gf_act.baseline_remaining_hours == 12.33


def test_approval_decision_requires_authorized_role(test_approval_db):
    """Test that unauthorized reviewers are rejected with ScheduleAuthorizationError."""
    proposal, val_rep = _build_test_proposal_and_validation()
    svc = ScheduleApprovalService(manager=test_approval_db)
    req = svc.create_approval_request(proposal, val_rep)

    unauthorized_reviewer = ScheduleReviewerIdentity(
        user_id="user-guest",
        display_name="Guest User",
        roles=["guest", "read_only"],
    )

    with pytest.raises(ScheduleAuthorizationError) as exc_info:
        svc.record_approval_decision(
            approval_id=req.approval_id,
            reviewer=unauthorized_reviewer,
            decision=ApprovalStatus.APPROVED,
        )
    assert "lacks authorization roles" in str(exc_info.value)


def test_approval_decision_explicit_approval_success(test_approval_db):
    """Test recording an authorized explicit human approval."""
    proposal, val_rep = _build_test_proposal_and_validation()
    svc = ScheduleApprovalService(manager=test_approval_db)
    req = svc.create_approval_request(proposal, val_rep)

    pm_reviewer = ScheduleReviewerIdentity(
        user_id="usr-pm-1",
        display_name="Lead PM",
        roles=["pm", "admin"],
    )

    approved_req = svc.record_approval_decision(
        approval_id=req.approval_id,
        reviewer=pm_reviewer,
        decision=ApprovalStatus.APPROVED,
        comments="Approved for 2-week iteration",
    )

    assert approved_req.status == ApprovalStatus.APPROVED
    assert approved_req.decision is not None
    assert approved_req.decision.reviewer.display_name == "Lead PM"
    assert len(approved_req.decision.approved_action_ids) == 2


@pytest.mark.asyncio
async def test_pre_execution_conflict_detection_assignee_changed(test_approval_db):
    """Test that pre-execution validation catches assignee change on live Jira issue."""
    proposal, val_rep = _build_test_proposal_and_validation()
    svc = ScheduleApprovalService(manager=test_approval_db)
    req = svc.create_approval_request(proposal, val_rep)

    pm_reviewer = ScheduleReviewerIdentity(user_id="pm", display_name="PM", roles=["pm"])
    approved_req = svc.record_approval_decision(req.approval_id, pm_reviewer, ApprovalStatus.APPROVED)

    # Mock live issue state where assignee changed from Muhammad Bilal Khan -> Syed Ali
    live_issues = {
        "GF-483": {
            "fields": {
                "project": {"key": "GF"},
                "assignee": {"displayName": "Syed Ali"},  # CHANGED
                "status": {"name": "To Do"},
                "priority": {"name": "Medium"},
                "duedate": "2026-10-01",
                "timetracking": {"remainingEstimateSeconds": 44400},
            }
        },
        "SMTPSUPORT-397": {
            "fields": {
                "project": {"key": "SMTPSUPORT"},
                "assignee": {"displayName": "Ahsan Iftikhar"},
                "status": {"name": "To Do"},
                "priority": {"name": "Medium"},
                "duedate": None,
                "timetracking": {"remainingEstimateSeconds": 16488},
            }
        },
    }

    val_res = await svc.validate_pre_execution_conflicts(approved_req, live_issues_override=live_issues)

    assert val_res.is_valid is False
    assert len(val_res.conflicts) == 1
    conflict = val_res.conflicts[0]
    assert conflict.conflict_category == ConflictCategory.ASSIGNEE_CHANGED
    assert conflict.issue_key == "GF-483"
    assert "Syed Ali" in conflict.message


@pytest.mark.asyncio
async def test_pre_execution_conflict_detection_status_changed(test_approval_db):
    """Test that pre-execution validation catches status transition on live Jira issue."""
    proposal, val_rep = _build_test_proposal_and_validation()
    svc = ScheduleApprovalService(manager=test_approval_db)
    req = svc.create_approval_request(proposal, val_rep)

    pm_reviewer = ScheduleReviewerIdentity(user_id="pm", display_name="PM", roles=["pm"])
    approved_req = svc.record_approval_decision(req.approval_id, pm_reviewer, ApprovalStatus.APPROVED)

    # Mock live issue state where status changed to IN PROGRESS
    live_issues = {
        "GF-483": {
            "fields": {
                "project": {"key": "GF"},
                "assignee": {"displayName": "Muhammad Bilal Khan"},
                "status": {"name": "IN PROGRESS"},  # CHANGED from To Do
                "priority": {"name": "Medium"},
                "duedate": "2026-10-01",
                "timetracking": {"remainingEstimateSeconds": 44400},
            }
        },
        "SMTPSUPORT-397": {
            "fields": {
                "project": {"key": "SMTPSUPORT"},
                "assignee": {"displayName": "Ahsan Iftikhar"},
                "status": {"name": "To Do"},
                "priority": {"name": "Medium"},
                "duedate": None,
                "timetracking": {"remainingEstimateSeconds": 16488},
            }
        },
    }

    val_res = await svc.validate_pre_execution_conflicts(approved_req, live_issues_override=live_issues)

    assert val_res.is_valid is False
    assert any(c.conflict_category == ConflictCategory.STATUS_CHANGED for c in val_res.conflicts)


@pytest.mark.asyncio
async def test_pre_execution_conflict_detection_due_date_externally_modified(test_approval_db):
    """Test that overwriting manual external due date changes made after approval is blocked."""
    proposal, val_rep = _build_test_proposal_and_validation()
    svc = ScheduleApprovalService(manager=test_approval_db)
    req = svc.create_approval_request(proposal, val_rep)

    pm_reviewer = ScheduleReviewerIdentity(user_id="pm", display_name="PM", roles=["pm"])
    approved_req = svc.record_approval_decision(req.approval_id, pm_reviewer, ApprovalStatus.APPROVED)

    # Mock live issue where someone externally set due date to 2026-10-15 (expected 2026-10-01)
    live_issues = {
        "GF-483": {
            "fields": {
                "project": {"key": "GF"},
                "assignee": {"displayName": "Muhammad Bilal Khan"},
                "status": {"name": "To Do"},
                "priority": {"name": "Medium"},
                "duedate": "2026-10-15",  # EXTERNALLY CHANGED
                "timetracking": {"remainingEstimateSeconds": 44400},
            }
        },
        "SMTPSUPORT-397": {
            "fields": {
                "project": {"key": "SMTPSUPORT"},
                "assignee": {"displayName": "Ahsan Iftikhar"},
                "status": {"name": "To Do"},
                "priority": {"name": "Medium"},
                "duedate": None,
                "timetracking": {"remainingEstimateSeconds": 16488},
            }
        },
    }

    val_res = await svc.validate_pre_execution_conflicts(approved_req, live_issues_override=live_issues)

    assert val_res.is_valid is False
    assert any(c.conflict_category == ConflictCategory.DUE_DATE_CHANGED for c in val_res.conflicts)


@pytest.mark.asyncio
async def test_pre_execution_version_mismatch_blocks_execution(test_approval_db):
    """Test that a modified proposal version invalidates previous approval."""
    proposal, val_rep = _build_test_proposal_and_validation()
    svc = ScheduleApprovalService(manager=test_approval_db)
    req = svc.create_approval_request(proposal, val_rep)

    pm_reviewer = ScheduleReviewerIdentity(user_id="pm", display_name="PM", roles=["pm"])
    approved_req = svc.record_approval_decision(req.approval_id, pm_reviewer, ApprovalStatus.APPROVED)

    # Proposal updated to version 2
    proposal_v2 = proposal.model_copy(update={"proposal_version": 2})

    live_issues = {
        "GF-483": {
            "fields": {
                "project": {"key": "GF"},
                "assignee": {"displayName": "Muhammad Bilal Khan"},
                "status": {"name": "To Do"},
                "priority": {"name": "Medium"},
                "duedate": "2026-10-01",
                "timetracking": {"remainingEstimateSeconds": 44400},
            }
        },
        "SMTPSUPORT-397": {
            "fields": {
                "project": {"key": "SMTPSUPORT"},
                "assignee": {"displayName": "Ahsan Iftikhar"},
                "status": {"name": "To Do"},
                "priority": {"name": "Medium"},
                "duedate": None,
                "timetracking": {"remainingEstimateSeconds": 16488},
            }
        },
    }

    val_res = await svc.validate_pre_execution_conflicts(
        approved_req,
        proposal=proposal_v2,
        live_issues_override=live_issues,
    )

    assert val_res.is_valid is False
    assert any(c.conflict_category == ConflictCategory.VERSION_MISMATCH for c in val_res.conflicts)


@pytest.mark.asyncio
async def test_successful_approved_execution_with_dry_run(test_approval_db):
    """Test end-to-end execution of approved actions through ActionEngine in dry_run mode."""
    proposal, val_rep = _build_test_proposal_and_validation()
    
    mock_engine = MagicMock()
    mock_engine.execute = AsyncMock(return_value=ActionResult(
        success=True,
        action_id="jira-act-1",
        status=ActionStatus.DRY_RUN_SIMULATED,
        target_system="jira",
        target_id="GF-483",
        dry_run=True,
    ))

    svc = ScheduleApprovalService(manager=test_approval_db, engine=mock_engine)
    req = svc.create_approval_request(proposal, val_rep)

    pm_reviewer = ScheduleReviewerIdentity(user_id="pm", display_name="Lead PM", roles=["pm"])
    approved_req = svc.record_approval_decision(req.approval_id, pm_reviewer, ApprovalStatus.APPROVED)

    live_issues = {
        "GF-483": {
            "fields": {
                "project": {"key": "GF"},
                "assignee": {"displayName": "Muhammad Bilal Khan"},
                "status": {"name": "To Do"},
                "priority": {"name": "Medium"},
                "duedate": "2026-10-01",
                "timetracking": {"remainingEstimateSeconds": 44400},
            }
        },
        "SMTPSUPORT-397": {
            "fields": {
                "project": {"key": "SMTPSUPORT"},
                "assignee": {"displayName": "Ahsan Iftikhar"},
                "status": {"name": "To Do"},
                "priority": {"name": "Medium"},
                "duedate": None,
                "timetracking": {"remainingEstimateSeconds": 16488},
            }
        },
    }

    res = await svc.execute_approved_schedule(
        approval_request=approved_req,
        executor=pm_reviewer,
        dry_run=True,
        live_issues_override=live_issues,
    )

    assert res["success"] is True
    assert res["status"] == "COMPLETED"
    assert res["dry_run"] is True
    assert res["total_actions"] == 2
    assert mock_engine.execute.call_count == 2
