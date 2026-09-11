"""Action Engine router, lifecycle manager, validation, approval gate, idempotency enforcer, and centralized Dry Run handler."""

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Set

from app.connectors.base.connector import BaseConnector
from app.connectors.discord.formatter import DiscordFormatter
from app.core.actions.base import BaseAction, ActionResult
from app.core.models.enums import (
    ActionType,
    ActionStatus,
    ApprovalClassification,
    Capability,
    SecurityLevel,
)
from app.core.approvals.engine import approval_engine
from app.services.user_mapping_service import user_mapping_service
from app.services.audit_service import AuditService, audit_service
from app.database.repositories import ActionRepository
from app.database.connection import db_manager, DatabaseManager
from app.config.settings import settings
from app.utils.logger import logger, sanitize_dict

# Map ActionType to required Capability
ACTION_TO_CAPABILITY = {
    ActionType.SEND_MESSAGE: Capability.SEND_DM,
    ActionType.SEND_NOTIFICATION: Capability.SEND_NOTIFICATION,
    ActionType.CREATE_TASK: Capability.CREATE_TASK,
    ActionType.UPDATE_TASK: Capability.UPDATE_TASK,
    ActionType.ASSIGN_TASK: Capability.ASSIGN_TASK,
    ActionType.TRANSITION_TASK: Capability.TRANSITION_TASK,
    ActionType.ADD_COMMENT: Capability.ADD_COMMENT,
    ActionType.CHANGE_PRIORITY: Capability.CHANGE_PRIORITY,
}

# V1 allowlist of fields permitted for UPDATE_TASK
ALLOWED_UPDATE_FIELDS: Set[str] = {
    "summary",
    "description",
    "priority",
    "labels",
    "duedate",
    "due_date",
    "components",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ActionEngine:
    """Central Action Engine governing all outbound system operations."""

    def __init__(self, manager: Optional[DatabaseManager] = None):
        self.mgr = manager or db_manager
        self.action_repo = ActionRepository(self.mgr)
        self.audit_service = AuditService(self.mgr)
        self._connectors: Dict[str, BaseConnector] = {}


    def register_connector(self, connector: BaseConnector) -> None:
        """Register a system connector."""
        self._connectors[connector.name.lower()] = connector
        logger.info(f"ActionEngine registered connector: {connector.name} (system: {connector.system_type})")

    def get_connector(self, name: str) -> Optional[BaseConnector]:
        return self._connectors.get(name.lower())

    def validate_action(self, action: BaseAction) -> Optional[str]:
        """Validate an action before approval or execution.

        Returns None if validation passes, or an error message if it fails.
        """
        # 1. Action type validation
        if not action.action_type or action.action_type not in ACTION_TO_CAPABILITY:
            return f"Action type '{action.action_type}' is not supported in V1."

        # 2. Destructive action prevention
        if action.parameters.get("is_destructive") or "delete" in action.action_type.value.lower():
            return f"Destructive action '{action.action_type.value}' is blocked in V1."

        # 3. Target system & connector check
        target_system = (action.target_system or "").strip().lower()
        if not target_system:
            return "Target system must be specified."
        connector = self.get_connector(target_system)
        if not connector:
            return f"Connector for target system '{target_system}' not found in registry."

        # 4. Capability check
        required_cap = ACTION_TO_CAPABILITY.get(action.action_type)
        if not required_cap:
            return f"Action type '{action.action_type.value}' is not mapped to any capability."
        if not connector.supports_capability(required_cap):
            return f"Connector '{target_system}' does not support required capability '{required_cap.value}'."

        # 5. Security level check
        sec_level = connector.get_security_level_for_capability(required_cap)
        if sec_level == SecurityLevel.DESTRUCTIVE:
            return f"Action capability '{required_cap.value}' is classified as DESTRUCTIVE and blocked in V1."

        # 6. Idempotency key present
        if not action.idempotency_key:
            action.ensure_idempotency_key()
        if not action.idempotency_key:
            return "Failed to generate idempotency key for action."

        # 7. Action-specific parameter constraints
        params = action.parameters or {}
        target_id = action.target_id

        if action.action_type == ActionType.SEND_MESSAGE:
            text = params.get("text") or params.get("message")
            target = params.get("recipient_id") or params.get("recipient") or params.get("channel") or target_id
            if not text or not str(text).strip():
                return "Message text cannot be empty for SEND_MESSAGE."
            if not target or not str(target).strip():
                return "Recipient or channel must be specified for SEND_MESSAGE."

        elif action.action_type == ActionType.SEND_NOTIFICATION:
            title = params.get("title")
            message = params.get("message") or params.get("text")
            embeds = params.get("embeds")
            if not embeds and not title and not message:
                return "Notification must contain at least a title, message, or embeds for SEND_NOTIFICATION."

        elif action.action_type == ActionType.CREATE_TASK:
            project_key = params.get("project_key") or target_id
            summary = params.get("summary") or params.get("title")
            if not project_key or not str(project_key).strip():
                return "Project key is required for CREATE_TASK."
            if not summary or not str(summary).strip():
                return "Summary is required for CREATE_TASK."

        elif action.action_type == ActionType.UPDATE_TASK:
            task_key = params.get("task_key") or target_id
            if not task_key or not str(task_key).strip():
                return "Task key is required for UPDATE_TASK."
            fields = params.get("fields")
            if not isinstance(fields, dict) or not fields:
                return "Fields dictionary is required for UPDATE_TASK."
            disallowed = [f for f in fields.keys() if f.lower() not in ALLOWED_UPDATE_FIELDS]
            if disallowed:
                return f"Fields {disallowed} are not permitted for update in V1. Allowed: {sorted(list(ALLOWED_UPDATE_FIELDS))}."

        elif action.action_type == ActionType.ASSIGN_TASK:
            task_key = params.get("task_key") or target_id
            assignee = params.get("assignee") or params.get("account_id") or params.get("assignee_id")
            if not task_key or not str(task_key).strip():
                return "Task key is required for ASSIGN_TASK."
            if not assignee or not str(assignee).strip():
                return "Valid Jira account ID is required for ASSIGN_TASK. User guessing or fuzzy names are prohibited."

        elif action.action_type == ActionType.TRANSITION_TASK:
            task_key = params.get("task_key") or target_id
            target_status = params.get("target_status") or params.get("status") or params.get("transition_id")
            if not task_key or not str(task_key).strip():
                return "Task key is required for TRANSITION_TASK."
            if not target_status or not str(target_status).strip():
                return "Target status or transition ID is required for TRANSITION_TASK."

        elif action.action_type == ActionType.ADD_COMMENT:
            task_key = params.get("task_key") or target_id
            comment = params.get("comment") or params.get("body")
            if not task_key or not str(task_key).strip():
                return "Task key is required for ADD_COMMENT."
            if not comment or not str(comment).strip():
                return "Comment body cannot be empty for ADD_COMMENT."
            if len(str(comment)) > 30000:
                return "Comment body exceeds maximum allowed length (30,000 characters)."

        elif action.action_type == ActionType.CHANGE_PRIORITY:
            task_key = params.get("task_key") or target_id
            priority = params.get("priority")
            if not task_key or not str(task_key).strip():
                return "Task key is required for CHANGE_PRIORITY."
            if not priority or not str(priority).strip():
                return "Priority is required for CHANGE_PRIORITY."

        return None

    async def execute(self, action: BaseAction, approved: bool = False) -> ActionResult:
        """Process and execute an action through the entire pipeline.

        Pipeline:
        1. Idempotency Check
        2. Policy & Initial Persistence (REQUESTED)
        3. Action Validation (VALIDATED or FAILED)
        4. User Mapping Resolution (for Mattermost)
        5. Security & Classification Check
        6. Approval Gating (PENDING_APPROVAL or APPROVED)
        7. Centralized Dry Run (DRY_RUN_SIMULATED)
        8. Real Connector Execution (EXECUTING -> COMPLETED or FAILED)
        9. Audit Logging
        """
        # Ensure Idempotency Key & Preview
        key = action.ensure_idempotency_key()
        if not action.preview:
            action.preview = approval_engine.generate_preview(action)

        target_system = (action.target_system or "").lower()
        target_id = action.target_id

        # ----------------------------------------------------------------------
        # 1. Idempotency Check
        # ----------------------------------------------------------------------
        existing = self.action_repo.get_by_idempotency_key(key)
        is_current_dry_run = bool(settings.DRY_RUN or action.dry_run)

        if existing:
            existing_status = existing.get("status")
            if existing_status == ActionStatus.COMPLETED.value or (
                existing_status == ActionStatus.DRY_RUN_SIMULATED.value and is_current_dry_run
            ):
                logger.info(f"Action {action.action_id} already executed with key {key}. Returning cached idempotent result.")
                self.audit_service.log_action(
                    actor=action.requested_by,
                    action=action.action_type.value,
                    target=target_id,
                    result="Idempotent Duplicate",
                    details={"idempotency_key": key, "status": existing_status}
                )
                return ActionResult(
                    success=True,
                    action_id=existing.get("action_id", action.action_id),
                    status=ActionStatus(existing_status),
                    target_system=target_system,
                    target_id=target_id,
                    result_data=existing.get("result_data") or existing.get("parameters", {}),
                    dry_run=bool(existing.get("dry_run", 0))
                )
            elif existing_status == ActionStatus.DRY_RUN_SIMULATED.value and not is_current_dry_run:
                # Upgrading previously dry-run simulated action to real execution
                logger.info(f"Action with key {key} was previously DRY_RUN_SIMULATED. Upgrading to live execution.")
                action.action_id = existing.get("action_id", action.action_id)
                self.action_repo.update_status(action.action_id, ActionStatus.REQUESTED.value, increment_attempt=False)


        # ----------------------------------------------------------------------
        # 2. Centrally Determine Approval Policy & Record REQUESTED
        # ----------------------------------------------------------------------
        classification = approval_engine.classify(action)
        action.requires_approval = (classification == ApprovalClassification.APPROVAL_REQUIRED)
        action.status = ActionStatus.REQUESTED

        existing_by_id = self.action_repo.get_by_action_id(action.action_id)
        if not existing_by_id:
            try:
                self.action_repo.insert(
                    action_id=action.action_id,
                    action_type=action.action_type.value,
                    target_system=target_system,
                    target_id=target_id,
                    parameters=action.parameters,
                    status=action.status.value,
                    idempotency_key=key,
                    dry_run=settings.DRY_RUN or action.dry_run,
                    preview=action.preview.model_dump() if action.preview else None,
                    requested_by=action.requested_by,
                    requires_approval=action.requires_approval
                )
            except Exception:
                pass  # Already inserted

            self.audit_service.log_action(
                actor=action.requested_by,
                action=action.action_type.value,
                target=target_id,
                result="Requested",
                details={"action_id": action.action_id, "requires_approval": action.requires_approval}
            )


        # ----------------------------------------------------------------------
        # 3. Action Validation
        # ----------------------------------------------------------------------
        val_error = self.validate_action(action)
        if val_error:
            logger.warning(f"Action {action.action_id} validation failed: {val_error}")
            if "does not support required capability" in val_error:
                action.status = ActionStatus.ACTION_UNSUPPORTED
                self.action_repo.update_status(action.action_id, ActionStatus.ACTION_UNSUPPORTED.value, last_error=val_error)
                self.audit_service.log_action(
                    actor=action.requested_by,
                    action=action.action_type.value,
                    target=target_id,
                    result="Action Unsupported",
                    details={"error": val_error}
                )
                return ActionResult(
                    success=False,
                    action_id=action.action_id,
                    status=ActionStatus.ACTION_UNSUPPORTED,
                    target_system=target_system,
                    target_id=target_id,
                    error_message=val_error
                )

            action.status = ActionStatus.FAILED
            action.validation_error = val_error
            self.action_repo.update_status(action.action_id, ActionStatus.FAILED.value, last_error=val_error)
            self.audit_service.log_action(
                actor=action.requested_by,
                action=action.action_type.value,
                target=target_id,
                result="Validation Failed",
                details={"error": val_error}
            )
            return ActionResult(
                success=False,
                action_id=action.action_id,
                status=ActionStatus.FAILED,
                target_system=target_system,
                target_id=target_id,
                error_message=val_error
            )

        # Transition to VALIDATED (only update and audit if not already approved via approve_action)
        if not (approved and action.status == ActionStatus.APPROVED):
            action.status = ActionStatus.VALIDATED
            self.action_repo.update_status(action.action_id, ActionStatus.VALIDATED.value, increment_attempt=False)
            self.audit_service.log_action(
                actor=action.requested_by,
                action=action.action_type.value,
                target=target_id,
                result="Validated",
                details={"action_id": action.action_id}
            )

        # ----------------------------------------------------------------------
        # 4. Strict User Mapping Resolution (for Mattermost DMs)
        # ----------------------------------------------------------------------
        if target_system == "mattermost":
            mm_connector = self.get_connector("mattermost")
            is_configured = getattr(mm_connector, "is_configured", False) if mm_connector else settings.is_mattermost_configured()
            if not is_configured:
                logger.info(f"Mattermost is not configured; skipping action {action.action_id} ({action.action_type.value}).")
                action.status = ActionStatus.SKIPPED
                self.action_repo.update_status(
                    action_id=action.action_id,
                    status=ActionStatus.SKIPPED.value,
                    last_error="connector_not_configured"
                )
                self.audit_service.log_action(
                    actor=action.requested_by,
                    action=action.action_type.value,
                    target=f"mattermost:{target_id}",
                    result="Skipped",
                    details={"reason": "connector_not_configured"}
                )
                return ActionResult(
                    success=True,
                    action_id=action.action_id,
                    status=ActionStatus.SKIPPED,
                    target_system=target_system,
                    target_id=target_id,
                    result_data={"skipped": True, "reason": "connector_not_configured"},
                    error_message="connector_not_configured"
                )

            if action.action_type == ActionType.SEND_MESSAGE:
                recipient_id = action.parameters.get("recipient_id") or target_id
                jira_user_id = action.parameters.get("jira_user_id") or recipient_id
                display_name = action.parameters.get("display_name") or action.parameters.get("recipient_name")

                resolved_mm_id, method = user_mapping_service.resolve_jira_to_mattermost(
                    jira_user_id=jira_user_id,
                    display_name=display_name
                )

                if not resolved_mm_id:
                    logger.warning(f"User mapping required for Jira user '{display_name or jira_user_id}'. Message will NOT be sent.")
                    action.status = ActionStatus.USER_MAPPING_REQUIRED
                    self.action_repo.update_status(
                        action_id=action.action_id,
                        status=ActionStatus.USER_MAPPING_REQUIRED.value,
                        last_error="No verified Mattermost account mapping exists."
                    )
                    self.audit_service.log_action(
                        actor=action.requested_by,
                        action=action.action_type.value,
                        target=f"mattermost:{jira_user_id}",
                        result="User Mapping Required",
                        details={"jira_user_id": jira_user_id, "display_name": display_name}
                    )
                    await self._notify_pm_unmapped_user(jira_user_name=display_name or jira_user_id, jira_user_id=jira_user_id)
                    return ActionResult(
                        success=False,
                        action_id=action.action_id,
                        status=ActionStatus.USER_MAPPING_REQUIRED,
                        target_system=target_system,
                        target_id=target_id,
                        error_message="User mapping required: no verified Mattermost account found."
                    )
                else:
                    action.parameters["recipient_id"] = resolved_mm_id
                    action.parameters["resolved_via"] = method

        # ----------------------------------------------------------------------
        # 5. Security & Classification Gate
        # ----------------------------------------------------------------------
        if classification == ApprovalClassification.BLOCKED:
            err = f"Action '{action.action_type.value}' is classified as BLOCKED."
            logger.error(err)
            action.status = ActionStatus.REJECTED
            self.action_repo.update_status(action.action_id, ActionStatus.REJECTED.value, last_error=err)
            self.audit_service.log_action(actor=action.requested_by, action=action.action_type.value, target=target_id, result="Blocked", details={"reason": err})
            return ActionResult(success=False, action_id=action.action_id, status=ActionStatus.REJECTED, target_system=target_system, target_id=target_id, error_message=err)

        # ----------------------------------------------------------------------
        # 6. Approval Gating
        # ----------------------------------------------------------------------
        if action.requires_approval and not approved:
            logger.info(f"Action {action.action_id} ({action.action_type.value}) requires PM approval before execution.")
            action.status = ActionStatus.PENDING_APPROVAL
            self.action_repo.update_status(action.action_id, ActionStatus.PENDING_APPROVAL.value, increment_attempt=False)
            self.audit_service.log_action(
                actor=action.requested_by,
                action=action.action_type.value,
                target=target_id,
                result="Pending Approval",
                details=action.preview.model_dump() if action.preview else None
            )
            return ActionResult(
                success=True,
                action_id=action.action_id,
                status=ActionStatus.PENDING_APPROVAL,
                target_system=target_system,
                target_id=target_id,
                result_data={"preview": action.preview.model_dump() if action.preview else {}}
            )

        if approved and action.requires_approval:
            action.status = ActionStatus.APPROVED
            # Update DB and audit if not already recorded by approve_action
            existing_rec = self.action_repo.get_by_action_id(action.action_id)
            if not existing_rec or existing_rec.get("status") != ActionStatus.APPROVED.value:
                self.action_repo.update_approval(
                    action.action_id,
                    approved_by=action.approved_by or "PM",
                    approved_at=action.approved_at or _utc_now_iso()
                )
                self.audit_service.log_action(
                    actor=action.approved_by or "PM",
                    action=action.action_type.value,
                    target=target_id,
                    result="Approved",
                    details={"approved_by": action.approved_by or "PM"}
                )

        # ----------------------------------------------------------------------
        # 7. Centralized Dry Run
        # ----------------------------------------------------------------------
        if settings.DRY_RUN or action.dry_run:
            summary = action.preview.summary if action.preview else f"{action.action_type.value} on {target_system}"
            dry_run_banner = (
                "\n" + "=" * 60 + "\n"
                "[DRY RUN SIMULATION]\n"
                f"Action:       {action.action_type.value}\n"
                f"Target:       {target_system.upper()} / {target_id}\n"
                f"Summary:      {summary}\n"
                f"Requested By: {action.requested_by}\n"
                "No external changes were made.\n"
                + "=" * 60 + "\n"
            )
            logger.info(dry_run_banner)

            action.status = ActionStatus.DRY_RUN_SIMULATED
            self.action_repo.update_result(
                action.action_id,
                ActionStatus.DRY_RUN_SIMULATED.value,
                result_data={"simulated": True, "summary": summary}
            )
            self.audit_service.log_action(
                actor=action.requested_by,
                action=action.action_type.value,
                target=target_id,
                result="Dry Run Simulated",
                details={"preview": summary, "parameters": action.parameters}
            )

            return ActionResult(
                success=True,
                action_id=action.action_id,
                status=ActionStatus.DRY_RUN_SIMULATED,
                target_system=target_system,
                target_id=target_id,
                result_data={"simulated": True, "summary": summary},
                dry_run=True
            )

        # ----------------------------------------------------------------------
        # 8. Real Connector Network Execution
        # ----------------------------------------------------------------------
        action.status = ActionStatus.EXECUTING
        self.action_repo.update_status(action.action_id, ActionStatus.EXECUTING.value)
        self.audit_service.log_action(
            actor=action.requested_by,
            action=action.action_type.value,
            target=target_id,
            result="Executing",
            details={"target": target_id}
        )

        connector = self.get_connector(target_system)
        try:
            result_data = await connector.execute_action(action)
            action.status = ActionStatus.COMPLETED
            action.result_data = result_data
            self.action_repo.update_result(
                action.action_id,
                ActionStatus.COMPLETED.value,
                result_data=result_data
            )
            self.audit_service.log_action(
                actor=action.requested_by,
                action=action.action_type.value,
                target=target_id,
                result="Success",
                details=result_data
            )
            return ActionResult(
                success=True,
                action_id=action.action_id,
                status=ActionStatus.COMPLETED,
                target_system=target_system,
                target_id=target_id,
                result_data=result_data,
                dry_run=False
            )
        except Exception as e:
            err = f"Execution failed on connector '{target_system}': {e}"
            logger.error(err, exc_info=True)
            action.status = ActionStatus.FAILED
            action.execution_error = str(e)
            self.action_repo.update_status(action.action_id, ActionStatus.FAILED.value, last_error=str(e))
            self.audit_service.log_action(
                actor=action.requested_by,
                action=action.action_type.value,
                target=target_id,
                result="Failed",
                details={"error": str(e)}
            )
            return ActionResult(
                success=False,
                action_id=action.action_id,
                status=ActionStatus.FAILED,
                target_system=target_system,
                target_id=target_id,
                error_message=str(e),
                dry_run=False
            )

    async def approve_action(self, action_id: str, approved_by: str = "PM") -> ActionResult:
        """Approve a pending action and execute it through the Action Engine."""
        action_rec = self.action_repo.get_by_action_id(action_id)
        if not action_rec:
            return ActionResult(
                success=False,
                action_id=action_id,
                status=ActionStatus.FAILED,
                target_system="",
                target_id="",
                error_message=f"Action '{action_id}' not found."
            )

        current_status = action_rec.get("status")
        if current_status != ActionStatus.PENDING_APPROVAL.value:
            return ActionResult(
                success=False,
                action_id=action_id,
                status=ActionStatus(current_status) if current_status in ActionStatus._value2member_map_ else ActionStatus.FAILED,
                target_system=action_rec.get("target_system", ""),
                target_id=action_rec.get("target_id", ""),
                error_message=f"Action '{action_id}' has status '{current_status}'. Only PENDING_APPROVAL actions can be approved."
            )

        # Mark APPROVED in database
        now_iso = _utc_now_iso()
        self.action_repo.update_approval(
            action_id=action_id,
            approved_by=approved_by,
            approved_at=now_iso,
            status=ActionStatus.APPROVED.value
        )
        self.audit_service.log_action(
            actor=approved_by,
            action=action_rec["action_type"],
            target=action_rec["target_id"],
            result="Approved",
            details={"approved_by": approved_by, "approved_at": now_iso}
        )

        # Reconstruct BaseAction
        action_type = ActionType(action_rec["action_type"])
        act = BaseAction(
            action_id=action_rec["action_id"],
            idempotency_key=action_rec.get("idempotency_key"),
            action_type=action_type,
            target_system=action_rec["target_system"],
            target_id=action_rec["target_id"],
            parameters=action_rec.get("parameters", {}),
            requested_by=action_rec.get("requested_by") or "PM",
            approved_by=approved_by,
            approved_at=now_iso,
            requires_approval=True,
            status=ActionStatus.APPROVED,
            dry_run=bool(action_rec.get("dry_run", 0))
        )

        return await self.execute(act, approved=True)

    async def reject_action(
        self,
        action_id: str,
        rejected_by: str = "PM",
        reason: Optional[str] = None
    ) -> ActionResult:
        """Reject a pending action and prevent future execution."""
        action_rec = self.action_repo.get_by_action_id(action_id)
        if not action_rec:
            return ActionResult(
                success=False,
                action_id=action_id,
                status=ActionStatus.FAILED,
                target_system="",
                target_id="",
                error_message=f"Action '{action_id}' not found."
            )

        current_status = action_rec.get("status")
        if current_status != ActionStatus.PENDING_APPROVAL.value:
            return ActionResult(
                success=False,
                action_id=action_id,
                status=ActionStatus(current_status) if current_status in ActionStatus._value2member_map_ else ActionStatus.FAILED,
                target_system=action_rec.get("target_system", ""),
                target_id=action_rec.get("target_id", ""),
                error_message=f"Action '{action_id}' has status '{current_status}'. Only PENDING_APPROVAL actions can be rejected."
            )

        now_iso = _utc_now_iso()
        self.action_repo.update_rejection(
            action_id=action_id,
            rejected_by=rejected_by,
            rejection_reason=reason,
            rejected_at=now_iso,
            status=ActionStatus.REJECTED.value
        )
        self.audit_service.log_action(
            actor=rejected_by,
            action=action_rec["action_type"],
            target=action_rec["target_id"],
            result="Rejected",
            details={"rejected_by": rejected_by, "reason": reason, "rejected_at": now_iso}
        )

        return ActionResult(
            success=True,
            action_id=action_id,
            status=ActionStatus.REJECTED,
            target_system=action_rec.get("target_system", ""),
            target_id=action_rec.get("target_id", ""),
            result_data={"rejected_by": rejected_by, "reason": reason}
        )

    async def _notify_pm_unmapped_user(self, jira_user_name: str, jira_user_id: str) -> None:
        """Send Discord alert notifying PM of an unmapped user."""
        discord_conn = self.get_connector("discord")
        if not discord_conn:
            return
        embed_payload = DiscordFormatter.format_user_mapping_required(
            jira_user_name=jira_user_name,
            jira_user_id=jira_user_id,
            intended_action="Send Stale Task Mattermost Direct Message"
        )
        try:
            alert_action = BaseAction(
                action_type=ActionType.SEND_NOTIFICATION,
                target_system="discord",
                target_id=settings.PM_DISCORD_CHANNEL,
                parameters={"embeds": embed_payload.get("embeds")},
                requested_by="UserMappingService"
            )
            if not settings.DRY_RUN:
                await discord_conn.execute_action(alert_action)
            else:
                logger.info(f"[DRY RUN] Would send Discord alert for unmapped user '{jira_user_name}'")
        except Exception as e:
            logger.warning(f"Failed to post unmapped user notification to Discord: {e}")


# Global action engine instance
action_engine = ActionEngine()
