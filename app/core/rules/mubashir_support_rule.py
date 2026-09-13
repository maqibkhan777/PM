"""Workflow validation rule for Mubashir Butt Support ticket creation."""

import re
from typing import Any, Dict, List, Optional
from app.config.settings import settings
from app.core.actions.base import BaseAction
from app.core.actions.types import create_add_comment_action, create_send_notification_action
from app.core.events.base import BaseEvent
from app.core.events.types import TaskCreated
from app.core.rules.base import BaseRule
from app.core.performance.roles import resolve_canonical_account_id
from app.database.repositories import EmployeeRoleRepository, PluginBoardRepository
from app.connectors.discord.formatter import DiscordFormatter, COLOR_ATTENTION
from app.services.notification_deduplication import notification_dedup_service
from app.utils.logger import logger


MUBASHIR_CANONICAL_ACCOUNT_ID = "712020:e268bcd8-d981-4b4d-992d-d5694745df8b"
SUPPORT_ISSUE_TYPES = {"support", "customer support", "support ticket", "helpdesk"}
APPROVED_SPRINT_NAMES = {"support board", "feature request"}


def extract_sprint_names(issue_fields: Dict[str, Any]) -> List[str]:
    """Extract and normalize all assigned sprint names from Jira issue fields.

    Inspects standard Jira Cloud Sprint customfields (customfield_10020, sprint, sprints)
    and serialized sprint representations.
    """
    if not issue_fields or not isinstance(issue_fields, dict):
        return []

    sprint_names: List[str] = []

    def _extract_from_val(val: Any) -> None:
        if not val:
            return
        if isinstance(val, dict):
            name = val.get("name")
            if name and isinstance(name, str) and str(name).strip():
                sprint_names.append(str(name).strip())
        elif isinstance(val, list):
            for item in val:
                _extract_from_val(item)
        elif isinstance(val, str):
            # Serialized sprint string e.g. "com.atlassian.greenhopper...[id=1,name=Support Board,...]"
            m = re.search(r"name=([^,\]]+)", val)
            if m:
                sprint_names.append(m.group(1).strip())
            elif val.strip():
                sprint_names.append(val.strip())

    # 1. Check customfield_10020 (standard Jira Cloud sprint field)
    if "customfield_10020" in issue_fields:
        _extract_from_val(issue_fields["customfield_10020"])

    # 2. Check explicit 'sprint' and 'sprints' fields
    if "sprint" in issue_fields:
        _extract_from_val(issue_fields["sprint"])
    if "sprints" in issue_fields:
        _extract_from_val(issue_fields["sprints"])

    # 3. Check any customfield containing sprint dictionaries
    for k, v in issue_fields.items():
        if k.startswith("customfield_") and k != "customfield_10020":
            if isinstance(v, list) and v and isinstance(v[0], dict) and ("boardId" in v[0] or "state" in v[0]) and "name" in v[0]:
                _extract_from_val(v)

    return list(dict.fromkeys(sprint_names))


def extract_labels(issue_fields: Dict[str, Any]) -> List[str]:
    """Extract product/service labels from Jira issue fields."""
    if not issue_fields or not isinstance(issue_fields, dict):
        return []
    labels = issue_fields.get("labels", [])
    if isinstance(labels, list):
        return [str(l).strip() for l in labels if str(l).strip()]
    return []


class MubashirSupportRule(BaseRule):
    """Automatic quality and workflow check when Mubashir creates a Support ticket:
    
    1. Sprint Requirement: Must belong to 'Support Board' or 'Feature Request' sprint.
    2. Label Requirement: Must have an appropriate product/service label.
    """

    def __init__(self, manager: Optional[Any] = None):
        super().__init__(
            name="MubashirSupportRule",
            description="Enforces sprint and product label requirements on Support tickets created by Mubashir Butt."
        )
        from app.database.connection import db_manager
        self.mgr = manager or db_manager
        self.role_repo = EmployeeRoleRepository(self.mgr)
        self.plugin_repo = PluginBoardRepository(self.mgr)
        from app.services.notification_deduplication import NotificationDeduplicationService
        self.dedup_service = NotificationDeduplicationService(manager=self.mgr)

    def evaluate(self, event: BaseEvent, context: Optional[Dict[str, Any]] = None) -> List[BaseAction]:
        if not self.enabled or not isinstance(event, TaskCreated):
            return []

        # Prevent bootstrap / historical import floods
        if getattr(event, "is_initial_sync", False):
            return []

        task_key = event.task_key or event.task_id or "Unknown"

        # 1. Resolve canonical creator account ID
        creator_id = getattr(event, "creator_id", None) or event.actor_id
        creator_name = getattr(event, "creator_name", None) or event.actor_name
        can_id = resolve_canonical_account_id(creator_id, display_name=creator_name, role_repo=self.role_repo)

        if can_id != MUBASHIR_CANONICAL_ACCOUNT_ID:
            # Check by authoritative display name as secondary check
            assignment = self.role_repo.get_by_display_name(creator_name) if creator_name else None
            if not assignment or assignment.get("account_id") != MUBASHIR_CANONICAL_ACCOUNT_ID:
                return []

        # 2. Check issue type is Support
        issue_type = (getattr(event, "issue_type", "") or "Task").strip().lower()
        if issue_type not in SUPPORT_ISSUE_TYPES:
            return []

        # 3. Inspect issue payload / fields
        raw_payload = getattr(event, "payload", {}) or {}
        issue_data = raw_payload.get("issue", {}) if isinstance(raw_payload, dict) else {}
        fields = issue_data.get("fields", {}) if isinstance(issue_data, dict) else {}

        # SPRINT REQUIREMENT:
        # Check actual assigned Jira sprint names against approved sprint names ("Support Board", "Feature Request")
        sprint_names = extract_sprint_names(fields)
        sprint_passed = any(s.strip().lower() in APPROVED_SPRINT_NAMES for s in sprint_names)

        # LABEL REQUIREMENT:
        # Check if product/service label is present
        labels = extract_labels(fields)
        label_passed = len(labels) > 0

        # If both requirements passed, no action needed
        if sprint_passed and label_passed:
            logger.info(f"Mubashir Support ticket {task_key} satisfied all workflow requirements (sprints={sprint_names}, labels={labels}).")
            return []

        # Deduplication check
        dedup_condition = "MubashirSupportCreationReview"
        if not self.dedup_service.should_notify(
            rule_id=self.name,
            target_id=task_key,
            condition=dedup_condition
        ):
            return []

        # Construct unified reminder feedback
        missing_reasons: List[str] = []
        jira_comment_lines: List[str] = [
            f"[~accountid:{MUBASHIR_CANONICAL_ACCOUNT_ID}] Automated PM Workflow Check for Support Ticket {task_key}:"
        ]

        if not sprint_passed:
            found_str = f" (currently: {', '.join(sprint_names)})" if sprint_names else " (none assigned)"
            missing_reasons.append(f"Missing approved sprint{found_str}")
            jira_comment_lines.append(
                "• **Sprint Requirement:** Please assign this Support ticket to an approved sprint: `Support Board` or `Feature Request`."
            )

        if not label_passed:
            missing_reasons.append("Missing product/service label")
            jira_comment_lines.append(
                "• **Label Requirement:** Please apply the appropriate product/service label to this ticket (e.g. `free`, `pro`, or configured product label)."
            )

        jira_comment_lines.append(
            "\n*This is an automated operational reminder to ensure proper support queue triage and board tracking.*"
        )
        comment_body = "\n".join(jira_comment_lines)

        actions: List[BaseAction] = []

        # 1. Jira Comment Action via ActionEngine
        comment_action = create_add_comment_action(
            target_system="jira",
            task_key=task_key,
            comment_body=comment_body,
            task_title=event.title or "Support Ticket",
            requested_by=self.name
        )
        actions.append(comment_action)

        # 2. Discord Notification (Attention / Yellow)
        jira_url = settings.get_jira_browse_url(task_key)
        issue_link = f"[{task_key}]({jira_url})"
        fields_list = [
            {"name": "Issue", "value": issue_link, "inline": True},
            {"name": "Creator", "value": "Mubashir Butt", "inline": True},
            {"name": "Status", "value": fields.get("status", {}).get("name", "To Do"), "inline": True},
            {"name": "Workflow Requirement(s) Missing", "value": "\n".join([f"• {r}" for r in missing_reasons]), "inline": False},
        ]
        embed_payload = DiscordFormatter.format_embed(
            title=f"⚠️ Support Ticket Workflow Check — {task_key}",
            description=f"Support ticket {issue_link} created by Mubashir Butt requires attention before queue triage.",
            color=COLOR_ATTENTION,
            fields=fields_list,
            url=jira_url
        )

        notif_action = create_send_notification_action(
            target_system="discord",
            channel=settings.PM_DISCORD_CHANNEL,
            title=f"⚠️ Support Ticket Workflow Check — {task_key}",
            message=f"Mubashir Butt created Support ticket {task_key} with missing requirements: {', '.join(missing_reasons)}",
            level="WARNING",
            fields=fields_list,
            requested_by=self.name
        )
        notif_action.parameters["embeds"] = embed_payload.get("embeds")
        actions.append(notif_action)

        # Record deduplication
        notification_dedup_service.record_notification_sent(
            rule_id=self.name,
            target_id=task_key,
            condition=dedup_condition
        )

        logger.info(f"MubashirSupportRule generated {len(actions)} actions for {task_key} ({', '.join(missing_reasons)})")
        return actions
