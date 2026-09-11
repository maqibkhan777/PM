"""Comprehensive tests for Action Engine, V1 capabilities, lifecycle, approval, security, and idempotency."""

from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch
import pytest

from app.core.actions.engine import ActionEngine
from app.core.actions.base import BaseAction, ActionResult
from app.core.actions.types import (
    create_send_message_action,
    create_send_notification_action,
    create_create_task_action,
    create_update_task_action,
    create_assign_task_action,
    create_transition_task_action,
    create_add_comment_action,
    create_change_priority_action,
)
from app.core.models.enums import ActionType, ActionStatus, Capability
from app.connectors.discord import DiscordWebhookConnector
from app.connectors.jira import JiraConnector
from app.database.repositories import ActionRepository, AuditRepository
from app.config.settings import settings


class MockJiraClient:
    """Mock Jira client for deterministic Action Engine testing."""

    def __init__(self):
        self.transitions = [
            {"id": "11", "name": "In Progress", "to": {"name": "In Progress"}},
            {"id": "21", "name": "Done", "to": {"name": "Done"}},
            {"id": "31", "name": "Duplicate Status", "to": {"name": "Ambiguous"}},
            {"id": "32", "name": "Duplicate Status", "to": {"name": "Ambiguous"}},
        ]
        self.transition_calls: List[tuple] = []
        self.assign_calls: List[tuple] = []
        self.comment_calls: List[tuple] = []
        self.update_field_calls: List[tuple] = []
        self.create_issue_calls: List[tuple] = []
        self.update_priority_calls: List[tuple] = []

    async def get_transitions(self, issue_key: str) -> List[Dict[str, Any]]:
        return list(self.transitions)

    async def transition_issue(self, issue_key: str, transition_id: str) -> Dict[str, Any]:
        self.transition_calls.append((issue_key, transition_id))
        return {"status": "success", "transition_id": transition_id, "transitioned": True}

    async def assign_issue(self, issue_key: str, account_id: str) -> Dict[str, Any]:
        self.assign_calls.append((issue_key, account_id))
        return {"assigned": True, "account_id": account_id}

    async def add_comment(self, issue_key: str, body: str) -> Dict[str, Any]:
        self.comment_calls.append((issue_key, body))
        return {"id": "comment-101", "body": body}

    async def update_fields(self, issue_key: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        self.update_field_calls.append((issue_key, fields))
        return {"updated": True, "fields": fields}

    async def create_issue(
        self,
        project_key: str,
        summary: str,
        issue_type: str = "Task",
        description: Optional[str] = None,
        assignee: Optional[str] = None,
        priority: Optional[str] = None,
        labels: Optional[List[str]] = None,
        **kwargs: Any
    ) -> Dict[str, Any]:
        self.create_issue_calls.append((project_key, summary, issue_type, description, assignee, priority, labels))
        return {"id": "10099", "key": f"{project_key}-99", "issue_key": f"{project_key}-99"}


    async def update_priority(self, issue_key: str, priority_name: str) -> Dict[str, Any]:
        self.update_priority_calls.append((issue_key, priority_name))
        return {"priority": priority_name}


class MockDiscordConnector(DiscordWebhookConnector):
    """Mock Discord connector recording dispatches."""

    def __init__(self):
        super().__init__(webhook_url="https://discord.com/api/webhooks/mock")
        self.dispatched_actions: List[Any] = []

    async def execute_action(self, action: Any) -> Dict[str, Any]:
        self.dispatched_actions.append(action)
        return {"status": "success", "http_code": 204}


@pytest.fixture
def test_setup(temp_db):
    """Setup ActionEngine with mock Jira and Discord connectors."""
    engine = ActionEngine(manager=temp_db)
    mock_jira = MockJiraClient()
    jira_conn = JiraConnector(client=mock_jira)
    discord_conn = MockDiscordConnector()

    engine.register_connector(jira_conn)
    engine.register_connector(discord_conn)
    return engine, mock_jira, discord_conn, temp_db


# ==============================================================================
# 1. GENERAL & LIFECYCLE TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_action_lifecycle_transitions_persisted(test_setup):
    """Test standard V1 lifecycle: REQUESTED -> VALIDATED -> EXECUTING -> COMPLETED without manual approval."""
    engine, mock_jira, _, temp_db = test_setup
    settings.DRY_RUN = False

    action = create_transition_task_action(
        target_system="jira",
        task_key="WSSS-326",
        target_status="Done",
        current_status="In Progress",
        requested_by="PM"
    )

    # 1. Execute -> automatically validates and executes to COMPLETED
    res = await engine.execute(action)
    assert res.status == ActionStatus.COMPLETED
    assert res.success is True

    repo = ActionRepository(temp_db)
    rec = repo.get_by_action_id(action.action_id)
    assert rec is not None
    assert rec["status"] == ActionStatus.COMPLETED.value
    assert rec["requires_approval"] == 0
    assert rec["requested_by"] == "PM"
    assert rec["result_data"] is not None

    # Verify executed Jira call
    assert len(mock_jira.transition_calls) == 1
    assert mock_jira.transition_calls[0] == ("WSSS-326", "21")

    # Verify audit trail contains Requested, Validated, Executing, Success
    audit_repo = AuditRepository(temp_db)
    logs = audit_repo.list_logs()
    results = [l["result"] for l in logs]
    assert "Requested" in results
    assert "Validated" in results
    assert "Executing" in results
    assert "Success" in results


@pytest.mark.asyncio
async def test_action_approval_and_rejection_lifecycle(test_setup):
    """Test approval and rejection lifecycle for actions requiring approval: PENDING_APPROVAL -> REJECTED / APPROVED."""
    engine, mock_jira, _, temp_db = test_setup
    settings.DRY_RUN = False

    action = create_add_comment_action(
        target_system="jira",
        task_key="WSSS-326",
        comment_body="Please review",
        requested_by="RulesEngine"
    )
    action.requires_approval = True

    # Manually insert in PENDING_APPROVAL
    repo = ActionRepository(temp_db)
    repo.insert(
        action_id=action.action_id,
        action_type=action.action_type.value,
        target_system="jira",
        target_id="WSSS-326",
        parameters=action.parameters,
        status=ActionStatus.PENDING_APPROVAL.value,
        idempotency_key=action.ensure_idempotency_key(),
        dry_run=False,
        requested_by="RulesEngine",
        requires_approval=True
    )

    # Reject action
    res_rej = await engine.reject_action(action.action_id, rejected_by="PM", reason="Not appropriate now")
    assert res_rej.success is True
    assert res_rej.status == ActionStatus.REJECTED

    rec = repo.get_by_action_id(action.action_id)
    assert rec["status"] == ActionStatus.REJECTED.value
    assert rec["rejected_by"] == "PM"
    assert rec["rejection_reason"] == "Not appropriate now"

    # Ensure cannot approve a rejected action
    res_try_approve = await engine.approve_action(action.action_id, approved_by="PM")
    assert res_try_approve.success is False
    assert "Only PENDING_APPROVAL actions can be approved" in res_try_approve.error_message


@pytest.mark.asyncio
async def test_action_validation_failure_lifecycle(test_setup):
    """Test validation failure path: REQUESTED -> VALIDATION FAILED -> FAILED."""
    engine, _, _, temp_db = test_setup

    # Missing comment body
    action = BaseAction(
        action_type=ActionType.ADD_COMMENT,
        target_system="jira",
        target_id="WSSS-326",
        parameters={"comment": ""},
        requested_by="RulesEngine"
    )

    res = await engine.execute(action)
    assert res.success is False
    assert res.status == ActionStatus.FAILED
    assert "Comment body cannot be empty" in res.error_message

    repo = ActionRepository(temp_db)
    rec = repo.get_by_action_id(action.action_id)
    assert rec["status"] == ActionStatus.FAILED.value


@pytest.mark.asyncio
async def test_security_blocking_destructive_actions(test_setup):
    """Test that destructive actions are blocked in V1."""
    engine, _, _, temp_db = test_setup

    destructive_action = BaseAction(
        action_type=ActionType.TRANSITION_TASK,
        target_system="jira",
        target_id="WSSS-326",
        parameters={"status": "Done", "is_destructive": True},
        requested_by="RulesEngine"
    )

    res = await engine.execute(destructive_action)
    assert res.success is False
    assert res.status == ActionStatus.FAILED
    assert "Destructive action" in res.error_message


# ==============================================================================
# 2. DRY-RUN & IDEMPOTENCY TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_centralized_dry_run_mode(test_setup):
    """Test centralized dry-run: DRY_RUN=true never calls connectors."""
    engine, mock_jira, _, temp_db = test_setup
    settings.DRY_RUN = True

    action = create_create_task_action(
        project_key="WSSS",
        summary="Create test task in dry-run",
        requested_by="RulesEngine"
    )

    # Even if approved, dry-run must simulate without calling external connector
    res = await engine.execute(action, approved=True)
    assert res.success is True
    assert res.status == ActionStatus.DRY_RUN_SIMULATED
    assert res.dry_run is True

    assert len(mock_jira.create_issue_calls) == 0

    repo = ActionRepository(temp_db)
    rec = repo.get_by_action_id(action.action_id)
    assert rec["status"] == ActionStatus.DRY_RUN_SIMULATED.value


@pytest.mark.asyncio
async def test_idempotency_duplicate_prevention(test_setup):
    """Test that executing an identical action key twice returns cached result and avoids duplicate mutation."""
    engine, mock_jira, _, temp_db = test_setup
    settings.DRY_RUN = False

    action = create_add_comment_action(
        target_system="jira",
        task_key="WSSS-326",
        comment_body="Unique deterministic comment",
        requested_by="RulesEngine"
    )

    # First execution (approved)
    res1 = await engine.execute(action, approved=True)
    assert res1.success is True
    assert res1.status == ActionStatus.COMPLETED
    assert len(mock_jira.comment_calls) == 1

    # Second execution of same action
    res2 = await engine.execute(action, approved=True)
    assert res2.success is True
    assert res2.status == ActionStatus.COMPLETED
    # Jira connector MUST NOT be called a second time
    assert len(mock_jira.comment_calls) == 1

    # Audit must record idempotent duplicate
    audit_repo = AuditRepository(temp_db)
    results = [l["result"] for l in audit_repo.list_logs()]
    assert "Idempotent Duplicate" in results


# ==============================================================================
# 3. ACTION #1 — SEND_MESSAGE TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_send_message_execution_and_validation(test_setup):
    """Test SEND_MESSAGE: requires no approval, validates parameters, and executes."""
    engine, _, discord_conn, _ = test_setup
    settings.DRY_RUN = False

    # Valid message
    action = create_send_message_action(
        target_system="discord",
        target_id="pm-alerts",
        text="Hello team, daily update is ready",
        requested_by="RulesEngine"
    )

    res = await engine.execute(action)
    assert res.success is True
    assert res.status == ActionStatus.COMPLETED
    assert len(discord_conn.dispatched_actions) == 1

    # Invalid empty message
    empty_action = create_send_message_action(
        target_system="discord",
        target_id="pm-alerts",
        text="   ",
        requested_by="RulesEngine"
    )
    res_empty = await engine.execute(empty_action)
    assert res_empty.success is False
    assert res_empty.status == ActionStatus.FAILED
    assert "Message text cannot be empty" in res_empty.error_message


# ==============================================================================
# 4. ACTION #2 — SEND_NOTIFICATION TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_send_notification_execution(test_setup):
    """Test SEND_NOTIFICATION: requires no approval, formats embed and dispatches."""
    engine, _, discord_conn, _ = test_setup
    settings.DRY_RUN = False

    action = create_send_notification_action(
        target_system="discord",
        channel="pm-alerts",
        title="Deployment Complete",
        message="V1 release has been deployed",
        level="SUCCESS"
    )

    res = await engine.execute(action)
    assert res.success is True
    assert res.status == ActionStatus.COMPLETED
    assert len(discord_conn.dispatched_actions) == 1


# ==============================================================================
# 5. ACTION #3 — CREATE_TASK TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_create_task_execution(test_setup):
    """Test CREATE_TASK: executes Jira create automatically in V1, captures created issue."""
    engine, mock_jira, _, temp_db = test_setup
    settings.DRY_RUN = False

    action = create_create_task_action(
        project_key="WSSS",
        summary="Fix payment gateway timeout",
        description="Detailed description here",
        issue_type="Task",
        requested_by="PM"
    )

    # In V1, standard task creation executes directly to COMPLETED
    res = await engine.execute(action)
    assert res.success is True
    assert res.status == ActionStatus.COMPLETED
    assert res.result_data.get("issue_key") == "WSSS-99"

    assert len(mock_jira.create_issue_calls) == 1
    assert mock_jira.create_issue_calls[0][0] == "WSSS"
    assert mock_jira.create_issue_calls[0][1] == "Fix payment gateway timeout"


# ==============================================================================
# 6. ACTION #4 — UPDATE_TASK TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_update_task_execution_and_allowlist(test_setup):
    """Test UPDATE_TASK: enforces field allowlist and updates allowed fields upon approval."""
    engine, mock_jira, _, _ = test_setup
    settings.DRY_RUN = False

    # Disallowed field test
    bad_action = create_update_task_action(
        task_key="WSSS-326",
        fields={"security_level": "Secret", "summary": "New Title"},
        requested_by="PM"
    )
    res_bad = await engine.execute(bad_action)
    assert res_bad.success is False
    assert res_bad.status == ActionStatus.FAILED
    assert "not permitted for update in V1" in res_bad.error_message

    # Allowed fields test
    valid_action = create_update_task_action(
        task_key="WSSS-326",
        fields={"summary": "Updated Task Summary", "priority": "High"},
        requested_by="PM"
    )
    res_app = await engine.execute(valid_action, approved=True)
    assert res_app.success is True
    assert res_app.status == ActionStatus.COMPLETED
    assert len(mock_jira.update_field_calls) == 1
    assert mock_jira.update_field_calls[0] == ("WSSS-326", {"summary": "Updated Task Summary", "priority": "High"})


# ==============================================================================
# 7. ACTION #5 — ASSIGN_TASK TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_assign_task_execution_and_no_fuzzy_matching(test_setup):
    """Test ASSIGN_TASK: requires explicit Atlassian account ID, blocks guessing."""
    engine, mock_jira, _, _ = test_setup
    settings.DRY_RUN = False

    # Missing account ID
    bad_action = create_assign_task_action(
        task_key="WSSS-326",
        assignee="",
        requested_by="PM"
    )
    res_bad = await engine.execute(bad_action)
    assert res_bad.success is False
    assert res_bad.status == ActionStatus.FAILED
    assert "Valid Jira account ID is required" in res_bad.error_message

    # Valid account ID
    valid_action = create_assign_task_action(
        task_key="WSSS-326",
        assignee="557058:ba931089-a292-4f11",
        assignee_name="Aqib Khan",
        requested_by="PM"
    )
    res = await engine.execute(valid_action, approved=True)
    assert res.success is True
    assert res.status == ActionStatus.COMPLETED
    assert len(mock_jira.assign_calls) == 1
    assert mock_jira.assign_calls[0] == ("WSSS-326", "557058:ba931089-a292-4f11")


# ==============================================================================
# 8. ACTION #6 — TRANSITION_TASK TESTS & AMBIGUITY RESOLUTION
# ==============================================================================

@pytest.mark.asyncio
async def test_transition_task_ambiguous_and_missing_status(test_setup):
    """Test TRANSITION_TASK: safely handles missing or ambiguous status names."""
    engine, mock_jira, _, _ = test_setup
    settings.DRY_RUN = False

    # Non-existent status
    action_not_found = create_transition_task_action(
        target_system="jira",
        task_key="WSSS-326",
        target_status="NonExistentStatus",
        requested_by="PM"
    )
    res1 = await engine.execute(action_not_found, approved=True)
    assert res1.success is False
    assert res1.status == ActionStatus.FAILED
    assert "not found on issue" in res1.error_message

    # Ambiguous status (matches both ID 31 and ID 32)
    action_ambig = create_transition_task_action(
        target_system="jira",
        task_key="WSSS-326",
        target_status="Duplicate Status",
        requested_by="PM"
    )
    res2 = await engine.execute(action_ambig, approved=True)
    assert res2.success is False
    assert res2.status == ActionStatus.FAILED
    assert "Ambiguous transition" in res2.error_message


# ==============================================================================
# 9. ACTION #7 — ADD_COMMENT TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_add_comment_execution(test_setup):
    """Test ADD_COMMENT: requires approval, validates comment, executes Jira comment API."""
    engine, mock_jira, _, temp_db = test_setup
    settings.DRY_RUN = False

    action = create_add_comment_action(
        target_system="jira",
        task_key="WSSS-326",
        comment_body="Fixed root cause in auth middleware.",
        requested_by="PM"
    )

    res = await engine.execute(action, approved=True)
    assert res.success is True
    assert res.status == ActionStatus.COMPLETED
    assert len(mock_jira.comment_calls) == 1
    assert mock_jira.comment_calls[0] == ("WSSS-326", "Fixed root cause in auth middleware.")


# ==============================================================================
# 10. END-TO-END REFERENCE TRANSITION TEST (SECTION 28)
# ==============================================================================

@pytest.mark.asyncio
async def test_reference_end_to_end_transition(test_setup):
    """Reference end-to-end TRANSITION_TASK test for V1:
    1. Create action
    2. Validate
    3. Persist
    4. Execute
    5. Resolve Jira transition
    6. Execute Jira mutation
    7. Persist COMPLETED
    8. Store result
    9. Write audit entries
    10. Idempotency test (no duplicate mutation)
    11. Dry run test (DRY_RUN=true)
    """
    engine, mock_jira, _, temp_db = test_setup
    settings.DRY_RUN = False

    # 1. Create action
    action = create_transition_task_action(
        target_system="jira",
        task_key="WSSS-326",
        target_status="In Progress",
        current_status="To Do",
        requested_by="RulesEngine"
    )

    # 2-8: Execute -> Validate, Persist, Execute, Resolve transition, Execute Jira mutation, Persist COMPLETED, Store result
    res = await engine.execute(action)
    assert res.status == ActionStatus.COMPLETED
    assert res.success is True
    assert res.result_data.get("transitioned") is True
    assert res.result_data.get("transition_id") == "11"

    repo = ActionRepository(temp_db)
    rec = repo.get_by_action_id(action.action_id)
    assert rec["status"] == ActionStatus.COMPLETED.value
    assert "Transition Jira task WSSS-326 from 'To Do' to 'In Progress'" in rec["preview"]["summary"]
    assert rec["result_data"]["transitioned"] is True

    assert len(mock_jira.transition_calls) == 1
    assert mock_jira.transition_calls[0] == ("WSSS-326", "11")

    # 9. Audit entries
    audit_repo = AuditRepository(temp_db)
    audit_results = [l["result"] for l in audit_repo.list_logs()]
    assert "Requested" in audit_results
    assert "Validated" in audit_results
    assert "Executing" in audit_results
    assert "Success" in audit_results

    # 14. Execute same action again -> no duplicate Jira transition, previous result returned
    res_dup = await engine.execute(action, approved=True)
    assert res_dup.success is True
    assert res_dup.status == ActionStatus.COMPLETED
    assert len(mock_jira.transition_calls) == 1  # Still 1 call, not 2!

    audit_results_after_dup = [l["result"] for l in audit_repo.list_logs()]
    assert "Idempotent Duplicate" in audit_results_after_dup

    # 15. Test with DRY_RUN=true
    settings.DRY_RUN = True
    dry_action = create_transition_task_action(
        target_system="jira",
        task_key="WSSS-327",
        target_status="Done",
        requested_by="PM"
    )
    res_dry = await engine.execute(dry_action, approved=True)
    assert res_dry.status == ActionStatus.DRY_RUN_SIMULATED
    assert res_dry.dry_run is True
    assert len(mock_jira.transition_calls) == 1  # No new mutation made in Jira


# ==============================================================================
# 11. ADDITIONAL HARDENING & EDGE CASE TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_unsupported_connector_and_unsupported_capability(test_setup):
    """Test validation rejection when connector is not found or does not support required capability."""
    engine, _, _, _ = test_setup

    # 1. Unknown target system connector
    unknown_sys_action = BaseAction(
        action_type=ActionType.SEND_MESSAGE,
        target_system="slack",
        target_id="general",
        parameters={"text": "Hello Slack"},
        requested_by="PM"
    )
    res1 = await engine.execute(unknown_sys_action)
    assert res1.success is False
    assert res1.status == ActionStatus.FAILED
    assert "Connector for target system 'slack' not found" in res1.error_message

    # 2. Unsupported capability on connector
    # Mock connector supporting only SEND_DM
    class LimitedConnector(DiscordWebhookConnector):
        def get_capabilities(self):
            return {Capability.SEND_DM}

    engine.register_connector(LimitedConnector())
    # Action requiring CREATE_TASK on discord (discord only has SEND_DM here)
    unsupported_cap_action = BaseAction(
        action_type=ActionType.CREATE_TASK,
        target_system="discord",
        target_id="proj",
        parameters={"project_key": "PROJ", "summary": "Task"},
        requested_by="PM"
    )
    res2 = await engine.execute(unsupported_cap_action)
    assert res2.success is False
    assert res2.status == ActionStatus.ACTION_UNSUPPORTED
    assert "does not support required capability" in res2.error_message


@pytest.mark.asyncio
async def test_create_task_with_optional_fields_and_idempotency(test_setup):
    """Test CREATE_TASK with assignee, priority, labels, dry-run, and idempotency."""
    engine, mock_jira, _, temp_db = test_setup
    settings.DRY_RUN = False

    action = create_create_task_action(
        project_key="WSSS",
        summary="Add payment webhook",
        description="Support Stripe webhooks",
        issue_type="Story",
        assignee="557058:ba931089",
        priority="High",
        labels=["backend", "payments"],
        requested_by="PM"
    )

    # Preview checks
    assert "Project: WSSS" in action.preview.summary
    assert "Summary: Add payment webhook" in action.preview.summary
    assert "Assignee: 557058:ba931089" in action.preview.summary

    # Execute approved
    res = await engine.execute(action, approved=True)
    assert res.success is True
    assert res.status == ActionStatus.COMPLETED
    assert len(mock_jira.create_issue_calls) == 1

    # Idempotent duplicate check
    res_dup = await engine.execute(action, approved=True)
    assert res_dup.success is True
    assert res_dup.status == ActionStatus.COMPLETED
    assert len(mock_jira.create_issue_calls) == 1  # No duplicate call

    # Dry-run check on another task
    settings.DRY_RUN = True
    dry_action = create_create_task_action(
        project_key="WSSS",
        summary="Dry run task",
        requested_by="PM"
    )
    res_dry = await engine.execute(dry_action, approved=True)
    assert res_dry.status == ActionStatus.DRY_RUN_SIMULATED
    assert res_dry.dry_run is True
    assert len(mock_jira.create_issue_calls) == 1


@pytest.mark.asyncio
async def test_update_task_dry_run_and_idempotency(test_setup):
    """Test UPDATE_TASK dry-run and idempotency."""
    engine, mock_jira, _, _ = test_setup
    settings.DRY_RUN = False

    action = create_update_task_action(
        task_key="WSSS-326",
        fields={"summary": "Updated summary", "description": "New description"},
        requested_by="PM"
    )

    # Execute approved
    res = await engine.execute(action, approved=True)
    assert res.success is True
    assert res.status == ActionStatus.COMPLETED
    assert len(mock_jira.update_field_calls) == 1

    # Idempotent duplicate check
    res_dup = await engine.execute(action, approved=True)
    assert res_dup.success is True
    assert res_dup.status == ActionStatus.COMPLETED
    assert len(mock_jira.update_field_calls) == 1

    # Dry-run check
    settings.DRY_RUN = True
    dry_action = create_update_task_action(
        task_key="WSSS-327",
        fields={"priority": "Highest"},
        requested_by="PM"
    )
    res_dry = await engine.execute(dry_action, approved=True)
    assert res_dry.status == ActionStatus.DRY_RUN_SIMULATED
    assert len(mock_jira.update_field_calls) == 1


@pytest.mark.asyncio
async def test_assign_task_dry_run_and_idempotency(test_setup):
    """Test ASSIGN_TASK dry-run and idempotency."""
    engine, mock_jira, _, _ = test_setup
    settings.DRY_RUN = False

    action = create_assign_task_action(
        task_key="WSSS-326",
        assignee="557058:ba931089",
        assignee_name="Aqib Khan",
        requested_by="PM"
    )

    # Execute approved
    res = await engine.execute(action, approved=True)
    assert res.success is True
    assert res.status == ActionStatus.COMPLETED
    assert len(mock_jira.assign_calls) == 1

    # Idempotent duplicate check
    res_dup = await engine.execute(action, approved=True)
    assert res_dup.success is True
    assert res_dup.status == ActionStatus.COMPLETED
    assert len(mock_jira.assign_calls) == 1

    # Dry-run check
    settings.DRY_RUN = True
    dry_action = create_assign_task_action(
        task_key="WSSS-327",
        assignee="557058:ba931089",
        requested_by="PM"
    )
    res_dry = await engine.execute(dry_action, approved=True)
    assert res_dry.status == ActionStatus.DRY_RUN_SIMULATED
    assert len(mock_jira.assign_calls) == 1


@pytest.mark.asyncio
async def test_add_comment_length_limit_dry_run_and_idempotency(test_setup):
    """Test ADD_COMMENT length limit (>30,000 chars), dry-run, and idempotency."""
    engine, mock_jira, _, _ = test_setup
    settings.DRY_RUN = False

    # Exceed length limit
    huge_comment = "a" * 30001
    bad_action = create_add_comment_action(
        target_system="jira",
        task_key="WSSS-326",
        comment_body=huge_comment,
        requested_by="PM"
    )
    res_bad = await engine.execute(bad_action, approved=True)
    assert res_bad.success is False
    assert res_bad.status == ActionStatus.FAILED
    assert "exceeds maximum allowed length" in res_bad.error_message

    # Valid comment
    valid_action = create_add_comment_action(
        target_system="jira",
        task_key="WSSS-326",
        comment_body="Valid comment content",
        requested_by="PM"
    )
    res_valid = await engine.execute(valid_action, approved=True)
    assert res_valid.success is True
    assert res_valid.status == ActionStatus.COMPLETED
    assert len(mock_jira.comment_calls) == 1

    # Idempotency
    res_dup = await engine.execute(valid_action, approved=True)
    assert res_dup.success is True
    assert len(mock_jira.comment_calls) == 1

    # Dry run
    settings.DRY_RUN = True
    dry_action = create_add_comment_action(
        target_system="jira",
        task_key="WSSS-327",
        comment_body="Dry run comment",
        requested_by="PM"
    )
    res_dry = await engine.execute(dry_action, approved=True)
    assert res_dry.status == ActionStatus.DRY_RUN_SIMULATED
    assert len(mock_jira.comment_calls) == 1


@pytest.mark.asyncio
async def test_change_priority_action_full_lifecycle(test_setup):
    """Test CHANGE_PRIORITY action executing automatically in V1."""
    engine, mock_jira, _, _ = test_setup
    settings.DRY_RUN = False

    action = create_change_priority_action(
        task_key="WSSS-326",
        priority="High",
        current_priority="Low",
        requested_by="RulesEngine"
    )

    # 1. Executes directly in V1
    res = await engine.execute(action)
    assert res.success is True
    assert res.status == ActionStatus.COMPLETED
    assert len(mock_jira.update_priority_calls) == 1
    assert mock_jira.update_priority_calls[0] == ("WSSS-326", "High")

    # 2. Idempotent duplicate check
    res_dup = await engine.execute(action)
    assert res_dup.success is True
    assert len(mock_jira.update_priority_calls) == 1

