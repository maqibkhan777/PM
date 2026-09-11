"""Health check and connector status routes."""

from fastapi import APIRouter
from app.services.orchestrator import orchestrator
from app.database.connection import db_manager
from app.config.settings import settings

router = APIRouter(tags=["Health"])


@router.get("/health")
async def get_health():
    """Overall system health check."""
    db_ok = False
    try:
        with db_manager.session() as conn:
            cursor = conn.execute("SELECT 1")
            db_ok = cursor.fetchone() is not None
    except Exception:
        db_ok = False

    jira_health = await orchestrator.jira_connector.health_check()
    mm_health = await orchestrator.mattermost_connector.health_check()
    discord_health = await orchestrator.discord_webhook_connector.health_check()
    discord_bot_health = await orchestrator.discord_bot_connector.health_check()

    return {
        "application": "OK",
        "version": "0.1.0",
        "dry_run": settings.DRY_RUN,
        "database": "OK" if db_ok else "ERROR",
        "jira": jira_health.status,
        "mattermost": mm_health.status,
        "discord": discord_health.status,
        "discord_bot": discord_bot_health.status,
        "scheduler_running": orchestrator.periodic_scheduler.is_running if hasattr(orchestrator, "periodic_scheduler") else False
    }


@router.get("/health/connectors")
async def get_connectors_health():
    """Detailed health, polling status, and capabilities of all registered connectors."""
    jira_h = await orchestrator.jira_connector.health_check()
    mm_h = await orchestrator.mattermost_connector.health_check()
    discord_h = await orchestrator.discord_webhook_connector.health_check()
    discord_bot_h = await orchestrator.discord_bot_connector.health_check()

    return {
        "jira": {
            "connected": jira_h.is_connected,
            "status": jira_h.status,
            "polling_enabled": settings.JIRA_POLLING_ENABLED,
            "polling_status": jira_h.details.get("polling_status", "disabled"),
            "last_poll_success": jira_h.details.get("last_poll_success"),
            "team_group": settings.JIRA_TEAM_GROUP,
            "team_group_scoped": settings.is_jira_team_group_configured()
        },
        "mattermost": {
            "configured": mm_h.details.get("configured", False),
            "connected": mm_h.is_connected,
            "status": mm_h.details.get("status", "not_configured")
        },
        "discord": {
            "configured": settings.is_discord_configured(),
            "connected": discord_h.is_connected,
            "status": discord_h.status
        },
        "connectors": [
            {
                "name": orchestrator.jira_connector.name,
                "system_type": orchestrator.jira_connector.system_type,
                "status": jira_h.status,
                "is_connected": jira_h.is_connected,
                "capabilities": [c.value for c in orchestrator.jira_connector.get_capabilities()],
                "details": jira_h.details
            },
            {
                "name": orchestrator.mattermost_connector.name,
                "system_type": orchestrator.mattermost_connector.system_type,
                "status": mm_h.status,
                "is_connected": mm_h.is_connected,
                "capabilities": [c.value for c in orchestrator.mattermost_connector.get_capabilities()],
                "details": mm_h.details
            },
            {
                "name": orchestrator.discord_webhook_connector.name,
                "system_type": orchestrator.discord_webhook_connector.system_type,
                "status": discord_h.status,
                "is_connected": discord_h.is_connected,
                "capabilities": [c.value for c in orchestrator.discord_webhook_connector.get_capabilities()],
                "details": discord_h.details
            },
            {
                "name": orchestrator.discord_bot_connector.name,
                "system_type": orchestrator.discord_bot_connector.system_type,
                "status": discord_bot_h.status,
                "is_connected": discord_bot_h.is_connected,
                "capabilities": [c.value for c in orchestrator.discord_bot_connector.get_capabilities()],
                "details": discord_bot_h.details
            }
        ]
    }
