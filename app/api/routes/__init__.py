"""API routes package."""

from app.api.routes.health import router as health_router
from app.api.routes.webhooks import router as webhooks_router
from app.api.routes.events import router as events_router
from app.api.routes.actions import router as actions_router
from app.api.routes.rules import router as rules_router
from app.api.routes.reports import router as reports_router
from app.api.routes.mappings import router as mappings_router
from app.api.routes.jira_poll import router as jira_poll_router
from app.api.routes.test_notifications import router as test_notifications_router

__all__ = [
    "health_router",
    "webhooks_router",
    "events_router",
    "actions_router",
    "rules_router",
    "reports_router",
    "mappings_router",
    "jira_poll_router",
    "test_notifications_router",
]
