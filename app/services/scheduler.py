import asyncio
from datetime import datetime, timedelta
import json
import zoneinfo
from typing import Any, Dict, List, Optional
from app.core.events.types import StaleTask, OverdueTask
from app.core.rules.engine import rules_engine
from app.core.actions.engine import action_engine
from app.core.actions.types import create_add_comment_action, create_send_notification_action
from app.database.repositories import EventRepository, JiraIssueStateRepository
from app.database.connection import db_manager, DatabaseManager
from app.config.settings import settings
from app.connectors.discord.formatter import DiscordFormatter, COLOR_ATTENTION
from app.services.notification_deduplication import notification_dedup_service
from app.services.epic_review_service import EpicReviewService
from app.utils.time import utc_now, parse_iso_datetime, hours_between, calculate_business_days, utc_now_iso
from app.utils.logger import logger


class PeriodicScheduler:
    """In-process background worker orchestrating independent periodic tasks:

    1. Jira Polling (every JIRA_POLLING_INTERVAL_MINUTES, default 2m)
    2. Stale Task & Overdue Task Rule Evaluation (every SCHEDULER_INTERVAL_MINUTES, default 15m)
    """

    def __init__(self, manager: Optional[DatabaseManager] = None):
        self.mgr = manager or db_manager
        self.event_repo = EventRepository(self.mgr)
        self.issue_state_repo = JiraIssueStateRepository(self.mgr)
        from app.services.notification_deduplication import NotificationDeduplicationService
        from app.core.retention.service import RetentionService
        self.dedup_service = NotificationDeduplicationService(self.mgr)
        self.retention_service = RetentionService(self.mgr)
        self._running = False
        self._rules_task: Optional[asyncio.Task] = None
        self._polling_task: Optional[asyncio.Task] = None
        self._interval_minutes = settings.SCHEDULER_INTERVAL_MINUTES
        self._polling_interval_minutes = settings.JIRA_POLLING_INTERVAL_MINUTES
        self._last_performance_analysis_at: Optional[datetime] = None
        self._last_daily_retention_date: Optional[str] = None
        self._last_monday_retention_date: Optional[str] = None

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        """Start the background scheduler tasks."""
        if self._running:
            logger.warning("PeriodicScheduler is already running.")
            return
        self._running = True

        # Independent timer 1: Rules evaluation loop (e.g. every 15 min)
        self._rules_task = asyncio.create_task(self._run_rules_loop())

        # Independent timer 2: Jira Polling loop (e.g. every 2 min)
        if settings.JIRA_POLLING_ENABLED:
            self._polling_task = asyncio.create_task(self._run_polling_loop())

        logger.info(
            f"PeriodicScheduler started (Rules interval: {self._interval_minutes}m, "
            f"Jira polling interval: {self._polling_interval_minutes}m, "
            f"polling enabled: {settings.JIRA_POLLING_ENABLED})."
        )

    def stop(self) -> None:
        """Stop the background scheduler tasks."""
        self._running = False
        if self._rules_task and not self._rules_task.done():
            self._rules_task.cancel()
            self._rules_task = None
        if self._polling_task and not self._polling_task.done():
            self._polling_task.cancel()
            self._polling_task = None
        logger.info("PeriodicScheduler stopped.")

    async def _run_rules_loop(self) -> None:
        """Main loop for periodic stale/overdue rule evaluations."""
        while self._running:
            try:
                await self.run_cycle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Unexpected error during scheduler rules cycle: {e}", exc_info=True)

            try:
                await asyncio.sleep(self._interval_minutes * 60)
            except asyncio.CancelledError:
                break

    async def _run_polling_loop(self) -> None:
        """Independent lightweight loop for periodic Jira polling."""
        # Initial short pause to allow application startup to finish cleanly
        await asyncio.sleep(2)
        while self._running:
            try:
                await self.run_poll_cycle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Unexpected error during Jira polling cycle: {e}", exc_info=True)

            try:
                await asyncio.sleep(self._polling_interval_minutes * 60)
            except asyncio.CancelledError:
                break

    async def run_poll_cycle(self) -> Dict[str, Any]:
        """Execute a single Jira polling cycle via orchestrator."""
        from app.services.orchestrator import orchestrator
        return await orchestrator.jira_poller.poll()

    async def run_cycle(self) -> Dict[str, Any]:
        """Execute a single time-based rules evaluation cycle immediately."""
        logger.info("Starting scheduler evaluation cycle for time-based rules...")
        stale_evaluated = 0
        overdue_evaluated = 0
        actions_dispatched = 0

        # 1. Evaluate Stale Tasks from local Jira state projection
        try:
            stale_actions = await self._evaluate_stale_tasks()
            stale_evaluated = len(stale_actions)
            for act in stale_actions:
                await action_engine.execute(act)
                actions_dispatched += 1
        except Exception as e:
            logger.error(f"Error during StaleTask evaluation: {e}", exc_info=True)

        # 2. Evaluate Overdue Tasks from local Jira state projection
        try:
            overdue_actions = await self._evaluate_overdue_tasks()
            overdue_evaluated = len(overdue_actions)
            for act in overdue_actions:
                await action_engine.execute(act)
                actions_dispatched += 1
        except Exception as e:
            logger.error(f"Error during OverdueTask evaluation: {e}", exc_info=True)

        # 3. Evaluate Scheduled Daily Worklog Report
        daily_report_status = None
        try:
            daily_report_res = await self._evaluate_daily_worklog_report()
            if daily_report_res:
                daily_report_status = daily_report_res.get("status")
        except Exception as e:
            logger.error(f"Error during Daily Worklog Report evaluation: {e}", exc_info=True)

        # 4. Evaluate Scheduled Daily Overdue Digest
        daily_overdue_status = None
        try:
            overdue_digest_res = await self._evaluate_daily_overdue_digest()
            if overdue_digest_res:
                daily_overdue_status = overdue_digest_res.get("status")
        except Exception as e:
            logger.error(f"Error during Daily Overdue Digest evaluation: {e}", exc_info=True)

        # 5. Evaluate Scheduled Daily PM Attention Digest
        daily_attention_status = None
        try:
            attention_digest_res = await self._evaluate_daily_pm_attention_digest()
            if attention_digest_res:
                daily_attention_status = attention_digest_res.get("status")
        except Exception as e:
            logger.error(f"Error during Daily PM Attention Digest evaluation: {e}", exc_info=True)

        # 5b. Evaluate Scheduled Mubashir Automation Report
        mubashir_report_status = None
        try:
            mubashir_report_res = await self._evaluate_mubashir_automation_report()
            if mubashir_report_res:
                mubashir_report_status = mubashir_report_res.get("status")
        except Exception as e:
            logger.error(f"Error during Mubashir Automation Report evaluation: {e}", exc_info=True)

        # 5c. Evaluate Scheduled Daily Activity Report
        daily_activity_status = None
        try:
            activity_report_res = await self._evaluate_daily_activity_report()
            if activity_report_res:
                daily_activity_status = activity_report_res.get("status")
        except Exception as e:
            logger.error(f"Error during Daily Activity Report evaluation: {e}", exc_info=True)

        # 6. Evaluate Scheduled Performance Foundation Analysis
        perf_analysis_status = None
        try:
            perf_analysis_res = await self._evaluate_performance_analysis()
            if perf_analysis_res:
                perf_analysis_status = perf_analysis_res.get("status")
        except Exception as e:
            logger.error(f"Error during Performance Foundation Analysis evaluation: {e}", exc_info=True)

        # 7. Evaluate Mubashir Stale Support Tickets (3 business days threshold)
        mubashir_stale_count = 0
        try:
            mubashir_actions = await self._evaluate_mubashir_stale_support_tickets()
            mubashir_stale_count = len(mubashir_actions)
            for act in mubashir_actions:
                await action_engine.execute(act)
                actions_dispatched += 1
        except Exception as e:
            logger.error(f"Error during Mubashir stale support ticket evaluation: {e}", exc_info=True)

        # 8. Evaluate Scheduled Active Epic Review
        epic_review_status = None
        try:
            epic_review_res = await self._evaluate_active_epic_review()
            if epic_review_res:
                epic_review_status = epic_review_res.get("status")
        except Exception as e:
            logger.error(f"Error during Active Epic review evaluation: {e}", exc_info=True)

        # 9. Evaluate Scheduled Daily Data Retention Maintenance
        daily_retention_status = None
        try:
            daily_retention_res = await self._evaluate_daily_retention()
            if daily_retention_res:
                daily_retention_status = daily_retention_res.get("status")
        except Exception as e:
            logger.error(f"Error during Daily Data Retention evaluation: {e}", exc_info=True)

        # 10. Evaluate Scheduled Monday Raw Event Retention Maintenance
        monday_retention_status = None
        try:
            monday_retention_res = await self._evaluate_monday_retention()
            if monday_retention_res:
                monday_retention_status = monday_retention_res.get("status")
        except Exception as e:
            logger.error(f"Error during Monday Raw Event Retention evaluation: {e}", exc_info=True)

        logger.info(
            f"Scheduler cycle complete. Evaluated: {stale_evaluated} stale, "
            f"{overdue_evaluated} overdue, {mubashir_stale_count} mubashir stale. "
            f"Actions dispatched: {actions_dispatched}. "
            f"Daily worklog status: {daily_report_status or 'idle'}, "
            f"Daily overdue status: {daily_overdue_status or 'idle'}, "
            f"Daily attention status: {daily_attention_status or 'idle'}, "
            f"Mubashir automation report status: {mubashir_report_status or 'idle'}, "
            f"Daily activity status: {daily_activity_status or 'idle'}, "
            f"Performance analysis status: {perf_analysis_status or 'idle'}, "
            f"Epic review status: {epic_review_status or 'idle'}, "
            f"Daily retention status: {daily_retention_status or 'idle'}, "
            f"Monday retention status: {monday_retention_status or 'idle'}"
        )
        return {
            "timestamp": utc_now_iso(),
            "stale_actions": stale_evaluated,
            "overdue_actions": overdue_evaluated,
            "mubashir_stale_actions": mubashir_stale_count,
            "actions_dispatched": actions_dispatched,
            "daily_report_status": daily_report_status,
            "daily_overdue_status": daily_overdue_status,
            "daily_attention_status": daily_attention_status,
            "mubashir_report_status": mubashir_report_status,
            "daily_activity_status": daily_activity_status,
            "performance_analysis_status": perf_analysis_status,
            "epic_review_status": epic_review_status,
            "daily_retention_status": daily_retention_status,
            "monday_retention_status": monday_retention_status,
        }

    def _sync_recent_events_to_projection(self) -> None:
        """Sync recent raw events into local jira_issue_state projection cache if not already present."""
        try:
            team_group = settings.JIRA_TEAM_GROUP.strip() if settings.is_jira_team_group_configured() else None
            recent_events = self.event_repo.list_events(limit=50)
            for e in reversed(recent_events):
                tkey = e.get("task_key") or e.get("task_id")
                if not tkey or self.issue_state_repo.get(tkey):
                    continue
                payload = e.get("payload", {})
                issue_data = payload.get("issue", {})
                fields = issue_data.get("fields", {}) if isinstance(issue_data.get("fields"), dict) else {}
                status_name = fields.get("status", {}).get("name") or payload.get("new_status") or "Unknown"
                assignee = fields.get("assignee") or {}
                components_raw = fields.get("components", []) if isinstance(fields.get("components"), list) else []
                components_list = [c.get("name") if isinstance(c, dict) else str(c) for c in components_raw]
                labels_list = fields.get("labels", []) if isinstance(fields.get("labels"), list) else []
                subtasks_raw = fields.get("subtasks", []) if isinstance(fields.get("subtasks"), list) else []
                orig_est_secs = fields.get("timeoriginalestimate") or (fields.get("timetracking", {}).get("originalEstimateSeconds") if isinstance(fields.get("timetracking"), dict) else None)
                time_spent_secs = fields.get("timespent") or (fields.get("timetracking", {}).get("timeSpentSeconds") if isinstance(fields.get("timetracking"), dict) else None)
                creator_obj = fields.get("creator") or fields.get("reporter") or {}
                creator_id = creator_obj.get("accountId") or creator_obj.get("name") if isinstance(creator_obj, dict) else str(creator_obj)

                self.issue_state_repo.upsert(
                    jira_issue_key=tkey,
                    summary=fields.get("summary") or "Task",
                    status=status_name,
                    assignee=assignee.get("displayName") or assignee.get("name") or e.get("actor_name"),
                    due_date=fields.get("duedate"),
                    last_seen_at=e.get("timestamp"),
                    last_activity_at=e.get("timestamp"),
                    raw_reference=None,
                    team_group=team_group,
                    issue_type=fields.get("issuetype", {}).get("name", "Task") if isinstance(fields.get("issuetype"), dict) else "Task",
                    labels=labels_list,
                    components=components_list,
                    subtask_count=len(subtasks_raw),
                    original_estimate_seconds=orig_est_secs,
                    time_spent_seconds=time_spent_secs,
                    creator_id=creator_id
                )
        except Exception as err:
            logger.debug(f"Event sync to projection failed: {err}")

    async def _evaluate_stale_tasks(self) -> List[Any]:
        """Find active tasks in 'In Progress' whose last meaningful activity exceeds threshold."""
        self._sync_recent_events_to_projection()
        actions = []
        team_group = settings.JIRA_TEAM_GROUP.strip() if settings.is_jira_team_group_configured() else None
        stale_candidates = self.issue_state_repo.get_stale_candidates(
            threshold_hours=settings.STALE_TASK_HOURS,
            team_group=team_group
        )

        for item in stale_candidates:
            tkey = item.get("jira_issue_key")
            last_activity_str = item.get("last_activity_at")
            if not tkey or not last_activity_str:
                continue

            last_dt = parse_iso_datetime(last_activity_str)
            if not last_dt:
                continue

            hours_inactive = hours_between(last_dt)
            if hours_inactive >= settings.STALE_TASK_HOURS:
                stale_event = StaleTask(
                    source="scheduler",
                    task_key=tkey,
                    task_title=item.get("summary") or "Task in Progress",
                    assignee_name=item.get("assignee"),
                    hours_inactive=hours_inactive,
                    threshold_hours=settings.STALE_TASK_HOURS
                )
                acts = rules_engine.evaluate_event(stale_event)
                actions.extend(acts)

        return actions

    async def _evaluate_overdue_tasks(self) -> List[Any]:
        """Find active tasks with due date in the past using local state projection."""
        self._sync_recent_events_to_projection()
        actions = []
        team_group = settings.JIRA_TEAM_GROUP.strip() if settings.is_jira_team_group_configured() else None
        overdue_candidates = self.issue_state_repo.get_overdue_candidates(team_group=team_group)

        for item in overdue_candidates:
            tkey = item.get("jira_issue_key")
            due_date_str = item.get("due_date")
            status_name = item.get("status") or "Unknown"

            if not tkey or not due_date_str:
                continue

            overdue_event = OverdueTask(
                source="scheduler",
                task_key=tkey,
                task_title=item.get("summary", "Task"),
                assignee_name=item.get("assignee"),
                due_date=due_date_str,
                current_status=status_name
            )
            acts = rules_engine.evaluate_event(overdue_event)
            actions.extend(acts)

        return actions

    async def _evaluate_daily_worklog_report(self) -> Optional[Dict[str, Any]]:
        """Check if daily worklog report should be triggered based on scheduled time (23:59 Asia/Karachi).

        Handles midnight boundary: if the scheduler ticks after midnight but within
        a grace window, the report fires for the previous calendar day (the intended date).
        """
        if not settings.DAILY_WORKLOG_REPORT_ENABLED:
            return None

        try:
            tz_str = settings.DAILY_WORKLOG_REPORT_TIMEZONE or settings.get_report_timezone()
            tz = zoneinfo.ZoneInfo(tz_str)
            now_tz = datetime.now(tz)
        except Exception:
            now_tz = datetime.now()

        current_time_str = now_tz.strftime("%H:%M")
        scheduled_time = (settings.DAILY_WORKLOG_REPORT_TIME or settings.get_worklog_report_time()).strip()

        # Determine the report date based on scheduled time and current time:
        # If scheduled time is post-midnight (e.g. 00:00–06:00), or if current time is post-midnight,
        # the Daily Worklog Report reports on the COMPLETED PREVIOUS CALENDAR DAY.
        # Otherwise, if scheduled late in the evening (>= 18:00) and current_time >= scheduled_time,
        # it reports on today.
        sched_hour = 0
        sched_min = 15
        try:
            parts = scheduled_time.split(":")
            sched_hour = int(parts[0])
            sched_min = int(parts[1]) if len(parts) > 1 else 0
        except Exception:
            pass

        report_date_str = None
        if sched_hour < 12:
            # Scheduled morning/post-midnight (e.g. 00:15 PKT)
            # Must only fire once current time reaches scheduled time
            if (now_tz.hour * 60 + now_tz.minute) < (sched_hour * 60 + sched_min):
                return None
            # Target is explicitly the completed PREVIOUS calendar day
            yesterday_tz = now_tz - timedelta(days=1)
            report_date_str = yesterday_tz.strftime("%Y-%m-%d")
        else:
            # Scheduled evening (e.g. 23:59 PKT)
            GRACE_WINDOW_MINUTES = 90
            if current_time_str >= scheduled_time:
                report_date_str = now_tz.strftime("%Y-%m-%d")
            elif now_tz.hour * 60 + now_tz.minute < GRACE_WINDOW_MINUTES:
                yesterday_tz = now_tz - timedelta(days=1)
                report_date_str = yesterday_tz.strftime("%Y-%m-%d")
            else:
                return None

        from app.core.reports.worklog_report import DailyWorklogReportGenerator
        generator = DailyWorklogReportGenerator(manager=self.mgr)
        team_group = settings.JIRA_TEAM_GROUP.strip() if settings.is_jira_team_group_configured() else "Team"

        # Check idempotency: skip if already sent for this report date
        if not generator.history_repo.has_report_been_sent(team_group, report_date_str):
            logger.info(
                f"Triggering scheduled daily worklog report for team '{team_group}' on {report_date_str} "
                f"(current_time={current_time_str}, scheduled_time={scheduled_time})"
            )
            return await generator.send_report_to_discord(
                target_date=report_date_str,
                force=False,
                sync_jira=True
            )
        return None

    async def _evaluate_daily_overdue_digest(self) -> Optional[Dict[str, Any]]:
        """Check if daily overdue digest should be triggered based on scheduled time (08:40 Asia/Karachi)."""
        if not settings.OVERDUE_DIGEST_ENABLED:
            return None

        try:
            tz_str = settings.OVERDUE_DIGEST_TIMEZONE or settings.get_report_timezone()
            tz = zoneinfo.ZoneInfo(tz_str)
            now_tz = datetime.now(tz)
        except Exception:
            now_tz = datetime.now()

        today_str = now_tz.strftime("%Y-%m-%d")
        current_time_str = now_tz.strftime("%H:%M")

        scheduled_time = (settings.OVERDUE_DIGEST_TIME or settings.get_default_report_time()).strip()
        if current_time_str >= scheduled_time:
            from app.core.reports.overdue_report import DailyOverdueReportGenerator
            generator = DailyOverdueReportGenerator(manager=self.mgr)
            team_group = settings.JIRA_TEAM_GROUP.strip() if settings.is_jira_team_group_configured() else "Mursaleen Cluster"

            # Check persistent idempotency: skip if already sent today
            if not generator.history_repo.has_report_been_sent(team_group, today_str, report_type="overdue_digest"):
                logger.info(
                    f"Triggering scheduled daily overdue digest for team '{team_group}' on {today_str} "
                    f"(current_time={current_time_str}, scheduled_time={scheduled_time})"
                )
                return await generator.send_digest_to_discord(
                    target_date=today_str,
                    force=False,
                    record_history=True
                )
        return None

    async def _evaluate_daily_pm_attention_digest(self) -> Optional[Dict[str, Any]]:
        """Check if daily PM Attention Digest should be triggered based on scheduled time (08:40 Asia/Karachi)."""
        if not settings.PM_ATTENTION_DIGEST_ENABLED:
            return None

        try:
            tz_str = settings.PM_ATTENTION_DIGEST_TIMEZONE or settings.get_report_timezone()
            tz = zoneinfo.ZoneInfo(tz_str)
            now_tz = datetime.now(tz)
        except Exception:
            now_tz = datetime.now()

        today_str = now_tz.strftime("%Y-%m-%d")
        current_time_str = now_tz.strftime("%H:%M")

        scheduled_time = (settings.PM_ATTENTION_DIGEST_TIME or settings.get_default_report_time()).strip()
        if current_time_str >= scheduled_time:
            from app.core.reports.attention_report import DailyPMAttentionReportGenerator
            generator = DailyPMAttentionReportGenerator(manager=self.mgr)
            team_group = settings.JIRA_TEAM_GROUP.strip() if settings.is_jira_team_group_configured() else "Mursaleen Cluster"

            # Check persistent idempotency: skip if already sent today
            if not generator.history_repo.has_report_been_sent(team_group, today_str, report_type="pm_attention_digest"):
                logger.info(
                    f"Triggering scheduled daily PM Attention Digest for team '{team_group}' on {today_str} "
                    f"(current_time={current_time_str}, scheduled_time={scheduled_time})"
                )
                return await generator.send_digest_to_discord(
                    target_date=today_str,
                    force=False,
                    record_history=True
                )
        return None

    async def _evaluate_mubashir_automation_report(self) -> Optional[Dict[str, Any]]:
        """Check if daily Mubashir Automation Report should be triggered for previous calendar day."""
        if not settings.MUBASHIR_AUTOMATION_REPORT_ENABLED:
            return None

        try:
            tz_str = settings.MUBASHIR_AUTOMATION_REPORT_TIMEZONE or settings.get_report_timezone()
            tz = zoneinfo.ZoneInfo(tz_str)
            now_tz = datetime.now(tz)
        except Exception:
            tz = zoneinfo.ZoneInfo("Asia/Karachi")
            now_tz = datetime.now(tz)

        current_time_str = now_tz.strftime("%H:%M")
        scheduled_time = (settings.MUBASHIR_AUTOMATION_REPORT_TIME or "08:40").strip()

        if current_time_str < scheduled_time:
            return None

        # Critical Date Rule: target date is PREVIOUS calendar day in configured timezone
        yesterday_tz = now_tz - timedelta(days=1)
        target_date_str = yesterday_tz.strftime("%Y-%m-%d")
        team_group = "Mursaleen Cluster"

        try:
            from app.core.reports.mubashir_report import MubashirAutomationReportGenerator
            generator = MubashirAutomationReportGenerator(manager=self.mgr)

            # Check persistent idempotency: skip if already sent for that previous-day date
            if generator.history_repo.has_report_been_sent(
                team_group=team_group,
                report_date=target_date_str,
                report_type="mubashir_automation_report",
            ):
                logger.debug(
                    f"Mubashir Automation Report for '{team_group}' on {target_date_str} already recorded. Skipping."
                )
                return {
                    "status": "skipped_duplicate",
                    "date": target_date_str,
                    "team_name": team_group,
                }

            logger.info(
                f"Triggering scheduled Mubashir Automation Report for '{team_group}' on {target_date_str} "
                f"(current_time={current_time_str}, scheduled_time={scheduled_time})"
            )
            result = await generator.send_report_to_discord(
                target_date=target_date_str,
                force=False,
                record_history=True,
            )
            logger.info(
                f"Mubashir Automation Report evaluation complete: status={result.get('status')} "
                f"(date={target_date_str}, comments={result.get('total_comments', 0)})"
            )
            return result
        except Exception as e:
            logger.error(f"Error evaluating Mubashir Automation Report: {e}", exc_info=True)
            return {
                "status": "failed",
                "date": target_date_str,
                "team_name": team_group,
                "error": str(e),
            }

    async def _evaluate_daily_activity_report(self) -> Optional[Dict[str, Any]]:
        """Check if daily activity report should be triggered for previous calendar day."""
        if not settings.DAILY_ACTIVITY_REPORT_ENABLED:
            return None

        try:
            tz_str = settings.DAILY_ACTIVITY_REPORT_TIMEZONE or settings.get_report_timezone()
            tz = zoneinfo.ZoneInfo(tz_str)
            now_tz = datetime.now(tz)
        except Exception:
            tz = zoneinfo.ZoneInfo("Asia/Karachi")
            now_tz = datetime.now(tz)

        current_time_str = now_tz.strftime("%H:%M")
        scheduled_time = (settings.DAILY_ACTIVITY_REPORT_TIME or settings.get_default_report_time()).strip()

        if current_time_str < scheduled_time:
            return None

        # Critical Date Rule: target date is PREVIOUS calendar day in configured timezone
        # A morning report (e.g. 08:40) summarizes the completed previous day's activity.
        yesterday_tz = now_tz - timedelta(days=1)
        target_date_str = yesterday_tz.strftime("%Y-%m-%d")

        from app.core.reports.daily_report import DailyActivityReportGenerator
        generator = DailyActivityReportGenerator(manager=self.mgr)
        team_group = settings.JIRA_TEAM_GROUP.strip() if settings.is_jira_team_group_configured() else "Mursaleen Cluster"

        # Check persistent idempotency: skip if already sent for that previous-day date
        if not generator.history_repo.has_report_been_sent(team_group, target_date_str, report_type="daily_activity_report"):
            logger.info(
                f"Triggering scheduled daily activity report for team '{team_group}' on {target_date_str} "
                f"(current_time={current_time_str}, scheduled_time={scheduled_time})"
            )
            return await generator.send_report_to_discord(
                target_date=target_date_str,
                force=False,
                record_history=True
            )
        return None

    async def _evaluate_performance_analysis(self) -> Optional[Dict[str, Any]]:
        """Run performance data foundation analysis if enabled and interval elapsed."""
        if not settings.PERFORMANCE_ANALYSIS_ENABLED:
            return None

        now = utc_now()
        interval_minutes = settings.PERFORMANCE_ANALYSIS_INTERVAL_MINUTES
        if self._last_performance_analysis_at:
            elapsed = (now - self._last_performance_analysis_at).total_seconds() / 60.0
            if elapsed < interval_minutes:
                return None

        from app.core.performance.engine import PerformanceAnalysisEngine
        engine = PerformanceAnalysisEngine(manager=self.mgr)
        team_group = settings.JIRA_TEAM_GROUP.strip() if settings.is_jira_team_group_configured() else None

        logger.info(f"Triggering scheduled performance analysis (team_group={team_group})...")
        run_res = engine.run_analysis(team_group=team_group)
        self._last_performance_analysis_at = now
        return {"status": run_res.status, "analysis_run_id": run_res.analysis_run_id}

    async def _evaluate_mubashir_stale_support_tickets(self) -> List[Any]:
        """Find internal Support tickets reported by and assigned to Mubashir, not Ready to Release/Done, inactive for >= 3 business days."""
        if not settings.MUBASHIR_STALE_SUPPORT_ENABLED:
            return []
        self._sync_recent_events_to_projection()
        actions = []
        now_tz = datetime.now(zoneinfo.ZoneInfo("Asia/Karachi"))

        # Issue 2 locked requirement:
        # 1. Issue type: Support (Support, "Support Ticket", "Customer Support", "helpdesk")
        # 2. Reporter: Mubashir Butt
        # 3. Assignee: Mubashir Butt
        # 4. Status: status != "Ready to Release" AND statusCategory != Done
        # 5. Inactivity: >= 3 business days
        mubashir_account_id = "712020:e268bcd8-d981-4b4d-992d-d5694745df8b"
        excluded_statuses = {"ready to release", "done", "completed", "resolved", "closed"}

        # Search candidates in Jira directly if client is available or local projection
        candidate_issues: List[Dict[str, Any]] = []

        # 1. Try Live Jira query if configured
        try:
            if settings.is_jira_configured():
                from app.connectors.jira.client import JiraClient
                jclient = JiraClient()
                jql = (
                    f'reporter = "{mubashir_account_id}" '
                    f'AND assignee = "{mubashir_account_id}" '
                    f'AND issuetype in (Support, "Support Ticket", "Customer Support", "helpdesk") '
                    f'AND status != "Ready to Release" '
                    f'AND statusCategory != Done '
                    f'ORDER BY updated ASC'
                )
                res = await jclient.search_issues(jql=jql, max_results=50, fields=["summary", "status", "reporter", "assignee", "updated", "issuetype"])
                candidate_issues = res.get("issues", []) if isinstance(res, dict) else []
        except Exception as e:
            logger.debug(f"Live Jira search for Mubashir stale support tickets notice: {e}")

        # 2. Fallback to local jira_issue_state projection cache
        if not candidate_issues:
            with self.mgr.session() as conn:
                cur = conn.execute("SELECT * FROM jira_issue_state")
                for r in cur.fetchall():
                    d = dict(r)
                    raw = json.loads(d["raw_reference"]) if d.get("raw_reference") else {}
                    fields = raw.get("fields", {}) if isinstance(raw, dict) else {}
                    
                    # Reporter check: check reporter, creator in fields or raw
                    reporter = fields.get("reporter") or raw.get("reporter") or fields.get("creator") or raw.get("creator") or {}
                    reporter_id = d.get("reporter_account_id") or d.get("creator_id") or (
                        reporter.get("accountId") or reporter.get("name")
                        if isinstance(reporter, dict) else str(reporter)
                    )
                    
                    # Assignee check: check assignee in fields, raw, or projection
                    assignee = fields.get("assignee") or raw.get("assignee") or {}
                    assignee_id = d.get("assignee_account_id") or (
                        assignee.get("accountId") or assignee.get("name")
                        if isinstance(assignee, dict) else str(assignee)
                    )
                    # If assignee was not recorded in mock projection, fall back to reporter
                    if not assignee_id:
                        assignee_id = reporter_id
                        assignee = reporter

                    # Issue type check
                    itype = d.get("issue_type") or (
                        fields.get("issuetype", {}).get("name", "")
                        if isinstance(fields.get("issuetype"), dict)
                        else str(raw.get("issue_type", ""))
                    )

                    # Both reporter and assignee must be Mubashir Butt, and issuetype must be Support
                    if (
                        reporter_id == mubashir_account_id
                        and assignee_id == mubashir_account_id
                        and itype.lower() in ("support", "support ticket", "customer support", "helpdesk")
                    ):
                        st_name = d.get("status", "").strip().lower()
                        if st_name not in excluded_statuses:
                            candidate_issues.append({
                                "key": d["jira_issue_key"],
                                "fields": {
                                    "summary": d.get("summary", "Support Ticket"),
                                    "status": {"name": d.get("status")},
                                    "updated": d.get("last_activity_at") or d.get("updated_at"),
                                    "reporter": reporter,
                                    "assignee": assignee,
                                },
                                "last_activity_at": d.get("last_activity_at") or d.get("updated_at")
                            })

        for item in candidate_issues:
            task_key = item.get("key")
            fields = item.get("fields", {})
            summary = fields.get("summary", "Support Ticket")
            status_name = fields.get("status", {}).get("name", "Unknown") if isinstance(fields.get("status"), dict) else str(fields.get("status", "Unknown"))

            # Strict status exclusion: status != Ready to Release and not closed/done
            if not task_key or status_name.strip().lower() in excluded_statuses:
                continue

            # Strict reporter & assignee verification in Python
            reporter_obj = fields.get("reporter") or {}
            reporter_id = (
                reporter_obj.get("accountId") or reporter_obj.get("name")
                if isinstance(reporter_obj, dict) else str(reporter_obj)
            )
            assignee_obj = fields.get("assignee") or {}
            assignee_id = (
                assignee_obj.get("accountId") or assignee_obj.get("name")
                if isinstance(assignee_obj, dict) else str(assignee_obj)
            )
            if reporter_id and reporter_id != mubashir_account_id:
                continue
            if assignee_id and assignee_id != mubashir_account_id:
                continue

            last_act_str = item.get("last_activity_at") or fields.get("updated")
            if not last_act_str:
                continue

            last_act_dt = parse_iso_datetime(last_act_str)
            if not last_act_dt:
                continue

            business_days = calculate_business_days(last_act_dt, now_tz, tz_name="Asia/Karachi")
            if business_days >= 3.0:
                # Idempotency key based on issue key and last activity timestamp
                dedup_condition = f"mubashir_support_stale:{task_key}:{last_act_str}"
                dedup_svc = getattr(self, "dedup_service", None) or notification_dedup_service
                if not dedup_svc.should_notify(
                    rule_id="MubashirStaleSupport",
                    target_id=task_key,
                    condition=dedup_condition
                ):
                    continue

                comment_body = (
                    f"[~accountid:{mubashir_account_id}:Mubashir Butt]\n\n"
                    f"This Support ticket has had no meaningful update for 3 business days.\n"
                    f"Please update the ticket with the current status or next action."
                )

                comment_action = create_add_comment_action(
                    target_system="jira",
                    task_key=task_key,
                    comment_body=comment_body,
                    task_title=summary,
                    requested_by="MubashirStaleSupport"
                )
                actions.append(comment_action)

                # Record deduplication
                dedup_svc.record_notification_sent(
                    rule_id="MubashirStaleSupport",
                    target_id=task_key,
                    condition=dedup_condition
                )
                logger.info(f"Generated stale update reminder for Mubashir Support ticket {task_key} ({business_days:.1f} business days inactive)")

        return actions

    async def _evaluate_active_epic_review(self) -> Optional[Dict[str, Any]]:
        """Run scheduled review of active Epics assigned to or reported by PM."""
        if not settings.EPIC_REVIEW_ENABLED:
            return None
        if not settings.is_jira_configured():
            return None
        try:
            epic_service = EpicReviewService(manager=self.mgr)
            results = await epic_service.run_review()
            return {"status": "completed", "epics_reviewed": len(results), "details": results}
        except Exception as e:
            logger.error(f"Error during Active Epic review: {e}", exc_info=True)
            return {"status": "error", "error": str(e)}

    async def _evaluate_daily_retention(self) -> Optional[Dict[str, Any]]:
        """Run daily maintenance pruning 30d analytical runs, 90d audit logs, 30d notifications."""
        now_pkt = datetime.now(zoneinfo.ZoneInfo("Asia/Karachi"))
        today_pkt_str = now_pkt.strftime("%Y-%m-%d")

        # Run once per calendar day (Asia/Karachi)
        if self._last_daily_retention_date == today_pkt_str:
            return None

        # Prefer running at or after 04:00 AM PKT
        if now_pkt.hour < 4:
            return None

        logger.info(f"Triggering scheduled daily data retention maintenance (date: {today_pkt_str})...")
        try:
            res = self.retention_service.run_daily_maintenance(dry_run=False)
            self._last_daily_retention_date = today_pkt_str
            return {
                "status": res.status,
                "rows_deleted": res.total_rows_deleted,
                "tables_processed": res.total_tables_processed,
                "duration_ms": res.execution_duration_ms,
            }
        except Exception as e:
            logger.error(f"Daily retention maintenance encountered an error: {e}", exc_info=True)
            return {"status": "error", "error": str(e)}

    async def _evaluate_monday_retention(self) -> Optional[Dict[str, Any]]:
        """Run Monday weekly maintenance pruning processed raw events older than Monday 00:00:00 PKT."""
        now_pkt = datetime.now(zoneinfo.ZoneInfo("Asia/Karachi"))
        today_pkt_str = now_pkt.strftime("%Y-%m-%d")

        # Only on Mondays (weekday 0), at or after 03:00 AM PKT, once per day
        if now_pkt.weekday() != 0:
            return None

        if self._last_monday_retention_date == today_pkt_str:
            return None

        if now_pkt.hour < 3:
            return None

        logger.info(f"Triggering scheduled Monday raw event retention maintenance (date: {today_pkt_str})...")
        try:
            res = self.retention_service.run_weekly_monday_maintenance(dry_run=False)
            self._last_monday_retention_date = today_pkt_str
            return {
                "status": res.status,
                "rows_deleted": res.total_rows_deleted,
                "tables_processed": res.total_tables_processed,
                "duration_ms": res.execution_duration_ms,
            }
        except Exception as e:
            logger.error(f"Monday raw event retention maintenance encountered an error: {e}", exc_info=True)
            return {"status": "error", "error": str(e)}


# Global scheduler instance
periodic_scheduler = PeriodicScheduler()


