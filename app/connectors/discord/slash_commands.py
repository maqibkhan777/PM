"""Discord Slash Command interaction parser and handler for PM Agent."""

import asyncio
from typing import Any, Dict, List, Optional, Tuple
from app.config.settings import settings
from app.core.actions.types import (
    create_send_message_action,
    create_send_notification_action,
    create_create_task_action,
    create_update_task_action,
    create_assign_task_action,
    create_transition_task_action,
    create_add_comment_action,
)
from app.core.models.enums import ActionStatus
from app.services.user_identity_service import user_identity_service
from app.database.repositories import UserRepository
from app.utils.logger import logger

# Discord Interaction Response Types
INTERACTION_RESPONSE_TYPE_PONG = 1
INTERACTION_RESPONSE_TYPE_CHANNEL_MESSAGE = 4
INTERACTION_RESPONSE_TYPE_DEFERRED_CHANNEL_MESSAGE = 5

ALLOWED_UPDATE_FIELDS = {
    "summary",
    "description",
    "priority",
    "labels",
    "duedate",
    "due_date",
    "components",
}

PM_HELP_TEXT = """**PM Commands**

`/pm status <ticket>`
`/pm transition <ticket> <status>`
`/pm assign <ticket> <user>`
`/pm comment <ticket> <comment>`
`/pm create <project> <summary>`
`/pm update <ticket> <field> <value>`
`/pm notify <user> <message>`
`/pm message <user> <message>`"""


class DiscordSlashCommandHandler:
    """Handles parsing, authorization, execution, and response formatting for Discord slash commands."""

    def __init__(self, user_repo: Optional[UserRepository] = None, action_engine: Optional[Any] = None):
        self.user_repo = user_repo or UserRepository()
        self._action_engine = action_engine

    def get_action_engine(self) -> Any:
        if self._action_engine is not None:
            return self._action_engine
        from app.core.actions.engine import action_engine
        return action_engine

    async def resolve_jira_user(self, user_query: str, jira_client: Optional[Any] = None) -> Tuple[Optional[str], Optional[str]]:
        """Safely resolve a Jira user input to (account_id, display_name) without fuzzy guessing.
        
        Returns:
            Tuple of (account_id, display_name) or (None, None) if unresolved.
        """
        if not user_query or not user_query.strip():
            return None, None

        q = user_query.strip()

        # 1. Direct Atlassian Account ID (contains ':' or standard 24+ char hex/uuid format)
        if ":" in q or (len(q) >= 24 and " " not in q and "-" in q):
            return q, q

        # 2. Check active PM identity ("me" or matching display name / email)
        if q.lower() == "me" or user_identity_service.is_me(display_name=q, email=q):
            ident = await user_identity_service.get_my_identity(client=jira_client)
            if ident.get("account_id"):
                return ident["account_id"], ident.get("display_name") or q

        # 3. Exact match in local UserRepository projection
        users = self.user_repo.find_by_name(display_name=q, external_system="jira")
        if not users:
            users = self.user_repo.find_by_email(email=q, external_system="jira")
        if len(users) == 1:
            return users[0]["external_user_id"], users[0].get("display_name") or q

        # 4. Query Jira API for exact or unambiguous match if configured
        try:
            if not jira_client:
                jira_conn = self.get_action_engine().get_connector("jira")
                jira_client = getattr(jira_conn, "client", None)

            if jira_client and settings.is_jira_configured():
                matched_users = await jira_client.get_users(query=q)
                if isinstance(matched_users, list):
                    # Filter active users with exact display name, email, or single exact query match
                    exact_matches = [
                        u for u in matched_users
                        if u.get("active", True) and (
                            u.get("displayName", "").strip().lower() == q.lower()
                            or u.get("emailAddress", "").strip().lower() == q.lower()
                            or u.get("accountId") == q
                        )
                    ]
                    if len(exact_matches) == 1:
                        return exact_matches[0]["accountId"], exact_matches[0].get("displayName") or q
                    elif len(matched_users) == 1 and matched_users[0].get("active", True):
                        return matched_users[0]["accountId"], matched_users[0].get("displayName") or q
        except Exception as e:
            logger.warning(f"Error querying Jira users during user resolution for '{q}': {e}")

        # Unresolved - never fuzzy guess
        return None, None

    async def handle_status_command(self, ticket: str, jira_connector: Optional[Any] = None) -> str:
        """Handle read-only /pm status command."""
        if not ticket or not ticket.strip():
            return "❌ Ticket key is required. Example: `/pm status WSSS-326`"

        clean_ticket = ticket.strip().upper()
        connector = jira_connector or self.get_action_engine().get_connector("jira")
        if not connector or not hasattr(connector, "client"):
            return f"❌ Jira connector is unavailable."

        try:
            issue_data = await connector.client.get_issue(clean_ticket)
            if not issue_data or not isinstance(issue_data, dict) or "fields" not in issue_data:
                return f"❌ Ticket {clean_ticket} not found."

            fields = issue_data.get("fields", {})
            summary = fields.get("summary", "No summary")
            status_name = fields.get("status", {}).get("name", "Unknown")
            assignee = fields.get("assignee", {})
            assignee_name = assignee.get("displayName", "Unassigned") if assignee else "Unassigned"
            priority = fields.get("priority", {})
            priority_name = priority.get("name", "None") if priority else "None"
            due_date = fields.get("duedate") or "None"

            return (
                f"📋 **{clean_ticket}**: {summary}\n\n"
                f"**Status:** {status_name}\n"
                f"**Assignee:** {assignee_name}\n"
                f"**Priority:** {priority_name}\n"
                f"**Due:** {due_date}"
            )
        except Exception as e:
            err_str = str(e)
            if "404" in err_str:
                return f"❌ Ticket {clean_ticket} not found."
            logger.error(f"Failed to fetch ticket status for {clean_ticket}: {e}")
            return f"❌ Could not retrieve status for {clean_ticket}.\nReason: {err_str}"

    async def execute_subcommand(
        self,
        subcommand: str,
        options: Dict[str, Any],
        discord_user_id: str,
        channel_id: Optional[str] = None
    ) -> str:
        """Execute a parsed /pm subcommand with strict authorization and structured ActionEngine execution."""
        # 1. Authorization check
        if not settings.is_discord_user_allowed(discord_user_id):
            logger.warning(f"Unauthorized Discord user '{discord_user_id}' attempted /pm {subcommand}")
            return "❌ You are not authorized to use PM commands."

        sub = (subcommand or "").strip().lower()
        actor = f"discord:{discord_user_id}"
        engine = self.get_action_engine()

        # 2. Help command
        if sub in ("help", ""):
            return PM_HELP_TEXT

        # 3. Read-only Status command (does NOT create an Action)
        if sub == "status":
            ticket = options.get("ticket") or options.get("task_key") or options.get("issue_key", "")
            return await self.handle_status_command(ticket=ticket)

        # 4. Mutation subcommands -> pass exclusively through ActionEngine
        if sub == "transition":
            ticket = (options.get("ticket") or options.get("task_key", "")).strip().upper()
            target_status = options.get("status") or options.get("target_status", "")
            if not ticket or not target_status:
                return "❌ Both `ticket` and `status` are required. Example: `/pm transition WSSS-326 Done`"

            action = create_transition_task_action(
                target_system="jira",
                task_key=ticket,
                target_status=str(target_status).strip(),
                requested_by=actor
            )
            res = await engine.execute(action)
            if res.dry_run or res.status == ActionStatus.DRY_RUN_SIMULATED:
                return f"🧪 DRY RUN\nWould transition {ticket} to {target_status}."
            if res.success:
                return f"✅ {ticket} transitioned to {target_status}."
            return f"❌ Could not transition {ticket} to {target_status}.\nReason: {res.error_message or 'Action failed'}"

        elif sub == "assign":
            ticket = (options.get("ticket") or options.get("task_key", "")).strip().upper()
            user_input = options.get("user") or options.get("assignee", "")
            if not ticket or not user_input:
                return "❌ Both `ticket` and `user` are required. Example: `/pm assign WSSS-326 Aqib`"

            account_id, display_name = await self.resolve_jira_user(str(user_input))
            if not account_id:
                return "❌ I couldn't safely identify that Jira user."

            action = create_assign_task_action(
                task_key=ticket,
                assignee=account_id,
                assignee_name=display_name,
                target_system="jira",
                requested_by=actor
            )
            res = await engine.execute(action)
            target_name = display_name or user_input
            if res.dry_run or res.status == ActionStatus.DRY_RUN_SIMULATED:
                return f"🧪 DRY RUN\nWould assign {ticket} to {target_name}."
            if res.success:
                return f"✅ Assigned {ticket} to {target_name}."
            return f"❌ Could not assign {ticket}.\nReason: {res.error_message or 'Action failed'}"

        elif sub == "comment":
            ticket = (options.get("ticket") or options.get("task_key", "")).strip().upper()
            comment_text = options.get("comment") or options.get("text") or options.get("message", "")
            if not ticket or not comment_text:
                return "❌ Both `ticket` and `comment` are required. Example: `/pm comment WSSS-326 \"Please verify this.\"`"

            action = create_add_comment_action(
                target_system="jira",
                task_key=ticket,
                comment_body=str(comment_text).strip(),
                requested_by=actor
            )
            res = await engine.execute(action)
            if res.dry_run or res.status == ActionStatus.DRY_RUN_SIMULATED:
                return f"🧪 DRY RUN\nWould add comment to {ticket}:\n{comment_text}"
            if res.success:
                return f"✅ Comment added to {ticket}."
            return f"❌ Could not add comment to {ticket}.\nReason: {res.error_message or 'Action failed'}"

        elif sub == "create":
            project = (options.get("project") or options.get("project_key", "")).strip().upper()
            summary = options.get("summary") or options.get("title", "")
            if not project or not summary:
                return "❌ Both `project` and `summary` are required. Example: `/pm create WSSS \"Fix checkout issue\"`"

            description = options.get("description")
            priority = options.get("priority")
            assignee = options.get("assignee") or options.get("user")
            labels = options.get("labels")
            if isinstance(labels, str):
                labels = [l.strip() for l in labels.split(",") if l.strip()]

            # Resolve assignee if provided
            resolved_assignee = None
            if assignee:
                acc_id, _ = await self.resolve_jira_user(str(assignee))
                if not acc_id:
                    return f"❌ I couldn't safely identify that Jira user for assignment: '{assignee}'."
                resolved_assignee = acc_id

            action = create_create_task_action(
                project_key=project,
                summary=str(summary).strip(),
                description=description,
                issue_type=options.get("issue_type", "Task"),
                assignee=resolved_assignee,
                priority=priority,
                labels=labels,
                target_system="jira",
                requested_by=actor
            )
            res = await engine.execute(action)
            if res.dry_run or res.status == ActionStatus.DRY_RUN_SIMULATED:
                return f"🧪 DRY RUN\nWould create task in {project}: {summary}."
            if res.success:
                created_key = res.result_data.get("key") or res.result_data.get("issue_key") or f"{project}-task"
                return f"✅ Created {created_key}: {summary}."
            return f"❌ Could not create task in {project}.\nReason: {res.error_message or 'Action failed'}"

        elif sub == "update":
            ticket = (options.get("ticket") or options.get("task_key", "")).strip().upper()
            field = (options.get("field") or "").strip().lower()
            value = options.get("value")
            if not ticket or not field or value is None:
                return "❌ `ticket`, `field`, and `value` are required. Example: `/pm update WSSS-326 priority High`"

            if field not in ALLOWED_UPDATE_FIELDS:
                return f"❌ Field '{field}' is not permitted for update. Allowed fields: {sorted(list(ALLOWED_UPDATE_FIELDS))}."

            fields_payload: Dict[str, Any] = {}
            if field == "labels" and isinstance(value, str):
                fields_payload["labels"] = [l.strip() for l in value.split(",") if l.strip()]
            else:
                fields_payload[field] = value

            action = create_update_task_action(
                task_key=ticket,
                fields=fields_payload,
                target_system="jira",
                requested_by=actor
            )
            res = await engine.execute(action)
            if res.dry_run or res.status == ActionStatus.DRY_RUN_SIMULATED:
                return f"🧪 DRY RUN\nWould update {ticket}: {field} = {value}."
            if res.success:
                return f"✅ Updated {ticket}."
            return f"❌ Could not update {ticket}.\nReason: {res.error_message or 'Action failed'}"

        elif sub == "notify":
            user_or_channel = options.get("user") or options.get("recipient") or options.get("channel") or settings.PM_DISCORD_CHANNEL
            message = options.get("message") or options.get("text", "")
            if not message:
                return "❌ `message` is required for notify. Example: `/pm notify Aqib \"WSSS-326 needs attention\"`"

            action = create_send_notification_action(
                target_system="discord",
                channel=str(user_or_channel),
                message=str(message),
                title="PM Notification",
                requested_by=actor
            )
            res = await engine.execute(action)
            if res.dry_run or res.status == ActionStatus.DRY_RUN_SIMULATED:
                return f"🧪 DRY RUN\nWould send notification to {user_or_channel}: {message}."
            if res.success:
                return f"✅ Notification sent to {user_or_channel}."
            return f"❌ Could not send notification.\nReason: {res.error_message or 'Action failed'}"

        elif sub == "message":
            recipient = options.get("user") or options.get("recipient", "")
            message = options.get("message") or options.get("text", "")
            if not recipient or not message:
                return "❌ Both `user` and `message` are required. Example: `/pm message Aqib \"Can you check WSSS-326?\"`"

            action = create_send_message_action(
                target_system="discord",
                target_id=str(recipient),
                text=str(message),
                requested_by=actor
            )
            res = await engine.execute(action)
            if res.dry_run or res.status == ActionStatus.DRY_RUN_SIMULATED:
                return f"🧪 DRY RUN\nWould send message to {recipient}: {message}."
            if res.success:
                return f"✅ Message sent to {recipient}."
            return f"❌ Could not send message.\nReason: {res.error_message or 'Action failed'}"

        else:
            return f"❌ Unknown PM command `/pm {sub}`. Type `/pm help` for available commands."

    def parse_interaction_options(self, options_list: Optional[List[Dict[str, Any]]]) -> Tuple[str, Dict[str, Any]]:
        """Extract subcommand and flat dictionary of option values from Discord Interaction data payload."""
        if not options_list:
            return "help", {}

        # Check if first option is a SUB_COMMAND (type 1)
        first_opt = options_list[0]
        if first_opt.get("type") == 1 or "options" in first_opt:
            subcommand = first_opt.get("name", "help")
            inner_opts = first_opt.get("options", [])
            extracted = {o.get("name"): o.get("value") for o in inner_opts if "name" in o}
            return subcommand, extracted

        # Direct options under root command
        subcommand = options_list[0].get("name", "help")
        extracted = {o.get("name"): o.get("value") for o in options_list if "name" in o}
        return subcommand, extracted

    async def handle_interaction(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Handle standard Discord interaction payload.
        
        Supports:
        - Type 1: PING -> PONG
        - Type 2: APPLICATION_COMMAND (/pm ...)
        """
        int_type = payload.get("type")
        if int_type == INTERACTION_RESPONSE_TYPE_PONG:
            return {"type": INTERACTION_RESPONSE_TYPE_PONG}

        data = payload.get("data", {})
        command_name = data.get("name", "").lower()
        if command_name != "pm":
            return {
                "type": INTERACTION_RESPONSE_TYPE_CHANNEL_MESSAGE,
                "data": {"content": f"❌ Unsupported command `/{command_name}`."}
            }

        user_info = payload.get("member", {}).get("user") or payload.get("user", {})
        discord_user_id = str(user_info.get("id", ""))
        channel_id = payload.get("channel_id")

        subcommand, options = self.parse_interaction_options(data.get("options"))
        response_text = await self.execute_subcommand(
            subcommand=subcommand,
            options=options,
            discord_user_id=discord_user_id,
            channel_id=channel_id
        )

        return {
            "type": INTERACTION_RESPONSE_TYPE_CHANNEL_MESSAGE,
            "data": {"content": response_text}
        }


# Global singleton handler
discord_slash_command_handler = DiscordSlashCommandHandler()
