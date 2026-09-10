"""Isolated development and testing endpoints for PM Operations Agent."""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.actions.types import create_send_notification_action
from app.core.actions.engine import action_engine
from app.config.settings import settings
from app.database.connection import db_manager, DatabaseManager
from app.database.repositories import JiraIssueStateRepository
from app.utils.logger import logger
from app.utils.time import utc_now_iso, parse_iso_datetime, hours_between

router = APIRouter(prefix="/test", tags=["Development / Testing"])


class TestNotificationRequest(BaseModel):
    """Payload for triggering a test notification."""
    title: str = Field(
        default="🧪 Test Notification from PM Operations Agent",
        description="Notification title displayed in the Discord embed"
    )
    message: str = Field(
        default="This is a safe test notification verifying the ActionEngine -> Discord connector pipeline.",
        description="Main body/description of the test message"
    )
    channel: Optional[str] = Field(
        default=None,
        description="Target Discord channel identifier (defaults to configured PM_DISCORD_CHANNEL)"
    )
    level: str = Field(
        default="INFO",
        description="Severity level: INFO, SUCCESS, WARNING, or ERROR"
    )


@router.post("/discord-notification")
async def send_test_discord_notification(
    payload: Optional[TestNotificationRequest] = None
) -> Dict[str, Any]:
    """Send a safe test notification through ActionEngine -> Discord connector.

    - Does NOT modify Jira data.
    - Routes through ActionEngine and approval/dry-run enforcement.
    - Respects the DRY_RUN environment setting.
    - Clearly flags in logs that this is a test notification.
    """
    req = payload or TestNotificationRequest()
    channel = req.channel or settings.PM_DISCORD_CHANNEL

    logger.info(
        f"🧪 [TEST NOTIFICATION] Received manual test notification request: "
        f"title='{req.title}', channel='{channel}', dry_run={settings.DRY_RUN}"
    )

    # Ensure Discord connector is registered in ActionEngine
    if not action_engine.get_connector("discord"):
        from app.connectors.discord import DiscordWebhookConnector
        action_engine.register_connector(DiscordWebhookConnector())

    action = create_send_notification_action(
        target_system="discord",
        channel=channel,
        title=req.title,
        message=req.message,
        level=req.level.upper(),
        fields=[
            {"name": "Source", "value": "Development Test Endpoint (`/test/discord-notification`)", "inline": False},
            {"name": "Dry Run Mode", "value": str(settings.DRY_RUN), "inline": True},
            {"name": "Dispatched At", "value": utc_now_iso(), "inline": True}
        ],
        requested_by="DevTestEndpoint"
    )

    result = await action_engine.execute(action)

    logger.info(
        f"🧪 [TEST NOTIFICATION] ActionEngine execution completed. "
        f"action_id={result.action_id}, success={result.success}, "
        f"status={result.status.value}, dry_run={result.dry_run}"
    )

    if not result.success:
        return {
            "status": "failed",
            "action_id": result.action_id,
            "action_status": result.status.value,
            "dry_run": result.dry_run,
            "error": result.error_message,
            "message": f"Test notification failed: {result.error_message}"
        }

    return {
        "status": "success",
        "action_id": result.action_id,
        "action_status": result.status.value,
        "dry_run": result.dry_run,
        "target_system": result.target_system,
        "target_id": result.target_id,
        "message": (
            "Test notification simulated successfully under DRY_RUN mode (no external HTTP call dispatched)."
            if result.dry_run
            else "Test notification successfully dispatched to Discord webhook."
        )
    }


def get_scheduler_scoping_diagnostic(manager: Optional[DatabaseManager] = None) -> Dict[str, Any]:
    """Inspect and evaluate local Jira issue state projection and scheduler scoping.

    - Uses the exact same filtering semantics as PeriodicScheduler._evaluate_stale_tasks()
      and PeriodicScheduler._evaluate_overdue_tasks().
    - Does NOT modify Jira data.
    - Does NOT dispatch any actions or notifications.
    """
    mgr = manager or db_manager
    repo = JiraIssueStateRepository(mgr)

    is_scoped = settings.is_jira_team_group_configured()
    team_group = settings.JIRA_TEAM_GROUP.strip() if is_scoped else None

    # 1. Database projection statistics
    with mgr.session() as conn:
        total_rows = conn.execute("SELECT COUNT(*) FROM jira_issue_state").fetchone()[0]
        if is_scoped:
            rows_matching = conn.execute(
                "SELECT COUNT(*) FROM jira_issue_state WHERE team_group = ?", (team_group,)
            ).fetchone()[0]
            rows_null = conn.execute(
                "SELECT COUNT(*) FROM jira_issue_state WHERE team_group IS NULL"
            ).fetchone()[0]
            rows_different = conn.execute(
                "SELECT COUNT(*) FROM jira_issue_state WHERE team_group IS NOT NULL AND team_group != ?", (team_group,)
            ).fetchone()[0]
        else:
            rows_matching = 0
            rows_null = conn.execute(
                "SELECT COUNT(*) FROM jira_issue_state WHERE team_group IS NULL"
            ).fetchone()[0]
            rows_different = conn.execute(
                "SELECT COUNT(*) FROM jira_issue_state WHERE team_group IS NOT NULL"
            ).fetchone()[0]

    # 2. Evaluate stale candidates using the exact same filtering as PeriodicScheduler
    raw_stale = repo.get_stale_candidates(
        threshold_hours=settings.STALE_TASK_HOURS,
        team_group=team_group
    )
    stale_candidates: List[Dict[str, Any]] = []
    for item in raw_stale:
        tkey = item.get("jira_issue_key")
        last_activity_str = item.get("last_activity_at")
        if not tkey or not last_activity_str:
            continue
        last_dt = parse_iso_datetime(last_activity_str)
        if not last_dt:
            continue
        hours_inactive = hours_between(last_dt)
        if hours_inactive >= settings.STALE_TASK_HOURS:
            stale_candidates.append({
                "jira_issue_key": tkey,
                "summary": item.get("summary"),
                "assignee": item.get("assignee"),
                "team_group": item.get("team_group"),
                "status": item.get("status"),
                "last_activity_at": last_activity_str,
                "hours_inactive": round(hours_inactive, 1),
                "qualification_reason": (
                    f"Active task in status '{item.get('status')}' has had no meaningful activity "
                    f"for {hours_inactive:.1f}h (threshold: {settings.STALE_TASK_HOURS}h; last activity: {last_activity_str})"
                )
            })

    # 3. Evaluate overdue candidates using the exact same filtering as PeriodicScheduler
    raw_overdue = repo.get_overdue_candidates(team_group=team_group)
    overdue_candidates: List[Dict[str, Any]] = []
    for item in raw_overdue:
        tkey = item.get("jira_issue_key")
        due_date_str = item.get("due_date")
        status_name = item.get("status") or "Unknown"
        if not tkey or not due_date_str:
            continue
        overdue_candidates.append({
            "jira_issue_key": tkey,
            "summary": item.get("summary"),
            "assignee": item.get("assignee"),
            "team_group": item.get("team_group"),
            "status": status_name,
            "due_date": due_date_str,
            "qualification_reason": (
                f"Incomplete task in status '{status_name}' has due date '{due_date_str}' which is past due"
            )
        })

    return {
        "diagnostic": "scheduler_team_scoping",
        "description": "Read-only diagnostic verifying stale/overdue scheduler candidate team scoping",
        "timestamp": utc_now_iso(),
        "configured_jira_team_group": team_group,
        "team_scoping_enabled": is_scoped,
        "database_projection_summary": {
            "total_rows": total_rows,
            "rows_matching_team_group": rows_matching,
            "rows_with_null_team_group": rows_null,
            "rows_with_different_team_group": rows_different,
        },
        "scheduler_evaluation_scope": {
            "team_group_filter": team_group if is_scoped else "none_unscoped",
            "stale_threshold_hours": settings.STALE_TASK_HOURS,
        },
        "stale_candidates_count": len(stale_candidates),
        "stale_candidates": stale_candidates,
        "overdue_candidates_count": len(overdue_candidates),
        "overdue_candidates": overdue_candidates,
    }


@router.get("/diagnostic/scheduler-scoping")
async def get_scheduler_scoping_diagnostic_endpoint() -> Dict[str, Any]:
    """Read-only diagnostic endpoint to verify stale/overdue scheduler candidate team scoping.

    - Does NOT modify Jira data.
    - Does NOT dispatch any notifications or actions.
    - Uses identical scheduler scoping and filtering semantics.
    """
    logger.info("Running read-only scheduler team-scoping diagnostic via /test/diagnostic/scheduler-scoping...")
    return get_scheduler_scoping_diagnostic()


class TestSendWorklogReportRequest(BaseModel):
    """Payload for triggering a test send of the daily worklog report."""
    date: Optional[str] = Field(
        default=None,
        description="Optional report date in YYYY-MM-DD format (defaults to current date in configured timezone)"
    )
    force: bool = Field(
        default=False,
        description="If True, bypasses the daily idempotency check and sends even if already sent today"
    )
    sync_jira: bool = Field(
        default=True,
        description="If True, syncs latest worklogs from Jira before generating the report"
    )


@router.get("/report/daily-worklog")
async def get_daily_worklog_report_endpoint(
    date: Optional[str] = None,
    sync_jira: bool = True
) -> Dict[str, Any]:
    """Read-only development/testing endpoint to preview the consolidated daily team worklog report.

    - Does NOT send any Discord notifications.
    - Does NOT modify any Jira data.
    - Can optionally sync latest Jira worklogs for the date if sync_jira=True.
    """
    from app.core.reports.worklog_report import daily_worklog_report_generator
    logger.info(f"Generating read-only daily worklog report preview (date={date}, sync_jira={sync_jira})...")
    report = await daily_worklog_report_generator.generate_report(target_date=date, sync_jira=sync_jira)
    return {
        "status": "success",
        "diagnostic_mode": "DEVELOPMENT_TESTING_READ_ONLY",
        "report": report
    }


@router.post("/report/daily-worklog/send")
async def send_daily_worklog_report_endpoint(
    payload: Optional[TestSendWorklogReportRequest] = None
) -> Dict[str, Any]:
    """Development/testing endpoint to generate and dispatch the daily team worklog report to Discord.

    - Routes through ActionEngine -> Discord connector.
    - Respects DRY_RUN setting.
    - Idempotent by default unless force=True.
    """
    from app.core.reports.worklog_report import daily_worklog_report_generator
    req = payload or TestSendWorklogReportRequest()
    logger.info(f"Triggering test send of daily worklog report (date={req.date}, force={req.force}, sync_jira={req.sync_jira})...")
    return await daily_worklog_report_generator.send_report_to_discord(
        target_date=req.date,
        force=req.force,
        sync_jira=req.sync_jira
    )


class TestSendOverdueDigestRequest(BaseModel):
    """Payload for triggering a test send of the daily overdue digest."""
    date: Optional[str] = Field(
        default=None,
        description="Optional digest date in YYYY-MM-DD format (defaults to current date in configured timezone)"
    )
    force: bool = Field(
        default=True,
        description="If True, allows manual test sending even if already sent today"
    )
    record_history: bool = Field(
        default=False,
        description="If True, records the digest in SQLite daily_report_history as permanently sent. Defaults to False for safe manual testing."
    )


@router.get("/report/overdue-digest")
async def get_overdue_digest_endpoint(
    date: Optional[str] = None
) -> Dict[str, Any]:
    """Read-only development/testing endpoint to preview the daily consolidated overdue task digest.

    - Does NOT send any Discord notifications.
    - Does NOT record any history.
    - Uses identical overdue detection and team scoping logic as the scheduled job.
    """
    from app.core.reports.overdue_report import daily_overdue_report_generator
    logger.info(f"Generating read-only overdue digest preview (date={date})...")
    digest = daily_overdue_report_generator.generate_digest(target_date=date)
    return {
        "status": "success",
        "diagnostic_mode": "DEVELOPMENT_TESTING_READ_ONLY",
        "digest": digest
    }


@router.post("/report/overdue-digest/send")
async def send_overdue_digest_endpoint(
    payload: Optional[TestSendOverdueDigestRequest] = None
) -> Dict[str, Any]:
    """Development/testing endpoint to generate and dispatch the daily overdue digest to Discord.

    - Uses the EXACT same digest-generation logic as the 09:00 scheduled job.
    - By default, record_history=False so manual testing does NOT mark the real daily 09:00 digest as permanently sent.
    - Routes through ActionEngine -> Discord connector.
    - Respects DRY_RUN setting.
    """
    from app.core.reports.overdue_report import daily_overdue_report_generator
    req = payload or TestSendOverdueDigestRequest()
    logger.info(
        f"Triggering test send of daily overdue digest (date={req.date}, force={req.force}, record_history={req.record_history})..."
    )
    return await daily_overdue_report_generator.send_digest_to_discord(
        target_date=req.date,
        force=req.force,
        record_history=req.record_history
    )


class TestSendPMAttentionRequest(BaseModel):
    """Payload for triggering a test send of the daily PM Attention Digest."""
    date: Optional[str] = Field(
        default=None,
        description="Optional digest date in YYYY-MM-DD format (defaults to current date in configured timezone)"
    )
    force: bool = Field(
        default=True,
        description="If True, allows manual test sending even if already sent today"
    )
    record_history: bool = Field(
        default=False,
        description="If True, records the digest in SQLite daily_report_history as permanently sent. Defaults to False for safe manual testing."
    )


@router.get("/report/pm-attention")
async def get_pm_attention_endpoint(
    date: Optional[str] = None
) -> Dict[str, Any]:
    """Read-only development/testing endpoint to preview the daily PM Attention Digest.

    - Does NOT send any Discord notifications.
    - Does NOT record any history.
    - Uses identical attention detection and team scoping logic as the scheduled job.
    """
    from app.core.reports.attention_report import daily_pm_attention_report_generator
    logger.info(f"Generating read-only PM attention preview (date={date})...")
    digest = daily_pm_attention_report_generator.generate_digest(target_date=date)
    return {
        "status": "success",
        "diagnostic_mode": "DEVELOPMENT_TESTING_READ_ONLY",
        "digest": digest
    }


@router.post("/report/pm-attention/send")
async def send_pm_attention_endpoint(
    payload: Optional[TestSendPMAttentionRequest] = None
) -> Dict[str, Any]:
    """Development/testing endpoint to generate and dispatch the daily PM Attention Digest to Discord.

    - Uses the EXACT same digest-generation logic as the 09:00 scheduled job.
    - By default, record_history=False so manual testing does NOT mark the real daily 09:00 digest as permanently sent.
    - Routes through ActionEngine -> Discord connector.
    - Respects DRY_RUN setting.
    """
    from app.core.reports.attention_report import daily_pm_attention_report_generator
    req = payload or TestSendPMAttentionRequest()
    logger.info(
        f"Triggering test send of daily PM Attention Digest (date={req.date}, force={req.force}, record_history={req.record_history})..."
    )
    return await daily_pm_attention_report_generator.send_digest_to_discord(
        target_date=req.date,
        force=req.force,
        record_history=req.record_history
    )




