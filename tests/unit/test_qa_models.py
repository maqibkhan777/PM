"""Unit tests for QA Domain Models, Verifiers, and Safeguards."""

import pytest
from app.config.settings import Settings
from app.core.qa.models import (
    TestTier,
    QAStatus,
    QAPriority,
    QATestResult,
    QARunSummary,
    QAScenario,
)
from app.core.qa.verifiers import (
    redact_sensitive_tokens,
    verify_discord_payload,
    verify_mattermost_payload,
    verify_jira_state,
)


def test_qa_test_result_standard_schema():
    """Verify QATestResult formats output conforming to the required QA Result Model."""
    res = QATestResult(
        test_id="RT-ASSIGN-001",
        run_id="2026-09-12-001",
        test_tier=TestTier.LIVE_E2E,
        category="Assignment",
        title="Assign to PM",
        status=QAStatus.PASS,
        priority=QAPriority.P0,
        jira_issue="TREN-378",
        jira_assignee="Mubashir",
        event_id="evt_123",
        action_id="act_456",
        idempotency_key="idem_789",
        discord_message_id="msg_999",
        mattermost_message_id="post_888",
        expected="Assigned to PM alert generated",
        actual="Alert generated successfully",
        evidence="qa/evidence/RT-ASSIGN-001.json",
    )
    d = res.to_qa_dict()
    assert d["test_id"] == "RT-ASSIGN-001"
    assert d["run_id"] == "2026-09-12-001"
    assert d["status"] == "PASS"
    assert d["jira_issue"] == "TREN-378"
    assert d["jira_assignee"] == "Mubashir"
    assert d["event_id"] == "evt_123"
    assert d["action_id"] == "act_456"
    assert d["idempotency_key"] == "idem_789"
    assert d["discord_message_id"] == "msg_999"
    assert d["mattermost_message_id"] == "post_888"
    assert d["expected"] == "Assigned to PM alert generated"
    assert d["actual"] == "Alert generated successfully"
    assert d["evidence"] == "qa/evidence/RT-ASSIGN-001.json"


def test_qa_run_summary_pass_rate():
    """Verify QARunSummary calculates pass rate and categorizes results correctly."""
    r1 = QATestResult(
        test_id="T1", run_id="R1", test_tier=TestTier.DRY_RUN_E2E, category="C", title="T1",
        status=QAStatus.PASS, expected="E", actual="A"
    )
    r2 = QATestResult(
        test_id="T2", run_id="R1", test_tier=TestTier.DRY_RUN_E2E, category="C", title="T2",
        status=QAStatus.FAIL, expected="E", actual="A"
    )
    r3 = QATestResult(
        test_id="T3", run_id="R1", test_tier=TestTier.DRY_RUN_E2E, category="C", title="T3",
        status=QAStatus.SKIPPED, expected="E", actual="A"
    )
    summary = QARunSummary(
        run_id="R1",
        tier=TestTier.DRY_RUN_E2E,
        target_issue="TREN-378",
        started_at="2026-09-12T04:00:00Z",
        completed_at="2026-09-12T04:01:00Z",
        total_tests=3,
        passed=1,
        failed=1,
        skipped=1,
        results=[r1, r2, r3],
    )
    assert summary.total_tests == 3
    assert summary.passed == 1
    assert summary.failed == 1
    assert summary.skipped == 1
    # Active tests = 3 - 1 skipped = 2; pass rate = 1/2 = 50.0%
    assert summary.pass_rate_percent == 50.0


def test_token_redaction_security():
    """Verify sensitive tokens and webhook secrets are redacted."""
    sensitive = {
        "JIRA_API_TOKEN": "ATATT3xFfGF0SECRETTOKEN",
        "DISCORD_BOT_TOKEN": "MTEyOTk1SECRETTOKEN",
        "webhook_url": "https://discord.com/api/webhooks/123456789/AbCdEfGhIjKlMnOpQrStUvWxYz",
        "nested": {
            "token": "secret_nested_token",
            "safe_field": "TREN-378",
        },
    }
    redacted = redact_sensitive_tokens(sensitive)
    assert redacted["JIRA_API_TOKEN"] == "[REDACTED]"
    assert redacted["DISCORD_BOT_TOKEN"] == "[REDACTED]"
    assert "https://discord.com/api/webhooks/[REDACTED_WEBHOOK]" in redacted["webhook_url"]
    assert redacted["nested"]["token"] == "[REDACTED]"
    assert redacted["nested"]["safe_field"] == "TREN-378"


def test_discord_and_mattermost_verifiers():
    """Verify payload structure validation for Discord and Mattermost."""
    discord_payload = {
        "content": "",
        "embeds": [
            {
                "title": "Task Assigned: TREN-378",
                "description": "Assigned to Aqib Khan",
            }
        ]
    }
    assert verify_discord_payload(discord_payload, expected_issue="TREN-378", expected_title_fragment="Task Assigned")
    assert not verify_discord_payload(discord_payload, expected_issue="OTHER-999")

    mm_payload = {
        "channel_id": "pm-alerts",
        "message": "Inactivity Reminder for issue TREN-378",
    }
    assert verify_mattermost_payload(mm_payload, expected_issue="TREN-378", expected_text_fragment="Inactivity Reminder")
    assert not verify_mattermost_payload(mm_payload, expected_issue="OTHER-999")


def test_live_qa_safeguard_assertions():
    """Verify that assert_live_qa_safe blocks execution when disabled or given wrong ticket."""
    safe_settings = Settings(LIVE_QA_ENABLED=False, LIVE_QA_JIRA_ISSUE="TREN-378")
    
    # Must raise when LIVE_QA_ENABLED is False
    with pytest.raises(RuntimeError, match="LIVE_QA_ENABLED is False"):
        safe_settings.assert_live_qa_safe("TREN-378")

    enabled_settings = Settings(LIVE_QA_ENABLED=True, LIVE_QA_JIRA_ISSUE="TREN-378")
    
    # Must pass for matching ticket
    enabled_settings.assert_live_qa_safe("TREN-378")
    enabled_settings.assert_live_qa_safe("tren-378")

    # Must block when target ticket is not TREN-378
    with pytest.raises(ValueError, match="does not match configured safe fixture"):
        enabled_settings.assert_live_qa_safe("PROD-100")
