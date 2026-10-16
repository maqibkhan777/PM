"""Worklog Reminder Service (Event 1: Mursaleen Cluster Daily Worklog Reminders).

Specification:
- Schedule: Mon–Fri at 17:00 PKT (Asia/Karachi).
- Team: Jira group 'Mursaleen Cluster'.
- Eligibility: Active mapped members with < 60 min logged work for target date.
- Message: "You have not yet logged your work. Kindly log your work for today in the respective tickets."
- Suppression:
  * Weekends -> SKIPPED_WEEKEND
  * Official Pakistani Federal Public Holidays / Manual Admin Suppressions -> SKIPPED_HOLIDAY
  * Canonical Exclusions (Aqib, Mursaleen, Usman) -> EXCLUDED
  * >= 60 min worklog -> COMPLIANT
  * 0 min with no activity -> SKIPPED_NO_ACTIVITY_LEAVE_UNVERIFIED
  * 1-59m or 0m with activity -> REMINDER_CANDIDATE (or SKIPPED_APPROVED_LEAVE if verified)
"""

import datetime
import hashlib
import json
import uuid
import zoneinfo
from typing import Any, Dict, List, Optional, Tuple

from app.config.settings import settings
from app.connectors.discord.formatter import DiscordFormatter
from app.connectors.discord.webhook_connector import DiscordWebhookConnector
from app.core.actions.engine import action_engine
from app.core.actions.types import create_send_message_action
from app.core.models.enums import ActionStatus, EventScope
from app.database.connection import DatabaseManager, db_manager
from app.database.repositories import (
    Event1ApprovalRepository,
    HolidayRepository,
    SuppressionRepository,
    UserMappingRepository,
)
from app.services.user_mapping_service import UserMappingService
from app.utils.logger import logger
from app.utils.time import utc_now_iso

EVENT1_REMINDER_TEXT = (
    "You have not yet logged your work. Kindly log your work for today in the respective tickets."
)


class WorklogReminderService:
    """Service orchestrating Event 1 evaluation, Discord leave verification approval, and gated Mattermost dispatch."""

    def __init__(self, manager: Optional[DatabaseManager] = None):
        self.mgr = manager or db_manager
        self.holiday_repo = HolidayRepository(self.mgr)
        self.suppression_repo = SuppressionRepository(self.mgr)
        self.mapping_repo = UserMappingRepository(self.mgr)
        self.mapping_service = UserMappingService(self.mgr)
        self.approval_repo = Event1ApprovalRepository(self.mgr)
        self.discord_connector = DiscordWebhookConnector()
        # Test-only recipient overrides: strictly temporary and isolated to designated test runs
        self._test_recipient_overrides: set = set()

    def is_date_suppressed_or_holiday(self, target_date: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """Check whether target date is a weekend, official holiday, or manual suppression."""
        # 1. Weekend check
        dt = datetime.datetime.strptime(target_date, "%Y-%m-%d")
        if dt.weekday() in (5, 6):
            return True, "SKIPPED_WEEKEND", {"reason": "Weekend (Saturday/Sunday)"}

        # 2. Official Federal Public Holiday check
        is_official = self.holiday_repo.is_official_public_holiday(target_date)
        hol_record = self.holiday_repo.get_by_date(target_date)

        # 3. Manual Discord Admin Suppression check
        supp_record = self.suppression_repo.get_suppression("EVENT1", target_date)

        if is_official or supp_record:
            details = {
                "official_holiday": hol_record,
                "manual_suppression": supp_record,
            }
            return True, "SKIPPED_HOLIDAY", details

        return False, "ACTIVE", None

    def calculate_cutoff_expiry(self, target_date: str) -> str:
        """Calculate approval window cutoff ISO timestamp (21:00 PKT on target_date)."""
        pkt_tz = zoneinfo.ZoneInfo("Asia/Karachi")
        dt = datetime.datetime.strptime(target_date, "%Y-%m-%d")
        cutoff_dt = dt.replace(hour=21, minute=0, second=0, microsecond=0, tzinfo=pkt_tz)
        return cutoff_dt.isoformat()

    def is_past_cutoff(self, target_date: str) -> bool:
        """Check whether current time has passed 21:00 PKT on target_date."""
        pkt_tz = zoneinfo.ZoneInfo("Asia/Karachi")
        now_pkt = datetime.datetime.now(pkt_tz)
        dt = datetime.datetime.strptime(target_date, "%Y-%m-%d")
        cutoff_dt = dt.replace(hour=21, minute=0, second=0, microsecond=0, tzinfo=pkt_tz)
        return now_pkt > cutoff_dt

    def evaluate_member(
        self,
        jira_account_id: str,
        display_name: str,
        worklog_minutes: int,
        has_activity: bool,
        leave_status: Optional[str] = "unavailable",
        target_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Evaluate an individual cluster member's worklog status for target date."""
        # 1. Canonical exclusions check
        if settings.is_canonical_excluded(jira_account_id, display_name):
            if jira_account_id in self._test_recipient_overrides:
                logger.info(
                    f"TEST_RECIPIENT_OVERRIDE event=EVENT1 jira_account_id={jira_account_id} evaluate_member_permitted=True"
                )
            else:
                return {
                    "jira_account_id": jira_account_id,
                    "display_name": display_name,
                    "status": "EXCLUDED",
                    "reason": "Canonical exclusion (PM / Management / Lead)",
                    "worklog_minutes": worklog_minutes,
                    "send_reminder": False,
                }

        # 2. Mattermost identity resolution
        mm_id, method = self.mapping_service.resolve_jira_to_mattermost(jira_account_id, display_name)
        if not mm_id:
            return {
                "jira_account_id": jira_account_id,
                "display_name": display_name,
                "status": "UNDETERMINED",
                "reason": "No verified Mattermost mapping exists",
                "worklog_minutes": worklog_minutes,
                "send_reminder": False,
            }

        # 3. Compliance threshold check (>= 60 minutes)
        if worklog_minutes >= 60:
            return {
                "jira_account_id": jira_account_id,
                "display_name": display_name,
                "mattermost_user_id": mm_id,
                "status": "COMPLIANT",
                "reason": f"Met threshold ({worklog_minutes}m >= 60m)",
                "worklog_minutes": worklog_minutes,
                "send_reminder": False,
            }

        # 4. Underlogged (1 to 59 minutes)
        if 1 <= worklog_minutes < 60:
            if leave_status == "approved":
                return {
                    "jira_account_id": jira_account_id,
                    "display_name": display_name,
                    "mattermost_user_id": mm_id,
                    "status": "SKIPPED_APPROVED_LEAVE",
                    "reason": "Positively verified approved leave",
                    "worklog_minutes": worklog_minutes,
                    "send_reminder": False,
                }
            return {
                "jira_account_id": jira_account_id,
                "display_name": display_name,
                "mattermost_user_id": mm_id,
                "status": "REMINDER_CANDIDATE",
                "reason": f"Underlogged ({worklog_minutes}m < 60m)",
                "worklog_minutes": worklog_minutes,
                "send_reminder": True,
            }

        # 5. Zero worklog
        if not has_activity:
            return {
                "jira_account_id": jira_account_id,
                "display_name": display_name,
                "mattermost_user_id": mm_id,
                "status": "SKIPPED_NO_ACTIVITY_LEAVE_UNVERIFIED",
                "reason": "Zero worklog and zero Jira activity",
                "worklog_minutes": 0,
                "send_reminder": False,
            }

        if leave_status == "approved":
            return {
                "jira_account_id": jira_account_id,
                "display_name": display_name,
                "mattermost_user_id": mm_id,
                "status": "SKIPPED_APPROVED_LEAVE",
                "reason": "Positively verified approved leave",
                "worklog_minutes": 0,
                "send_reminder": False,
            }

        return {
            "jira_account_id": jira_account_id,
            "display_name": display_name,
            "mattermost_user_id": mm_id,
            "status": "REMINDER_CANDIDATE",
            "reason": "Zero worklog but active on Jira tickets",
            "worklog_minutes": 0,
            "send_reminder": True,
        }

    async def fetch_cluster_worklogs_and_activity(
        self,
        target_date: str,
        team_group: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve authoritative roster, worklogs, and activity for Mursaleen Cluster on target date from Jira.
        
        Steps:
        1. Query authoritative Jira group members for 'Mursaleen Cluster' (or settings.EVENT1_TEAM_GROUP).
        2. Filter out inactive accounts and canonical exclusions.
        3. Sync today's worklogs from Jira via DailyWorklogReportGenerator.sync_jira_worklogs_for_date.
        4. Query recent Jira activity (changelogs/comments/updates on target date).
        5. Build standard roster_worklogs payload for evaluate_date.
        """
        group_name = team_group or getattr(settings, "EVENT1_TEAM_GROUP", "Mursaleen Cluster")
        from app.connectors.jira.client import JiraClient
        from app.core.reports.worklog_report import DailyWorklogReportGenerator
        from app.database.repositories import JiraWorklogRepository

        client = JiraClient()
        worklog_repo = JiraWorklogRepository(self.mgr)
        excluded_ids = settings.get_canonical_excluded_account_ids()

        # 1. Authoritative group members
        try:
            raw_members = await client.get_group_members(group_name)
        except Exception as e:
            logger.error(f"Failed to fetch Jira group members for '{group_name}': {e}", exc_info=True)
            raw_members = []

        eligible_roster: Dict[str, str] = {}
        for m in raw_members:
            aid = m.get("accountId")
            name = m.get("displayName") or "Unknown"
            if not aid or not m.get("active", True):
                continue
            if aid in excluded_ids:
                if aid in self._test_recipient_overrides:
                    logger.info(
                        f"TEST_RECIPIENT_OVERRIDE event=EVENT1 jira_account_id={aid} display_name={name}"
                    )
                else:
                    continue
            eligible_roster[aid] = name

        # 2. Sync worklogs for target date
        generator = DailyWorklogReportGenerator(manager=self.mgr, client=client)
        try:
            await generator.sync_jira_worklogs_for_date(target_date)
        except Exception as e:
            logger.warning(f"Notice: Live worklog sync for {target_date} had an issue or was skipped: {e}")

        # 3. Read synced worklogs from local repository
        worklogs = worklog_repo.get_worklogs_for_date(target_date, team_group=group_name)
        if not worklogs:
            worklogs = worklog_repo.get_worklogs_for_date(target_date)

        worklog_seconds_by_user: Dict[str, int] = {aid: 0 for aid in eligible_roster}
        for w in worklogs:
            author_id = w.get("author_account_id")
            if author_id in worklog_seconds_by_user:
                worklog_seconds_by_user[author_id] += int(w.get("time_spent_seconds", 0))

        # 4. Query Jira ticket activity (comments/updates) on target date
        activity_detected_users: set = set()
        try:
            jql = f'updatedDate >= "{target_date}" AND updatedDate <= "{target_date} 23:59"'
            res = await client.search_issues(
                jql=jql,
                max_results=50,
                expand="changelog",
                fields=["comment", "updated"]
            )
            issues = res.get("issues", []) if isinstance(res, dict) else []
            for issue in issues:
                fields = issue.get("fields", {})
                # Check comment authors
                comments = (fields.get("comment") or {}).get("comments", [])
                for c in comments:
                    c_author = (c.get("author") or {}).get("accountId")
                    c_created = str(c.get("created") or "")[:10]
                    if c_author in eligible_roster and c_created == target_date:
                        activity_detected_users.add(c_author)

                # Check changelog histories
                changelog = issue.get("changelog", {})
                histories = changelog.get("histories", []) if isinstance(changelog, dict) else []
                for h in histories:
                    h_author = (h.get("author") or {}).get("accountId")
                    h_created = str(h.get("created") or "")[:10]
                    if h_author in eligible_roster and h_created == target_date:
                        activity_detected_users.add(h_author)
        except Exception as e:
            logger.debug(f"Notice: Activity query for {target_date} fallback: {e}")

        # 5. Build roster list
        roster_items: List[Dict[str, Any]] = []
        for aid, disp_name in eligible_roster.items():
            mins = worklog_seconds_by_user.get(aid, 0) // 60
            has_act = (aid in activity_detected_users) or (mins > 0)
            roster_items.append({
                "account_id": aid,
                "jira_account_id": aid,
                "display_name": disp_name,
                "time_spent_minutes": mins,
                "has_activity": has_act,
                "leave_status": "unavailable",
            })

        return roster_items

    async def is_cluster_member(self, jira_account_id: str, group_name: Optional[str] = None) -> bool:
        """Verify whether jira_account_id belongs to the authoritative Mursaleen Cluster Jira group."""
        group = group_name or getattr(settings, "EVENT1_TEAM_GROUP", "Mursaleen Cluster")
        from app.connectors.jira.client import JiraClient
        client = JiraClient()
        try:
            members = await client.get_group_members(group)
            for m in members:
                if m.get("accountId") == jira_account_id and m.get("active", True):
                    return True
        except Exception as e:
            logger.warning(f"Error querying group membership for {jira_account_id} in {group}: {e}")
        return False

    async def send_discord_approval_request(
        self,
        approval_id: str,
        display_name: str,
        jira_account_id: str,
        mattermost_user_id: str,
        target_date: str,
        worklog_minutes: int,
        has_activity: bool,
        dry_run: Optional[bool] = None,
    ) -> Optional[str]:
        """Dispatch approval request embed with interactive buttons to #pm-alerts.
        
        Strictly respects DRY_RUN: if DRY_RUN=True, returns simulated message ID without live Discord POST.
        """
        payload = DiscordFormatter.format_event1_approval_request(
            approval_id=approval_id,
            display_name=display_name,
            jira_account_id=jira_account_id,
            mattermost_user_id=mattermost_user_id,
            target_date=target_date,
            worklog_minutes=worklog_minutes,
            has_activity=has_activity,
        )

        is_dry_run = settings.DRY_RUN if dry_run is None else dry_run

        try:
            from app.connectors.discord.gateway_client import discord_gateway_client
            channel_id = settings.DISCORD_PM_CHANNEL_ID
            if not channel_id:
                channel_id = "1293539342186188835"  # default PM alerts channel fallback

            if is_dry_run:
                # Deterministic simulation without touching Discord network
                sim_result = await discord_gateway_client.send_channel_message(
                    channel_id=channel_id,
                    content="",
                    embeds=payload.get("embeds"),
                    components=payload.get("components"),
                    dry_run=True,
                )
                if sim_result and isinstance(sim_result, dict):
                    return str(sim_result.get("id", f"sim_{approval_id}"))
                return f"sim_{approval_id}"

            if discord_gateway_client and discord_gateway_client.is_connected:
                msg_data = await discord_gateway_client.send_channel_message(
                    channel_id=channel_id,
                    content="",
                    embeds=payload.get("embeds"),
                    components=payload.get("components"),
                    dry_run=False,
                )
                if msg_data and isinstance(msg_data, dict):
                    return str(msg_data.get("id", ""))
            return None
        except Exception as e:
            logger.warning(f"Notice: Failed to dispatch live Discord approval message: {e}")
            return None

    async def evaluate_date(
        self,
        target_date: Optional[str] = None,
        roster_worklogs: Optional[List[Dict[str, Any]]] = None,
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        """Run complete Event 1 evaluation for a target date across Mursaleen Cluster.
        
        CRITICAL CHANGE: For each REMINDER_CANDIDATE, do NOT immediately send a Mattermost DM.
        Instead, create a PENDING approval record and send a Discord leave verification request.
        """
        pkt_now = datetime.datetime.now(zoneinfo.ZoneInfo("Asia/Karachi"))
        resolved_date = target_date or pkt_now.strftime("%Y-%m-%d")

        # 1. Pre-execution Holiday & Weekend check
        is_suppressed, suppression_status, supp_details = self.is_date_suppressed_or_holiday(resolved_date)
        if is_suppressed:
            logger.info(f"Event 1 execution on {resolved_date} skipped: {suppression_status}")
            return {
                "target_date": resolved_date,
                "status": suppression_status,
                "details": supp_details,
                "evaluations": [],
                "approval_requests_created": 0,
                "reminders_prepared": 0,
                "actions_dispatched": 0,
            }

        # 2. Retrieve eligible roster members from Jira if not provided
        if roster_worklogs is None:
            worklogs_by_user = await self.fetch_cluster_worklogs_and_activity(resolved_date)
        else:
            worklogs_by_user = roster_worklogs

        evaluations: List[Dict[str, Any]] = []
        approval_requests_created = 0

        # Calculate 21:00 PKT cutoff
        expires_at = self.calculate_cutoff_expiry(resolved_date)

        for item in worklogs_by_user:
            acc_id = item.get("account_id") or item.get("jira_account_id", "")
            disp_name = item.get("display_name") or item.get("author_display_name", "")
            minutes = item.get("time_spent_minutes", 0)
            has_act = item.get("has_activity", minutes > 0)
            leave = item.get("leave_status", "unavailable")

            eval_res = self.evaluate_member(
                jira_account_id=acc_id,
                display_name=disp_name,
                worklog_minutes=minutes,
                has_activity=has_act,
                leave_status=leave,
                target_date=resolved_date,
            )
            evaluations.append(eval_res)

            if eval_res.get("send_reminder"):
                mm_user_id = eval_res.get("mattermost_user_id")

                # Generate deterministic SHA256 idempotency key
                key_raw = f"EVENT1:{resolved_date}:{mm_user_id}"
                idempotency_key = hashlib.sha256(key_raw.encode("utf-8")).hexdigest()[:16]

                # Deterministic approval ID
                approval_id_raw = f"APPR:{resolved_date}:{acc_id}"
                approval_id = hashlib.sha256(approval_id_raw.encode("utf-8")).hexdigest()[:16]

                # Check if approval record already exists for this (account_id, date)
                existing_approval = self.approval_repo.get_by_employee_and_date(acc_id, resolved_date)
                if existing_approval:
                    logger.debug(
                        f"Approval request already exists for {disp_name} on {resolved_date} "
                        f"(status={existing_approval.get('status')}). Skipping duplicate Discord dispatch."
                    )
                    continue

                # Persist pending approval in SQLite
                created_id, was_created = self.approval_repo.create_pending_approval(
                    approval_id=approval_id,
                    jira_account_id=acc_id,
                    mattermost_user_id=str(mm_user_id),
                    display_name=disp_name,
                    target_date=resolved_date,
                    worklog_minutes=minutes,
                    has_activity=has_act,
                    idempotency_key=idempotency_key,
                    expires_at=expires_at,
                    event_scope=EventScope.MURSALEEN_WORKLOG_REMINDER.value,
                )

                if was_created:
                    approval_requests_created += 1

                    # Dispatch Discord approval embed to #pm-alerts
                    msg_id = await self.send_discord_approval_request(
                        approval_id=approval_id,
                        display_name=disp_name,
                        jira_account_id=acc_id,
                        mattermost_user_id=str(mm_user_id),
                        target_date=resolved_date,
                        worklog_minutes=minutes,
                        has_activity=has_act,
                        dry_run=dry_run,
                    )
                    if msg_id:
                        self.approval_repo.update_discord_message_info(approval_id, msg_id)

        return {
            "target_date": resolved_date,
            "status": "COMPLETED",
            "evaluations": evaluations,
            "approval_requests_created": approval_requests_created,
            "reminders_prepared": approval_requests_created,
            "actions_dispatched": 0,  # Zero live dispatches before authorized 👍
            "dry_run": dry_run,
        }

    async def handle_approval_decision(
        self,
        approval_id: str,
        decision: str,  # 'APPROVE' or 'REJECT'
        decided_by: str,
        force_dry_run: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Process an administrator's 👍 or 👎 approval decision.
        
        Guarantees:
        - Only authorized PM admin (1293539342186188835) may decide.
        - Atomic status transition from PENDING -> APPROVED or REJECTED.
        - If REJECTED (👎), marks REJECTED, sends nothing.
        - If APPROVED (👍), performs exhaustive revalidation before calling Mattermost dispatch.
        - State machine: PENDING -> APPROVED -> DELIVERY_PENDING -> SENT (or FAILED on dispatch error).
        - Fails closed on any revalidation error or cutoff expiration.
        """
        clean_dec = decision.strip().upper()
        clean_user = decided_by.strip()
        user_id_only = clean_user.replace("discord:", "").strip()

        # 1. Administrator Authorization Check
        if not settings.is_discord_user_allowed(user_id_only):
            logger.warning(f"Unauthorized approval attempt by user '{decided_by}' for approval '{approval_id}'.")
            return {
                "success": False,
                "status": "UNAUTHORIZED",
                "message": f"❌ User `{decided_by}` is not authorized to approve Event 1 worklog reminders.",
            }

        # 2. Fetch pending approval record
        approval = self.approval_repo.get_by_id(approval_id)
        if not approval:
            return {
                "success": False,
                "status": "NOT_FOUND",
                "message": f"❌ Approval request `{approval_id}` not found.",
            }

        current_status = approval.get("status")
        target_date = approval.get("target_date", "")

        # 3. Check already resolved or invalid state
        if current_status != "PENDING":
            return {
                "success": False,
                "status": current_status,
                "message": f"⚠️ Approval request `{approval_id}` is already in status `{current_status}`.",
            }

        # 4. Check 21:00 PKT cutoff / expiry
        if self.is_past_cutoff(target_date):
            self.approval_repo.transition_status(
                approval_id=approval_id,
                target_status="EXPIRED",
                decided_by=f"discord:{user_id_only}",
                decision_reason="Approval received after 21:00 PKT cutoff",
                expected_status="PENDING",
            )
            return {
                "success": False,
                "status": "EXPIRED",
                "message": f"❌ Approval request expired (past 21:00 PKT cutoff on `{target_date}`). No message sent.",
            }

        # ----------------------------------------------------------------------
        # Case A: REJECT (👎)
        # ----------------------------------------------------------------------
        if clean_dec in ("REJECT", "REJECTED", "THUMBS_DOWN", "DOWN"):
            success = self.approval_repo.transition_status(
                approval_id=approval_id,
                target_status="REJECTED",
                decided_by=f"discord:{user_id_only}",
                decision_reason="Rejected by administrator via Discord 👎",
                expected_status="PENDING",
            )
            if not success:
                return {
                    "success": False,
                    "status": "CONCURRENT_CONFLICT",
                    "message": "⚠️ Request was concurrently updated by another process.",
                }
            logger.info(f"Event 1 reminder for {approval.get('display_name')} on {target_date} REJECTED by {decided_by}.")
            return {
                "success": True,
                "status": "REJECTED",
                "message": f"🛑 Reminder for **{approval.get('display_name')}** on `{target_date}` rejected. No Mattermost message will be sent.",
            }

        # ----------------------------------------------------------------------
        # Case B: APPROVE (👍)
        # ----------------------------------------------------------------------
        if clean_dec not in ("APPROVE", "APPROVED", "THUMBS_UP", "UP"):
            return {
                "success": False,
                "status": "INVALID_DECISION",
                "message": f"❌ Unknown decision `{decision}`. Expected APPROVE or REJECT.",
            }

        # Atomically transition PENDING -> APPROVED
        transition_ok = self.approval_repo.transition_status(
            approval_id=approval_id,
            target_status="APPROVED",
            decided_by=f"discord:{user_id_only}",
            decision_reason="Approved by administrator via Discord 👍",
            expected_status="PENDING",
        )
        if not transition_ok:
            return {
                "success": False,
                "status": "CONCURRENT_CONFLICT",
                "message": "⚠️ Request was concurrently resolved or modified.",
            }

        # ----------------------------------------------------------------------
        # Exhaustive Revalidation Immediately Before Dispatch
        # ----------------------------------------------------------------------
        jira_acc_id = approval.get("jira_account_id")
        disp_name = approval.get("display_name")
        mm_user_id = approval.get("mattermost_user_id")
        idempotency_key = approval.get("idempotency_key")

        # 1. Revalidate Canonical Exclusions
        if settings.is_canonical_excluded(jira_acc_id, disp_name):
            if jira_acc_id in self._test_recipient_overrides:
                logger.info(
                    f"TEST_RECIPIENT_OVERRIDE event=EVENT1 jira_account_id={jira_acc_id} display_name={disp_name} revalidation_permitted=True"
                )
            else:
                err_msg = f"Revalidation failed: Recipient {disp_name} ({jira_acc_id}) is canonically excluded."
                logger.warning(err_msg)
                self.approval_repo.record_error(approval_id, err_msg)
                return {"success": False, "status": "EXCLUDED", "message": f"🛑 {err_msg} Send cancelled."}

        # 2. Revalidate Weekend / Official Holiday / Manual Suppression
        is_supp, supp_status, _ = self.is_date_suppressed_or_holiday(target_date)
        if is_supp:
            err_msg = f"Revalidation failed: Date {target_date} is {supp_status}."
            logger.warning(err_msg)
            self.approval_repo.record_error(approval_id, err_msg)
            return {"success": False, "status": "BLOCKED", "message": f"🛑 {err_msg} Send cancelled."}

        # 3. Revalidate Active Jira -> Mattermost Mapping
        resolved_mm_id, _ = self.mapping_service.resolve_jira_to_mattermost(jira_acc_id, disp_name)
        if not resolved_mm_id or resolved_mm_id != mm_user_id:
            err_msg = f"Revalidation failed: Mapping for {disp_name} changed or is invalid."
            logger.warning(err_msg)
            self.approval_repo.record_error(approval_id, err_msg)
            return {"success": False, "status": "MAPPING_INVALID", "message": f"🛑 {err_msg} Send cancelled."}

        # 4. Revalidate Authoritative Cluster Membership
        is_member = await self.is_cluster_member(jira_acc_id)
        if not is_member:
            err_msg = f"Revalidation failed: User {disp_name} ({jira_acc_id}) is not an active member of Mursaleen Cluster."
            logger.warning(err_msg)
            self.approval_repo.record_error(approval_id, err_msg)
            return {"success": False, "status": "NON_CLUSTER_MEMBER", "message": f"🛑 {err_msg} Send cancelled."}

        # 5. Transition to DELIVERY_PENDING before network dispatch
        claim_ok = self.approval_repo.transition_status(
            approval_id=approval_id,
            target_status="DELIVERY_PENDING",
            decided_by=f"discord:{user_id_only}",
            decision_reason="Dispatch claimed; initiating delivery",
            expected_status="APPROVED",
        )
        if not claim_ok:
            return {
                "success": False,
                "status": "CONCURRENT_CONFLICT",
                "message": "⚠️ Could not transition to DELIVERY_PENDING (already claimed or resolved).",
            }

        # 6. Determine Effective Dry Run
        effective_dry_run = settings.DRY_RUN if force_dry_run is None else force_dry_run

        # 7. Build and execute Action via ActionEngine with EventScope.MURSALEEN_WORKLOG_REMINDER
        if "mattermost" not in action_engine._connectors:
            from app.connectors.mattermost.connector import MattermostConnector
            action_engine.register_connector(MattermostConnector())

        action = create_send_message_action(
            target_system="mattermost",
            target_id=str(mm_user_id),
            text=EVENT1_REMINDER_TEXT,
            requested_by="Event1_LeaveVerificationApproval",
        )
        action.idempotency_key = idempotency_key
        action.dry_run = effective_dry_run
        action.parameters["event_scope"] = EventScope.MURSALEEN_WORKLOG_REMINDER.value
        action.parameters["jira_user_id"] = jira_acc_id
        action.parameters["display_name"] = disp_name
        if jira_acc_id in self._test_recipient_overrides:
            action.parameters["test_recipient_override"] = True

        try:
            result = await action_engine.execute(action)
        except Exception as ex:
            err_msg = f"ActionEngine dispatch failed: {ex}"
            logger.error(err_msg, exc_info=True)
            self.approval_repo.mark_failed(approval_id, err_msg)
            return {"success": False, "status": "DISPATCH_FAILED", "message": f"❌ {err_msg}"}

        if result.success or result.status in (ActionStatus.COMPLETED, ActionStatus.DRY_RUN_SIMULATED):
            # Atomically mark SENT
            dispatch_summary = json.dumps({"status": result.status.value, "action_id": result.action_id, "dry_run": result.dry_run})
            self.approval_repo.mark_sent(approval_id, dispatch_result=dispatch_summary)
            logger.info(f"Event 1 reminder successfully dispatched for {disp_name} (dry_run={result.dry_run}).")
            return {
                "success": True,
                "status": "SENT",
                "message": (
                    f"✅ Reminder authorized and dispatched for **{disp_name}** on `{target_date}` "
                    f"({'(DRY RUN simulation)' if result.dry_run else 'LIVE'})."
                ),
            }
        else:
            err_details = result.error_message or "ActionEngine failed"
            self.approval_repo.mark_failed(approval_id, err_details)
            return {
                "success": False,
                "status": result.status.value,
                "message": f"❌ Dispatch error: {err_details}",
            }


# Global singleton service
worklog_reminder_service = WorklogReminderService()
