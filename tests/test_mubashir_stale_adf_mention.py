"""Focused unit and integration tests for Mubashir Stale Support reminder ADF mention and concise wording."""

import pytest
from datetime import datetime, timedelta
import zoneinfo
from unittest.mock import AsyncMock, patch

from app.config.settings import settings
from app.connectors.jira.client import JiraClient, text_to_adf_doc
from app.core.actions.engine import ActionEngine
from app.core.actions.types import create_add_comment_action
from app.database.repositories import JiraIssueStateRepository
from app.services.scheduler import PeriodicScheduler

MUBASHIR_CANONICAL_ACCOUNT_ID = "712020:e268bcd8-d981-4b4d-992d-d5694745df8b"


def test_text_to_adf_doc_mention_conversion():
    """Verify that wiki-style mention tags convert to genuine ADF mention nodes."""
    raw_comment = (
        f"[~accountid:{MUBASHIR_CANONICAL_ACCOUNT_ID}:Mubashir Butt]\n\n"
        f"This Support ticket has had no meaningful update for 3 business days.\n"
        f"Please update the ticket with the current status or next action."
    )

    adf = text_to_adf_doc(raw_comment)
    assert adf["type"] == "doc"
    assert adf["version"] == 1
    assert len(adf["content"]) == 2

    # Paragraph 1: Mention node
    p1 = adf["content"][0]
    assert p1["type"] == "paragraph"
    assert len(p1["content"]) == 1
    mention = p1["content"][0]
    assert mention["type"] == "mention"
    assert mention["attrs"]["id"] == MUBASHIR_CANONICAL_ACCOUNT_ID
    assert mention["attrs"]["text"] == "@Mubashir Butt"
    assert mention["attrs"]["userType"] == "DEFAULT"

    # Paragraph 2: Text node with operational wording
    p2 = adf["content"][1]
    assert p2["type"] == "paragraph"
    assert len(p2["content"]) == 1
    text_node = p2["content"][0]
    assert text_node["type"] == "text"
    assert "This Support ticket has had no meaningful update for 3 business days." in text_node["text"]
    assert "Please update the ticket with the current status or next action." in text_node["text"]
    assert "Automated Stale Update Reminder" not in text_node["text"]


def test_text_to_adf_doc_plain_text():
    """Verify plain text strings without mentions are cleanly formatted into ADF."""
    plain = "This is a simple plain text comment."
    adf = text_to_adf_doc(plain)
    assert adf["type"] == "doc"
    assert len(adf["content"]) == 1
    assert adf["content"][0]["content"][0]["text"] == plain


def test_text_to_adf_doc_passthrough():
    """Verify pre-constructed ADF doc dict passes through unmodified."""
    custom_doc = {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "custom"}]}
        ]
    }
    adf = text_to_adf_doc(custom_doc)
    assert adf == custom_doc


@pytest.mark.asyncio
async def test_jira_client_add_comment_payload():
    """Verify JiraClient.add_comment delivers the correct ADF payload structure."""
    client = JiraClient(base_url="https://example.atlassian.net", email="test@example.com", api_token="secret")
    
    with patch.object(client, "_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = {"id": "10001"}

        raw_comment = (
            f"[~accountid:{MUBASHIR_CANONICAL_ACCOUNT_ID}:Mubashir Butt]\n\n"
            f"This Support ticket has had no meaningful update for 3 business days.\n"
            f"Please update the ticket with the current status or next action."
        )

        res = await client.add_comment("TREN-378", raw_comment)
        assert res["id"] == "10001"

        mock_req.assert_called_once()
        args, kwargs = mock_req.call_args
        assert args[0] == "POST"
        assert args[1] == "/rest/api/3/issue/TREN-378/comment"
        
        json_data = kwargs["json_data"]
        assert "body" in json_data
        body_adf = json_data["body"]
        assert body_adf["type"] == "doc"
        # Must have mention node
        p1 = body_adf["content"][0]
        assert p1["content"][0]["type"] == "mention"
        assert p1["content"][0]["attrs"]["id"] == MUBASHIR_CANONICAL_ACCOUNT_ID


@pytest.mark.asyncio
async def test_stale_support_evaluation_generates_correct_comment_and_idempotency(temp_db, monkeypatch):
    """Verify PeriodicScheduler generates the concise reminder and respects idempotency."""
    monkeypatch.setattr(type(settings), "is_jira_configured", lambda self: False)
    state_repo = JiraIssueStateRepository(temp_db)
    scheduler = PeriodicScheduler(temp_db)

    tz = zoneinfo.ZoneInfo("Asia/Karachi")
    now_tz = datetime.now(tz)
    past_iso = (now_tz - timedelta(days=5)).isoformat()

    state_repo.upsert(
        jira_issue_key="TREN-378",
        summary="Mubashir Support QA Test Ticket",
        status="Support team review",
        last_seen_at=past_iso,
        last_activity_at=past_iso,
        project_key="TREN",
        raw_reference={
            "fields": {
                "summary": "Mubashir Support QA Test Ticket",
                "status": {"name": "Support team review"},
                "issuetype": {"name": "Support"},
                "creator": {"accountId": MUBASHIR_CANONICAL_ACCOUNT_ID, "displayName": "Mubashir Butt"},
            }
        }
    )

    actions = await scheduler._evaluate_mubashir_stale_support_tickets()
    assert len(actions) == 1
    action = actions[0]
    assert action.target_id == "TREN-378"
    assert action.requested_by == "MubashirStaleSupport"

    comment = action.parameters["comment"]
    assert "This Support ticket has had no meaningful update for 3 business days." in comment
    assert "Please update the ticket with the current status or next action." in comment
    assert "Automated Stale Update Reminder" not in comment

    # Route through ActionEngine in DRY_RUN mode
    from app.connectors.jira.connector import JiraConnector
    mock_client = AsyncMock()
    mock_client.add_comment = AsyncMock(return_value={"id": "c-123"})
    jira_conn = JiraConnector(client=mock_client)

    engine = ActionEngine(temp_db)
    engine.register_connector(jira_conn)

    monkeypatch.setattr(settings, "DRY_RUN", True)
    result = await engine.execute(action, approved=True)
    assert result.success is True
    assert result.dry_run is True
    # In DRY_RUN mode, client.add_comment must NOT be called
    mock_client.add_comment.assert_not_called()

    # Immediate duplicate evaluation -> Idempotent (0 actions)
    actions2 = await scheduler._evaluate_mubashir_stale_support_tickets()
    assert len(actions2) == 0

