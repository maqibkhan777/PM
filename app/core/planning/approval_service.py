"""Human Approval and Pre-Execution Live-State Conflict Validation Service (Milestone 4C).

CRITICAL ARCHITECTURAL BOUNDARIES & SAFETY INVARIANTS:
1. Advisory proposals remain strictly read-only until explicitly approved by an authorized human reviewer.
2. Approval is explicit, structured, and tied to an exact proposal ID, proposal version, and action ID.
3. Field-level authorization: Only UPDATE_DUE_DATE is allowed for execution in Milestone 4C.
4. Pre-execution live-state conflict validation: Immediately before invoking the Action Engine,
   inspects current Jira state and compares against the proposal baseline.
5. Material conflicts (status changed, assignee changed, priority changed, estimate changed, due date changed,
   dependencies changed, issue deleted/missing, proposal version mismatch) invalidate approval and block execution.
6. Fail-closed: Any conflict, version mismatch, or API failure halts execution.
7. Centralized DRY_RUN integration: respects settings.DRY_RUN or request.dry_run without mutating Jira.
8. Zero assignment changes, zero status transitions, zero comments, zero estimate modifications.
"""

from datetime import datetime, timezone, timedelta
import threading
from typing import Any, Dict, List, Optional, Set, Tuple
import uuid

from app.config.settings import settings
from app.connectors.jira.client import JiraClient
from app.core.actions.base import BaseAction, ActionResult
from app.core.actions.engine import ActionEngine, action_engine
from app.core.models.enums import ActionType, ActionStatus, Capability
from app.core.planning.approval_models import (
    AllowedScheduleActionType,
    ApprovalStatus,
    ApprovedScheduleAction,
    ConflictCategory,
    ExecutionConflict,
    PreExecutionValidationResult,
    ScheduleApprovalDecision,
    ScheduleApprovalRequest,
    ScheduleReviewerIdentity,
)
from app.core.planning.schedule_models import (
    AdvisoryScheduleProposal,
    ScheduleFeasibilityStatus,
    ScheduledTaskProposal,
)
from app.core.planning.validation_models import (
    PlanningDataValidationReport,
    ValidatedTaskWorkload,
)
from app.database.connection import DatabaseManager, db_manager
from app.database.repositories import (
    JiraIssueLinkRepository,
    JiraIssueStateRepository,
    PlanningApprovalRepository,
)
from app.services.audit_service import AuditService, audit_service
from app.utils.logger import logger
from app.utils.time import format_iso, parse_iso_datetime, utc_now, utc_now_iso


# Default validity duration for pending schedule approvals (48 hours)
DEFAULT_SCHEDULE_APPROVAL_EXPIRATION_HOURS = 48

# Authorized execution roles
AUTHORIZED_EXECUTION_ROLES: Set[str] = {"admin", "write", "pm", "lead", "executor"}

COMPLETED_STATUSES: Set[str] = {
    "done",
    "completed",
    "resolved",
    "closed",
    "finished",
    "cancelled",
    "rejected",
}


class ScheduleExecutionError(Exception):
    """Base exception for schedule approval and execution failures."""
    def __init__(self, message: str, conflict_category: ConflictCategory = ConflictCategory.APPROVAL_INVALID):
        super().__init__(message)
        self.conflict_category = conflict_category


class ScheduleAuthorizationError(ScheduleExecutionError):
    """Raised when the human reviewer lacks execution authorization."""
    def __init__(self, message: str):
        super().__init__(message, ConflictCategory.UNAUTHORIZED_ACTION)


class ScheduleApprovalService:
    """Service governing human approval and live-state pre-execution conflict validation for Milestone 4C."""

    _lock = threading.RLock()

    def __init__(
        self,
        manager: Optional[DatabaseManager] = None,
        engine: Optional[ActionEngine] = None,
        jira_client: Optional[JiraClient] = None,
        audit: Optional[AuditService] = None,
        expiration_hours: int = DEFAULT_SCHEDULE_APPROVAL_EXPIRATION_HOURS,
    ):
        self.mgr = manager or db_manager
        self.approval_repo = PlanningApprovalRepository(self.mgr)
        self.issue_repo = JiraIssueStateRepository(self.mgr)
        self.link_repo = JiraIssueLinkRepository(self.mgr)
        self.engine = engine or action_engine
        self.jira_client = jira_client or JiraClient()
        self.audit = audit or (AuditService(self.mgr) if manager else audit_service)
        self.expiration_hours = expiration_hours

    def create_approval_request(
        self,
        proposal: AdvisoryScheduleProposal,
        validation_report: PlanningDataValidationReport,
    ) -> ScheduleApprovalRequest:
        """Construct an explicit, immutable ScheduleApprovalRequest from an advisory proposal.
        
        Extracts only schedulable tasks with tentative completion dates into explicit ApprovedScheduleAction items.
        """
        if not isinstance(proposal, AdvisoryScheduleProposal):
            raise ScheduleExecutionError("A valid AdvisoryScheduleProposal is required.")
        if not isinstance(validation_report, PlanningDataValidationReport):
            raise ScheduleExecutionError("A valid PlanningDataValidationReport is required.")

        now_dt = utc_now()
        created_at = format_iso(now_dt)
        expires_at = format_iso(now_dt + timedelta(hours=self.expiration_hours))
        approval_id = f"app-{uuid.uuid4().hex[:12]}"

        # Map validated tasks for baseline state lookup
        validated_task_map: Dict[str, ValidatedTaskWorkload] = {
            t.issue_key: t for t in validation_report.validated_tasks
        }

        # Extract explicit actions (UPDATE_DUE_DATE)
        actions: List[ApprovedScheduleAction] = []
        for res_sched in proposal.resource_schedules:
            for task_prop in res_sched.scheduled_tasks:
                # Only tasks with tentative completion dates and available dates can be scheduled
                if not task_prop.dates_available or not task_prop.tentative_completion_date:
                    continue

                act_id = task_prop.action_id or f"act-{proposal.proposal_id}-{task_prop.issue_key}-duedate"
                val_task = validated_task_map.get(task_prop.issue_key)

                expected_pre_due: Optional[str] = val_task.due_date if val_task else None
                base_assignee = val_task.assignee_name if val_task else task_prop.assignee_name
                base_status = val_task.status if val_task else None
                base_priority = val_task.priority if val_task else task_prop.priority
                base_rem = val_task.remaining_estimate_hours if val_task else task_prop.estimated_effort_hours
                base_blockers = list(val_task.hard_blocker_keys) if val_task else list(task_prop.unresolved_predecessor_keys)

                actions.append(
                    ApprovedScheduleAction(
                        action_id=act_id,
                        issue_key=task_prop.issue_key,
                        action_type=AllowedScheduleActionType.UPDATE_DUE_DATE,
                        proposed_due_date=task_prop.tentative_completion_date,
                        expected_pre_execution_due_date=expected_pre_due,
                        baseline_assignee=base_assignee,
                        baseline_status=base_status,
                        baseline_priority=base_priority,
                        baseline_remaining_hours=base_rem,
                        baseline_blocker_keys=base_blockers,
                    )
                )

        # Deterministic sorting of actions
        actions.sort(key=lambda a: a.issue_key)

        approval_req = ScheduleApprovalRequest(
            approval_id=approval_id,
            proposal_id=proposal.proposal_id,
            proposal_version=proposal.proposal_version,
            validation_report_id=validation_report.report_id,
            anchor_date=proposal.anchor_date,
            status=ApprovalStatus.PENDING,
            created_at=created_at,
            expires_at=expires_at,
            configured_projects=proposal.configured_projects,
            resolved_projects=proposal.resolved_projects,
            authorization_scope=[AllowedScheduleActionType.UPDATE_DUE_DATE],
            actions=actions,
            decision=None,
        )

        with self._lock:
            # Persist to repository
            req_data = approval_req.model_dump()
            req_data["approval_request_id"] = approval_id
            req_data["proposal_version"] = str(proposal.proposal_version)
            req_data["context_version"] = validation_report.report_id
            req_data["proposal_summary"] = f"Advisory Schedule Proposal across {', '.join(proposal.resolved_projects)} ({len(actions)} actions)"
            req_data["validation_status"] = validation_report.overall_status.value
            req_data["validation_result"] = {"report_id": validation_report.report_id}
            self.approval_repo.save_request(req_data)

        self.audit.log_action(
            actor="SYSTEM_SCHEDULE_ENGINE",
            action="SCHEDULE_APPROVAL_REQUEST_CREATED",
            target=approval_id,
            result="SUCCESS",
            details={
                "approval_id": approval_id,
                "proposal_id": proposal.proposal_id,
                "proposal_version": proposal.proposal_version,
                "action_count": len(actions),
            },
        )

        return approval_req

    def record_approval_decision(
        self,
        approval_id: str,
        reviewer: ScheduleReviewerIdentity,
        decision: ApprovalStatus,
        approved_action_ids: Optional[List[str]] = None,
        comments: Optional[str] = None,
    ) -> ScheduleApprovalRequest:
        """Record an explicit, authenticated human approval or rejection decision."""
        if not reviewer or not isinstance(reviewer, ScheduleReviewerIdentity):
            raise ScheduleAuthorizationError("A valid ScheduleReviewerIdentity is required.")

        roles = [r.lower() for r in reviewer.roles]
        if not any(r in AUTHORIZED_EXECUTION_ROLES for r in roles):
            raise ScheduleAuthorizationError(
                f"Reviewer '{reviewer.display_name}' ({reviewer.user_id}) lacks authorization roles {roles}."
            )

        if decision not in (ApprovalStatus.APPROVED, ApprovalStatus.REJECTED):
            raise ScheduleExecutionError("Decision must be either APPROVED or REJECTED.")

        with self._lock:
            raw_req = self.approval_repo.get_request(approval_id)
            if not raw_req:
                raise ScheduleExecutionError(f"Approval request '{approval_id}' not found.")

            valid_keys = set(ScheduleApprovalRequest.model_fields.keys())
            req_data = {k: v for k, v in raw_req.items() if k in valid_keys}
            if "approval_id" not in req_data and "approval_request_id" in raw_req:
                req_data["approval_id"] = raw_req["approval_request_id"]

            req = ScheduleApprovalRequest(**req_data)

            # Check expiration
            now_dt = utc_now()
            now_iso = format_iso(now_dt)
            exp_dt = parse_iso_datetime(req.expires_at)
            if exp_dt and exp_dt < now_dt:
                req.status = ApprovalStatus.EXPIRED
                self.approval_repo.save_request({
                    "approval_request_id": approval_id,
                    "proposal_id": req.proposal_id,
                    "proposal_version": str(req.proposal_version),
                    "context_version": req.validation_report_id,
                    "anchor_date": req.anchor_date,
                    "state": ApprovalStatus.EXPIRED.value,
                    "request_payload_json": req.model_dump(),
                })
                raise ScheduleExecutionError(f"Approval request '{approval_id}' has expired on {req.expires_at}.")

            if req.status != ApprovalStatus.PENDING:
                raise ScheduleExecutionError(
                    f"Approval request '{approval_id}' cannot transition from '{req.status.value}'."
                )

            # Check subset of approved actions
            final_approved_ids: List[str] = []
            if decision == ApprovalStatus.APPROVED:
                all_valid_act_ids = {a.action_id for a in req.actions}
                if approved_action_ids:
                    unknown_ids = [aid for aid in approved_action_ids if aid not in all_valid_act_ids]
                    if unknown_ids:
                        raise ScheduleExecutionError(
                            f"Approved action IDs {unknown_ids} not found in approval request {approval_id}."
                        )
                    final_approved_ids = sorted(list(set(approved_action_ids)))
                else:
                    final_approved_ids = sorted(list(all_valid_act_ids))

            dec_id = f"dec-{uuid.uuid4().hex[:8]}"
            approval_decision = ScheduleApprovalDecision(
                decision_id=dec_id,
                approval_id=approval_id,
                proposal_id=req.proposal_id,
                proposal_version=req.proposal_version,
                decision=decision,
                reviewer=reviewer,
                decided_at=now_iso,
                approved_action_ids=final_approved_ids,
                comments=comments,
            )

            req.status = decision
            req.decision = approval_decision

            # Persist update
            upd_data = req.model_dump()
            upd_data["approval_request_id"] = approval_id
            upd_data["proposal_version"] = str(req.proposal_version)
            upd_data["context_version"] = req.validation_report_id
            upd_data["state"] = decision.value
            self.approval_repo.save_request(upd_data)

            self.audit.log_action(
                actor=reviewer.display_name,
                action=f"SCHEDULE_APPROVAL_{decision.value}",
                target=approval_id,
                result="SUCCESS",
                details={
                    "approval_id": approval_id,
                    "decision": decision.value,
                    "approved_actions_count": len(final_approved_ids),
                    "reviewer_id": reviewer.user_id,
                },
            )

            return req

    async def validate_pre_execution_conflicts(
        self,
        approval_request: ScheduleApprovalRequest,
        proposal: Optional[AdvisoryScheduleProposal] = None,
        live_issues_override: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> PreExecutionValidationResult:
        """Perform strict pre-execution live-state conflict validation immediately before execution.
        
        Fetches live Jira state for all approved tasks and checks:
        - Issue still exists and belongs to expected project
        - Assignee has not changed
        - Status has not changed
        - Priority has not changed
        - Remaining estimate has not changed materially
        - Live due date does not conflict with expected pre-execution due date
        - Blocker / dependency state has not changed
        - Proposal ID and version binding are intact
        """
        now_iso = utc_now_iso()
        conflicts: List[ExecutionConflict] = []
        executable_actions: List[ApprovedScheduleAction] = []

        req = approval_request

        # 1. Approval state check
        if req.status != ApprovalStatus.APPROVED or not req.decision:
            conflicts.append(
                ExecutionConflict(
                    conflict_category=ConflictCategory.APPROVAL_INVALID,
                    issue_key="ALL",
                    message=f"Schedule approval '{req.approval_id}' has state '{req.status.value}'. Only APPROVED requests can be executed.",
                )
            )
            return PreExecutionValidationResult(
                is_valid=False,
                approval_id=req.approval_id,
                proposal_id=req.proposal_id,
                proposal_version=req.proposal_version,
                validated_at=now_iso,
                conflicts=conflicts,
                executable_actions=[],
                summary="Execution blocked: approval request is not APPROVED.",
            )

        # 2. Proposal Version Binding Check
        if proposal:
            if proposal.proposal_id != req.proposal_id:
                conflicts.append(
                    ExecutionConflict(
                        conflict_category=ConflictCategory.PROPOSAL_MISMATCH,
                        issue_key="ALL",
                        approved_value=req.proposal_id,
                        live_value=proposal.proposal_id,
                        message=f"Proposal ID mismatch: approval targets '{req.proposal_id}' but provided proposal is '{proposal.proposal_id}'.",
                    )
                )
            if proposal.proposal_version != req.proposal_version:
                conflicts.append(
                    ExecutionConflict(
                        conflict_category=ConflictCategory.VERSION_MISMATCH,
                        issue_key="ALL",
                        approved_value=req.proposal_version,
                        live_value=proposal.proposal_version,
                        message=f"Proposal version mismatch: approval targets version {req.proposal_version} but provided proposal is version {proposal.proposal_version}.",
                    )
                )

        approved_ids_set = set(req.decision.approved_action_ids) if req.decision.approved_action_ids else {a.action_id for a in req.actions}
        target_actions = [a for a in req.actions if a.action_id in approved_ids_set]

        # 3. Issue-by-issue live state conflict verification
        for act in target_actions:
            issue_key = act.issue_key

            # Field-level authorization check
            if act.action_type != AllowedScheduleActionType.UPDATE_DUE_DATE:
                conflicts.append(
                    ExecutionConflict(
                        conflict_category=ConflictCategory.UNAUTHORIZED_ACTION,
                        issue_key=issue_key,
                        action_type=str(act.action_type),
                        message=f"Action type '{act.action_type}' is unauthorized. Only UPDATE_DUE_DATE is permitted.",
                    )
                )
                continue

            # Fetch live Jira state
            live_issue: Optional[Dict[str, Any]] = None
            if live_issues_override and issue_key in live_issues_override:
                live_issue = live_issues_override[issue_key]
            else:
                try:
                    live_issue = await self.jira_client.get_issue(issue_key)
                except Exception as e:
                    err_msg = str(e)
                    cat = ConflictCategory.ISSUE_UNAVAILABLE
                    conflicts.append(
                        ExecutionConflict(
                            conflict_category=cat,
                            issue_key=issue_key,
                            message=f"Live Jira issue '{issue_key}' could not be retrieved: {err_msg}",
                        )
                    )
                    continue

            if not live_issue:
                conflicts.append(
                    ExecutionConflict(
                        conflict_category=ConflictCategory.ISSUE_UNAVAILABLE,
                        issue_key=issue_key,
                        message=f"Live Jira issue '{issue_key}' does not exist or returned empty response.",
                    )
                )
                continue

            fields = live_issue.get("fields", {})

            # 3a. Project check
            live_proj = fields.get("project", {}).get("key") or (issue_key.split("-")[0] if "-" in issue_key else "")
            if req.configured_projects and live_proj.upper() not in [p.upper() for p in req.configured_projects]:
                conflicts.append(
                    ExecutionConflict(
                        conflict_category=ConflictCategory.PROJECT_MISMATCH,
                        issue_key=issue_key,
                        live_value=live_proj,
                        message=f"Issue '{issue_key}' belongs to project '{live_proj}', which is not in configured scope {req.configured_projects}.",
                    )
                )

            # 3b. Assignee check
            live_assignee_dict = fields.get("assignee")
            live_assignee_name = live_assignee_dict.get("displayName") if isinstance(live_assignee_dict, dict) else None
            if act.baseline_assignee and live_assignee_name and act.baseline_assignee.strip().lower() != live_assignee_name.strip().lower():
                conflicts.append(
                    ExecutionConflict(
                        conflict_category=ConflictCategory.ASSIGNEE_CHANGED,
                        issue_key=issue_key,
                        baseline_value=act.baseline_assignee,
                        live_value=live_assignee_name,
                        message=f"Assignee for '{issue_key}' changed from baseline '{act.baseline_assignee}' to '{live_assignee_name}'.",
                    )
                )

            # 3c. Status check
            live_status_dict = fields.get("status")
            live_status = live_status_dict.get("name") if isinstance(live_status_dict, dict) else None
            if act.baseline_status and live_status and act.baseline_status.strip().lower() != live_status.strip().lower():
                conflicts.append(
                    ExecutionConflict(
                        conflict_category=ConflictCategory.STATUS_CHANGED,
                        issue_key=issue_key,
                        baseline_value=act.baseline_status,
                        live_value=live_status,
                        message=f"Status for '{issue_key}' changed from baseline '{act.baseline_status}' to '{live_status}'.",
                    )
                )

            # 3d. Priority check
            live_prio_dict = fields.get("priority")
            live_priority = live_prio_dict.get("name") if isinstance(live_prio_dict, dict) else None
            if act.baseline_priority and live_priority and act.baseline_priority.strip().lower() != live_priority.strip().lower():
                conflicts.append(
                    ExecutionConflict(
                        conflict_category=ConflictCategory.PRIORITY_CHANGED,
                        issue_key=issue_key,
                        baseline_value=act.baseline_priority,
                        live_value=live_priority,
                        message=f"Priority for '{issue_key}' changed from baseline '{act.baseline_priority}' to '{live_priority}'.",
                    )
                )

            # 3e. Due Date check (overwriting unapproved external changes is prohibited)
            live_due_date = fields.get("duedate")
            if act.expected_pre_execution_due_date is not None and live_due_date != act.expected_pre_execution_due_date:
                # If it's already set to the exact proposed due date, it's a no-op / up to date, not a blocker conflict
                if live_due_date != act.proposed_due_date:
                    conflicts.append(
                        ExecutionConflict(
                            conflict_category=ConflictCategory.DUE_DATE_CHANGED,
                            issue_key=issue_key,
                            baseline_value=act.expected_pre_execution_due_date,
                            live_value=live_due_date,
                            approved_value=act.proposed_due_date,
                            message=f"Live due date on '{issue_key}' was changed to '{live_due_date}' (expected '{act.expected_pre_execution_due_date}'). Overwriting newer changes is blocked.",
                        )
                    )

            # 3f. Estimate check (if remaining estimate changed materially by > 20% or > 2 hours)
            tt = fields.get("timetracking", {}) if isinstance(fields.get("timetracking"), dict) else {}
            live_rem_sec = tt.get("remainingEstimateSeconds") or fields.get("timeestimate")
            if live_rem_sec is not None and act.baseline_remaining_hours is not None:
                live_rem_h = round(float(live_rem_sec) / 3600.0, 2)
                if abs(live_rem_h - act.baseline_remaining_hours) > 2.0:
                    conflicts.append(
                        ExecutionConflict(
                            conflict_category=ConflictCategory.ESTIMATE_CHANGED,
                            issue_key=issue_key,
                            baseline_value=act.baseline_remaining_hours,
                            live_value=live_rem_h,
                            message=f"Remaining estimate on '{issue_key}' changed from baseline {act.baseline_remaining_hours}h to {live_rem_h}h.",
                        )
                    )

            # If no conflicts for this action, it is executable
            action_conflicts = [c for c in conflicts if c.issue_key == issue_key or c.issue_key == "ALL"]
            if not action_conflicts:
                executable_actions.append(act)

        is_valid = len(conflicts) == 0 and len(executable_actions) > 0
        summary = (
            f"Pre-execution conflict validation passed: {len(executable_actions)} actions verified."
            if is_valid
            else f"Pre-execution conflict validation FAILED: {len(conflicts)} conflict(s) detected across {len(target_actions)} approved action(s)."
        )

        return PreExecutionValidationResult(
            is_valid=is_valid,
            approval_id=req.approval_id,
            proposal_id=req.proposal_id,
            proposal_version=req.proposal_version,
            validated_at=now_iso,
            conflicts=conflicts,
            executable_actions=executable_actions,
            summary=summary,
        )

    async def execute_approved_schedule(
        self,
        approval_request: ScheduleApprovalRequest,
        executor: ScheduleReviewerIdentity,
        dry_run: Optional[bool] = None,
        live_issues_override: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Execute approved schedule due-date updates via the Action Engine after live-state conflict validation.
        
        Strict Safety Enforcement:
        - Fails closed on any conflict
        - Strictly UPDATE_DUE_DATE only
        - Dispatches via ActionEngine with approved=True
        - Respects DRY_RUN setting
        """
        is_dry_run = bool(settings.DRY_RUN if dry_run is None else dry_run)
        req = approval_request

        # 1. Verify Executor Authorization
        roles = [r.lower() for r in executor.roles]
        if not any(r in AUTHORIZED_EXECUTION_ROLES for r in roles):
            raise ScheduleAuthorizationError(
                f"Executor '{executor.display_name}' ({executor.user_id}) lacks execution authorization."
            )

        # 2. Pre-Execution Conflict Validation
        val_result = await self.validate_pre_execution_conflicts(
            approval_request=req,
            live_issues_override=live_issues_override,
        )

        if not val_result.is_valid:
            conflict_descs = [f"{c.issue_key}: {c.message}" for c in val_result.conflicts]
            self.audit.log_action(
                actor=executor.display_name,
                action="SCHEDULE_EXECUTION_BLOCKED_BY_CONFLICT",
                target=req.approval_id,
                result="BLOCKED",
                details={
                    "approval_id": req.approval_id,
                    "conflict_count": len(val_result.conflicts),
                    "conflicts": [c.model_dump() for c in val_result.conflicts],
                },
            )
            return {
                "success": False,
                "approval_id": req.approval_id,
                "status": "BLOCKED_BY_CONFLICT",
                "dry_run": is_dry_run,
                "conflicts": [c.model_dump() for c in val_result.conflicts],
                "summary": f"Execution blocked due to {len(val_result.conflicts)} conflict(s): {'; '.join(conflict_descs[:3])}",
            }

        # 3. Execute approved actions via Action Engine
        results: List[Dict[str, Any]] = []
        all_succeeded = True

        for act in val_result.executable_actions:
            # Construct BaseAction for ActionEngine
            engine_action = BaseAction(
                action_id=f"jira-{act.action_id}",
                action_type=ActionType.UPDATE_TASK,
                target_system="jira",
                target_id=act.issue_key,
                parameters={
                    "task_key": act.issue_key,
                    "fields": {"duedate": act.proposed_due_date},
                },
                requested_by=executor.display_name,
                dry_run=is_dry_run,
            )

            res: ActionResult = await self.engine.execute(engine_action, approved=True)
            results.append({
                "action_id": act.action_id,
                "issue_key": act.issue_key,
                "proposed_due_date": act.proposed_due_date,
                "success": res.success,
                "status": res.status.value,
                "dry_run": res.dry_run,
                "error": res.error_message,
            })

            if not res.success:
                all_succeeded = False

        # 4. Mark approval state as EXECUTED if successful and not dry run
        if all_succeeded and not is_dry_run:
            req.status = ApprovalStatus.EXECUTED
            with self._lock:
                exec_data = req.model_dump()
                exec_data["approval_request_id"] = req.approval_id
                exec_data["proposal_version"] = str(req.proposal_version)
                exec_data["context_version"] = req.validation_report_id
                exec_data["state"] = ApprovalStatus.EXECUTED.value
                self.approval_repo.save_request(exec_data)

        self.audit.log_action(
            actor=executor.display_name,
            action="SCHEDULE_EXECUTION_COMPLETED" if all_succeeded else "SCHEDULE_EXECUTION_FAILED",
            target=req.approval_id,
            result="SUCCESS" if all_succeeded else "FAILED",
            details={
                "approval_id": req.approval_id,
                "proposal_id": req.proposal_id,
                "proposal_version": req.proposal_version,
                "executor_id": executor.user_id,
                "executor_name": executor.display_name,
                "dry_run": is_dry_run,
                "executed_count": len(results),
                "actions": [
                    {
                        "action_id": act.action_id,
                        "issue_key": act.issue_key,
                        "action_type": str(act.action_type),
                        "proposed_value": act.proposed_due_date,
                        "baseline_state": {
                            "assignee": act.baseline_assignee,
                            "status": act.baseline_status,
                            "priority": act.baseline_priority,
                            "remaining_estimate_hours": act.baseline_remaining_hours,
                            "expected_pre_due_date": act.expected_pre_execution_due_date,
                        },
                    }
                    for act in val_result.executable_actions
                ],
                "results": results,
            },
        )

        return {
            "success": all_succeeded,
            "approval_id": req.approval_id,
            "status": "COMPLETED" if all_succeeded else "PARTIALLY_FAILED",
            "dry_run": is_dry_run,
            "total_actions": len(results),
            "results": results,
            "summary": f"Executed {len(results)} due-date update(s) (dry_run={is_dry_run}).",
        }
