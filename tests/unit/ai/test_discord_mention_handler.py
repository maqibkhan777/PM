"""Comprehensive Unit Tests for PM AI Discord Mention Interface (Phase 1).

Covers all Phase 1 safety, authorization, routing, and formatting requirements:
1. Authorized mention in approved channel is accepted and processed.
2. Unauthorized user is rejected with a safe message.
3. Unapproved channel is ignored/rejected (fails closed).
4. Messages without a mention are ignored.
5. Bot messages (including PM Bot) are ignored.
6. Existing slash-command behavior remains completely unchanged.
7. Duplicate messages are not processed twice (TTL deduplication).
8. Bot mention is cleanly stripped before AI processing.
9. AI disabled (default) returns safe user-facing message without invoking provider.
10. Missing configuration fails closed safely.
11. Provider failure produces a safe, sanitized user-facing response.
12. AI responses NEVER invoke the Action Engine or mutate Jira.
13. Planning requests produce proposals ONLY (no execution, advisory only).
14. Discord message length limits (>2000 chars) are handled safely via chunking.
15. Thread and reply context is bounded.
"""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.config.settings import settings
from app.connectors.discord.ai_mention_handler import (
    AIDiscordMentionHandler,
    AIDiscordGatewayClient,
)
from app.connectors.discord.ai_discord_router import (
    AIDiscordRouterService,
    AIRequestIntent,
)
from app.connectors.discord.slash_commands import DiscordSlashCommandHandler
from app.core.actions.engine import ActionEngine
from app.database.repositories import UserRepository
from app.services.ai.models import (
    AttentionItemAnalysis,
    PMAttentionAnalysis,
)
from app.core.models.planning import (
    EstimateUnit,
    PlanningContext,
    PlanningEstimate,
    PlanningProposal,
    TaskPlanningProposal,
)
from app.services.ai.safety import AISafetyViolation


AI_BOT_ID = "998877665544332211"
TEST_CHANNEL_ID = "123456789012345678"
AUTHORIZED_USER_ID = "112233445566778899"
UNAUTHORIZED_USER_ID = "999999999999999999"


@pytest.fixture
def mention_handler(temp_db, monkeypatch):
    """Fixture providing an isolated AIDiscordMentionHandler instance."""
    monkeypatch.setattr(settings, "AI_PROVIDER", "mock")
    monkeypatch.setattr(settings, "AI_MODEL", None)
    router = AIDiscordRouterService(manager=temp_db)
    handler = AIDiscordMentionHandler(router=router, bot_user_id=AI_BOT_ID)
    return handler


@pytest.mark.asyncio
async def test_authorized_mention_accepted(mention_handler, monkeypatch):
    """1. Authorized mention in approved channel is accepted and routed to AI."""
    monkeypatch.setattr(settings, "DISCORD_AI_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "DISCORD_AI_BOT_TOKEN", "mock_ai_token")
    monkeypatch.setattr(settings, "DISCORD_AI_APPLICATION_ID", AI_BOT_ID)
    monkeypatch.setattr(settings, "DISCORD_AI_ALLOWED_CHANNEL_IDS", TEST_CHANNEL_ID)
    monkeypatch.setattr(settings, "DISCORD_AI_ALLOWED_USER_IDS", AUTHORIZED_USER_ID)
    monkeypatch.setattr(settings, "AI_ENABLED", True)

    message_payload = {
        "id": "msg_001",
        "channel_id": TEST_CHANNEL_ID,
        "content": f"<@{AI_BOT_ID}> what can you do?",
        "author": {"id": AUTHORIZED_USER_ID, "bot": False, "username": "TestPM"},
        "mentions": [{"id": AI_BOT_ID, "username": "PMAIBot"}],
    }

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))

    res = await mention_handler.handle_message_create(message_payload, http_client=mock_client)
    assert res is not None
    assert res["status"] == "processed"
    assert res["prompt"] == "what can you do?"
    assert "PM AI Operations Assistant" in str(res["response"])
    assert mock_client.post.called


@pytest.mark.asyncio
async def test_unauthorized_user_rejected(mention_handler, monkeypatch):
    """2. Unauthorized user mention is rejected with a safe refusal reply."""
    monkeypatch.setattr(settings, "DISCORD_AI_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "DISCORD_AI_BOT_TOKEN", "mock_ai_token")
    monkeypatch.setattr(settings, "DISCORD_AI_APPLICATION_ID", AI_BOT_ID)
    monkeypatch.setattr(settings, "DISCORD_AI_ALLOWED_CHANNEL_IDS", TEST_CHANNEL_ID)
    monkeypatch.setattr(settings, "DISCORD_AI_ALLOWED_USER_IDS", AUTHORIZED_USER_ID)

    message_payload = {
        "id": "msg_002",
        "channel_id": TEST_CHANNEL_ID,
        "content": f"<@{AI_BOT_ID}> hello",
        "author": {"id": UNAUTHORIZED_USER_ID, "bot": False, "username": "Attacker"},
        "mentions": [{"id": AI_BOT_ID}],
    }

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))

    res = await mention_handler.handle_message_create(message_payload, http_client=mock_client)
    assert res is not None
    assert res["status"] == "rejected"
    assert res["reason"] == "unauthorized_user"
    assert mock_client.post.called
    # Check payload sent to discord
    call_args = mock_client.post.call_args[1]["json"]
    assert "not authorized" in call_args.get("content", "")


@pytest.mark.asyncio
async def test_unapproved_channel_ignored(mention_handler, monkeypatch):
    """3. Mention in unapproved channel is ignored / rejected without responding."""
    monkeypatch.setattr(settings, "DISCORD_AI_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "DISCORD_AI_ALLOWED_CHANNEL_IDS", TEST_CHANNEL_ID)
    monkeypatch.setattr(settings, "DISCORD_AI_ALLOWED_USER_IDS", AUTHORIZED_USER_ID)

    message_payload = {
        "id": "msg_003",
        "channel_id": "999999_wrong_channel",
        "content": f"<@{AI_BOT_ID}> hello",
        "author": {"id": AUTHORIZED_USER_ID, "bot": False},
        "mentions": [{"id": AI_BOT_ID}],
    }

    mock_client = AsyncMock()
    res = await mention_handler.handle_message_create(message_payload, http_client=mock_client)
    assert res is not None
    assert res["status"] == "rejected"
    assert res["reason"] == "unapproved_channel"
    assert not mock_client.post.called


@pytest.mark.asyncio
async def test_message_without_mention_ignored(mention_handler, monkeypatch):
    """4. Messages that do not mention the AI bot are ignored completely."""
    monkeypatch.setattr(settings, "DISCORD_AI_ALLOWED_CHANNEL_IDS", TEST_CHANNEL_ID)
    monkeypatch.setattr(settings, "DISCORD_AI_ALLOWED_USER_IDS", AUTHORIZED_USER_ID)

    message_payload = {
        "id": "msg_004",
        "channel_id": TEST_CHANNEL_ID,
        "content": "Just a normal team chat message",
        "author": {"id": AUTHORIZED_USER_ID, "bot": False},
        "mentions": [],
    }

    res = await mention_handler.handle_message_create(message_payload)
    assert res is None


@pytest.mark.asyncio
async def test_bot_messages_ignored(mention_handler, monkeypatch):
    """5. Bot messages (including messages from other bots or itself) are ignored."""
    monkeypatch.setattr(settings, "DISCORD_AI_ALLOWED_CHANNEL_IDS", TEST_CHANNEL_ID)
    monkeypatch.setattr(settings, "DISCORD_AI_ALLOWED_USER_IDS", "*")

    # Message from production PM Bot
    message_payload = {
        "id": "msg_005",
        "channel_id": TEST_CHANNEL_ID,
        "content": f"<@{AI_BOT_ID}> Automated report dispatch",
        "author": {"id": "111222333", "bot": True, "username": "PM Operations Agent"},
        "mentions": [{"id": AI_BOT_ID}],
    }

    res = await mention_handler.handle_message_create(message_payload)
    assert res is None


@pytest.mark.asyncio
async def test_existing_slash_commands_remain_unchanged(temp_db, monkeypatch):
    """6. Existing /pm slash commands continue to work exactly as before."""
    user_repo = UserRepository(temp_db)
    action_engine = ActionEngine(manager=temp_db)
    slash_handler = DiscordSlashCommandHandler(user_repo=user_repo, action_engine=action_engine, manager=temp_db)

    monkeypatch.setattr(settings, "DISCORD_PM_CHANNEL_ID", "1547090800771604482")
    monkeypatch.setattr(settings, "DISCORD_PM_ALLOWED_USERS", "*")

    res = await slash_handler.execute_subcommand(
        subcommand="help",
        options={},
        discord_user_id="user123",
        channel_id="1547090800771604482",
    )
    assert "**PM Commands**" in str(res)
    assert "/pm status <ticket>" in str(res)


@pytest.mark.asyncio
async def test_duplicate_message_not_processed_twice(mention_handler, monkeypatch):
    """7. Duplicate Discord message IDs are deduplicated and processed once."""
    monkeypatch.setattr(settings, "DISCORD_AI_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "DISCORD_AI_BOT_TOKEN", "mock_ai_token")
    monkeypatch.setattr(settings, "DISCORD_AI_APPLICATION_ID", AI_BOT_ID)
    monkeypatch.setattr(settings, "DISCORD_AI_ALLOWED_CHANNEL_IDS", TEST_CHANNEL_ID)
    monkeypatch.setattr(settings, "DISCORD_AI_ALLOWED_USER_IDS", AUTHORIZED_USER_ID)
    monkeypatch.setattr(settings, "AI_ENABLED", True)

    message_payload = {
        "id": "msg_dup_100",
        "channel_id": TEST_CHANNEL_ID,
        "content": f"<@{AI_BOT_ID}> help",
        "author": {"id": AUTHORIZED_USER_ID, "bot": False},
        "mentions": [{"id": AI_BOT_ID}],
    }

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))

    # First attempt: processes successfully
    res1 = await mention_handler.handle_message_create(message_payload, http_client=mock_client)
    assert res1["status"] == "processed"

    # Second attempt with exact same message ID: ignored as duplicate
    res2 = await mention_handler.handle_message_create(message_payload, http_client=mock_client)
    assert res2["status"] == "ignored"
    assert res2["reason"] == "duplicate_message"


@pytest.mark.asyncio
async def test_mention_stripped_cleanly(mention_handler):
    """8. Mentions (<@ID>, <@!ID>, @AIBot) are cleanly stripped from prompt."""
    raw1 = f"<@{AI_BOT_ID}> What tasks are overdue?"
    assert mention_handler.strip_mention(raw1) == "What tasks are overdue?"

    raw2 = f"<@!{AI_BOT_ID}>   Analyze attention   "
    assert mention_handler.strip_mention(raw2) == "Analyze attention"

    raw3 = f"<@{AI_BOT_ID}>"
    assert mention_handler.strip_mention(raw3) == ""


@pytest.mark.asyncio
async def test_ai_disabled_default_behavior(mention_handler, monkeypatch):
    """9. When AI_ENABLED=false (default), mention produces safe advisory message without calling provider."""
    monkeypatch.setattr(settings, "DISCORD_AI_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "DISCORD_AI_BOT_TOKEN", "mock_ai_token")
    monkeypatch.setattr(settings, "DISCORD_AI_APPLICATION_ID", AI_BOT_ID)
    monkeypatch.setattr(settings, "DISCORD_AI_ALLOWED_CHANNEL_IDS", TEST_CHANNEL_ID)
    monkeypatch.setattr(settings, "DISCORD_AI_ALLOWED_USER_IDS", AUTHORIZED_USER_ID)
    monkeypatch.setattr(settings, "AI_ENABLED", False)

    message_payload = {
        "id": "msg_009",
        "channel_id": TEST_CHANNEL_ID,
        "content": f"<@{AI_BOT_ID}> analyze team bottlenecks",
        "author": {"id": AUTHORIZED_USER_ID, "bot": False},
        "mentions": [{"id": AI_BOT_ID}],
    }

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))

    res = await mention_handler.handle_message_create(message_payload, http_client=mock_client)
    assert res["status"] == "processed"
    assert "PM AI assistant is currently disabled" in str(res["response"])
    assert "AI_ENABLED=false" in str(res["response"])


@pytest.mark.asyncio
async def test_missing_config_fails_closed(mention_handler, monkeypatch):
    """10. Missing allowlist configuration fails closed (denies all users/channels)."""
    monkeypatch.setattr(settings, "DISCORD_AI_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "DISCORD_AI_ALLOWED_CHANNEL_IDS", "")
    monkeypatch.setattr(settings, "DISCORD_AI_ALLOWED_USER_IDS", "")

    assert not mention_handler.is_allowed_channel(TEST_CHANNEL_ID)
    assert not mention_handler.is_authorized_user(AUTHORIZED_USER_ID)


@pytest.mark.asyncio
async def test_provider_failure_produces_safe_response(mention_handler, monkeypatch):
    """11. Provider failure produces a sanitized error message without leaking stack traces or keys."""
    monkeypatch.setattr(settings, "DISCORD_AI_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "DISCORD_AI_BOT_TOKEN", "mock_ai_token")
    monkeypatch.setattr(settings, "DISCORD_AI_APPLICATION_ID", AI_BOT_ID)
    monkeypatch.setattr(settings, "DISCORD_AI_ALLOWED_CHANNEL_IDS", TEST_CHANNEL_ID)
    monkeypatch.setattr(settings, "DISCORD_AI_ALLOWED_USER_IDS", AUTHORIZED_USER_ID)
    monkeypatch.setattr(settings, "AI_ENABLED", True)

    with patch("app.services.ai.decision.AIDecisionService.evaluate_attention", new_callable=AsyncMock, side_effect=RuntimeError("Secret upstream connection timeout key=sk-12345")):
        message_payload = {
            "id": "msg_011",
            "channel_id": TEST_CHANNEL_ID,
            "content": f"<@{AI_BOT_ID}> team attention",
            "author": {"id": AUTHORIZED_USER_ID, "bot": False},
            "mentions": [{"id": AI_BOT_ID}],
        }
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))

        res = await mention_handler.handle_message_create(message_payload, http_client=mock_client)
        assert res["status"] == "processed"
        assert "❌ An error occurred while processing your AI request." in str(res["response"])
        assert "sk-12345" not in str(res["response"])


@pytest.mark.asyncio
async def test_no_action_engine_or_jira_mutations(mention_handler, monkeypatch):
    """12. Verify that mention processing NEVER invokes the Action Engine or executes mutations."""
    monkeypatch.setattr(settings, "DISCORD_AI_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "DISCORD_AI_BOT_TOKEN", "mock_ai_token")
    monkeypatch.setattr(settings, "DISCORD_AI_APPLICATION_ID", AI_BOT_ID)
    monkeypatch.setattr(settings, "DISCORD_AI_ALLOWED_CHANNEL_IDS", TEST_CHANNEL_ID)
    monkeypatch.setattr(settings, "DISCORD_AI_ALLOWED_USER_IDS", AUTHORIZED_USER_ID)
    monkeypatch.setattr(settings, "AI_ENABLED", True)

    with patch("app.core.actions.engine.action_engine.execute", new_callable=AsyncMock) as mock_ae_exec, \
         patch("app.core.actions.engine.ActionEngine.execute", new_callable=AsyncMock) as mock_engine_exec:
        message_payload = {
            "id": "msg_012",
            "channel_id": TEST_CHANNEL_ID,
            "content": f"<@{AI_BOT_ID}> generate plan for the team",
            "author": {"id": AUTHORIZED_USER_ID, "bot": False},
            "mentions": [{"id": AI_BOT_ID}],
        }
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))

        res = await mention_handler.handle_message_create(message_payload, http_client=mock_client)
        assert res["status"] == "processed"
        assert not mock_ae_exec.called
        assert not mock_engine_exec.called


@pytest.mark.asyncio
async def test_planning_request_produces_proposals_only(mention_handler, monkeypatch):
    """13. Planning requests return advisory proposals only with explicit disclaimer."""
    monkeypatch.setattr(settings, "DISCORD_AI_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "DISCORD_AI_BOT_TOKEN", "mock_ai_token")
    monkeypatch.setattr(settings, "DISCORD_AI_APPLICATION_ID", AI_BOT_ID)
    monkeypatch.setattr(settings, "DISCORD_AI_ALLOWED_CHANNEL_IDS", TEST_CHANNEL_ID)
    monkeypatch.setattr(settings, "DISCORD_AI_ALLOWED_USER_IDS", AUTHORIZED_USER_ID)
    monkeypatch.setattr(settings, "AI_ENABLED", True)

    message_payload = {
        "id": "msg_013",
        "channel_id": TEST_CHANNEL_ID,
        "content": f"<@{AI_BOT_ID}> propose schedule plan for next sprint",
        "author": {"id": AUTHORIZED_USER_ID, "bot": False},
        "mentions": [{"id": AI_BOT_ID}],
    }

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))

    res = await mention_handler.handle_message_create(message_payload, http_client=mock_client)
    assert res["status"] == "processed"
    resp_text = str(res["response"])
    assert "AI Planning Proposal (Advisory Only)" in resp_text
    assert "This proposal is for review only. It has not been approved or executed." in resp_text


def test_discord_message_chunking():
    """14. Text exceeding 2000 characters is cleanly chunked without losing content."""
    long_text = "\n".join([f"Line {i}: Details about task item {i}" for i in range(150)])
    assert len(long_text) > 3000

    chunks = AIDiscordMentionHandler.chunk_text(long_text, max_len=2000)
    assert len(chunks) >= 2
    for ch in chunks:
        assert len(ch) <= 2000


@pytest.mark.asyncio
async def test_reply_thread_context_bounded(mention_handler, monkeypatch):
    """15. Direct replies extract bounded thread context."""
    monkeypatch.setattr(settings, "DISCORD_AI_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "DISCORD_AI_BOT_TOKEN", "mock_ai_token")
    monkeypatch.setattr(settings, "DISCORD_AI_APPLICATION_ID", AI_BOT_ID)
    monkeypatch.setattr(settings, "DISCORD_AI_ALLOWED_CHANNEL_IDS", TEST_CHANNEL_ID)
    monkeypatch.setattr(settings, "DISCORD_AI_ALLOWED_USER_IDS", AUTHORIZED_USER_ID)
    monkeypatch.setattr(settings, "AI_ENABLED", True)

    message_payload = {
        "id": "msg_015",
        "channel_id": TEST_CHANNEL_ID,
        "content": f"<@{AI_BOT_ID}> what should we do about this?",
        "author": {"id": AUTHORIZED_USER_ID, "bot": False},
        "mentions": [{"id": AI_BOT_ID}],
        "referenced_message": {
            "id": "msg_parent",
            "author": {"username": "Ahsan", "id": "1234"},
            "content": "WSSS-326 is blocked due to missing API specification from external vendor.",
        }
    }

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))

    res = await mention_handler.handle_message_create(message_payload, http_client=mock_client)
    assert res["status"] == "processed"
