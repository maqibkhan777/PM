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
            myself = await self.client.get_myself(force_refresh=True)
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
        from app.database.repositories import JiraPollingStateRepository
        checkpoint = JiraPollingStateRepository().get_checkpoint("jira")

        if not settings.is_jira_configured():
            return HealthStatus(
                name="Jira",
                status="NOT_CONFIGURED",
                is_connected=False,
                details={
                    "status": "not_configured",
                    "polling_enabled": settings.JIRA_POLLING_ENABLED,
                    "polling_status": "not_configured",
                    "last_poll_success": checkpoint,
                    "message": "Jira credentials not configured"
                }
            )
        try:
            myself = await self.client.get_myself()
            return HealthStatus(
                name="Jira",
                status="OK",
                is_connected=True,
                details={
                    "status": "connected",
                    "displayName": myself.get("displayName"),
                    "email": myself.get("emailAddress"),
                    "accountType": myself.get("accountType"),
                    "polling_enabled": settings.JIRA_POLLING_ENABLED,
                    "polling_status": "active" if settings.JIRA_POLLING_ENABLED else "disabled",
                    "last_poll_success": checkpoint,
                    "team_group": settings.JIRA_TEAM_GROUP,
                    "team_group_scoped": settings.is_jira_team_group_configured()
                }
            )
        except Exception as e:
            return HealthStatus(
                name="Jira",
                status="DEGRADED",
                is_connected=False,
                details={
                    "status": "degraded",
                    "polling_enabled": settings.JIRA_POLLING_ENABLED,
                    "polling_status": "degraded",
                    "last_poll_success": checkpoint,
                    "error": str(e)
                }
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
        raw_action_type = getattr(action, "action_type", str(action))
        action_type = raw_action_type.value if hasattr(raw_action_type, "value") else str(raw_action_type)
        params = getattr(action, "parameters", {})
        target_id = getattr(action, "target_id", "")

        logger.info(f"JiraConnector executing action: {action_type} on target: {target_id}")

        if action_type in ("TransitionTask", "TRANSITION_TASK"):
            transition_id = params.get("transition_id")
            if not transition_id:
                target_status = params.get("target_status") or params.get("status", "")
                if not target_status or not str(target_status).strip():
                    raise ValueError(f"Target status is required to transition issue {target_id}")
                available = await self.client.get_transitions(target_id)
                target_lower = str(target_status).strip().lower()
                matching = [
                    t for t in available
                    if t.get("name", "").strip().lower() == target_lower
                    or t.get("to", {}).get("name", "").strip().lower() == target_lower
                ]
                if len(matching) == 1:
                    transition_id = matching[0].get("id")
                elif len(matching) > 1:
                    matching_names = [m.get("name") for m in matching]
                    raise ValueError(
                        f"Ambiguous transition for status '{target_status}' on issue {target_id}. "
                        f"Multiple matching transitions found: {matching_names}"
                    )
                else:
                    avail_names = [t.get("name") or t.get("to", {}).get("name") for t in available]
                    raise ValueError(
                        f"Transition for status '{target_status}' not found on issue {target_id}. "
                        f"Available transitions: {avail_names}"
                    )

            if not transition_id:
                raise ValueError(f"Transition ID or matching status name not found for issue {target_id}")
            return await self.client.transition_issue(target_id, str(transition_id))

        elif action_type in ("AssignTask", "ASSIGN_TASK"):
            account_id = params.get("account_id") or params.get("assignee") or params.get("assignee_id")
            if not account_id or not str(account_id).strip():
                raise ValueError(f"Valid Jira account ID is required for task assignment on {target_id}")
            return await self.client.assign_issue(target_id, str(account_id).strip())

        elif action_type in ("AddComment", "ADD_COMMENT"):
            body = params.get("body") or params.get("comment", "")
            if not body or not str(body).strip():
                raise ValueError(f"Comment body cannot be empty for issue {target_id}")
            return await self.client.add_comment(target_id, str(body).strip())

        elif action_type in ("ChangePriority", "CHANGE_PRIORITY"):
            priority = params.get("priority", "Medium")
            return await self.client.update_priority(target_id, str(priority))

        elif action_type in ("UpdateTask", "UPDATE_TASK"):
            fields = params.get("fields", {})
            if not isinstance(fields, dict) or not fields:
                raise ValueError(f"Fields dictionary is required to update issue {target_id}")
            return await self.client.update_fields(target_id, fields)

        elif action_type in ("CreateTask", "CREATE_TASK"):
            project_key = params.get("project_key") or target_id
            summary = params.get("summary") or params.get("title", "Untitled")
            issue_type = params.get("issue_type", "Task")
            description = params.get("description")
            assignee = params.get("assignee") or params.get("account_id")
            priority = params.get("priority")
            labels = params.get("labels")
            return await self.client.create_issue(
                project_key=project_key,
                summary=summary,
                issue_type=issue_type,
                description=description,
                assignee=assignee,
                priority=priority,
                labels=labels,
            )


        else:
            raise NotImplementedError(f"Action '{action_type}' is not supported by JiraConnector")

