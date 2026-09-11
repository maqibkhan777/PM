"""Lightweight in-process asynchronous scheduler for evaluating time-based rules and Jira polling."""

import asyncio
from datetime import datetime
import zoneinfo
from typing import Any, Dict, List, Optional
from app.core.events.types import StaleTask, OverdueTask
from app.core.rules.engine import rules_engine
from app.core.actions.engine import action_engine
from app.database.repositories import EventRepository, JiraIssueStateRepository
from app.database.connection import db_manager, DatabaseManager
from app.config.settings import settings
from app.utils.time import utc_now, parse_iso_datetime, hours_between, utc_now_iso
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
        self._running = False
        self._rules_task: Optional[asyncio.Task] = None
        self._polling_task: Optional[asyncio.Task] = None
        self._interval_minutes = settings.SCHEDULER_INTERVAL_MINUTES
        self._polling_interval_minutes = settings.JIRA_POLLING_INTERVAL_MINUTES
        self._last_performance_analysis_at: Optional[datetime] = None

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

        # 6. Evaluate Scheduled Performance Foundation Analysis
        perf_analysis_status = None
        try:
            perf_analysis_res = await self._evaluate_performance_analysis()
            if perf_analysis_res:
                perf_analysis_status = perf_analysis_res.get("status")
        except Exception as e:
            logger.error(f"Error during Performance Foundation Analysis evaluation: {e}", exc_info=True)

        logger.info(
            f"Scheduler cycle complete. Evaluated: {stale_evaluated} stale, "
            f"{overdue_evaluated} overdue. Actions dispatched: {actions_dispatched}. "
            f"Daily worklog status: {daily_report_status or 'idle'}, "
            f"Daily overdue status: {daily_overdue_status or 'idle'}, "
            f"Daily attention status: {daily_attention_status or 'idle'}, "
            f"Performance analysis status: {perf_analysis_status or 'idle'}"
        )
        return {
            "timestamp": utc_now_iso(),
            "stale_actions": stale_evaluated,
            "overdue_actions": overdue_evaluated,
            "actions_dispatched": actions_dispatched,
            "daily_report_status": daily_report_status,
            "daily_overdue_status": daily_overdue_status,
            "daily_attention_status": daily_attention_status,
            "performance_analysis_status": perf_analysis_status,
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
                fields = issue_data.get("fields", {})
                status_name = fields.get("status", {}).get("name") or payload.get("new_status") or "Unknown"
                assignee = fields.get("assignee") or {}
                self.issue_state_repo.upsert(
                    jira_issue_key=tkey,
                    summary=fields.get("summary") or "Task",
                    status=status_name,
                    assignee=assignee.get("displayName") or assignee.get("name") or e.get("actor_name"),
                    due_date=fields.get("duedate"),
                    last_seen_at=e.get("timestamp"),
                    last_activity_at=e.get("timestamp"),
                    raw_reference=issue_data,
                    team_group=team_group
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
        """Check if daily worklog report should be triggered based on scheduled time."""
        if not settings.DAILY_WORKLOG_REPORT_ENABLED:
            return None

        try:
            tz = zoneinfo.ZoneInfo(settings.DAILY_WORKLOG_REPORT_TIMEZONE)
            now_tz = datetime.now(tz)
        except Exception:
            now_tz = datetime.now()

        today_str = now_tz.strftime("%Y-%m-%d")
        current_time_str = now_tz.strftime("%H:%M")

        scheduled_time = (settings.DAILY_WORKLOG_REPORT_TIME or "18:00").strip()
        if current_time_str >= scheduled_time:
            from app.core.reports.worklog_report import DailyWorklogReportGenerator
            generator = DailyWorklogReportGenerator(manager=self.mgr)
            team_group = settings.JIRA_TEAM_GROUP.strip() if settings.is_jira_team_group_configured() else "Team"

            # Check idempotency: skip if already sent today
            if not generator.history_repo.has_report_been_sent(team_group, today_str):
                logger.info(
                    f"Triggering scheduled daily worklog report for team '{team_group}' on {today_str} "
                    f"(current_time={current_time_str}, scheduled_time={scheduled_time})"
                )
                return await generator.send_report_to_discord(
                    target_date=today_str,
                    force=False,
                    sync_jira=True
                )
        return None

    async def _evaluate_daily_overdue_digest(self) -> Optional[Dict[str, Any]]:
        """Check if daily overdue digest should be triggered based on scheduled time."""
        if not settings.OVERDUE_DIGEST_ENABLED:
            return None

        try:
            tz = zoneinfo.ZoneInfo(settings.OVERDUE_DIGEST_TIMEZONE)
            now_tz = datetime.now(tz)
        except Exception:
            now_tz = datetime.now()

        today_str = now_tz.strftime("%Y-%m-%d")
        current_time_str = now_tz.strftime("%H:%M")

        scheduled_time = (settings.OVERDUE_DIGEST_TIME or "09:00").strip()
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
        """Check if daily PM Attention Digest should be triggered based on scheduled time."""
        if not settings.PM_ATTENTION_DIGEST_ENABLED:
            return None

        try:
            tz = zoneinfo.ZoneInfo(settings.PM_ATTENTION_DIGEST_TIMEZONE)
            now_tz = datetime.now(tz)
        except Exception:
            now_tz = datetime.now()

        today_str = now_tz.strftime("%Y-%m-%d")
        current_time_str = now_tz.strftime("%H:%M")

        scheduled_time = (settings.PM_ATTENTION_DIGEST_TIME or "09:00").strip()
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


# Global scheduler instance
periodic_scheduler = PeriodicScheduler()

