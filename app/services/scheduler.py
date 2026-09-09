"""Lightweight in-process asynchronous scheduler for evaluating time-based rules and Jira polling."""

import asyncio
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

        logger.info(
            f"Scheduler cycle complete. Evaluated: {stale_evaluated} stale, "
            f"{overdue_evaluated} overdue. Actions dispatched: {actions_dispatched}"
        )
        return {
            "timestamp": utc_now_iso(),
            "stale_actions": stale_evaluated,
            "overdue_actions": overdue_evaluated,
            "actions_dispatched": actions_dispatched
        }

    def _sync_recent_events_to_projection(self) -> None:
        """Sync recent raw events into local jira_issue_state projection cache if not already present."""
        try:
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
                    raw_reference=issue_data
                )
        except Exception as err:
            logger.debug(f"Event sync to projection failed: {err}")

    async def _evaluate_stale_tasks(self) -> List[Any]:
        """Find active tasks in 'In Progress' whose last meaningful activity exceeds threshold."""
        self._sync_recent_events_to_projection()
        actions = []
        stale_candidates = self.issue_state_repo.get_stale_candidates(threshold_hours=settings.STALE_TASK_HOURS)

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
        overdue_candidates = self.issue_state_repo.get_overdue_candidates()

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


# Global scheduler instance
periodic_scheduler = PeriodicScheduler()
