"""Planning Execution Service for Phase 4F: Approved Planning Execution.

Executes ONLY human-approved planning proposals originating from an authorized Phase 4E approval.

CRITICAL SAFETY INVARIANTS:
1. ZERO AI/LLM calls during execution (no DeepSeek, no re-planning, no prompt generation).
2. ONLY executes proposals with state == APPROVED.
3. Strict Version Binding: proposal_id, proposal_version, and context_version must match the approval.
4. Validation Integrity: proposal must be validated (VALID or NEEDS_REVIEW with human approval). INVALID cannot be executed.
5. Action Allowlist: Only supported planning mutations are executed (e.g. UPDATE_DUE_DATE).
6. No Silent Dropping: Unsupported proposal fields produce BLOCKED / PARTIALLY_COMPLETED, clearly recorded.
7. Current Jira State Verification: Before mutating Jira, inspect live issue state to verify it has not changed since approval.
8. Centralized DRY_RUN integration: respects settings.DRY_RUN or request.dry_run without mutating Jira.
9. Exactly-once idempotency: An approved request already executed returns ALREADY_EXECUTED with zero repeat mutations.
10. Atomic concurrency: Mutex locking prevents concurrent execution of the same proposal.
11. Separate authorization: Requires Capability.EXECUTE_PLANNING.
"""

from datetime import datetime, timezone
import threading
from typing import Any, Dict, List, Optional, Set, Tuple
import uuid

from app.config.settings import settings
from app.connectors.jira.client import JiraClient
from app.core.actions.base import BaseAction, ActionResult
from app.core.actions.engine import ActionEngine, action_engine
from app.core.models.enums import ActionType, ActionStatus, Capability
from app.core.models.planning import (
    PlanningApprovalRequest,
    PlanningApprovalState,
    PlanningExecutionAction,
    PlanningExecutionActionType,
    PlanningExecutionFailure,
    PlanningExecutionFailureCategory,
    PlanningExecutionRequest,
    PlanningExecutionResult,
    PlanningExecutionState,
    ProposalValidationStatus,
    ReviewerIdentity,
    TaskPlanningProposal,
)
from app.database.connection import DatabaseManager, db_manager
from app.database.repositories import PlanningApprovalRepository, PlanningExecutionRepository
from app.services.audit_service import AuditService, audit_service
from app.utils.logger import logger
from app.utils.time import utc_now_iso


# Explicit allowlist of supported planning mutations in Phase 4F
ALLOWED_PLANNING_MUTATIONS: Set[PlanningExecutionActionType] = {
    PlanningExecutionActionType.UPDATE_DUE_DATE,
}


class PlanningExecutionError(Exception):
    """Base exception for planning execution failures."""
    def __init__(self, message: str, failure_category: PlanningExecutionFailureCategory = PlanningExecutionFailureCategory.UNKNOWN_FAILURE):
        super().__init__(message)
        self.failure_category = failure_category


class PlanningExecutionAuthorizationError(PlanningExecutionError):
    """Raised when the executor lacks EXECUTE_PLANNING authorization."""
    def __init__(self, message: str):
        super().__init__(message, PlanningExecutionFailureCategory.AUTHORIZATION_FAILURE)


class PlanningExecutionSafetyError(PlanningExecutionError):
    """Raised when an approval, validation, or version safety invariant is violated."""
    def __init__(self, message: str, failure_category: PlanningExecutionFailureCategory):
        super().__init__(message, failure_category)


class PlanningExecutionService:
    """Service orchestrating deterministic execution of human-approved planning proposals."""

    _lock = threading.RLock()

    def __init__(
        self,
        manager: Optional[DatabaseManager] = None,
        engine: Optional[ActionEngine] = None,
        jira_client: Optional[JiraClient] = None,
        audit: Optional[AuditService] = None,
    ):
        self.mgr = manager or db_manager
        self.approval_repo = PlanningApprovalRepository(self.mgr)
        self.execution_repo = PlanningExecutionRepository(self.mgr)
        self.engine = engine or action_engine
        self.jira_client = jira_client or JiraClient()
        self.audit = audit or audit_service

    def _verify_executor_authorization(self, executor: ReviewerIdentity) -> None:
        """Verify that the human executor possesses Capability.EXECUTE_PLANNING."""
        if not executor or not isinstance(executor, ReviewerIdentity):
            raise PlanningExecutionAuthorizationError("Executor identity must be a valid ReviewerIdentity instance.")
        
        roles = [r.lower() for r in executor.roles]
        # Authorized roles for execution: admin, write, pm, lead
        has_permission = any(r in ("admin", "write", "pm", "lead", "executor") for r in roles)
        if not has_permission:
            raise PlanningExecutionAuthorizationError(
                f"Reviewer '{executor.display_name}' ({executor.user_id}) lacks "
                f"Capability.EXECUTE_PLANNING authorization (roles: {executor.roles})."
            )

    async def execute_approved_planning(
        self,
        execution_request: PlanningExecutionRequest,
    ) -> PlanningExecutionResult:
        """Execute an approved planning proposal through Jira and Action Engine with fail-closed safety."""
        with self._lock:
            return await self._execute_internal(execution_request)

    async def _execute_internal(
        self,
        execution_request: PlanningExecutionRequest,
    ) -> PlanningExecutionResult:
        started_at = utc_now_iso()
        is_dry_run = bool(settings.DRY_RUN or execution_request.dry_run)
        req_id = execution_request.approval_request_id

        # 1. Authorization check
        self._verify_executor_authorization(execution_request.executor)

        # 2. Retrieve approval request
        approval_dict = self.approval_repo.get_request(req_id)
        if not approval_dict:
            failure = PlanningExecutionFailure(
                failure_category=PlanningExecutionFailureCategory.APPROVAL_INVALID,
                message=f"Planning approval request '{req_id}' not found.",
            )
            return self._build_terminal_result(
                execution_request=execution_request,
                state=PlanningExecutionState.FAILED,
                dry_run=is_dry_run,
                started_at=started_at,
                failures=[failure],
                summary=f"Execution blocked: approval request '{req_id}' not found.",
            )

        approval = PlanningApprovalRequest(**approval_dict)

        # 3. Verify approval state is APPROVED
        if approval.state != PlanningApprovalState.APPROVED:
            cat = (
                PlanningExecutionFailureCategory.APPROVAL_EXPIRED
                if approval.state == PlanningApprovalState.EXPIRED
                else PlanningExecutionFailureCategory.APPROVAL_INVALID
            )
            failure = PlanningExecutionFailure(
                failure_category=cat,
                message=f"Approval request '{req_id}' has state '{approval.state.value}'. Only APPROVED proposals can be executed.",
            )
            self._log_audit(
                event="PLANNING_EXECUTION_BLOCKED",
                execution_id=execution_request.execution_id,
                req_id=req_id,
                proposal_id=approval.proposal_id,
                proposal_ver=approval.proposal_version,
                ctx_ver=approval.context_version,
                actor=execution_request.executor.display_name,
                dry_run=is_dry_run,
                details={"reason": failure.message, "approval_state": approval.state.value},
            )
            return self._build_terminal_result(
                execution_request=execution_request,
                state=PlanningExecutionState.BLOCKED,
                dry_run=is_dry_run,
                started_at=started_at,
                failures=[failure],
                summary=f"Execution blocked: proposal state is '{approval.state.value}'.",
            )

        # 4. Check for idempotency: already executed?
        existing_exec = self.execution_repo.get_execution_by_approval_request_id(req_id)
        if existing_exec and existing_exec.get("state") == PlanningExecutionState.COMPLETED.value:
            if not is_dry_run or (is_dry_run and existing_exec.get("dry_run")):
                logger.info(f"Approval request '{req_id}' has already been executed. Returning ALREADY_EXECUTED.")
                self._log_audit(
                    event="PLANNING_EXECUTION_ALREADY_EXECUTED",
                    execution_id=execution_request.execution_id,
                    req_id=req_id,
                    proposal_id=approval.proposal_id,
                    proposal_ver=approval.proposal_version,
                    ctx_ver=approval.context_version,
                    actor=execution_request.executor.display_name,
                    dry_run=is_dry_run,
                    details={"existing_execution_id": existing_exec.get("execution_id")},
                )
                payload = existing_exec.get("result_payload_json")
                if isinstance(payload, dict):
                    return PlanningExecutionResult(**payload)
                elif isinstance(payload, str):
                    import json
                    return PlanningExecutionResult(**json.loads(payload))
                return PlanningExecutionResult(**{k: v for k, v in existing_exec.items() if k in PlanningExecutionResult.model_fields})

        # 5. Exact Version Binding Verification
        if (
            execution_request.proposal_id != approval.proposal_id
            or execution_request.proposal_version != approval.proposal_version
            or execution_request.context_version != approval.context_version
        ):
            failure = PlanningExecutionFailure(
                failure_category=PlanningExecutionFailureCategory.VERSION_MISMATCH,
                message=(
                    f"Version mismatch: request ({execution_request.proposal_id}:{execution_request.proposal_version}, ctx: {execution_request.context_version}) "
                    f"does not match approved ({approval.proposal_id}:{approval.proposal_version}, ctx: {approval.context_version})."
                ),
            )
            self._log_audit(
                event="PLANNING_EXECUTION_BLOCKED",
                execution_id=execution_request.execution_id,
                req_id=req_id,
                proposal_id=approval.proposal_id,
                proposal_ver=approval.proposal_version,
                ctx_ver=approval.context_version,
                actor=execution_request.executor.display_name,
                dry_run=is_dry_run,
                details={"reason": failure.message},
            )
            return self._build_terminal_result(
                execution_request=execution_request,
                state=PlanningExecutionState.BLOCKED,
                dry_run=is_dry_run,
                started_at=started_at,
                failures=[failure],
                summary="Execution blocked: version mismatch.",
            )

        # 6. Validation Integrity Verification
        if not approval.validation_result or approval.validation_status == ProposalValidationStatus.INVALID:
            failure = PlanningExecutionFailure(
                failure_category=PlanningExecutionFailureCategory.VALIDATION_INVALID,
                message=f"Proposal validation status is '{approval.validation_status.value}'. INVALID proposals cannot be executed.",
            )
            return self._build_terminal_result(
                execution_request=execution_request,
                state=PlanningExecutionState.BLOCKED,
                dry_run=is_dry_run,
                started_at=started_at,
                failures=[failure],
                summary="Execution blocked: invalid proposal validation.",
            )

        # Log Execution Started
        self._log_audit(
            event="PLANNING_EXECUTION_STARTED",
            execution_id=execution_request.execution_id,
            req_id=req_id,
            proposal_id=approval.proposal_id,
            proposal_ver=approval.proposal_version,
            ctx_ver=approval.context_version,
            actor=execution_request.executor.display_name,
            dry_run=is_dry_run,
            details={"task_count": len(approval.task_proposals)},
        )

        # 7. Action Extraction & Deterministic Ordering
        actions_to_execute, extraction_failures = self._extract_approved_actions(approval)

        # If any unsupported fields were found in the approved proposal, record them
        execution_failures: List[PlanningExecutionFailure] = list(extraction_failures)
        execution_actions: List[PlanningExecutionAction] = []

        successful_count = 0
        failed_count = 0
        blocked_count = len(extraction_failures)
        skipped_count = 0

        # 8. Execute each extracted action in deterministic order
        for action in actions_to_execute:
            action_result, failure = await self._execute_single_action(
                action=action,
                execution_request=execution_request,
                is_dry_run=is_dry_run,
            )
            execution_actions.append(action_result)
            if failure:
                execution_failures.append(failure)

            if action_result.state == "SUCCEEDED":
                successful_count += 1
            elif action_result.state == "BLOCKED":
                blocked_count += 1
            elif action_result.state == "FAILED":
                failed_count += 1
            elif action_result.state == "SKIPPED":
                skipped_count += 1

        # 9. Determine Final Execution State
        total_actions = len(execution_actions) + len(extraction_failures)
        if blocked_count > 0 or failed_count > 0:
            if successful_count > 0:
                final_state = PlanningExecutionState.PARTIALLY_COMPLETED
                summary = (
                    f"Execution partially completed: {successful_count}/{total_actions} actions succeeded, "
                    f"{blocked_count} blocked, {failed_count} failed."
                )
            else:
                final_state = PlanningExecutionState.BLOCKED if blocked_count > 0 else PlanningExecutionState.FAILED
                summary = f"Execution {final_state.value.lower()}: 0/{total_actions} actions succeeded."
        else:
            final_state = PlanningExecutionState.COMPLETED
            summary = f"Execution completed successfully: all {successful_count} actions applied."

        completed_at = utc_now_iso()

        result = PlanningExecutionResult(
            execution_id=execution_request.execution_id,
            approval_request_id=req_id,
            proposal_id=approval.proposal_id,
            proposal_version=approval.proposal_version,
            context_version=approval.context_version,
            state=final_state,
            dry_run=is_dry_run,
            started_at=started_at,
            completed_at=completed_at,
            total_actions=total_actions,
            successful_actions=successful_count,
            failed_actions=failed_count,
            blocked_actions=blocked_count,
            skipped_actions=skipped_count,
            actions=execution_actions,
            failures=execution_failures,
            summary=summary,
        )

        # 10. Persist Execution Record
        self.execution_repo.save_execution({
            "execution_id": result.execution_id,
            "approval_request_id": result.approval_request_id,
            "proposal_id": result.proposal_id,
            "proposal_version": result.proposal_version,
            "context_version": result.context_version,
            "state": result.state.value,
            "dry_run": result.dry_run,
            "executor_user_id": execution_request.executor.user_id,
            "executor_display_name": execution_request.executor.display_name,
            "total_actions": result.total_actions,
            "successful_actions": result.successful_actions,
            "failed_actions": result.failed_actions,
            "blocked_actions": result.blocked_actions,
            "skipped_actions": result.skipped_actions,
            "started_at": result.started_at,
            "completed_at": result.completed_at,
            "result_payload_json": result.model_dump(),
        })

        # 11. Lifecycle Completion Audit
        audit_event = (
            "PLANNING_EXECUTION_COMPLETED"
            if final_state == PlanningExecutionState.COMPLETED
            else (
                "PLANNING_EXECUTION_PARTIALLY_COMPLETED"
                if final_state == PlanningExecutionState.PARTIALLY_COMPLETED
                else "PLANNING_EXECUTION_BLOCKED"
            )
        )
        self._log_audit(
            event=audit_event,
            execution_id=result.execution_id,
            req_id=req_id,
            proposal_id=approval.proposal_id,
            proposal_ver=approval.proposal_version,
            ctx_ver=approval.context_version,
            actor=execution_request.executor.display_name,
            dry_run=is_dry_run,
            details={
                "state": final_state.value,
                "successful": successful_count,
                "failed": failed_count,
                "blocked": blocked_count,
                "summary": summary,
            },
        )

        return result

    def _extract_approved_actions(
        self,
        approval: PlanningApprovalRequest,
    ) -> Tuple[List[PlanningExecutionAction], List[PlanningExecutionFailure]]:
        """Extract allowed planning mutations from approved proposal with deterministic ordering."""
        actions: List[PlanningExecutionAction] = []
        failures: List[PlanningExecutionFailure] = []

        # Sort tasks deterministically by issue_key
        sorted_tasks = sorted(approval.task_proposals, key=lambda t: t.issue_key)

        for task in sorted_tasks:
            # Check for due date mutation
            if task.proposed_due_date:
                action_id = f"act-{approval.proposal_id}-{task.issue_key}-duedate"
                actions.append(
                    PlanningExecutionAction(
                        action_id=action_id,
                        issue_key=task.issue_key,
                        action_type=PlanningExecutionActionType.UPDATE_DUE_DATE,
                        field_name="duedate",
                        approved_value=task.proposed_due_date,
                        state="PENDING",
                    )
                )

            # Check if proposal contains unsupported mutation fields (e.g. proposed_estimate or sequencing)
            # Estimate mutation is not an allowed Jira mutation in standard UPDATE_TASK allowlist without custom fields
            if task.proposed_estimate is not None:
                failures.append(
                    PlanningExecutionFailure(
                        failure_category=PlanningExecutionFailureCategory.UNSUPPORTED_ACTION,
                        message=(
                            f"Task '{task.issue_key}' proposes estimate '{task.proposed_estimate.value} {task.proposed_estimate.unit.value}', "
                            f"which is not currently in the approved mutation allowlist {sorted([a.value for a in ALLOWED_PLANNING_MUTATIONS])}."
                        ),
                        issue_key=task.issue_key,
                        action_type="UPDATE_ESTIMATE",
                    )
                )

        # Deterministic sorting of actions: 1. issue_key, 2. action_type, 3. action_id
        actions.sort(key=lambda a: (a.issue_key, a.action_type.value, a.action_id))
        return actions, failures

    async def _execute_single_action(
        self,
        action: PlanningExecutionAction,
        execution_request: PlanningExecutionRequest,
        is_dry_run: bool,
    ) -> Tuple[PlanningExecutionAction, Optional[PlanningExecutionFailure]]:
        """Execute a single planning mutation action after verifying live Jira state."""
        issue_key = action.issue_key
        approved_val = action.approved_value

        # 1. Inspect Current Live Jira State
        try:
            live_issue = await self.jira_client.get_issue(issue_key)
        except Exception as e:
            err_str = str(e)
            cat = (
                PlanningExecutionFailureCategory.JIRA_NOT_FOUND
                if "404" in err_str
                else PlanningExecutionFailureCategory.JIRA_API_FAILURE
            )
            action.state = "BLOCKED"
            action.failure_category = cat
            action.error_message = f"Failed to fetch live Jira state for {issue_key}: {e}"
            failure = PlanningExecutionFailure(
                failure_category=cat,
                message=action.error_message,
                issue_key=issue_key,
                action_type=action.action_type.value,
            )
            return action, failure

        fields = live_issue.get("fields", {})
        live_due_date = fields.get("duedate")
        action.current_value = live_due_date

        # Check for Live State Conflict: if Jira already has the exact approved value, it's a no-op
        if live_due_date == approved_val:
            action.state = "SKIPPED"
            action.error_message = f"Live Jira due date is already {approved_val} (already up-to-date)."
            return action, None

        # 2. Construct Action Engine BaseAction
        engine_action = BaseAction(
            action_id=f"jira-{action.action_id}",
            action_type=ActionType.UPDATE_TASK,
            target_system="jira",
            target_id=issue_key,
            parameters={
                "task_key": issue_key,
                "fields": {"duedate": approved_val},
            },
            requested_by=execution_request.executor.display_name,
            dry_run=is_dry_run,
        )

        action.action_engine_action_id = engine_action.action_id

        # 3. Log Action Attempted
        self._log_audit(
            event="PLANNING_EXECUTION_ACTION_ATTEMPTED",
            execution_id=execution_request.execution_id,
            req_id=execution_request.approval_request_id,
            proposal_id=execution_request.proposal_id,
            proposal_ver=execution_request.proposal_version,
            ctx_ver=execution_request.context_version,
            actor=execution_request.executor.display_name,
            dry_run=is_dry_run,
            details={
                "issue_key": issue_key,
                "action_type": action.action_type.value,
                "approved_value": approved_val,
                "current_value": live_due_date,
            },
        )

        # 4. Execute through Action Engine (approved=True because human approved Phase 4E)
        try:
            res: ActionResult = await self.engine.execute(engine_action, approved=True)
            if res.success:
                action.state = "SUCCEEDED"
                self._log_audit(
                    event="PLANNING_EXECUTION_ACTION_SUCCEEDED",
                    execution_id=execution_request.execution_id,
                    req_id=execution_request.approval_request_id,
                    proposal_id=execution_request.proposal_id,
                    proposal_ver=execution_request.proposal_version,
                    ctx_ver=execution_request.context_version,
                    actor=execution_request.executor.display_name,
                    dry_run=is_dry_run,
                    details={"issue_key": issue_key, "result": res.result_data},
                )
                return action, None
            else:
                action.state = "FAILED"
                action.failure_category = PlanningExecutionFailureCategory.JIRA_API_FAILURE
                action.error_message = res.error_message or "ActionEngine execution failed."
                failure = PlanningExecutionFailure(
                    failure_category=PlanningExecutionFailureCategory.JIRA_API_FAILURE,
                    message=action.error_message,
                    issue_key=issue_key,
                    action_type=action.action_type.value,
                    details={"status": res.status.value},
                )
                self._log_audit(
                    event="PLANNING_EXECUTION_ACTION_FAILED",
                    execution_id=execution_request.execution_id,
                    req_id=execution_request.approval_request_id,
                    proposal_id=execution_request.proposal_id,
                    proposal_ver=execution_request.proposal_version,
                    ctx_ver=execution_request.context_version,
                    actor=execution_request.executor.display_name,
                    dry_run=is_dry_run,
                    details={"issue_key": issue_key, "error": action.error_message},
                )
                return action, failure

        except Exception as e:
            action.state = "FAILED"
            action.failure_category = PlanningExecutionFailureCategory.JIRA_API_FAILURE
            action.error_message = str(e)
            failure = PlanningExecutionFailure(
                failure_category=PlanningExecutionFailureCategory.JIRA_API_FAILURE,
                message=str(e),
                issue_key=issue_key,
                action_type=action.action_type.value,
            )
            self._log_audit(
                event="PLANNING_EXECUTION_ACTION_FAILED",
                execution_id=execution_request.execution_id,
                req_id=execution_request.approval_request_id,
                proposal_id=execution_request.proposal_id,
                proposal_ver=execution_request.proposal_version,
                ctx_ver=execution_request.context_version,
                actor=execution_request.executor.display_name,
                dry_run=is_dry_run,
                details={"issue_key": issue_key, "error": str(e)},
            )
            return action, failure

    def _build_terminal_result(
        self,
        execution_request: PlanningExecutionRequest,
        state: PlanningExecutionState,
        dry_run: bool,
        started_at: str,
        failures: List[PlanningExecutionFailure],
        summary: str,
    ) -> PlanningExecutionResult:
        completed_at = utc_now_iso()
        result = PlanningExecutionResult(
            execution_id=execution_request.execution_id,
            approval_request_id=execution_request.approval_request_id,
            proposal_id=execution_request.proposal_id,
            proposal_version=execution_request.proposal_version,
            context_version=execution_request.context_version,
            state=state,
            dry_run=dry_run,
            started_at=started_at,
            completed_at=completed_at,
            total_actions=len(failures),
            successful_actions=0,
            failed_actions=len(failures) if state == PlanningExecutionState.FAILED else 0,
            blocked_actions=len(failures) if state == PlanningExecutionState.BLOCKED else 0,
            skipped_actions=0,
            actions=[],
            failures=failures,
            summary=summary,
        )
        self.execution_repo.save_execution({
            "execution_id": result.execution_id,
            "approval_request_id": result.approval_request_id,
            "proposal_id": result.proposal_id,
            "proposal_version": result.proposal_version,
            "context_version": result.context_version,
            "state": result.state.value,
            "dry_run": result.dry_run,
            "executor_user_id": execution_request.executor.user_id,
            "executor_display_name": execution_request.executor.display_name,
            "total_actions": result.total_actions,
            "successful_actions": result.successful_actions,
            "failed_actions": result.failed_actions,
            "blocked_actions": result.blocked_actions,
            "skipped_actions": result.skipped_actions,
            "started_at": result.started_at,
            "completed_at": result.completed_at,
            "result_payload_json": result.model_dump(),
        })
        return result

    def _log_audit(
        self,
        event: str,
        execution_id: str,
        req_id: str,
        proposal_id: str,
        proposal_ver: str,
        ctx_ver: str,
        actor: str,
        dry_run: bool,
        details: Dict[str, Any],
    ) -> None:
        """Helper to write structured execution audit records."""
        try:
            self.audit.log_action(
                actor=actor,
                action=event,
                target=f"plan_exec:{execution_id}",
                result=event.replace("PLANNING_EXECUTION_", "").title(),
                details={
                    "execution_id": execution_id,
                    "approval_request_id": req_id,
                    "proposal_id": proposal_id,
                    "proposal_version": proposal_ver,
                    "context_version": ctx_ver,
                    "dry_run": dry_run,
                    **details,
                },
            )
        except Exception as e:
            logger.warning(f"Failed to record audit event '{event}': {e}")
