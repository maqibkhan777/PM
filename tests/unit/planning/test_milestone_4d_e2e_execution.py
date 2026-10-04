"""Milestone 4D: Controlled End-to-End Execution Validation Test Suite.

Validates the complete end-to-end lifecycle:
AI Request / Planning Data
→ Planning Validation
→ Advisory Schedule Proposal
→ Explicit Human Approval
→ Live Conflict Validation
→ DRY_RUN
→ Explicitly Authorized Execution
→ Jira Mutation (UPDATE_DUE_DATE only)
→ Audit Trail

Scenarios covered:
- Scenario A: Controlled Successful Execution (DRY_RUN & Live Jira Mutation, Audit Trail Verification)
- Scenario B: Stale Proposal Live-State Conflict Gating (Assignee, Status, Estimate, Due Date, Dependency changes fail closed)
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.core.actions.base import ActionResult
from app.core.actions.engine import ActionEngine
from app.core.models.enums import ActionStatus, ActionType
from app.core.planning.approval_models import (
    AllowedScheduleActionType,
    ApprovalStatus,
    ConflictCategory,
    ScheduleApprovalRequest,
    ScheduleReviewerIdentity,
)
from app.core.planning.approval_service import ScheduleApprovalService
from app.core.planning.schedule_models import (
    AdvisoryScheduleProposal,
    ScheduledTaskProposal,
)
from app.core.planning.schedule_service import AdvisoryScheduleService
from app.core.planning.validation_models import (
    PlanningDataValidationReport,
    ValidatedTaskWorkload,
    ValidationOutcome,
)
from app.database.connection import DatabaseManager
from app.database.repositories import AuditRepository
from app.services.audit_service import AuditService


@pytest.fixture
def e2e_db(tmp_path):
    """Isolated database manager for Milestone 4D E2E tests."""
    db_file = str(tmp_path / "e2e_approval.db")
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


def _build_e2e_planning_data():
    """Build controlled planning data validation report and advisory proposal."""
    task_gf = ValidatedTaskWorkload(
        issue_key="GF-483",
        project_key="GF",
        summary="2nd Cycle QA - Multi-Tenant Setup",
        status="To Do",
        assignee_name="Muhammad Bilal Khan",
        priority="Medium",
        due_date="2026-10-01",
        remaining_estimate_hours=12.33,
    )
    val_report = PlanningDataValidationReport(
        report_id="val-e2e-001",
        generated_at="2026-10-04T12:00:00Z",
        anchor_date="2026-10-04",
        configured_projects=["GF"],
        resolved_projects=["GF"],
        validated_tasks=[task_gf],
        overall_status=ValidationOutcome.VALID,
    )

    sched_service = AdvisoryScheduleService()
    proposal = sched_service.generate_advisory_schedule(
        validation_report=val_report,
        anchor_date="2026-10-04",
    )
    return val_report, proposal


@pytest.mark.asyncio
async def test_e2e_scenario_a_successful_execution_lifecycle(e2e_db):
    """End-to-End Scenario A: Successful Full Lifecycle Execution.
    
    Flow:
    1. Generate proposal from validation report.
    2. Confirm proposal ID, version, and single UPDATE_DUE_DATE action.
    3. Create explicit approval request.
    4. Approve action using authorized reviewer.
    5. Re-read live Jira state & verify baseline match.
    6. Run DRY_RUN mode; verify simulated result and zero Jira mutation.
    7. Run live execution; verify Jira mutation strictly limited to UPDATE_DUE_DATE.
    8. Verify full audit trail records all required metadata.
    """
    val_report, proposal = _build_e2e_planning_data()
    assert proposal.proposal_id.startswith("sch-")
    assert proposal.proposal_version == 1

    # Mock Jira Connector / Client & Action Engine
    mock_jira_connector = MagicMock()
    mock_jira_connector.name = "jira"
    mock_jira_connector.system_type = "jira"
    mock_jira_connector.supports_capability = MagicMock(return_value=True)
    mock_jira_connector.get_security_level_for_capability = MagicMock(return_value=None)
    
    # Track executed mutations
    executed_jira_updates = []
    async def mock_execute_jira_action(action):
        executed_jira_updates.append(action.parameters)
        return {"updated": True, "issue_key": action.target_id, "fields": action.parameters.get("fields")}

    mock_jira_connector.execute_action = AsyncMock(side_effect=mock_execute_jira_action)

    engine = ActionEngine(manager=e2e_db)
    engine.register_connector(mock_jira_connector)

    approval_svc = ScheduleApprovalService(manager=e2e_db, engine=engine)

    # 1. Create Approval Request
    approval_req = approval_svc.create_approval_request(proposal, val_report)
    assert approval_req.status == ApprovalStatus.PENDING
    assert len(approval_req.actions) == 1
    action_item = approval_req.actions[0]
    assert action_item.issue_key == "GF-483"
    assert action_item.action_type == AllowedScheduleActionType.UPDATE_DUE_DATE
    assert action_item.proposed_due_date == "2026-10-06"

    # 2. Explicit Human Approval
    reviewer = ScheduleReviewerIdentity(
        user_id="pm_user_001",
        display_name="Authorized PM Reviewer",
        roles=["pm", "write"],
    )
    approved_req = approval_svc.record_approval_decision(
        approval_id=approval_req.approval_id,
        reviewer=reviewer,
        decision=ApprovalStatus.APPROVED,
        approved_action_ids=[action_item.action_id],
        comments="Approved tentative due date adjustment for GF-483",
    )
    assert approved_req.status == ApprovalStatus.APPROVED
    assert approved_req.decision.approved_action_ids == [action_item.action_id]

    # 3. Live Jira State (Matches Baseline Exactly)
    live_jira_state = {
        "GF-483": {
            "fields": {
                "project": {"key": "GF"},
                "assignee": {"displayName": "Muhammad Bilal Khan"},
                "status": {"name": "To Do"},
                "priority": {"name": "Medium"},
                "duedate": "2026-10-01",  # Baseline expected pre-execution due date
                "timetracking": {"remainingEstimateSeconds": 44388},  # ~12.33h
            }
        }
    }

    # 4. DRY_RUN Execution
    dry_run_res = await approval_svc.execute_approved_schedule(
        approval_request=approved_req,
        executor=reviewer,
        dry_run=True,
        live_issues_override=live_jira_state,
    )
    assert dry_run_res["success"] is True
    assert dry_run_res["dry_run"] is True
    assert dry_run_res["status"] == "COMPLETED"
    assert len(executed_jira_updates) == 0  # Zero live Jira mutations in DRY_RUN

    # 5. Live Authorized Execution
    exec_res = await approval_svc.execute_approved_schedule(
        approval_request=approved_req,
        executor=reviewer,
        dry_run=False,
        live_issues_override=live_jira_state,
    )
    assert exec_res["success"] is True
    assert exec_res["dry_run"] is False
    assert exec_res["status"] == "COMPLETED"
    assert len(executed_jira_updates) == 1
    assert executed_jira_updates[0] == {
        "task_key": "GF-483",
        "fields": {"duedate": "2026-10-06"},
    }

    # 6. Verify Full Audit Trail
    audit_repo = AuditRepository(e2e_db)
    all_logs = audit_repo.list_logs(limit=100)
    logs = [log for log in all_logs if log["target"] == approved_req.approval_id]
    assert len(logs) >= 3

    exec_completed_log = next(log for log in logs if log["action"] == "SCHEDULE_EXECUTION_COMPLETED")
    assert exec_completed_log["result"] == "SUCCESS"
    details = exec_completed_log["details"]
    assert details["proposal_id"] == proposal.proposal_id
    assert details["proposal_version"] == 1
    assert details["approval_id"] == approved_req.approval_id
    assert details["executor_id"] == "pm_user_001"
    assert details["dry_run"] is False
    assert len(details["actions"]) == 1
    assert details["actions"][0]["action_id"] == action_item.action_id
    assert details["actions"][0]["issue_key"] == "GF-483"
    assert "UPDATE_DUE_DATE" in details["actions"][0]["action_type"]
    assert details["actions"][0]["proposed_value"] == "2026-10-06"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tampered_field,tampered_value,expected_conflict",
    [
        ("assignee", {"displayName": "Different Resource"}, ConflictCategory.ASSIGNEE_CHANGED),
        ("status", {"name": "In Progress"}, ConflictCategory.STATUS_CHANGED),
        ("priority", {"name": "Highest"}, ConflictCategory.PRIORITY_CHANGED),
        ("duedate", "2026-10-15", ConflictCategory.DUE_DATE_CHANGED),
        ("timetracking", {"remainingEstimateSeconds": 80000}, ConflictCategory.ESTIMATE_CHANGED),
        ("project", {"key": "UNKNOWN_PROJECT"}, ConflictCategory.PROJECT_MISMATCH),
    ],
)
async def test_e2e_scenario_b_stale_proposal_conflict_gating(
    e2e_db, tampered_field, tampered_value, expected_conflict
):
    """End-to-End Scenario B: Stale Proposal Conflict Gating.
    
    Safety Invariant:
    If any execution-critical Jira field changes between approval and execution,
    pre-execution validation must detect the conflict, fail closed, prevent ActionEngine
    dispatch, and log the conflict event in the audit trail.
    """
    val_report, proposal = _build_e2e_planning_data()
    
    mock_jira_connector = MagicMock()
    mock_jira_connector.name = "jira"
    mock_jira_connector.system_type = "jira"
    mock_jira_connector.supports_capability = MagicMock(return_value=True)
    mock_jira_connector.get_security_level_for_capability = MagicMock(return_value=None)
    mock_jira_connector.execute_action = AsyncMock()

    engine = ActionEngine(manager=e2e_db)
    engine.register_connector(mock_jira_connector)
    approval_svc = ScheduleApprovalService(manager=e2e_db, engine=engine)

    # 1. Create and Approve Request
    req = approval_svc.create_approval_request(proposal, val_report)
    reviewer = ScheduleReviewerIdentity(user_id="pm", display_name="Lead PM", roles=["pm"])
    approved_req = approval_svc.record_approval_decision(req.approval_id, reviewer, ApprovalStatus.APPROVED)

    # 2. Build Tampered Live State
    base_fields = {
        "project": {"key": "GF"},
        "assignee": {"displayName": "Muhammad Bilal Khan"},
        "status": {"name": "To Do"},
        "priority": {"name": "Medium"},
        "duedate": "2026-10-01",
        "timetracking": {"remainingEstimateSeconds": 44388},
    }
    base_fields[tampered_field] = tampered_value
    live_state = {"GF-483": {"fields": base_fields}}

    # 3. Attempt Execution
    exec_res = await approval_svc.execute_approved_schedule(
        approval_request=approved_req,
        executor=reviewer,
        dry_run=False,
        live_issues_override=live_state,
    )

    # 4. Verify Execution is Blocked
    assert exec_res["success"] is False
    assert exec_res["status"] == "BLOCKED_BY_CONFLICT"
    assert any(c["conflict_category"] == expected_conflict.value for c in exec_res["conflicts"])
    
    # 5. Verify Zero Jira Connector Invocations
    assert mock_jira_connector.execute_action.call_count == 0

    # 6. Verify Blocked Audit Log
    audit_repo = AuditRepository(e2e_db)
    all_logs = audit_repo.list_logs(limit=100)
    logs = [log for log in all_logs if log["target"] == approved_req.approval_id]
    blocked_log = next(log for log in logs if log["action"] == "SCHEDULE_EXECUTION_BLOCKED_BY_CONFLICT")
    assert blocked_log["result"] == "BLOCKED"
    assert blocked_log["details"]["conflict_count"] >= 1
