"""Discord Slash Command interaction parser and handler for PM Agent."""

import asyncio
from typing import Any, Dict, List, Optional, Tuple, Union
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
`/pm worklog [user] [date]`
`/pm overdue [user] [date]`
`/pm queue <user> [date]`
`/pm attention [date]`
`/pm activity [date]`
`/pm transition <ticket> <status>`
`/pm assign <ticket> <user>`
`/pm comment <ticket> <comment>`
`/pm create <project> <summary> [description] [assignee] [comment]`
`/pm update <ticket> <field> <value>`
`/pm notify <user> <message>`
`/pm message <user> <message>`"""


class DiscordSlashCommandHandler:
    """Handles parsing, authorization, execution, and response formatting for Discord slash commands."""

    def __init__(
        self,
        user_repo: Optional[UserRepository] = None,
        action_engine: Optional[Any] = None,
        manager: Optional[Any] = None,
    ):
        from app.database.connection import db_manager
        self.mgr = manager or (getattr(user_repo, "mgr", None) if user_repo else None) or (getattr(action_engine, "mgr", None) if action_engine else None) or db_manager
        self.user_repo = user_repo or UserRepository(self.mgr)
        self._action_engine = action_engine

    def get_action_engine(self) -> Any:
        if self._action_engine is not None:
            return self._action_engine
        from app.core.actions.engine import action_engine
        return action_engine

    def _validate_target_date(self, target_date: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
        """Validate date format (YYYY-MM-DD) and calendar validity."""
        if not target_date or not str(target_date).strip():
            return None, None
        clean_date = str(target_date).strip()
        import re
        import datetime
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", clean_date):
            return None, f"❌ Invalid date format '{target_date}'. Expected `YYYY-MM-DD` (e.g. `2026-09-12`)."
        try:
            datetime.datetime.strptime(clean_date, "%Y-%m-%d")
        except ValueError:
            return None, f"❌ Invalid calendar date '{target_date}'. Expected a valid date in `YYYY-MM-DD` format."
        return clean_date, None

    async def resolve_jira_resource(self, user_query: str, jira_client: Optional[Any] = None) -> Tuple[Optional[str], Optional[str], str]:
        """Safely resolve a Jira user query to (account_id, display_name, status).
        
        Statuses:
        - "OK": Successfully and unambiguously resolved.
        - "EXCLUDED": Resolved user is in canonical exclusions.
        - "AMBIGUOUS": Multiple matching users found.
        - "NOT_FOUND": No matching user found.
        """
        if not user_query or not str(user_query).strip():
            return None, None, "NOT_FOUND"

        q = str(user_query).strip()

        from app.database.repositories import EmployeeRoleRepository
        from app.core.performance.roles import resolve_canonical_account_id, AUTHORITATIVE_JIRA_LEGACY_ACCOUNT_MAP
        role_repo = EmployeeRoleRepository(self.mgr)

        # 1. Check active PM identity ("me" or matching display name / email)
        if q.lower() == "me" or user_identity_service.is_me(display_name=q, email=q):
            ident = await user_identity_service.get_my_identity(client=jira_client)
            if ident.get("account_id"):
                acc_id = ident["account_id"]
                disp_name = ident.get("display_name") or q
                if settings.is_canonical_excluded(acc_id, disp_name):
                    return acc_id, disp_name, "EXCLUDED"
                return acc_id, disp_name, "OK"

        # 2. Check legacy alias map
        if q.lower() in AUTHORITATIVE_JIRA_LEGACY_ACCOUNT_MAP:
            can_id = AUTHORITATIVE_JIRA_LEGACY_ACCOUNT_MAP[q.lower()]
            assign = role_repo.get_by_account_id(can_id)
            disp_name = assign.get("display_name", q) if assign else q
            if settings.is_canonical_excluded(can_id, disp_name):
                return can_id, disp_name, "EXCLUDED"
            return can_id, disp_name, "OK"

        # 3. Direct Atlassian Account ID
        if ":" in q or (len(q) >= 24 and " " not in q and "-" in q):
            assign = role_repo.get_by_account_id(q)
            disp_name = assign.get("display_name", q) if assign else q
            if settings.is_canonical_excluded(q, disp_name):
                return q, disp_name, "EXCLUDED"
            return q, disp_name, "OK"

        # 4. Exact match against authoritative EmployeeRoleRepository
        by_acc = role_repo.get_by_account_id(q)
        if by_acc:
            acc_id = by_acc["account_id"]
            disp_name = by_acc.get("display_name", q)
            if settings.is_canonical_excluded(acc_id, disp_name):
                return acc_id, disp_name, "EXCLUDED"
            return acc_id, disp_name, "OK"

        by_name = role_repo.get_by_display_name(q)
        if by_name:
            acc_id = by_name["account_id"]
            disp_name = by_name.get("display_name", q)
            if settings.is_canonical_excluded(acc_id, disp_name):
                return acc_id, disp_name, "EXCLUDED"
            return acc_id, disp_name, "OK"

        # 5. Exact match in local UserRepository projection
        users = self.user_repo.find_by_name(display_name=q, external_system="jira")
        if not users:
            users = self.user_repo.find_by_email(email=q, external_system="jira")
        if len(users) == 1:
            acc_id = users[0]["external_user_id"]
            disp_name = users[0].get("display_name") or q
            if settings.is_canonical_excluded(acc_id, disp_name):
                return acc_id, disp_name, "EXCLUDED"
            return acc_id, disp_name, "OK"
        elif len(users) > 1:
            return None, None, "AMBIGUOUS"

        # 6. Query Jira API for exact or unambiguous match if configured
        try:
            if not jira_client:
                jira_conn = self.get_action_engine().get_connector("jira")
                jira_client = getattr(jira_conn, "client", None)

            if jira_client and settings.is_jira_configured():
                matched_users = await jira_client.get_users(query=q)
                if isinstance(matched_users, list):
                    exact_matches = [
                        u for u in matched_users
                        if u.get("active", True) and (
                            u.get("displayName", "").strip().lower() == q.lower()
                            or u.get("emailAddress", "").strip().lower() == q.lower()
                            or u.get("accountId") == q
                        )
                    ]
                    if len(exact_matches) == 1:
                        acc_id = exact_matches[0]["accountId"]
                        disp_name = exact_matches[0].get("displayName") or q
                        if settings.is_canonical_excluded(acc_id, disp_name):
                            return acc_id, disp_name, "EXCLUDED"
                        return acc_id, disp_name, "OK"
                    elif len(exact_matches) > 1:
                        return None, None, "AMBIGUOUS"
                    elif len(matched_users) == 1 and matched_users[0].get("active", True):
                        acc_id = matched_users[0]["accountId"]
                        disp_name = matched_users[0].get("displayName") or q
                        if settings.is_canonical_excluded(acc_id, disp_name):
                            return acc_id, disp_name, "EXCLUDED"
                        return acc_id, disp_name, "OK"
                    elif len(matched_users) > 1:
                        return None, None, "AMBIGUOUS"
        except Exception as e:
            logger.warning(f"Error querying Jira users during user resolution for '{q}': {e}")

        return None, None, "NOT_FOUND"

    async def resolve_jira_user(self, user_query: str, jira_client: Optional[Any] = None) -> Tuple[Optional[str], Optional[str]]:
        """Safely resolve a Jira user input to (account_id, display_name) without fuzzy guessing."""
        acc_id, disp_name, status = await self.resolve_jira_resource(user_query, jira_client=jira_client)
        if status in ("OK", "EXCLUDED") and acc_id:
            return acc_id, disp_name
        return None, None

    def _is_authorized_pm(self, discord_user_id: Optional[str]) -> bool:
        """Verify whether a Discord user is permitted to execute /pm commands.

        - If DISCORD_PM_COMMAND_ENABLED is False => DENY ALL.
        - If discord_user_id is None or empty => DENY.
        - If DISCORD_PM_ALLOWED_USERS == "*" => ALLOW.
        - If DISCORD_PM_ALLOWED_USERS is empty:
            * In production (APP_ENV=production) => DENY ALL.
            * In development/local (APP_ENV != production) => ALLOW ALL.
        - If DISCORD_PM_ALLOWED_USERS is configured => check membership in allowlist.
        """
        if not settings.DISCORD_PM_COMMAND_ENABLED:
            return False
        if not discord_user_id or not str(discord_user_id).strip():
            return False
        allowed = (settings.DISCORD_PM_ALLOWED_USERS or "").strip()
        if allowed == "*":
            return True
        if not allowed:
            if settings.is_production():
                return False
            return True
        allowed_list = [u.strip() for u in allowed.split(",") if u.strip()]
        return str(discord_user_id).strip() in allowed_list

    def _is_allowed_pm_channel(self, channel_id: Optional[str]) -> bool:
        """Verify whether a Discord channel is permitted for /pm slash commands.

        /pm commands are strictly restricted to #pm-alerts (or configured PM_DISCORD_CHANNEL).
        Any other channel (e.g. #notifications, #general, #dev-chat) or missing/empty channel context is rejected.
        """
        if not channel_id or not str(channel_id).strip():
            return False

        clean = str(channel_id).strip().lower()
        pm_channel = (settings.PM_DISCORD_CHANNEL or "pm-alerts").strip().lower()

        allowed_identifiers = {
            pm_channel,
            f"#{pm_channel}",
            "pm-alerts",
            "#pm-alerts",
        }

        return clean in allowed_identifiers

    # =========================================================================
    # Canonical Subcommand Handlers (Single Source of Truth)
    # =========================================================================

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
                return f"❌ Ticket {clean_ticket} not found or inaccessible."

            from app.connectors.discord.formatter import DiscordFormatter
            return DiscordFormatter.format_status_reply(issue_data)
        except Exception as e:
            logger.error(f"Error fetching status for {clean_ticket}: {e}")
            if "not found" in str(e).lower() or "404" in str(e):
                return f"❌ Ticket {clean_ticket} not found."
            return f"❌ Could not retrieve status for {clean_ticket}: {e}"

    async def handle_worklog_command(
        self,
        user_input: Optional[str] = None,
        target_date: Optional[str] = None
    ) -> Union[str, Dict[str, Any]]:
        """Handle /pm worklog command (canonical handler for team and resource worklogs)."""
        try:
            from app.core.reports.worklog_report import DailyWorklogReportGenerator
            from app.connectors.discord.formatter import DiscordFormatter
            gen = DailyWorklogReportGenerator(manager=self.mgr)
            if user_input:
                acc_id, disp_name, res_status = await self.resolve_jira_resource(str(user_input))
                if res_status == "EXCLUDED":
                    return "❌ This resource is not available for PM reporting."
                elif res_status == "AMBIGUOUS":
                    return "❌ Multiple users match. Please use the exact Jira display name."
                elif res_status == "NOT_FOUND" or not acc_id:
                    return "❌ Resource not found."
                data = await gen.generate_user_worklog_report(account_id=acc_id, display_name=disp_name, target_date=target_date, sync_jira=False)
                return DiscordFormatter.format_user_worklog_text(data)
            else:
                # On-demand: sync worklogs if Jira is active
                try:
                    if settings.is_jira_configured() and settings.is_jira_team_group_configured():
                        import datetime
                        resolved_date = target_date or datetime.datetime.now().strftime("%Y-%m-%d")
                        await gen.sync_jira_worklogs_for_date(resolved_date)
                except Exception as e:
                    logger.warning(f"On-demand worklog Jira sync encountered notice: {e}")
                data = await gen.generate_report(target_date=target_date, sync_jira=False)
                return DiscordFormatter.format_daily_worklog_embed(data)
        except Exception as e:
            logger.error(f"Error generating worklog report: {e}", exc_info=True)
            return "❌ Unable to generate the worklog report right now."

    async def handle_overdue_command(
        self,
        user_input: Optional[str] = None,
        target_date: Optional[str] = None
    ) -> Union[str, Dict[str, Any]]:
        """Handle /pm overdue command (canonical handler for overdue digest)."""
        try:
            from app.core.reports.overdue_report import DailyOverdueReportGenerator
            from app.connectors.discord.formatter import DiscordFormatter
            gen = DailyOverdueReportGenerator(manager=self.mgr)
            if user_input:
                acc_id, disp_name, res_status = await self.resolve_jira_resource(str(user_input))
                if res_status == "EXCLUDED":
                    return "❌ This resource is not available for PM reporting."
                elif res_status == "AMBIGUOUS":
                    return "❌ Multiple users match. Please use the exact Jira display name."
                elif res_status == "NOT_FOUND" or not acc_id:
                    return "❌ Resource not found."
                data = gen.generate_user_overdue_digest(account_id=acc_id, display_name=disp_name, target_date=target_date)
                return DiscordFormatter.format_user_overdue_digest_embed(data)
            else:
                data = gen.generate_digest(target_date=target_date)
                return DiscordFormatter.format_overdue_digest(data)
        except Exception as e:
            logger.error(f"Error generating overdue report: {e}", exc_info=True)
            return "❌ Unable to generate the overdue report right now."

    async def handle_queue_command(
        self,
        user_input: Optional[str] = None,
        target_date: Optional[str] = None
    ) -> Union[str, Dict[str, Any]]:
        """Handle /pm queue command (canonical handler for active queue report)."""
        if not user_input:
            return "❌ Target resource is required for active queue report. Example: `/pm queue user:\"Ahsan Amin\"`"
        try:
            acc_id, disp_name, res_status = await self.resolve_jira_resource(str(user_input))
            if res_status == "EXCLUDED":
                return "❌ This resource is not available for PM reporting."
            elif res_status == "AMBIGUOUS":
                return "❌ Multiple users match. Please use the exact Jira display name."
            elif res_status == "NOT_FOUND" or not acc_id:
                return "❌ Resource not found."

            from app.core.reports.queue_report import ResourceQueueReportGenerator
            from app.connectors.discord.formatter import DiscordFormatter
            gen = ResourceQueueReportGenerator(manager=self.mgr)
            data = gen.generate_user_queue_report(account_id=acc_id, display_name=disp_name, target_date=target_date)
            return DiscordFormatter.format_user_active_queue_embed(data)
        except Exception as e:
            logger.error(f"Error generating active queue report: {e}", exc_info=True)
            return "❌ Unable to generate the active queue report right now."

    async def handle_attention_command(self, target_date: Optional[str] = None) -> Union[str, Dict[str, Any]]:
        """Handle /pm attention command (canonical handler for PM attention digest)."""
        try:
            from app.core.reports.attention_report import DailyPMAttentionReportGenerator
            from app.connectors.discord.formatter import DiscordFormatter
            gen = DailyPMAttentionReportGenerator(manager=self.mgr)
            data = gen.generate_digest(target_date=target_date)
            return DiscordFormatter.format_pm_attention_digest(data)
        except Exception as e:
            logger.error(f"Error generating attention report: {e}", exc_info=True)
            return "❌ Unable to generate the attention report right now."

    async def handle_activity_command(self, target_date: Optional[str] = None) -> Union[str, Dict[str, Any]]:
        """Handle /pm activity command (canonical handler for Daily PM activity report)."""
        try:
            from app.core.reports.daily_report import DailyActivityReportGenerator
            from app.connectors.discord.formatter import DiscordFormatter
            gen = DailyActivityReportGenerator(manager=self.mgr)
            data = gen.generate_report(target_date=target_date)
            return DiscordFormatter.format_daily_report_embed(data)
        except Exception as e:
            logger.error(f"Error generating daily activity report: {e}", exc_info=True)
            return "❌ Unable to generate the daily activity report right now."

    async def handle_mubashir_command(self, target_date: Optional[str] = None) -> Union[str, Dict[str, Any]]:
        """Handle on-demand Mubashir Automation Report."""
        try:
            from app.core.reports.mubashir_report import MubashirAutomationReportGenerator
            from app.connectors.discord.formatter import DiscordFormatter
            gen = MubashirAutomationReportGenerator(manager=self.mgr)
            data = gen.generate_report(target_date=target_date)
            return DiscordFormatter.format_mubashir_automation_report(data)
        except Exception as e:
            logger.error(f"Error generating Mubashir automation report: {e}", exc_info=True)
            return "❌ Unable to generate the Mubashir automation report right now."

    async def handle_report_command(
        self,
        report_name: str,
        user_input: Optional[str] = None,
        target_date: Optional[str] = None
    ) -> Union[str, Dict[str, Any]]:
        """Handle deprecated /pm report compatibility shim, dispatching to canonical handlers."""
        rname = (report_name or "").strip().lower()
        if not rname:
            return (
                "❌ Please specify a report name: `overdue`, `worklog`, `attention`, `activity`, `queue`, or `mubashir`.\n"
                "Example: `/pm report name:overdue` or `/pm overdue`"
            )

        if rname in ("worklog", "worklogs", "daily_worklog"):
            return await self.handle_worklog_command(user_input=user_input, target_date=target_date)
        elif rname in ("overdue", "overdues"):
            return await self.handle_overdue_command(user_input=user_input, target_date=target_date)
        elif rname in ("queue", "active_queue"):
            return await self.handle_queue_command(user_input=user_input, target_date=target_date)
        elif rname in ("attention", "pm_attention", "digest"):
            return await self.handle_attention_command(target_date=target_date)
        elif rname in ("activity", "daily", "daily_activity"):
            return await self.handle_activity_command(target_date=target_date)
        elif rname in (
            "mubashir",
            "mubashir_automation",
            "mubashir_report",
        ):
            return await self.handle_mubashir_command(
                target_date=target_date
            )
        else:
            return f"❌ Unknown report '{report_name}'. Available reports: `overdue`, `worklog`, `attention`, `activity`, `queue`, `mubashir`."

    async def handle_transition_command(self, ticket: str, target_status: str, actor: str) -> str:
        """Handle /pm transition command."""
        clean_ticket = (ticket or "").strip().upper()
        clean_status = (target_status or "").strip()
        if not clean_ticket or not clean_status:
            return "❌ Both `ticket` and `status` are required. Example: `/pm transition WSSS-326 Done`"

        action = create_transition_task_action(
            target_system="jira",
            task_key=clean_ticket,
            target_status=clean_status,
            requested_by=actor
        )
        res = await self.get_action_engine().execute(action)
        if res.dry_run or res.status == ActionStatus.DRY_RUN_SIMULATED:
            return f"🧪 DRY RUN\nWould transition {clean_ticket} to {clean_status}."
        if res.success:
            return f"✅ {clean_ticket} transitioned to {clean_status}."
        return f"❌ Could not transition {clean_ticket} to {clean_status}.\nReason: {res.error_message or 'Action failed'}"

    async def handle_assign_command(self, ticket: str, user_input: str, actor: str) -> str:
        """Handle /pm assign command."""
        clean_ticket = (ticket or "").strip().upper()
        if not clean_ticket or not user_input:
            return "❌ Both `ticket` and `user` are required. Example: `/pm assign WSSS-326 Aqib`"

        account_id, display_name = await self.resolve_jira_user(str(user_input))
        if not account_id:
            return "❌ I couldn't safely identify that Jira user."

        action = create_assign_task_action(
            task_key=clean_ticket,
            assignee=account_id,
            assignee_name=display_name,
            target_system="jira",
            requested_by=actor
        )
        res = await self.get_action_engine().execute(action)
        target_name = display_name or user_input
        if res.dry_run or res.status == ActionStatus.DRY_RUN_SIMULATED:
            return f"🧪 DRY RUN\nWould assign {clean_ticket} to {target_name}."
        if res.success:
            return f"✅ Assigned {clean_ticket} to {target_name}."
        return f"❌ Could not assign {clean_ticket}.\nReason: {res.error_message or 'Action failed'}"

    async def handle_comment_command(self, ticket: str, comment_text: str, actor: str) -> str:
        """Handle /pm comment command."""
        clean_ticket = (ticket or "").strip().upper()
        clean_comment = str(comment_text or "").strip()
        if not clean_ticket or not clean_comment:
            return "❌ Both `ticket` and `comment` are required. Example: `/pm comment WSSS-326 \"Please verify this.\"`"

        action = create_add_comment_action(
            target_system="jira",
            task_key=clean_ticket,
            comment_body=clean_comment,
            requested_by=actor
        )
        res = await self.get_action_engine().execute(action)
        if res.dry_run or res.status == ActionStatus.DRY_RUN_SIMULATED:
            return f"🧪 DRY RUN\nWould add comment to {clean_ticket}:\n{clean_comment}"
        if res.success:
            return f"✅ Comment added to {clean_ticket}."
        return f"❌ Could not add comment to {clean_ticket}.\nReason: {res.error_message or 'Action failed'}"

    async def handle_create_command(
        self,
        project: str,
        summary: str,
        description: Optional[str] = None,
        assignee_input: Optional[str] = None,
        comment_text: Optional[str] = None,
        priority: Optional[str] = None,
        labels: Optional[Union[str, List[str]]] = None,
        issue_type: str = "Task",
        actor: str = "discord:user"
    ) -> str:
        """Handle /pm create command."""
        clean_project = str(project or "").strip().upper()
        clean_summary = str(summary or "").strip()
        if not clean_project or not clean_summary:
            return "❌ Both `project` and `summary` are required. Example: `/pm create project:WSSS summary:\"Fix login issue\"`"

        clean_desc = str(description).strip() if description is not None and str(description).strip() else None

        resolved_assignee = None
        if assignee_input:
            acc_id, disp_name, res_status = await self.resolve_jira_resource(str(assignee_input))
            if res_status == "EXCLUDED":
                return f"❌ User '{assignee_input}' is excluded from Jira assignments."
            elif res_status != "OK" or not acc_id:
                return "Could not resolve that Jira user. Please use an exact Jira display name or `me`."
            resolved_assignee = acc_id

        parsed_labels = None
        if isinstance(labels, str):
            parsed_labels = [l.strip() for l in labels.split(",") if l.strip()]
        elif isinstance(labels, list):
            parsed_labels = labels

        action = create_create_task_action(
            project_key=clean_project,
            summary=clean_summary,
            description=clean_desc,
            issue_type=issue_type or "Task",
            assignee=resolved_assignee,
            priority=priority,
            labels=parsed_labels,
            target_system="jira",
            requested_by=actor
        )
        res = await self.get_action_engine().execute(action)
        if not res.success and not (res.dry_run or res.status == ActionStatus.DRY_RUN_SIMULATED):
            return f"❌ Could not create task in {clean_project}.\nReason: {res.error_message or 'Action failed'}"

        created_key = res.result_data.get("key") or res.result_data.get("issue_key") or f"{clean_project}-SIMULATED"
        jira_url = settings.get_jira_browse_url(created_key)

        if comment_text and str(comment_text).strip():
            clean_comment = str(comment_text).strip()
            comment_action = create_add_comment_action(
                target_system="jira",
                task_key=created_key,
                comment_body=clean_comment,
                requested_by=actor
            )
            comment_res = await self.get_action_engine().execute(comment_action)
            if comment_res.success:
                if res.dry_run or res.status == ActionStatus.DRY_RUN_SIMULATED:
                    return f"🧪 DRY RUN\nCreated {created_key} successfully and added the comment.\n{jira_url}"
                return f"Created {created_key} successfully and added the comment.\n{jira_url}"
            else:
                if res.dry_run or res.status == ActionStatus.DRY_RUN_SIMULATED:
                    return f"🧪 DRY RUN\nCreated {created_key} successfully, but the requested comment could not be added.\n{jira_url}"
                return f"Created {created_key} successfully, but the requested comment could not be added.\n{jira_url}"
        else:
            if res.dry_run or res.status == ActionStatus.DRY_RUN_SIMULATED:
                return f"🧪 DRY RUN\nCreated {created_key} successfully.\n{jira_url}"
            return f"Created {created_key} successfully.\n{jira_url}"

    async def handle_update_command(self, ticket: str, field: str, value: Any, actor: str) -> str:
        """Handle /pm update command."""
        clean_ticket = str(ticket or "").strip().upper()
        clean_field = str(field or "").strip().lower()
        if not clean_ticket or not clean_field or value is None:
            return "❌ `ticket`, `field`, and `value` are required. Example: `/pm update WSSS-326 priority High`"

        if clean_field not in ALLOWED_UPDATE_FIELDS:
            return f"❌ Field '{clean_field}' is not permitted for update. Allowed fields: {sorted(list(ALLOWED_UPDATE_FIELDS))}."

        fields_payload: Dict[str, Any] = {}
        if clean_field == "labels" and isinstance(value, str):
            fields_payload["labels"] = [l.strip() for l in value.split(",") if l.strip()]
        else:
            fields_payload[clean_field] = value

        action = create_update_task_action(
            task_key=clean_ticket,
            fields=fields_payload,
            target_system="jira",
            requested_by=actor
        )
        res = await self.get_action_engine().execute(action)
        if res.dry_run or res.status == ActionStatus.DRY_RUN_SIMULATED:
            return f"🧪 DRY RUN\nWould update {clean_ticket}: {clean_field} = {value}."
        if res.success:
            return f"✅ Updated {clean_ticket}."
        return f"❌ Could not update {clean_ticket}.\nReason: {res.error_message or 'Action failed'}"

    async def handle_notify_command(self, target: str, message: str, actor: str) -> str:
        """Handle /pm notify command."""
        if not message or not str(message).strip():
            return "❌ `message` is required for notify. Example: `/pm notify Aqib \"WSSS-326 needs attention\"`"

        user_or_channel = target or settings.PM_DISCORD_CHANNEL
        action = create_send_notification_action(
            target_system="discord",
            channel=str(user_or_channel),
            message=str(message),
            title="PM Notification",
            requested_by=actor
        )
        res = await self.get_action_engine().execute(action)
        if res.dry_run or res.status == ActionStatus.DRY_RUN_SIMULATED:
            return f"🧪 DRY RUN\nWould send notification to {user_or_channel}: {message}."
        if res.success:
            return f"✅ Notification sent to {user_or_channel}."
        return f"❌ Could not send notification.\nReason: {res.error_message or 'Action failed'}"

    async def handle_message_command(self, recipient: str, message: str, actor: str) -> str:
        """Handle deprecated /pm message command (direct message)."""
        if not recipient or not message:
            return "❌ Both `user` and `message` are required. Example: `/pm message Aqib \"Can you check WSSS-326?\"`"

        action = create_send_message_action(
            target_system="discord",
            target_id=str(recipient),
            text=str(message),
            requested_by=actor
        )
        res = await self.get_action_engine().execute(action)
        if res.dry_run or res.status == ActionStatus.DRY_RUN_SIMULATED:
            return f"🧪 DRY RUN\nWould send message to {recipient}: {message}."
        if res.success:
            return f"✅ Message sent to {recipient}."
        return f"❌ Could not send message.\nReason: {res.error_message or 'Action failed'}"

    # =========================================================================
    # Main Dispatcher
    # =========================================================================

    async def execute_subcommand(
        self,
        subcommand: str,
        options: Dict[str, Any],
        discord_user_id: Optional[str] = None,
        channel_id: Optional[str] = None,
    ) -> Union[str, Dict[str, Any]]:
        """Execute a parsed /pm subcommand with RBAC verification."""
        actor = f"discord:{discord_user_id}" if discord_user_id else "discord:user"

        # 1. Authorization check
        if not self._is_authorized_pm(discord_user_id):
            return "❌ You are not authorized to use PM commands."

        # 2. Channel restriction check (/pm commands restricted exclusively to #pm-alerts)
        if not self._is_allowed_pm_channel(channel_id):
            return "❌ PM commands can only be used in #pm-alerts. Please use #pm-alerts for PM operations."

        sub = (subcommand or "help").strip().lower()

        # 2. Help command
        if sub in ("help", "commands"):
            return PM_HELP_TEXT

        # 3. Read-only Status command (does NOT create an Action)
        if sub == "status":
            ticket = options.get("ticket") or options.get("task_key") or options.get("issue_key", "")
            return await self.handle_status_command(ticket=ticket)

        # 4. Date validation for date-bearing commands
        target_date = options.get("date")
        if target_date:
            clean_date, date_err = self._validate_target_date(str(target_date))
            if date_err:
                return date_err
            target_date = clean_date

        user_input = options.get("user") or options.get("resource")

        # 5. Canonical Read-only Report commands
        if sub in ("worklog", "worklogs", "daily_worklog"):
            return await self.handle_worklog_command(user_input=user_input, target_date=target_date)

        if sub in ("overdue", "overdues"):
            return await self.handle_overdue_command(user_input=user_input, target_date=target_date)

        if sub in ("queue", "active_queue"):
            return await self.handle_queue_command(user_input=user_input, target_date=target_date)

        if sub in ("attention", "pm_attention", "digest"):
            return await self.handle_attention_command(target_date=target_date)

        if sub in ("activity", "daily", "daily_activity"):
            return await self.handle_activity_command(target_date=target_date)

        # 6. Deprecated /pm report compatibility shim
        if sub == "report":
            report_name = (options.get("name") or options.get("report") or options.get("type") or "").strip().lower()
            return await self.handle_report_command(report_name=report_name, user_input=user_input, target_date=target_date)

        # 7. Mutation subcommands -> pass exclusively through ActionEngine
        if sub == "transition":
            ticket = options.get("ticket") or options.get("task_key", "")
            target_status = options.get("status") or options.get("target_status", "")
            return await self.handle_transition_command(ticket=ticket, target_status=target_status, actor=actor)

        elif sub == "assign":
            ticket = options.get("ticket") or options.get("task_key", "")
            user_input = options.get("user") or options.get("assignee", "")
            return await self.handle_assign_command(ticket=ticket, user_input=user_input, actor=actor)

        elif sub == "comment":
            ticket = options.get("ticket") or options.get("task_key", "")
            comment_text = options.get("comment") or options.get("text") or options.get("message", "")
            return await self.handle_comment_command(ticket=ticket, comment_text=comment_text, actor=actor)

        elif sub == "create":
            project = options.get("project") or options.get("project_key", "")
            summary = options.get("summary") or options.get("title", "")
            description = options.get("description")
            assignee_input = options.get("assignee") or options.get("user")
            comment_text = options.get("comment")
            priority = options.get("priority")
            labels = options.get("labels")
            issue_type = options.get("issue_type", "Task")
            return await self.handle_create_command(
                project=project,
                summary=summary,
                description=description,
                assignee_input=assignee_input,
                comment_text=comment_text,
                priority=priority,
                labels=labels,
                issue_type=issue_type,
                actor=actor
            )

        elif sub == "update":
            ticket = options.get("ticket") or options.get("task_key", "")
            field = options.get("field") or ""
            value = options.get("value")
            return await self.handle_update_command(ticket=ticket, field=field, value=value, actor=actor)

        elif sub == "notify":
            target = options.get("target") or options.get("user") or options.get("recipient") or options.get("channel") or settings.PM_DISCORD_CHANNEL
            message = options.get("message") or options.get("text", "")
            return await self.handle_notify_command(target=target, message=message, actor=actor)

        elif sub == "message":
            recipient = options.get("user") or options.get("recipient") or options.get("target", "")
            message = options.get("message") or options.get("text", "")
            return await self.handle_message_command(recipient=recipient, message=message, actor=actor)

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
        response_data = await self.execute_subcommand(
            subcommand=subcommand,
            options=options,
            discord_user_id=discord_user_id,
            channel_id=channel_id
        )

        if isinstance(response_data, dict) and "embeds" in response_data:
            return {
                "type": INTERACTION_RESPONSE_TYPE_CHANNEL_MESSAGE,
                "data": {"embeds": response_data["embeds"]}
            }

        return {
            "type": INTERACTION_RESPONSE_TYPE_CHANNEL_MESSAGE,
            "data": {"content": str(response_data) if response_data is not None else ""}
        }


# Global singleton handler
discord_slash_command_handler = DiscordSlashCommandHandler()
