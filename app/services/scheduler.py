"""Lightweight in-process asynchronous scheduler for evaluating time-based rules."""

import asyncio
from typing import Any, Dict, List, Optional
from app.core.events.types import StaleTask, OverdueTask
from app.core.rules.engine import rules_engine
from app.core.actions.engine import action_engine
from app.database.repositories import EventRepository
from app.database.connection import db_manager, DatabaseManager
from app.config.settings import settings
from app.utils.time import utc_now, parse_iso_datetime, hours_between, utc_now_iso
from app.utils.logger import logger


class PeriodicScheduler:
    """In-process background worker evaluating time-based rules (Stale Tasks, Overdue Tasks)."""

    def __init__(self, manager: Optional[DatabaseManager] = None):
        self.mgr = manager or db_manager
        self.event_repo = EventRepository(self.mgr)
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._interval_minutes = settings.SCHEDULER_INTERVAL_MINUTES

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        """Start the background scheduler task."""
        if self._running:
            logger.warning("PeriodicScheduler is already running.")
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"PeriodicScheduler started (interval: {self._interval_minutes}m).")

    def stop(self) -> None:
        """Stop the background scheduler task."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            self._task = None
        logger.info("PeriodicScheduler stopped.")

    async def _run_loop(self) -> None:
        """Main loop sleeping between periodic execution cycles."""
        while self._running:
            try:
                await self.run_cycle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Unexpected error during scheduler cycle: {e}", exc_info=True)

            try:
                await asyncio.sleep(self._interval_minutes * 60)
            except asyncio.CancelledError:
                break

    async def run_cycle(self) -> Dict[str, Any]:
        """Execute a single scheduler evaluation cycle immediately."""
        logger.info("Starting scheduler evaluation cycle for time-based rules...")
        stale_evaluated = 0
        overdue_evaluated = 0
        actions_dispatched = 0

        # 1. Evaluate Stale Tasks from stored events state
        try:
            stale_actions = await self._evaluate_stale_tasks()
            stale_evaluated = len(stale_actions)
            for act in stale_actions:
                await action_engine.execute(act)
                actions_dispatched += 1
        except Exception as e:
            logger.error(f"Error during StaleTask evaluation: {e}", exc_info=True)

        # 2. Evaluate Overdue Tasks
        try:
            overdue_actions = await self._evaluate_overdue_tasks()
            overdue_evaluated = len(overdue_actions)
            for act in overdue_actions:
                await action_engine.execute(act)
                actions_dispatched += 1
        except Exception as e:
            logger.error(f"Error during OverdueTask evaluation: {e}", exc_info=True)

        logger.info(f"Scheduler cycle complete. Evaluated: {stale_evaluated} stale, {overdue_evaluated} overdue. Actions dispatched: {actions_dispatched}")
        return {
            "timestamp": utc_now_iso(),
            "stale_actions": stale_evaluated,
            "overdue_actions": overdue_evaluated,
            "actions_dispatched": actions_dispatched
        }

    async def _evaluate_stale_tasks(self) -> List[Any]:
        """Find active tasks in 'In Progress' with inactivity exceeding threshold."""
        actions = []
        recent_events = self.event_repo.list_events(limit=100)
        task_latest_activity: Dict[str, Dict[str, Any]] = {}

        # Build latest state per task
        for e in reversed(recent_events):
            tkey = e.get("task_key") or e.get("task_id")
            if not tkey:
                continue

            payload = e.get("payload", {})
            issue_data = payload.get("issue", {})
            fields = issue_data.get("fields", {})
            status_name = fields.get("status", {}).get("name") or payload.get("new_status") or "Unknown"
            assignee = fields.get("assignee") or {}

            task_latest_activity[tkey] = {
                "task_key": tkey,
                "status": status_name,
                "assignee_id": assignee.get("accountId") or e.get("actor_id"),
                "assignee_name": assignee.get("displayName") or e.get("actor_name"),
                "title": fields.get("summary") or "Task in Progress",
                "timestamp": e.get("timestamp")
            }

        # Check each task for staleness
        for tkey, tinfo in task_latest_activity.items():
            if tinfo["status"].lower() in ("in progress", "doing", "active"):
                last_dt = parse_iso_datetime(tinfo["timestamp"])
                if last_dt:
                    hours_inactive = hours_between(last_dt)
                    if hours_inactive >= settings.STALE_TASK_HOURS:
                        stale_event = StaleTask(
                            source="scheduler",
                            task_key=tkey,
                            task_title=tinfo["title"],
                            assignee_id=tinfo["assignee_id"],
                            assignee_name=tinfo["assignee_name"],
                            hours_inactive=hours_inactive,
                            threshold_hours=settings.STALE_TASK_HOURS
                        )
                        acts = rules_engine.evaluate_event(stale_event)
                        actions.extend(acts)

        return actions

    async def _evaluate_overdue_tasks(self) -> List[Any]:
        """Find active tasks with due date in the past."""
        actions = []
        recent_events = self.event_repo.list_events(limit=100)
        tasks_checked = set()

        for e in recent_events:
            tkey = e.get("task_key") or e.get("task_id")
            if not tkey or tkey in tasks_checked:
                continue
            tasks_checked.add(tkey)

            payload = e.get("payload", {})
            fields = payload.get("issue", {}).get("fields", {})
            due_date_str = fields.get("duedate")
            status_name = fields.get("status", {}).get("name") or "Unknown"

            if due_date_str and status_name.lower() not in ("done", "completed", "resolved", "closed"):
                due_dt = parse_iso_datetime(due_date_str)
                if due_dt and due_dt < utc_now():
                    overdue_event = OverdueTask(
                        source="scheduler",
                        task_key=tkey,
                        task_title=fields.get("summary", "Task"),
                        assignee_name=fields.get("assignee", {}).get("displayName"),
                        due_date=due_date_str,
                        current_status=status_name
                    )
                    acts = rules_engine.evaluate_event(overdue_event)
                    actions.extend(acts)

        return actions


# Global scheduler instance
periodic_scheduler = PeriodicScheduler()
