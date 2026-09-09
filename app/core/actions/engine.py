"""Action Engine router, approval gate, idempotency enforcer, and centralized Dry Run handler."""

import asyncio
from typing import Any, Dict, Optional
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
from app.services.audit_service import audit_service
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


class ActionEngine:
    """Central Action Engine governing all outbound system operations."""

    def __init__(self, manager: Optional[DatabaseManager] = None):
        self.mgr = manager or db_manager
        self.action_repo = ActionRepository(self.mgr)
        self._connectors: Dict[str, BaseConnector] = {}

    def register_connector(self, connector: BaseConnector) -> None:
        """Register a system connector."""
        self._connectors[connector.name.lower()] = connector
        logger.info(f"ActionEngine registered connector: {connector.name} (system: {connector.system_type})")

    def get_connector(self, name: str) -> Optional[BaseConnector]:
        return self._connectors.get(name.lower())

    async def execute(self, action: BaseAction, approved: bool = False) -> ActionResult:
        """Process and execute an action through the entire pipeline.

        Pipeline:
        1. Idempotency Check
        2. Strict User Mapping Resolution
        3. Capability and Security Level Check
        4. Approval Classification Gate
        5. Centralized Dry Run Simulation
        6. Connector Network Execution
        7. Audit Logging
        """
        # Ensure Idempotency Key & Preview
        key = action.ensure_idempotency_key()
        if not action.preview:
            action.preview = approval_engine.generate_preview(action)

        target_system = action.target_system.lower()
        target_id = action.target_id

        # ----------------------------------------------------------------------
        # 1. Idempotency Check
        # ----------------------------------------------------------------------
        existing = self.action_repo.get_by_idempotency_key(key)
        if existing and existing.get("status") in (ActionStatus.COMPLETED.value, ActionStatus.DRY_RUN_SIMULATED.value):
            logger.info(f"Action {action.action_id} already executed with key {key}. Returning cached idempotent result.")
            return ActionResult(
                success=True,
                action_id=action.action_id,
                status=ActionStatus(existing["status"]),
                target_system=target_system,
                target_id=target_id,
                result_data=existing.get("parameters", {}),
                dry_run=bool(existing.get("dry_run", 0))
            )

        # Save initial action record in DB
        action.status = ActionStatus.PENDING_APPROVAL if not approved else ActionStatus.APPROVED
        try:
            self.action_repo.insert(
                action_id=action.action_id,
                action_type=action.action_type.value,
                target_system=target_system,
                target_id=target_id,
                parameters=action.parameters,
                status=action.status.value,
                idempotency_key=key,
                dry_run=settings.DRY_RUN,
                preview=action.preview.model_dump() if action.preview else None
            )
        except Exception:
            pass  # Already inserted or updated

        # ----------------------------------------------------------------------
        # 2. Strict User Mapping Resolution (for Mattermost DMs)
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
                audit_service.log_action(
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
                    # UNRESOLVED: Do NOT send message! Create USER_MAPPING_REQUIRED alert
                    logger.warning(f"User mapping required for Jira user '{display_name or jira_user_id}'. Message will NOT be sent.")
                    action.status = ActionStatus.USER_MAPPING_REQUIRED
                    self.action_repo.update_status(
                        action_id=action.action_id,
                        status=ActionStatus.USER_MAPPING_REQUIRED.value,
                        last_error="No verified Mattermost account mapping exists."
                    )
                    audit_service.log_action(
                        actor=action.requested_by,
                        action=action.action_type.value,
                        target=f"mattermost:{jira_user_id}",
                        result="User Mapping Required",
                        details={"jira_user_id": jira_user_id, "display_name": display_name}
                    )

                    # Send Discord Alert to PM about missing mapping
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
        # 3. Capability and Security Level Check
        # ----------------------------------------------------------------------
        connector = self.get_connector(target_system)
        if not connector:
            err = f"Connector '{target_system}' not found in registry."
            logger.error(err)
            self.action_repo.update_status(action.action_id, ActionStatus.FAILED.value, last_error=err)
            audit_service.log_action(actor=action.requested_by, action=action.action_type.value, target=target_id, result="Failed", details={"error": err})
            return ActionResult(success=False, action_id=action.action_id, status=ActionStatus.FAILED, target_system=target_system, target_id=target_id, error_message=err)

        required_cap = ACTION_TO_CAPABILITY.get(action.action_type)
        if required_cap and not connector.supports_capability(required_cap):
            err = f"Connector '{target_system}' does not support required capability '{required_cap.value}'."
            logger.warning(err)
            self.action_repo.update_status(action.action_id, ActionStatus.ACTION_UNSUPPORTED.value, last_error=err)
            audit_service.log_action(actor=action.requested_by, action=action.action_type.value, target=target_id, result="Action Unsupported", details={"capability": required_cap.value})
            return ActionResult(success=False, action_id=action.action_id, status=ActionStatus.ACTION_UNSUPPORTED, target_system=target_system, target_id=target_id, error_message=err)

        # Destructive check
        if required_cap:
            sec_level = connector.get_security_level_for_capability(required_cap)
            if sec_level == SecurityLevel.DESTRUCTIVE:
                err = f"Destructive action '{action.action_type.value}' is blocked in V0.1."
                logger.error(err)
                self.action_repo.update_status(action.action_id, ActionStatus.REJECTED.value, last_error=err)
                audit_service.log_action(actor=action.requested_by, action=action.action_type.value, target=target_id, result="Blocked", details={"reason": err})
                return ActionResult(success=False, action_id=action.action_id, status=ActionStatus.REJECTED, target_system=target_system, target_id=target_id, error_message=err)

        # ----------------------------------------------------------------------
        # 4. Approval Gating
        # ----------------------------------------------------------------------
        classification = approval_engine.classify(action)
        if classification == ApprovalClassification.BLOCKED:
            err = f"Action '{action.action_type.value}' is classified as BLOCKED."
            logger.error(err)
            self.action_repo.update_status(action.action_id, ActionStatus.REJECTED.value, last_error=err)
            audit_service.log_action(actor=action.requested_by, action=action.action_type.value, target=target_id, result="Blocked", details={"reason": err})
            return ActionResult(success=False, action_id=action.action_id, status=ActionStatus.REJECTED, target_system=target_system, target_id=target_id, error_message=err)

        if classification == ApprovalClassification.APPROVAL_REQUIRED and not approved:
            logger.info(f"Action {action.action_id} ({action.action_type.value}) requires PM approval before execution.")
            self.action_repo.update_status(action.action_id, ActionStatus.PENDING_APPROVAL.value)
            audit_service.log_action(
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

        # ----------------------------------------------------------------------
        # 5. Centralized Dry Run
        # ----------------------------------------------------------------------
        if settings.DRY_RUN:
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

            self.action_repo.update_status(action.action_id, ActionStatus.DRY_RUN_SIMULATED.value)
            audit_service.log_action(
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
        # 6. Real Connector Network Execution
        # ----------------------------------------------------------------------
        self.action_repo.update_status(action.action_id, ActionStatus.EXECUTING.value)
        try:
            result_data = await connector.execute_action(action)
            self.action_repo.update_status(action.action_id, ActionStatus.COMPLETED.value)
            audit_service.log_action(
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
            self.action_repo.update_status(action.action_id, ActionStatus.FAILED.value, last_error=str(e))
            audit_service.log_action(
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
            # Send directly or in dry-run
            if not settings.DRY_RUN:
                await discord_conn.execute_action(alert_action)
            else:
                logger.info(f"[DRY RUN] Would send Discord alert for unmapped user '{jira_user_name}'")
        except Exception as e:
            logger.warning(f"Failed to post unmapped user notification to Discord: {e}")


# Global action engine instance
action_engine = ActionEngine()
