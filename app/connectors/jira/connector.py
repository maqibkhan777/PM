"""Jira Cloud Connector implementation."""

from typing import Any, Dict, List, Optional, Set
from app.connectors.base.connector import BaseConnector
from app.connectors.jira.client import JiraClient
from app.connectors.jira.normalizer import JiraEventNormalizer
from app.core.models.enums import Capability, TaskStatus, TaskPriority
from app.core.models.domain import HealthStatus, Task
from app.core.events.base import BaseEvent
from app.config.settings import settings
from app.utils.logger import logger


class JiraConnector(BaseConnector):
    """Connector for Jira Cloud REST API integration."""

    def __init__(self, client: Optional[JiraClient] = None):
        super().__init__(name="jira", system_type="issue_tracker")
        self.client = client or JiraClient()
        self.normalizer = JiraEventNormalizer()

    def get_capabilities(self) -> Set[Capability]:
        return {
            Capability.READ_TASK,
            Capability.SEARCH_TASKS,
            Capability.GET_USER,
            Capability.GET_PROJECTS,
            Capability.CREATE_TASK,
            Capability.UPDATE_TASK,
            Capability.ASSIGN_TASK,
            Capability.TRANSITION_TASK,
            Capability.ADD_COMMENT,
            Capability.CHANGE_PRIORITY,
        }

    async def connect(self) -> bool:
        """Validate Jira connectivity."""
        try:
            if not settings.is_jira_configured():
                logger.info("Jira is using placeholder/unconfigured credentials.")
                self._is_connected = False
                return False
            myself = await self.client.get_myself()
            self._is_connected = bool(myself.get("accountId") or myself.get("displayName"))
            logger.info(f"Connected to Jira as: {myself.get('displayName')} ({myself.get('emailAddress')})")
            return self._is_connected
        except Exception as e:
            logger.warning(f"Could not connect to Jira: {e}")
            self._is_connected = False
            return False

    async def disconnect(self) -> None:
        await self.client.close()
        self._is_connected = False

    async def health_check(self) -> HealthStatus:
        if not settings.is_jira_configured():
            return HealthStatus(
                name="Jira",
                status="NOT_CONFIGURED",
                is_connected=False,
                details={"message": "Placeholder credentials in use"}
            )
        try:
            myself = await self.client.get_myself()
            return HealthStatus(
                name="Jira",
                status="OK",
                is_connected=True,
                details={
                    "displayName": myself.get("displayName"),
                    "email": myself.get("emailAddress"),
                    "accountType": myself.get("accountType")
                }
            )
        except Exception as e:
            return HealthStatus(
                name="Jira",
                status="DEGRADED",
                is_connected=False,
                details={"error": str(e)}
            )

    async def handle_incoming_event(
        self,
        raw_payload: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None
    ) -> Optional[BaseEvent]:
        """Normalize incoming Jira webhook."""
        return self.normalizer.normalize(raw_payload)

    async def execute_action(self, action: Any) -> Dict[str, Any]:
        """Execute action via Jira API client."""
        action_type = getattr(action, "action_type", str(action))
        params = getattr(action, "parameters", {})
        target_id = getattr(action, "target_id", "")

        logger.info(f"JiraConnector executing action: {action_type} on target: {target_id}")

        if action_type == "TransitionTask" or action_type == "TRANSITION_TASK":
            transition_id = params.get("transition_id")
            if not transition_id:
                # Attempt to resolve transition_id by status name
                target_status = params.get("status", "")
                available = await self.client.get_transitions(target_id)
                for t in available:
                    if t.get("name", "").lower() == target_status.lower() or t.get("to", {}).get("name", "").lower() == target_status.lower():
                        transition_id = t.get("id")
                        break
            if not transition_id:
                raise ValueError(f"Transition ID or matching status name not found for issue {target_id}")
            return await self.client.transition_issue(target_id, str(transition_id))

        elif action_type == "AssignTask" or action_type == "ASSIGN_TASK":
            account_id = params.get("account_id") or params.get("assignee_id")
            return await self.client.assign_issue(target_id, account_id)

        elif action_type == "AddComment" or action_type == "ADD_COMMENT":
            body = params.get("body") or params.get("comment", "")
            return await self.client.add_comment(target_id, body)

        elif action_type == "ChangePriority" or action_type == "CHANGE_PRIORITY":
            priority = params.get("priority", "Medium")
            return await self.client.update_priority(target_id, priority)

        elif action_type == "UpdateTask" or action_type == "UPDATE_TASK":
            fields = params.get("fields", {})
            return await self.client.update_fields(target_id, fields)

        elif action_type == "CreateTask" or action_type == "CREATE_TASK":
            project_key = params.get("project_key") or target_id
            summary = params.get("summary") or params.get("title", "Untitled")
            issue_type = params.get("issue_type", "Task")
            description = params.get("description")
            return await self.client.create_issue(project_key, summary, issue_type, description)

        else:
            raise NotImplementedError(f"Action '{action_type}' is not supported by JiraConnector")
