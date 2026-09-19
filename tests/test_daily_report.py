"""Unit tests for the Daily Activity Report generator and Mursaleen Cluster filtering."""

import pytest
from app.core.reports.daily_report import DailyActivityReportGenerator
from app.database.repositories import EventRepository, EmployeeRoleRepository
from app.connectors.discord.formatter import DiscordFormatter
from app.config.settings import settings
from app.utils.time import utc_now


def test_daily_activity_report_mursaleen_cluster_filtering(temp_db):
    """Test generating daily activity metrics with strict authoritative Mursaleen Cluster member filtering."""
    event_repo = EventRepository(temp_db)
    report_gen = DailyActivityReportGenerator(manager=temp_db)

    today = utc_now().strftime("%Y-%m-%d")
    now_iso = utc_now().isoformat()

    # 1. Authoritative member by display name & canonical seed: Ahsan Amin
    event_repo.insert(
        event_type="TaskCreated",
        source="jira",
        external_event_id="e1",
        timestamp=now_iso,
        actor_name="Ahsan Amin",
        task_id="CF7-1",
        payload={"title": "Task 1"}
    )
    event_repo.insert(
        event_type="TaskStatusChanged",
        source="jira",
        external_event_id="e2",
        timestamp=now_iso,
        actor_name="Ahsan Amin",
        task_id="CF7-1",
        payload={"old_status": "To Do", "new_status": "In Progress"}
    )

    # 2. Authoritative member by account ID: Mubashir Butt
    event_repo.insert(
        event_type="TaskCommentAdded",
        source="jira",
        external_event_id="e3",
        timestamp=now_iso,
        actor_id="712020:e268bcd8-d981-4b4d-992d-d5694745df8b",
        actor_name="Mubashir Butt",
        task_id="SMTPSUPORT-50",
        payload={"comment": "Client update provided"}
    )

    # 3. Non-member / external user: Sara Connor (must be excluded)
    event_repo.insert(
        event_type="TaskCommentAdded",
        source="jira",
        external_event_id="e4",
        timestamp=now_iso,
        actor_name="Sara Connor",
        task_id="CF7-2",
        payload={"comment": "External review"}
    )

    # 4. Service account / QA Runner / MORITZ / Admin (must be excluded)
    event_repo.insert(
        event_type="TaskCreated",
        source="jira",
        external_event_id="e5",
        timestamp=now_iso,
        actor_name="QA Runner",
        task_id="QA-10",
        payload={"title": "Automated test ticket"}
    )
    event_repo.insert(
        event_type="TaskUpdated",
        source="jira",
        external_event_id="e6",
        timestamp=now_iso,
        actor_name="MORITZ",
        task_id="SYS-1",
        payload={"title": "System sync"}
    )

    # 5. Unresolved / Unknown actor (must be excluded)
    event_repo.insert(
        event_type="TaskStatusChanged",
        source="jira",
        external_event_id="e7",
        timestamp=now_iso,
        actor_name="Unknown Bot",
        task_id="BOT-1",
        payload={"old_status": "To Do", "new_status": "Done"}
    )

    report = report_gen.generate_report(target_date=today)

    assert report["date"] == today
    assert report["team_name"] == "Mursaleen Cluster"
    # Only 3 qualifying activities (2 for Ahsan Amin, 1 for Mubashir Butt)
    assert report["total_activities"] == 3
    assert report["active_members_count"] == 2
    assert report["tasks_created"] == 1
    assert report["status_transitions"] == 1
    assert report["comments_added"] == 1
    assert report["activities_by_resource"]["Ahsan Amin"] == 2
    assert report["activities_by_resource"]["Mubashir Butt"] == 1
    assert "Sara Connor" not in report["activities_by_resource"]
    assert "QA Runner" not in report["activities_by_resource"]
    assert "MORITZ" not in report["activities_by_resource"]

    # Verify transitions contain clickable Jira links
    assert len(report["recent_transitions"]) == 1
    trans = report["recent_transitions"][0]
    assert trans["task"] == "CF7-1"
    assert trans["task_url"] == settings.get_jira_browse_url("CF7-1")
    assert f"[CF7-1]({settings.get_jira_browse_url('CF7-1')})" == trans["jira_link"]
    assert trans["old_status"] == "To Do"
    assert trans["new_status"] == "In Progress"


def test_daily_activity_report_excluded_account_id(temp_db):
    """Test that canonical excluded account IDs are excluded even if in employee role table."""
    role_repo = EmployeeRoleRepository(temp_db)
    event_repo = EventRepository(temp_db)
    report_gen = DailyActivityReportGenerator(manager=temp_db)

    # Add a custom test user
    role_repo.upsert_assignment(
        account_id="excluded-test-acc-1",
        display_name="Excluded Engineer",
        designation="Software Engineer",
        role_category="engineering"
    )

    today = utc_now().strftime("%Y-%m-%d")
    now_iso = utc_now().isoformat()

    event_repo.insert(
        event_type="TaskCreated",
        source="jira",
        external_event_id="e-ex-1",
        timestamp=now_iso,
        actor_id="excluded-test-acc-1",
        actor_name="Excluded Engineer",
        task_id="TEST-1",
        payload={}
    )

    # With exclusion active
    orig_excluded = settings.DAILY_WORKLOG_EXCLUDED_ACCOUNT_IDS
    settings.DAILY_WORKLOG_EXCLUDED_ACCOUNT_IDS = "excluded-test-acc-1"
    try:
        report = report_gen.generate_report(target_date=today)
        assert report["total_activities"] == 0
        assert "Excluded Engineer" not in report["activities_by_resource"]
    finally:
        settings.DAILY_WORKLOG_EXCLUDED_ACCOUNT_IDS = orig_excluded


def test_daily_activity_embed_formatting_and_links(temp_db):
    """Test that DiscordFormatter produces a clean mobile embed with clickable Jira links."""
    event_repo = EventRepository(temp_db)
    report_gen = DailyActivityReportGenerator(manager=temp_db)

    today = "2026-09-12"
    now_iso = "2026-09-12T10:00:00Z"

    event_repo.insert(
        event_type="TaskStatusChanged",
        source="jira",
        external_event_id="ev-1",
        timestamp=now_iso,
        actor_name="Ahsan Amin",
        task_id="WSSS-326",
        payload={"old_status": "In Progress", "new_status": "Done"}
    )
    event_repo.insert(
        event_type="TaskCommentAdded",
        source="jira",
        external_event_id="ev-2",
        timestamp=now_iso,
        actor_name="Mubashir Butt",
        task_id="SMTPSUPORT-10",
        payload={"comment": "Client responded"}
    )

    report_data = report_gen.generate_report(target_date=today)
    embed_payload = DiscordFormatter.format_daily_report_embed(report_data)

    assert "embeds" in embed_payload
    assert len(embed_payload["embeds"]) == 1
    embed = embed_payload["embeds"][0]

    assert "📋" in embed["title"]
    assert "Daily Activity Report" in embed["title"]
    assert len(embed["title"]) <= 256
    assert len(embed["description"]) <= 3800
    assert "Ahsan Amin" in embed["description"]
    assert "Mubashir Butt" in embed["description"]
    assert "[WSSS-326](" in embed["description"]
    assert settings.get_jira_browse_url("WSSS-326") in embed["description"]
    assert "`In Progress` → `Done`" in embed["description"]
    assert embed["footer"]["text"] == "Generated by PM Operations Agent"

    total_chars = sum(len(e["title"]) + len(e["description"]) + len(e.get("footer", {}).get("text", "")) for e in embed_payload["embeds"])
    assert total_chars <= 5800


def test_daily_activity_embed_medium_chunking(temp_db):
    """Test that medium activity counts (e.g. 40 transitions) chunk safely into multiple embeds within cumulative budget."""
    event_repo = EventRepository(temp_db)
    report_gen = DailyActivityReportGenerator(manager=temp_db)

    today = "2026-09-12"
    now_iso = "2026-09-12T10:00:00Z"

    for i in range(1, 41):
        event_repo.insert(
            event_type="TaskStatusChanged",
            source="jira",
            external_event_id=f"med-ev-{i}",
            timestamp=now_iso,
            actor_name="Ahsan Amin",
            task_id=f"POSTSMTP-{i}",
            payload={"old_status": "To Do", "new_status": "In Progress"}
        )

    report_data = report_gen.generate_report(target_date=today)
    assert report_data["total_activities"] == 40
    assert len(report_data["recent_transitions"]) == 40

    embed_payload = DiscordFormatter.format_daily_report_embed(report_data)
    embeds = embed_payload["embeds"]
    assert len(embeds) == 2  # Part 1 and Part 2

    assert embeds[0]["title"] == "📋 Mursaleen Cluster — Daily Activity Report"
    assert embeds[1]["title"] == "📋 Mursaleen Cluster — Daily Activity Report (Part 2)"

    total_chars = sum(len(e["title"]) + len(e["description"]) + len(e.get("footer", {}).get("text", "")) for e in embeds)
    assert total_chars <= 5800

    for idx, emb in enumerate(embeds):
        assert len(emb["title"]) <= 256
        assert len(emb["description"]) <= 3800
        assert emb["footer"]["text"] == "Generated by PM Operations Agent"
        # Confirm no lines were split midway
        for line in emb["description"].splitlines():
            if line.startswith("• [POSTSMTP-"):
                assert line.endswith("— Ahsan Amin")

    # Confirm all 40 transitions are represented across the 2 embeds without omissions
    combined_desc = "\n".join(e["description"] for e in embeds)
    for i in range(1, 41):
        assert f"POSTSMTP-{i}" in combined_desc


def test_daily_activity_embed_massive_overflow_with_explicit_omission(temp_db):
    """Test that massive activity sets (e.g. 200 transitions) strictly stay within 5,800 chars and report omission count."""
    event_repo = EventRepository(temp_db)
    report_gen = DailyActivityReportGenerator(manager=temp_db)

    today = "2026-09-12"
    now_iso = "2026-09-12T10:00:00Z"

    for i in range(1, 201):
        event_repo.insert(
            event_type="TaskStatusChanged",
            source="jira",
            external_event_id=f"huge-ev-{i}",
            timestamp=now_iso,
            actor_name="Ahsan Amin",
            task_id=f"TASK-{i}",
            payload={"old_status": "To Do", "new_status": "In Progress"}
        )

    report_data = report_gen.generate_report(target_date=today)
    assert report_data["total_activities"] == 200
    assert len(report_data["recent_transitions"]) == 200
    assert report_data["status_transitions"] == 200

    embed_payload = DiscordFormatter.format_daily_report_embed(report_data)
    embeds = embed_payload["embeds"]
    assert len(embeds) <= 10

    total_chars = sum(len(e["title"]) + len(e["description"]) + len(e.get("footer", {}).get("text", "")) for e in embeds)
    assert total_chars <= 5800

    for emb in embeds:
        assert len(emb["title"]) <= 256
        assert len(emb["description"]) <= 3800
        assert emb["footer"]["text"] == "Generated by PM Operations Agent"

    # Verify that the explicit omission count is shown at the end of the last embed
    last_desc = embeds[-1]["description"]
    assert "more transition(s)" in last_desc
    assert not last_desc.endswith("...")
    # Verify no individual transition line was sliced
    for emb in embeds:
        for line in emb["description"].splitlines():
            if line.startswith("• [TASK-"):
                assert line.endswith("— Ahsan Amin")


@pytest.mark.asyncio
async def test_daily_activity_send_report_to_discord_idempotency(temp_db, monkeypatch):
    """Test send_report_to_discord records history and skips duplicate delivery on subsequent calls."""
    from unittest.mock import AsyncMock
    from app.core.actions.base import ActionResult
    from app.core.models.enums import ActionStatus
    from app.core.actions.engine import action_engine

    async def fake_execute(action):
        return ActionResult(
            action_id=getattr(action, "action_id", "act-sim-1"),
            target_system="discord",
            target_id="pm-alerts",
            status=ActionStatus.DRY_RUN_SIMULATED,
            success=True,
            result_data={"simulated": True}
        )

    monkeypatch.setattr(action_engine, "execute", fake_execute)

    report_gen = DailyActivityReportGenerator(manager=temp_db)
    event_repo = EventRepository(temp_db)
    today = "2026-09-12"
    now_iso = "2026-09-12T10:00:00Z"

    event_repo.insert(
        event_type="TaskCreated",
        source="jira",
        external_event_id="act-ev-1",
        timestamp=now_iso,
        actor_name="Ahsan Amin",
        task_id="CF7-100",
        payload={"title": "New feature"}
    )

    # First call: should generate and record history
    res1 = await report_gen.send_report_to_discord(target_date=today, force=False, record_history=True)
    assert res1["recorded_history"] is True
    assert res1["total_activities"] == 1
    assert report_gen.history_repo.has_report_been_sent("Mursaleen Cluster", today, report_type="daily_activity_report") is True

    # Second call without force: should skip with already_sent_today reason
    res2 = await report_gen.send_report_to_discord(target_date=today, force=False, record_history=True)
    assert res2["status"] == "skipped"
    assert res2["reason"] == "already_sent_today"

    # Third call with force=True: should bypass idempotency check
    res3 = await report_gen.send_report_to_discord(target_date=today, force=True, record_history=True)
    assert res3["status"].lower() in ("success", "simulated", "dry_run_simulated")
    assert res3["recorded_history"] is True
