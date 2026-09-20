"""Comprehensive tests for PM AI Phase 2B: Read-Only AI Attention Discord Interface."""

from unittest.mock import AsyncMock, patch, MagicMock
import pytest
from app.config.settings import settings
from app.connectors.discord.slash_commands import DiscordSlashCommandHandler
from app.database.repositories import UserRepository
from app.core.actions.engine import ActionEngine
from app.services.ai.models import (
    AttentionItemAnalysis,
    PMAttentionAnalysis,
    ProposedAction,
)
from app.services.ai.provider import MockAIProvider
from app.services.ai.safety import AISafetyViolation


CANONICAL_PM_CHANNEL_ID = "1547090800771604482"


@pytest.fixture
def slash_handler(temp_db):
    """Fixture providing an isolated DiscordSlashCommandHandler instance."""
    user_repo = UserRepository(temp_db)
    action_engine = ActionEngine(manager=temp_db)
    return DiscordSlashCommandHandler(user_repo=user_repo, action_engine=action_engine, manager=temp_db)


@pytest.mark.asyncio
async def test_ai_attention_correct_pm_channel_accepted(slash_handler, monkeypatch):
    """1. /pm ai attention invoked in the configured PM channel is accepted and processed."""
    monkeypatch.setattr(settings, "DISCORD_PM_CHANNEL_ID", CANONICAL_PM_CHANNEL_ID)
    monkeypatch.setattr(settings, "DISCORD_PM_ALLOWED_USERS", "*")
    monkeypatch.setattr(settings, "AI_ENABLED", False)

    res = await slash_handler.execute_subcommand(
        subcommand="ai attention",
        options={},
        discord_user_id="user123",
        channel_id=CANONICAL_PM_CHANNEL_ID,
    )
    assert "ℹ️ AI decision support is currently disabled" in str(res)


@pytest.mark.asyncio
async def test_ai_attention_wrong_channel_rejected(slash_handler, monkeypatch):
    """2. /pm ai attention invoked in any non-authorized channel (#notifications, #general, etc.) is rejected."""
    monkeypatch.setattr(settings, "DISCORD_PM_CHANNEL_ID", CANONICAL_PM_CHANNEL_ID)
    monkeypatch.setattr(settings, "DISCORD_PM_ALLOWED_USERS", "*")

    for bad_channel in ("notifications", "general", "dev-chat", "12345", None, "   "):
        res = await slash_handler.execute_subcommand(
            subcommand="ai attention",
            options={},
            discord_user_id="user123",
            channel_id=bad_channel,
        )
        assert "❌ PM commands can only be used in #pm-alerts." in str(res)


@pytest.mark.asyncio
async def test_ai_attention_unauthorized_user_rejected(slash_handler, monkeypatch):
    """3. /pm ai attention invoked by an unauthorized user is rejected by existing RBAC."""
    monkeypatch.setattr(settings, "DISCORD_PM_CHANNEL_ID", CANONICAL_PM_CHANNEL_ID)
    monkeypatch.setattr(settings, "DISCORD_PM_ALLOWED_USERS", "999888777")

    res = await slash_handler.execute_subcommand(
        subcommand="ai attention",
        options={},
        discord_user_id="unauthorized_111",
        channel_id=CANONICAL_PM_CHANNEL_ID,
    )
    assert "❌ You are not authorized to use PM commands." in str(res)


@pytest.mark.asyncio
async def test_ai_attention_ai_disabled_default_behavior(slash_handler, monkeypatch):
    """4. When AI_ENABLED=false (default), command returns clear deterministic message without calling provider."""
    monkeypatch.setattr(settings, "DISCORD_PM_CHANNEL_ID", CANONICAL_PM_CHANNEL_ID)
    monkeypatch.setattr(settings, "DISCORD_PM_ALLOWED_USERS", "*")
    monkeypatch.setattr(settings, "AI_ENABLED", False)

    with patch("app.services.ai.provider.MockAIProvider.analyze_attention", new_callable=AsyncMock) as mock_analyze:
        res = await slash_handler.execute_subcommand(
            subcommand="ai attention",
            options={},
            discord_user_id="user123",
            channel_id=CANONICAL_PM_CHANNEL_ID,
        )
        assert not mock_analyze.called
        assert "AI decision support is currently disabled" in str(res)
        assert "AI_ENABLED=false" in str(res)


@pytest.mark.asyncio
async def test_ai_attention_successful_mock_analysis_embeds(slash_handler, monkeypatch):
    """5. When AI_ENABLED=true, command calls provider, passes safety gate, and formats rich embeds."""
    monkeypatch.setattr(settings, "DISCORD_PM_CHANNEL_ID", CANONICAL_PM_CHANNEL_ID)
    monkeypatch.setattr(settings, "DISCORD_PM_ALLOWED_USERS", "*")
    monkeypatch.setattr(settings, "AI_ENABLED", True)

    custom_analysis = PMAttentionAnalysis(
        analysis_id="test-analysis-1",
        generated_at="2026-09-20T14:00:00Z",
        scope_team="Mursaleen Cluster",
        summary="Found 1 overdue issue requiring intervention.",
        attention_items=[
            AttentionItemAnalysis(
                issue_key="TREN-378",
                title="Payment gateway timeout bug",
                current_status="In Progress",
                assignee="Ahsan Amin",
                priority="High",
                due_date="2026-09-18",
                inactivity_duration="3 days",
                attention_reason="Task inactive for 3 days past due date.",
                supporting_evidence=["Due date 2026-09-18 passed", "Status is In Progress"],
                recommendation="Check in with Ahsan on blocker status.",
                confidence=0.90,
                uncertainty_or_missing_info=None,
            )
        ],
        evidence=["Overdue scan across Mursaleen Cluster"],
        recommendation="Review during daily standup.",
        confidence=0.88,
        requires_human_review=True,
    )

    with patch("app.services.ai.decision.AIDecisionService.evaluate_attention", new_callable=AsyncMock, return_value=custom_analysis) as mock_eval:
        res = await slash_handler.execute_subcommand(
            subcommand="ai attention",
            options={},
            discord_user_id="user123",
            channel_id=CANONICAL_PM_CHANNEL_ID,
        )
        assert mock_eval.called
        assert isinstance(res, dict) and "embeds" in res
        embeds = res["embeds"]
        assert len(embeds) >= 1
        assert "PM AI Attention Analysis" in embeds[0]["title"]
        assert "Found 1 overdue issue" in embeds[0]["description"]
        # Item in fields
        fields = embeds[0]["fields"]
        item_field = next((f for f in fields if "TREN-378" in f["name"]), None)
        assert item_field is not None
        assert "Payment gateway timeout bug" in item_field["name"]
        assert "Facts:" in item_field["value"]
        assert "AI Reason:" in item_field["value"]
        assert "Rec:" in item_field["value"]


@pytest.mark.asyncio
async def test_ai_attention_empty_attention_set(slash_handler, monkeypatch):
    """6. Empty attention set returns clean advisory embed with zero items."""
    monkeypatch.setattr(settings, "DISCORD_PM_CHANNEL_ID", CANONICAL_PM_CHANNEL_ID)
    monkeypatch.setattr(settings, "DISCORD_PM_ALLOWED_USERS", "*")
    monkeypatch.setattr(settings, "AI_ENABLED", True)

    empty_analysis = PMAttentionAnalysis(
        analysis_id="test-empty-1",
        generated_at="2026-09-20T14:00:00Z",
        scope_team="Mursaleen Cluster",
        summary="No attention items flagged.",
        attention_items=[],
        evidence=["All active tasks within normal SLAs"],
        recommendation="Maintain standard monitoring.",
        confidence=1.0,
        requires_human_review=True,
    )

    with patch("app.services.ai.decision.AIDecisionService.evaluate_attention", new_callable=AsyncMock, return_value=empty_analysis):
        res = await slash_handler.execute_subcommand(
            subcommand="ai attention",
            options={},
            discord_user_id="user123",
            channel_id=CANONICAL_PM_CHANNEL_ID,
        )
        assert isinstance(res, dict) and "embeds" in res
        assert "No attention items flagged" in res["embeds"][0]["description"]


@pytest.mark.asyncio
async def test_ai_attention_safety_gate_violation_handled(slash_handler, monkeypatch):
    """7. When safety gate raises AISafetyViolation, error is gracefully caught and reported without crashing."""
    monkeypatch.setattr(settings, "DISCORD_PM_CHANNEL_ID", CANONICAL_PM_CHANNEL_ID)
    monkeypatch.setattr(settings, "DISCORD_PM_ALLOWED_USERS", "*")
    monkeypatch.setattr(settings, "AI_ENABLED", True)

    with patch("app.services.ai.decision.AIDecisionService.evaluate_attention", new_callable=AsyncMock, side_effect=AISafetyViolation("Test safety rejection")):
        res = await slash_handler.execute_subcommand(
            subcommand="ai attention",
            options={},
            discord_user_id="user123",
            channel_id=CANONICAL_PM_CHANNEL_ID,
        )
        assert "❌ AI Attention Analysis failed safety verification:" in str(res)
        assert "Test safety rejection" in str(res)


@pytest.mark.asyncio
async def test_ai_attention_provider_exception_handled(slash_handler, monkeypatch):
    """8. When provider raises unexpected exception, sanitized generic message is returned without leaking secrets."""
    monkeypatch.setattr(settings, "DISCORD_PM_CHANNEL_ID", CANONICAL_PM_CHANNEL_ID)
    monkeypatch.setattr(settings, "DISCORD_PM_ALLOWED_USERS", "*")
    monkeypatch.setattr(settings, "AI_ENABLED", True)

    with patch("app.services.ai.decision.AIDecisionService.evaluate_attention", new_callable=AsyncMock, side_effect=RuntimeError("Internal provider failure")):
        res = await slash_handler.execute_subcommand(
            subcommand="ai attention",
            options={},
            discord_user_id="user123",
            channel_id=CANONICAL_PM_CHANNEL_ID,
        )
        assert "❌ Unable to generate the AI attention analysis right now." in str(res)
        assert "Internal provider failure" not in str(res)  # Internal message not leaked


@pytest.mark.asyncio
async def test_ai_attention_no_action_engine_execution(slash_handler, monkeypatch):
    """9. Confirm that invoking /pm ai attention NEVER triggers ActionEngine execution."""
    monkeypatch.setattr(settings, "DISCORD_PM_CHANNEL_ID", CANONICAL_PM_CHANNEL_ID)
    monkeypatch.setattr(settings, "DISCORD_PM_ALLOWED_USERS", "*")
    monkeypatch.setattr(settings, "AI_ENABLED", True)

    with patch("app.core.actions.engine.action_engine.execute", new_callable=AsyncMock) as mock_action_exec, \
         patch("app.core.actions.engine.ActionEngine.execute", new_callable=AsyncMock) as mock_engine_exec:
        res = await slash_handler.execute_subcommand(
            subcommand="ai attention",
            options={},
            discord_user_id="user123",
            channel_id=CANONICAL_PM_CHANNEL_ID,
        )
        assert not mock_action_exec.called
        assert not mock_engine_exec.called


@pytest.mark.asyncio
async def test_ai_attention_interaction_parser_support(slash_handler, monkeypatch):
    """10. Interaction payload with nested SUB_COMMAND_GROUP or SUB_COMMAND correctly parses to 'ai attention'."""
    monkeypatch.setattr(settings, "DISCORD_PM_CHANNEL_ID", CANONICAL_PM_CHANNEL_ID)
    monkeypatch.setattr(settings, "DISCORD_PM_ALLOWED_USERS", "*")
    monkeypatch.setattr(settings, "AI_ENABLED", False)

    # Simulated Discord Gateway interaction payload for /pm ai attention
    interaction_payload = {
        "type": 2,
        "data": {
            "name": "pm",
            "options": [
                {
                    "type": 2,  # SUB_COMMAND_GROUP
                    "name": "ai",
                    "options": [
                        {
                            "type": 1,  # SUB_COMMAND
                            "name": "attention",
                            "options": [],
                        }
                    ],
                }
            ],
        },
        "user": {"id": "user123"},
        "channel_id": CANONICAL_PM_CHANNEL_ID,
    }

    resp = await slash_handler.handle_interaction(interaction_payload)
    assert resp["type"] == 4  # CHANNEL_MESSAGE
    content = resp["data"].get("content", "")
    assert "AI decision support is currently disabled" in content


@pytest.mark.asyncio
async def test_ai_attention_discord_pagination_bounds():
    """11. Verify AIAttentionReportFormatter pagination creates multiple embeds when fields exceed 25."""
    from app.services.ai.report_formatter import AIAttentionReportFormatter

    items = [
        AttentionItemAnalysis(
            issue_key=f"PROJ-{i}",
            title=f"Task {i}",
            current_status="In Progress",
            attention_reason=f"Stalled for {i} days",
            recommendation="Review with assignee",
            confidence=0.85,
        )
        for i in range(30)
    ]
    large_analysis = PMAttentionAnalysis(
        analysis_id="large-1",
        generated_at="2026-09-20T14:00:00Z",
        scope_team="Mursaleen Cluster",
        summary="30 items flagged",
        attention_items=items,
        evidence=["Large batch scan"],
        recommendation="Bulk review",
        confidence=0.88,
        requires_human_review=True,
    )

    embed_payload = AIAttentionReportFormatter.format_discord_embeds(large_analysis)
    assert "embeds" in embed_payload
    embeds = embed_payload["embeds"]
    assert len(embeds) == 2  # Split across header/first 25 and continuation embed
    assert len(embeds[0]["fields"]) <= 25
    assert len(embeds[1]["fields"]) <= 25
    assert "Continued — Page 2" in embeds[1]["title"]
