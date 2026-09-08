"""In-process asynchronous Event Bus."""

import asyncio
import inspect
from collections import deque
from typing import Any, Callable, Dict, List, Set, Union
from app.core.events.base import BaseEvent
from app.utils.logger import logger


class EventBus:
    """Lightweight in-process asynchronous Event Bus with deduplication and error boundaries."""

    def __init__(self, dedup_history_size: int = 1000):
        # Maps event_type -> list of subscriber callables
        self._subscribers: Dict[str, List[Callable]] = {}
        # Wildcard subscribers for all events
        self._global_subscribers: List[Callable] = []
        # In-memory deduplication set & bounded deque
        self._seen_event_ids: Set[str] = set()
        self._seen_history: deque = deque(maxlen=dedup_history_size)
        self._lock = asyncio.Lock()

    def subscribe(
        self,
        event_type: Union[str, type],
        handler: Callable[[BaseEvent], Any]
    ) -> None:
        """Register a subscriber handler for a specific event type or wildcard ('*')."""
        if isinstance(event_type, type) and issubclass(event_type, BaseEvent):
            key = event_type.__name__
        else:
            key = str(event_type)

        if key == "*":
            if handler not in self._global_subscribers:
                self._global_subscribers.append(handler)
                logger.debug(f"Registered global wildcard event subscriber: {handler.__name__ if hasattr(handler, '__name__') else handler}")
        else:
            if key not in self._subscribers:
                self._subscribers[key] = []
            if handler not in self._subscribers[key]:
                self._subscribers[key].append(handler)
                logger.debug(f"Registered event subscriber for '{key}': {handler.__name__ if hasattr(handler, '__name__') else handler}")

    def unsubscribe(self, event_type: Union[str, type], handler: Callable) -> None:
        """Unregister a subscriber handler."""
        key = event_type.__name__ if isinstance(event_type, type) else str(event_type)
        if key == "*":
            if handler in self._global_subscribers:
                self._global_subscribers.remove(handler)
        elif key in self._subscribers and handler in self._subscribers[key]:
            self._subscribers[key].remove(handler)

    async def is_duplicate(self, event: BaseEvent) -> bool:
        """Check and record event ID for deduplication."""
        async with self._lock:
            if event.id in self._seen_event_ids:
                return True
            if len(self._seen_history) == self._seen_history.maxlen:
                oldest = self._seen_history[0]
                self._seen_event_ids.discard(oldest)
            self._seen_history.append(event.id)
            self._seen_event_ids.add(event.id)
            return False

    async def publish(self, event: BaseEvent) -> None:
        """Dispatch an event asynchronously to all registered subscribers."""
        if await self.is_duplicate(event):
            logger.warning(f"Duplicate event skipped on EventBus: id={event.id}, type={event.event_type}")
            return

        event_type = event.event_type
        logger.info(f"EventBus publishing event: {event_type} (id={event.id}, source={event.source}, task={event.task_key or event.task_id})")

        # Collect handlers
        handlers = list(self._subscribers.get(event_type, []))
        handlers.extend(self._global_subscribers)

        if not handlers:
            logger.debug(f"No subscribers registered for event type: {event_type}")
            return

        # Execute handlers with isolated error boundary
        tasks = []
        for handler in handlers:
            tasks.append(self._safe_execute_handler(handler, event))

        await asyncio.gather(*tasks, return_exceptions=True)

    async def _safe_execute_handler(self, handler: Callable, event: BaseEvent) -> None:
        """Execute a single handler safely catching any exceptions."""
        try:
            if inspect.iscoroutinefunction(handler):
                await handler(event)
            else:
                # Run synchronous handler in thread pool to avoid blocking event loop
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, handler, event)
        except Exception as e:
            handler_name = getattr(handler, "__name__", str(handler))
            logger.error(
                f"Error in event subscriber handler '{handler_name}' for event {event.event_type} (id={event.id}): {e}",
                exc_info=True
            )


# Global event bus instance
event_bus = EventBus()
