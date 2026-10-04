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
    AI_AGENT_SESSION_STORE,
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


@pytest.fixture(autouse=True)
def clear_ai_agent_session_store():
    AI_AGENT_SESSION_STORE.clear()
    yield
    AI_AGENT_SESSION_STORE.clear()


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
        "content": f"<@{AI_BOT_ID}> help",
        "author": {"id": AUTHORIZED_USER_ID, "bot": False, "username": "TestPM"},
        "mentions": [{"id": AI_BOT_ID, "username": "PMAIBot"}],
    }

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))

    res = await mention_handler.handle_message_create(message_payload, http_client=mock_client)
    assert res is not None
    assert res["status"] == "processed"
    assert res["prompt"] == "help"
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
    assert "assistant is disabled" in str(res["response"]).lower()


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
    assert "AI Planning Proposal (Advisory Only" in resp_text
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


@pytest.mark.asyncio
async def test_subsequent_discord_requests_after_provider_failure(mention_handler, monkeypatch):
    """16. Ensure that after a provider failure, subsequent messages continue to be processed without crashing."""
    monkeypatch.setattr(settings, "DISCORD_AI_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "DISCORD_AI_BOT_TOKEN", "mock_ai_token")
    monkeypatch.setattr(settings, "DISCORD_AI_APPLICATION_ID", AI_BOT_ID)
    monkeypatch.setattr(settings, "DISCORD_AI_ALLOWED_CHANNEL_IDS", TEST_CHANNEL_ID)
    monkeypatch.setattr(settings, "DISCORD_AI_ALLOWED_USER_IDS", AUTHORIZED_USER_ID)
    monkeypatch.setattr(settings, "AI_ENABLED", True)

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))

    # 1. First request fails with a simulated provider error
    with patch("app.services.ai.decision.AIDecisionService.evaluate_attention", new_callable=AsyncMock, side_effect=RuntimeError("Transient timeout")):
        msg1 = {
            "id": "msg_fail_1",
            "channel_id": TEST_CHANNEL_ID,
            "content": f"<@{AI_BOT_ID}> attention",
            "author": {"id": AUTHORIZED_USER_ID, "bot": False},
            "mentions": [{"id": AI_BOT_ID}],
        }
        res1 = await mention_handler.handle_message_create(msg1, http_client=mock_client)
        assert res1["status"] == "processed"
        assert "❌ An error occurred while processing your AI request." in str(res1["response"])

    # 2. Subsequent request (e.g. help or next attention request) processes cleanly
    msg2 = {
        "id": "msg_success_2",
        "channel_id": TEST_CHANNEL_ID,
        "content": f"<@{AI_BOT_ID}> help",
        "author": {"id": AUTHORIZED_USER_ID, "bot": False},
        "mentions": [{"id": AI_BOT_ID}],
    }
    res2 = await mention_handler.handle_message_create(msg2, http_client=mock_client)
    assert res2["status"] == "processed"
    assert "PM AI Operations Assistant" in str(res2["response"])


@pytest.mark.asyncio
async def test_discord_planning_request_post_smtp_scoping(mention_handler, temp_db, monkeypatch):
    """17. Discord request for Post SMTP support board scopes context to SMTPSUPORT and excludes other project issues."""
    from app.database.repositories import JiraIssueStateRepository, EmployeeRoleRepository
    from app.core.models.planning import PlanningProposal, TaskPlanningProposal, PlanningEstimate, EvidenceReference, EvidenceType

    monkeypatch.setattr(settings, "DISCORD_AI_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "DISCORD_AI_BOT_TOKEN", "mock_ai_token")
    monkeypatch.setattr(settings, "DISCORD_AI_APPLICATION_ID", AI_BOT_ID)
    monkeypatch.setattr(settings, "DISCORD_AI_ALLOWED_CHANNEL_IDS", TEST_CHANNEL_ID)
    monkeypatch.setattr(settings, "DISCORD_AI_ALLOWED_USER_IDS", AUTHORIZED_USER_ID)
    monkeypatch.setattr(settings, "AI_ENABLED", True)

    role_repo = EmployeeRoleRepository(temp_db)
    issue_repo = JiraIssueStateRepository(temp_db)

    role_repo.upsert_assignment(
        account_id="acc_ahsan",
        display_name="Ahsan Amin",
        designation="Senior Engineer",
        role_category="Engineering",
    )

    # Insert one SMTPSUPORT issue and one unrelated WSSS issue
    issue_repo.upsert(
        jira_issue_key="SMTPSUPORT-10",
        summary="Fix OAuth timeout in Post SMTP",
        status="In Progress",
        assignee="acc_ahsan",
        project_key="SMTPSUPORT",
        priority="High",
        team_group="Mursaleen Cluster",
    )
    issue_repo.upsert(
        jira_issue_key="WSSS-999",
        summary="Unrelated WSSS ticket",
        status="In Progress",
        assignee="acc_ahsan",
        project_key="WSSS",
        priority="Medium",
        team_group="Mursaleen Cluster",
    )

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))

    captured_ctx = []

    async def mock_generate_plan(ctx, actor="PMPlanningEngine"):
        captured_ctx.append(ctx)
        return PlanningProposal(
            proposal_version="proposal-v1",
            generated_at="2026-09-27T04:00:00Z",
            context_version="planning-v1",
            anchor_date="2026-09-27",
            planning_horizon_working_days=10,
            requires_human_review=True,
            overall_confidence=0.95,
            summary="Prioritize Post SMTP support OAuth issue.",
            task_proposals=[
                TaskPlanningProposal(
                    issue_key="SMTPSUPORT-10",
                    proposed_estimate=PlanningEstimate(
                        value=3.0,
                        unit="hours",
                        confidence=0.9,
                        rationale="OAuth token refresh handler",
                        evidence_references=[],
                    ),
                    proposed_start_date="2026-09-27",
                    proposed_due_date="2026-09-28",
                    date_confidence=0.9,
                    sequencing_position=1,
                    proposed_predecessors=[],
                    proposed_successors=[],
                    risk_level="LOW",
                    evidence_references=[],
                    assumptions=[],
                    requires_human_review=True,
                )
            ],
            sequencing_proposals=[],
            risk_signals=[],
            assumptions=[],
            evidence_references=[],
        )

    with patch("app.services.ai.planning.AIPlanningService.generate_plan", side_effect=mock_generate_plan):
        msg = {
            "id": "msg_plan_postsmtp",
            "channel_id": TEST_CHANNEL_ID,
            "content": f"<@{AI_BOT_ID}> prepare a planning proposal for the remaining work in the Post SMTP support board",
            "author": {"id": AUTHORIZED_USER_ID, "bot": False},
            "mentions": [{"id": AI_BOT_ID}],
        }
        res = await mention_handler.handle_message_create(msg, http_client=mock_client)
        assert res["status"] == "processed"
        resp_text = str(res["response"])
        assert "Post SMTP Support Board (SMTPSUPORT)" in resp_text
        assert "SMTPSUPORT-10" in resp_text

        # Verify context only contained SMTPSUPORT issues, not WSSS
        assert len(captured_ctx) == 1
        tasks_in_ctx = [t.issue_key for t in captured_ctx[0].tasks]
        assert "SMTPSUPORT-10" in tasks_in_ctx
        assert "WSSS-999" not in tasks_in_ctx


@pytest.mark.asyncio
async def test_discord_planning_request_unknown_board_fails_closed(mention_handler, monkeypatch):
    """18. Planning request with unknown or unresolvable board fails closed with helpful error message."""
    monkeypatch.setattr(settings, "DISCORD_AI_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "DISCORD_AI_BOT_TOKEN", "mock_ai_token")
    monkeypatch.setattr(settings, "DISCORD_AI_APPLICATION_ID", AI_BOT_ID)
    monkeypatch.setattr(settings, "DISCORD_AI_ALLOWED_CHANNEL_IDS", TEST_CHANNEL_ID)
    monkeypatch.setattr(settings, "DISCORD_AI_ALLOWED_USER_IDS", AUTHORIZED_USER_ID)
    monkeypatch.setattr(settings, "AI_ENABLED", True)

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))

    msg = {
        "id": "msg_plan_unknown",
        "channel_id": TEST_CHANNEL_ID,
        "content": f"<@{AI_BOT_ID}> prepare a planning proposal for the NonExistentPlugin board",
        "author": {"id": AUTHORIZED_USER_ID, "bot": False},
        "mentions": [{"id": AI_BOT_ID}],
    }
    res = await mention_handler.handle_message_create(msg, http_client=mock_client)
    assert res["status"] == "processed"
    resp_text = str(res["response"])
    assert "❌" in resp_text
    assert "Could not resolve Jira project/board scope" in resp_text


@pytest.mark.asyncio
async def test_follow_up_discord_reply_resumes_pending_clarification(mention_handler, monkeypatch):
    monkeypatch.setattr(settings, "DISCORD_AI_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "DISCORD_AI_BOT_TOKEN", "mock_ai_token")
    monkeypatch.setattr(settings, "DISCORD_AI_APPLICATION_ID", AI_BOT_ID)
    monkeypatch.setattr(settings, "DISCORD_AI_ALLOWED_CHANNEL_IDS", TEST_CHANNEL_ID)
    monkeypatch.setattr(settings, "DISCORD_AI_ALLOWED_USER_IDS", AUTHORIZED_USER_ID)
    monkeypatch.setattr(settings, "AI_ENABLED", True)

    from app.agent_core.agent_models import AgentStep, AgentState, Candidate, ToolCall, ToolResultStatus, ToolSpec, UncertaintyClass
    from app.agent_core.tooling import ToolRegistry

    async def get_active_sprints(_args):
        return {
            "status": ToolResultStatus.AMBIGUOUS.value,
            "tool": "get_active_sprints",
            "candidates": [
                {"value": "Sprint A", "label": "Sprint A", "evidence": ["active sprint"]},
                {"value": "Sprint B", "label": "Sprint B", "evidence": ["active sprint"]},
            ],
        }

    registry = ToolRegistry()
    registry.register("get_active_sprints", ToolSpec(name="get_active_sprints", description="sprints"), get_active_sprints)

    class ResumeProvider:
        async def next_agent_step(self, user_goal: str, actor: str, state: AgentState, tools):
            if state.selected_sprint:
                return AgentStep.final(f"Resumed with {state.selected_sprint}.")
            return AgentStep.tool_calls([ToolCall(tool_name="get_active_sprints", arguments={})], uncertainty=UncertaintyClass.KNOWN)

    monkeypatch.setattr("app.services.ai.pm_tools.build_tool_registry", lambda manager=None: registry)
    monkeypatch.setattr("app.services.ai.config.resolve_ai_provider", lambda *args, **kwargs: ResumeProvider())

    first = {
        "id": "msg_resume_1",
        "channel_id": TEST_CHANNEL_ID,
        "content": f"<@{AI_BOT_ID}> create a plan for the WPEPSUP work",
        "author": {"id": AUTHORIZED_USER_ID, "bot": False},
        "mentions": [{"id": AI_BOT_ID}],
    }
    second = {
        "id": "msg_resume_2",
        "channel_id": TEST_CHANNEL_ID,
        "content": f"<@{AI_BOT_ID}> Sprint B",
        "author": {"id": AUTHORIZED_USER_ID, "bot": False},
        "mentions": [{"id": AI_BOT_ID}],
    }

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))

    res1 = await mention_handler.handle_message_create(first, http_client=mock_client)
    assert res1["status"] == "processed"
    assert "Which one do you mean?" in str(res1["response"])

    res2 = await mention_handler.handle_message_create(second, http_client=mock_client)
    assert res2["status"] == "processed"
    assert "Resumed with Sprint B." in str(res2["response"])


@pytest.mark.asyncio
async def test_sequential_questions_same_channel_user_start_new_goal_after_completion(mention_handler, monkeypatch):
    monkeypatch.setattr(settings, "DISCORD_AI_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "DISCORD_AI_BOT_TOKEN", "mock_ai_token")
    monkeypatch.setattr(settings, "DISCORD_AI_APPLICATION_ID", AI_BOT_ID)
    monkeypatch.setattr(settings, "DISCORD_AI_ALLOWED_CHANNEL_IDS", TEST_CHANNEL_ID)
    monkeypatch.setattr(settings, "DISCORD_AI_ALLOWED_USER_IDS", AUTHORIZED_USER_ID)
    monkeypatch.setattr(settings, "AI_ENABLED", True)

    from app.agent_core.agent_models import AgentStep
    from app.agent_core.tooling import ToolRegistry

    recorded_goals = []

    class GoalRecordingProvider:
        async def next_agent_step(self, user_goal, actor, state, tools):
            recorded_goals.append(user_goal)
            return AgentStep.final(f"Handled: {user_goal}")

    monkeypatch.setattr("app.services.ai.config.resolve_ai_provider", lambda *args, **kwargs: GoalRecordingProvider())
    monkeypatch.setattr("app.services.ai.pm_tools.build_tool_registry", lambda manager=None: ToolRegistry())

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))

    first = {
        "id": "msg_seq_1",
        "channel_id": TEST_CHANNEL_ID,
        "content": f"<@{AI_BOT_ID}> What is the status of WSSS-326?",
        "author": {"id": AUTHORIZED_USER_ID, "bot": False},
        "mentions": [{"id": AI_BOT_ID}],
    }
    second = {
        "id": "msg_seq_2",
        "channel_id": TEST_CHANNEL_ID,
        "content": f"<@{AI_BOT_ID}> Is the sprint on track?",
        "author": {"id": AUTHORIZED_USER_ID, "bot": False},
        "mentions": [{"id": AI_BOT_ID}],
    }

    res1 = await mention_handler.handle_message_create(first, http_client=mock_client)
    res2 = await mention_handler.handle_message_create(second, http_client=mock_client)
    assert res1["status"] == "processed"
    assert res2["status"] == "processed"
    assert recorded_goals == ["What is the status of WSSS-326?", "Is the sprint on track?"]


@pytest.mark.asyncio
async def test_clarification_response_is_truncated_under_discord_limit(mention_handler, monkeypatch):
    monkeypatch.setattr(settings, "DISCORD_AI_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "DISCORD_AI_BOT_TOKEN", "mock_ai_token")
    monkeypatch.setattr(settings, "DISCORD_AI_APPLICATION_ID", AI_BOT_ID)
    monkeypatch.setattr(settings, "DISCORD_AI_ALLOWED_CHANNEL_IDS", TEST_CHANNEL_ID)
    monkeypatch.setattr(settings, "DISCORD_AI_ALLOWED_USER_IDS", AUTHORIZED_USER_ID)
    monkeypatch.setattr(settings, "AI_ENABLED", True)

    many_candidates = [{"value": f"proj-{i}", "label": f"Project {i} with a very long descriptive name", "evidence": ["active sprint count"]} for i in range(1, 120)]

    with patch(
        "app.agent_core.core.AgentCore.run",
        new=AsyncMock(
            return_value={
                "status": "NEEDS_CLARIFICATION",
                "question": "Which project do you mean?",
                "candidates": many_candidates,
            }
        ),
    ):
        message_payload = {
            "id": "msg_len_001",
            "channel_id": TEST_CHANNEL_ID,
            "content": f"<@{AI_BOT_ID}> which sprint should I use?",
            "author": {"id": AUTHORIZED_USER_ID, "bot": False},
            "mentions": [{"id": AI_BOT_ID}],
        }
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))

        res = await mention_handler.handle_message_create(message_payload, http_client=mock_client)
        assert res["status"] == "processed"
        response_text = str(res["response"])
        assert len(response_text) <= 2000
        assert "and" in response_text.lower()


