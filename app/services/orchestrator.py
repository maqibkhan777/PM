"""System Orchestrator wiring connectors, event bus, rules engine, and action engine."""

import asyncio
from typing import Any, Dict, Optional
from app.connectors.jira import JiraConnector, JiraPoller
from app.connectors.discord import DiscordWebhookConnector, DiscordBotConnector
from app.connectors.mattermost import MattermostConnector
from app.core.events.bus import event_bus
from app.core.events.base import BaseEvent
from app.core.rules.engine import rules_engine
from app.core.actions.engine import action_engine
from app.services.scheduler import periodic_scheduler
from app.database.repositories import EventRepository, JiraIssueStateRepository
from app.database.connection import db_manager, DatabaseManager
from app.config.settings import settings
from app.utils.logger import logger
from app.utils.time import utc_now_iso


class SystemOrchestrator:
    """Coordinates lifecycle, connectors, event-to-rule pipeline, and background jobs."""

    def __init__(self, manager: Optional[DatabaseManager] = None):
        self.mgr = manager or db_manager
        self.event_repo = EventRepository(self.mgr)
        self.issue_state_repo = JiraIssueStateRepository(self.mgr)
        self.jira_connector = JiraConnector()
        self.jira_poller = JiraPoller(client=self.jira_connector.client, manager=self.mgr)
        self.discord_webhook_connector = DiscordWebhookConnector()
        self.discord_bot_connector = DiscordBotConnector()
        self.mattermost_connector = MattermostConnector()
        self._is_initialized = False

    @property
    def scheduler(self):
        return periodic_scheduler

    @property
    def periodic_scheduler(self):
        return periodic_scheduler

    async def initialize(self) -> None:
        """Initialize connectors and wire event bus subscribers."""
        if self._is_initialized:
            return

        logger.info("Initializing PM Operations Agent Orchestrator...")

        # 1. Register Connectors in Action Engine
        action_engine.register_connector(self.jira_connector)
        action_engine.register_connector(self.discord_webhook_connector)
        action_engine.register_connector(self.discord_bot_connector)
        action_engine.register_connector(self.mattermost_connector)

        # 2. Attempt connector initial connection checks
        await self.jira_connector.connect()
        await self.discord_webhook_connector.connect()
        await self.discord_bot_connector.connect()
        await self.mattermost_connector.connect()

        # 3. Wire Event Bus Wildcard to Rules Engine
        event_bus.subscribe("*", self._on_event_received)

        # 4. Start Scheduler if enabled
        if settings.SCHEDULER_ENABLED:
            periodic_scheduler.start()

        self._is_initialized = True
        logger.info("PM Operations Agent Orchestrator initialized successfully.")

    async def shutdown(self) -> None:
        """Gracefully shut down connectors and background workers."""
        logger.info("Shutting down PM Operations Agent Orchestrator...")
        periodic_scheduler.stop()
        await self.jira_connector.disconnect()
        await self.discord_webhook_connector.disconnect()
        await self.discord_bot_connector.disconnect()
        await self.mattermost_connector.disconnect()
        self._is_initialized = False

    async def _on_event_received(self, event: BaseEvent) -> None:
        """Handle normalized event: evaluate through rules engine and execute generated actions."""
        try:
            actions = rules_engine.evaluate_event(event)
            for action in actions:
                await action_engine.execute(action)
        except Exception as e:
            logger.error(f"Error processing event {event.event_type} in rules pipeline: {e}", exc_info=True)

    async def ingest_polled_event(self, event: BaseEvent) -> str:
        """Ingest a verified event produced by JiraPoller into the database and Event Bus."""
        if not self._is_initialized:
            await self.initialize()

        event_id = self.event_repo.insert(
            event_type=event.event_type,
            source=event.source,
            external_event_id=event.external_event_id,
            timestamp=event.timestamp,
            payload=event.payload or event.model_dump(),
            actor_id=event.actor_id,
            actor_name=event.actor_name,
            project_id=event.project_id,
            task_id=event.task_key or event.task_id,
            processing_status="PROCESSING"
        )
        event.id = event_id

        try:
            await event_bus.publish(event)
            self.event_repo.update_status(event_id, "PROCESSED")
            logger.info(f"Polled event {event_id} ({event.event_type} for {event.task_key or event.task_id}) processed.")
        except Exception as e:
            logger.error(f"Error publishing polled event {event_id}: {e}", exc_info=True)
            self.event_repo.update_status(event_id, "FAILED", last_error=str(e))

        return event_id

    async def process_raw_webhook(
        self,
        source: str,
        raw_payload: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None
    ) -> str:
        """Rapid ingestion: persists event as RECEIVED and schedules background processing."""
        if not self._is_initialized:
            await self.initialize()

        connector = action_engine.get_connector(source)
        if not connector:
            raise ValueError(f"Unknown event source connector: {source}")

        # Try to normalize immediately or extract identifiers
        normalized_event = await connector.handle_incoming_event(raw_payload, headers)
        event_type = normalized_event.event_type if normalized_event else "RawEvent"
        external_id = normalized_event.external_event_id if normalized_event else raw_payload.get("webhookEvent")

        # Deduplication check
        if external_id and self.event_repo.exists_by_external_id(source, external_id):
            logger.warning(f"Duplicate webhook event ignored: source={source}, external_id={external_id}")
            return "duplicate"

        event_id = self.event_repo.insert(
            event_type=event_type,
            source=source,
            external_event_id=external_id,
            timestamp=normalized_event.timestamp if normalized_event else utc_now_iso(),
            payload=raw_payload,
            actor_id=normalized_event.actor_id if normalized_event else None,
            actor_name=normalized_event.actor_name if normalized_event else None,
            project_id=normalized_event.project_id if normalized_event else None,
            task_id=normalized_event.task_key or (normalized_event.task_id if normalized_event else None),
            processing_status="RECEIVED"
        )

        # Trigger background processing task
        asyncio.create_task(self._process_event_background(event_id, normalized_event, source, raw_payload, headers))
        return event_id

    async def _process_event_background(
        self,
        event_id: str,
        normalized_event: Optional[BaseEvent],
        source: str,
        raw_payload: Dict[str, Any],
        headers: Optional[Dict[str, str]]
    ) -> None:
        """Background worker that pushes the event through the Event Bus and updates status."""
        self.event_repo.update_status(event_id, "PROCESSING", increment_attempts=True)
        try:
            if not normalized_event:
                connector = action_engine.get_connector(source)
                if connector:
                    normalized_event = await connector.handle_incoming_event(raw_payload, headers)

            if normalized_event:
                # Ensure the event ID matches the DB event ID for traceability
                normalized_event.id = event_id

                # Keep local Jira issue state projection updated from webhooks
                if source == "jira" and raw_payload.get("issue"):
                    issue = raw_payload["issue"]
                    fields = issue.get("fields", {})
                    self.issue_state_repo.upsert(
                        jira_issue_key=issue.get("key", normalized_event.task_key or ""),
                        summary=fields.get("summary", getattr(normalized_event, "title", None)),
                        status=fields.get("status", {}).get("name", getattr(normalized_event, "status", getattr(normalized_event, "new_status", "Unknown"))),
                        assignee=fields.get("assignee", {}).get("displayName", getattr(normalized_event, "assignee_name", None)),
                        priority=fields.get("priority", {}).get("name", getattr(normalized_event, "priority", None)),
                        due_date=fields.get("duedate"),
                        updated_at=fields.get("updated"),
                        last_seen_at=utc_now_iso(),
                        last_activity_at=normalized_event.timestamp or fields.get("updated"),
                        project_key=fields.get("project", {}).get("key", normalized_event.project_key),
                        raw_reference=issue
                    )

                await event_bus.publish(normalized_event)
                self.event_repo.update_status(event_id, "PROCESSED")
                logger.info(f"Event {event_id} ({normalized_event.event_type}) successfully processed.")
            else:
                self.event_repo.update_status(event_id, "PROCESSED")
                logger.debug(f"Event {event_id} processed with no normalized actions.")
        except Exception as e:
            logger.error(f"Event {event_id} processing failed: {e}", exc_info=True)
            self.event_repo.update_status(event_id, "FAILED", last_error=str(e))

    async def reprocess_event(self, event_id: str) -> Dict[str, Any]:
        """Manually reprocess a failed or retry-pending event."""
        event_rec = self.event_repo.get_by_id(event_id)
        if not event_rec:
            raise ValueError(f"Event with id '{event_id}' not found.")

        current_status = event_rec.get("processing_status")
        if current_status not in ("FAILED", "RETRY_PENDING", "RECEIVED"):
            raise ValueError(f"Event '{event_id}' has status '{current_status}' and cannot be retried.")

        source = event_rec.get("source", "jira")
        raw_payload = event_rec.get("payload", {})
        self.event_repo.update_status(event_id, "RETRY_PENDING")

        asyncio.create_task(self._process_event_background(event_id, None, source, raw_payload, None))
        return {
            "status": "reprocessing_scheduled",
            "event_id": event_id,
            "previous_status": current_status
        }


# Global orchestrator instance
orchestrator = SystemOrchestrator()
