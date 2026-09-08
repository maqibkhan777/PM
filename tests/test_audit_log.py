"""Unit tests for audit logging and secret redaction."""

import pytest
from app.services.audit_service import AuditService
from app.utils.logger import sanitize_dict, redact_text


def test_audit_log_insertion(temp_db):
    """Test inserting and listing audit log records."""
    audit = AuditService(manager=temp_db)
    log_id = audit.log_action(
        actor="RulesEngine",
        action="TransitionTask",
        target="CF7-421",
        result="Success",
        details={"status": "In Progress"}
    )
    assert log_id is not None

    logs = audit.list_logs(limit=10)
    assert len(logs) == 1
    assert logs[0]["action"] == "TransitionTask"
    assert logs[0]["target"] == "CF7-421"


def test_sensitive_data_redaction():
    """Test that API tokens and auth headers are redacted from dictionaries and text."""
    dirty_dict = {
        "api_token": "secret_token_12345",
        "authorization": "Bearer ya29.a0AfH6SM...",
        "user": "ahsan",
        "nested": {
            "webhook_url": "https://discord.com/api/webhooks/12345678/my-super-secret-token"
        }
    }

    clean_dict = sanitize_dict(dirty_dict)
    assert clean_dict["api_token"] == "******"
    assert clean_dict["authorization"] == "******"
    assert clean_dict["user"] == "ahsan"
    assert clean_dict["nested"]["webhook_url"] == "******"

    text = "Sending request with Bearer secret_bearer_token_xyz"
    redacted = redact_text(text)
    assert "secret_bearer_token_xyz" not in redacted
    assert "Bearer ******" in redacted
