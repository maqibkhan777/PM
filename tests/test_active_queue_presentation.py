"""Unit and integration tests for Phase 3: Mobile-Friendly Discord Presentation for Active Queue."""

import pytest
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

from app.config.settings import settings
from app.connectors.discord.formatter import DiscordFormatter
from app.connectors.discord.slash_commands import DiscordSlashCommandHandler
from app.connectors.discord.bot_connector import DiscordBotConnector
from app.connectors.discord import DiscordWebhookConnector
from app.connectors.jira.connector import JiraConnector
from app.core.actions.engine import ActionEngine
from app.database.schema import init_db
from app.database.repositories import JiraIssueStateRepository, UserRepository


class MockJiraClientForQueue:
    """Mock Jira client for testing queue slash commands."""

    def __init__(self):
        self.users = [
            {"accountId": "712020:c12d2371-c035-423a-bc44-30799981aa7a", "displayName": "Abdul Subhan", "emailAddress": "abdul@example.com", "active": True},
            {"accountId": "712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de", "displayName": "Ahsan Amin", "emailAddress": "ahsan@example.com", "active": True},
        ]

    async def get_users(self, query: str) -> List[Dict[str, Any]]:
        q = query.lower()
        return [u for u in self.users if q in u["displayName"].lower() or q in u["emailAddress"].lower() or q == u["accountId"]]


@pytest.fixture
def slash_setup(temp_db):
    """Setup ActionEngine and DiscordSlashCommandHandler for isolated testing."""
    engine = ActionEngine(manager=temp_db)
    mock_jira_client = MockJiraClientForQueue()
    jira_conn = JiraConnector(client=mock_jira_client)
    engine.register_connector(jira_conn)

    class WrapperDiscordConn(DiscordWebhookConnector):
        async def execute_action(self, action: Any) -> Dict[str, Any]:
            return {"status": "success", "http_code": 204}

    engine.register_connector(WrapperDiscordConn(webhook_url="https://discord.com/mock"))

    user_repo = UserRepository(temp_db)
    for u in mock_jira_client.users:
        user_repo.upsert(
            external_system="jira",
            external_user_id=u["accountId"],
            display_name=u["displayName"],
            email=u["emailAddress"],
        )

    handler = DiscordSlashCommandHandler(user_repo=user_repo, action_engine=engine)
    bot = DiscordBotConnector(bot_token="test_token_123", slash_handler=handler)

    settings.DISCORD_PM_ALLOWED_USERS = "123456789,987654321"
    settings.DISCORD_PM_CHANNEL_ID = "pm-alerts"
    settings.DRY_RUN = False

    return handler, bot, mock_jira_client, engine, temp_db


def _make_queue_ticket(
    key: str,
    summary: str = "Test ticket summary",
    status: str = "In Progress",
    priority: str = "High",
    due_date: str = "2026-09-20",
) -> Dict[str, Any]:
    return {
        "key": key,
        "summary": summary,
        "status": status,
        "priority": priority,
        "due_date": due_date,
        "due_date_raw": due_date,
        "url": settings.get_jira_browse_url(key),
    }


def test_normal_queue_mobile_format_and_clickable_links():
    """Verify normal queue produces mobile embed format with clickable Jira links and no ASCII table."""
    queue_data = {
        "account_id": "712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de",
        "display_name": "Ahsan Amin",
        "team_name": "Mursaleen Cluster",
        "date": "2026-09-17",
        "formatted_date": "Thu, Sep 17, 2026",
        "active_count": 2,
        "tickets": [
            _make_queue_ticket("PROJ-101", "Fix checkout button timeout", "In Progress", "High", "2026-09-20"),
            _make_queue_ticket("PROJ-102", "Optimize database query latency", "To Do", "Medium", "2026-09-22"),
        ],
        "is_excluded": False,
        "jql": "filter = 15370",
        "filter_id": "15370",
        "source": "jira_live",
        "cache_fallback": False,
    }

    embed_payload = DiscordFormatter.format_user_active_queue_embed(queue_data)
    assert isinstance(embed_payload, dict) and "embeds" in embed_payload
    assert len(embed_payload["embeds"]) == 1

    embed = embed_payload["embeds"][0]
    assert embed["title"] == "📋 Active Queue — Ahsan Amin"
    desc = embed["description"]

    # Source & metrics
    assert "🟢 Live Jira queue" in desc
    assert "Active Tasks:** 2" in desc
    assert "Thu, Sep 17, 2026" in desc

    # Tickets format (no table markup)
    assert "| Ticket |" not in desc
    assert "|-------|" not in desc
    assert "`| `[" not in desc

    # Clickable links
    expected_url_101 = settings.get_jira_browse_url("PROJ-101")
    assert f"[PROJ-101]({expected_url_101})" in desc
    assert "Fix checkout button timeout" in desc
    assert "In Progress • High • Due: 2026-09-20" in desc

    expected_url_102 = settings.get_jira_browse_url("PROJ-102")
    assert f"[PROJ-102]({expected_url_102})" in desc
    assert "Optimize database query latency" in desc
    assert "To Do • Medium • Due: 2026-09-22" in desc


def test_empty_queue_mobile_presentation():
    """Verify empty queue produces clean zero-state message with retained source indication."""
    queue_data = {
        "account_id": "712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de",
        "display_name": "Ahsan Amin",
        "team_name": "Mursaleen Cluster",
        "date": "2026-09-17",
        "formatted_date": "Thu, Sep 17, 2026",
        "active_count": 0,
        "tickets": [],
        "is_excluded": False,
        "jql": "filter = 15370",
        "filter_id": "15370",
        "source": "jira_live",
        "cache_fallback": False,
    }

    res = DiscordFormatter.format_user_active_queue_embed(queue_data)
    embed = res["embeds"][0]
    assert embed["title"] == "📋 Active Queue — Ahsan Amin"
    assert "🟢 Live Jira queue" in embed["description"]
    assert "✅ No active tasks in queue for Ahsan Amin." in embed["description"]
    assert embed["color"] == 3066993  # COLOR_GREEN


def test_live_jira_source_indicator():
    """Verify live Jira queue displays green indicator."""
    queue_data = {
        "display_name": "Daniyal Raza",
        "formatted_date": "Today",
        "active_count": 1,
        "tickets": [_make_queue_ticket("TREN-1")],
        "source": "jira_live",
        "cache_fallback": False,
    }
    res = DiscordFormatter.format_user_active_queue_embed(queue_data)
    assert "🟢 Live Jira queue" in res["embeds"][0]["description"]


def test_sqlite_fallback_source_indicator():
    """Verify SQLite cache fallback displays orange warning indicator."""
    queue_data = {
        "display_name": "Daniyal Raza",
        "formatted_date": "Today",
        "active_count": 1,
        "tickets": [_make_queue_ticket("FALLBACK-1")],
        "source": "sqlite_cache",
        "cache_fallback": True,
    }
    res = DiscordFormatter.format_user_active_queue_embed(queue_data)
    assert "🟠 Cached queue — Jira unavailable" in res["embeds"][0]["description"]


def test_canonical_no_filter_source_indicator():
    """Verify unmapped/canonical queue displays neutral canonical indicator."""
    queue_data = {
        "display_name": "Mubashir Butt",
        "formatted_date": "Today",
        "active_count": 1,
        "tickets": [_make_queue_ticket("MUB-1")],
        "source": "sqlite_cache",
        "cache_fallback": False,
        "filter_id": None,
    }
    res = DiscordFormatter.format_user_active_queue_embed(queue_data)
    assert "📋 Canonical queue" in res["embeds"][0]["description"]


def test_large_queue_multi_embed_chunking_and_no_line_splits():
    """Verify large queue splits across multiple embeds without breaking individual ticket lines."""
    tickets = [
        _make_queue_ticket(
            f"BIG-{i}",
            summary=f"Task {i} medium summary for embed chunking verification across multiple parts",
            status="In Progress",
            priority="High",
            due_date="2026-09-25",
        )
        for i in range(1, 26)
    ]

    queue_data = {
        "display_name": "Ahsan Iftikhar",
        "formatted_date": "Thu, Sep 17, 2026",
        "active_count": len(tickets),
        "tickets": tickets,
        "source": "jira_live",
        "cache_fallback": False,
    }

    res = DiscordFormatter.format_user_active_queue_embed(queue_data)
    embeds = res["embeds"]

    # Should create 2 embeds safely within budget
    assert len(embeds) >= 2
    assert embeds[0]["title"] == "📋 Active Queue — Ahsan Iftikhar"
    assert embeds[1]["title"] == "📋 Active Queue — Ahsan Iftikhar (Part 2)"

    # Check each embed does not exceed limits
    for embed in embeds:
        assert len(embed["description"]) <= 3800
        # Verify no broken lines (every bullet should be followed by its metadata line)
        desc_lines = embed["description"].split("\n")
        for idx, line in enumerate(desc_lines):
            if line.startswith("• [BIG-"):
                assert idx + 1 < len(desc_lines)
                assert "In Progress • High • Due:" in desc_lines[idx + 1]

    # Verify all 25 tickets are accounted for across the embeds
    all_keys = [f"BIG-{i}" for i in range(1, 26)]
    found_keys = []
    for k in all_keys:
        if any(k in e["description"] for e in embeds):
            found_keys.append(k)
    assert len(found_keys) == 25


def test_heavy_overflow_explicit_omission_and_safety_budget():
    """Verify queue exceeding total presentation budget reports explicit omitted count without silent data loss."""
    # Create 120 verbose tickets that will exceed the 5800 cumulative character budget
    tickets = [
        _make_queue_ticket(
            f"OVERFLOW-{i}",
            summary="Very long detailed ticket summary testing maximum budget constraints across Discord embeds",
            status="In Progress",
            priority="Critical",
            due_date="2026-09-30",
        )
        for i in range(1, 121)
    ]

    queue_data = {
        "display_name": "Muhammad Ali Siddiqui",
        "formatted_date": "Thu, Sep 17, 2026",
        "active_count": 120,
        "tickets": tickets,
        "source": "jira_live",
        "cache_fallback": False,
    }

    res = DiscordFormatter.format_user_active_queue_embed(queue_data)
    embeds = res["embeds"]

    assert len(embeds) <= 10

    total_chars = sum(len(e["title"]) + len(e["description"]) + len(e.get("footer", {}).get("text", "")) for e in embeds)
    assert total_chars <= 5800

    # The last embed must explicitly state omitted tasks
    last_embed_desc = embeds[-1]["description"]
    assert "more active task(s)" in last_embed_desc
    assert "Active Tasks:** 120" in embeds[0]["description"]


def test_text_fallback_mobile_format():
    """Verify format_user_active_queue_text produces clean mobile-friendly markdown without tables."""
    queue_data = {
        "display_name": "Azain Hassan",
        "formatted_date": "Thu, Sep 17, 2026",
        "active_count": 1,
        "tickets": [_make_queue_ticket("AZA-10", "Implement search index", "Doing", "Medium", "2026-09-21")],
        "source": "jira_live",
        "cache_fallback": False,
    }

    text = DiscordFormatter.format_user_active_queue_text(queue_data)
    assert "📋 **Active Queue — Azain Hassan**" in text
    assert "🟢 Live Jira queue" in text
    assert "[AZA-10](" in text
    assert "Doing • Medium • Due: 2026-09-21" in text
    assert "| Ticket |" not in text
    assert "`| `[" not in text


@pytest.mark.asyncio
async def test_slash_command_queue_and_report_equivalence(slash_setup):
    """Verify /pm queue user:<res> and /pm report name:queue user:<res> produce identical embed payloads."""
    handler, bot, jira_client, engine, temp_db = slash_setup
    state_repo = JiraIssueStateRepository(temp_db)

    state_repo.upsert(
        jira_issue_key="WSSS-801",
        summary="Configure caching layer",
        status="In Progress",
        assignee="Abdul Subhan",
        priority="High",
        due_date="2026-09-20",
        team_group=settings.JIRA_TEAM_GROUP,
        raw_reference={"assignee": {"accountId": "712020:c12d2371-c035-423a-bc44-30799981aa7a", "displayName": "Abdul Subhan"}},
    )

    # 1. /pm queue user:Abdul Subhan
    res_direct = await handler.execute_subcommand(
        subcommand="queue",
        options={"user": "Abdul Subhan"},
        discord_user_id="123456789",
        channel_id="pm-alerts",
    )

    # 2. /pm report name:queue user:Abdul Subhan
    res_report = await handler.execute_subcommand(
        subcommand="report",
        options={"name": "queue", "user": "Abdul Subhan"},
        discord_user_id="123456789",
        channel_id="pm-alerts",
    )

    assert isinstance(res_direct, dict) and "embeds" in res_direct
    assert isinstance(res_report, dict) and "embeds" in res_report
    assert res_direct == res_report

    embed = res_direct["embeds"][0]
    assert embed["title"] == "📋 Active Queue — Abdul Subhan"
    assert "[WSSS-801](" in embed["description"]
    assert "Configure caching layer" in embed["description"]
    assert "In Progress • High • Due: 2026-09-20" in embed["description"]
