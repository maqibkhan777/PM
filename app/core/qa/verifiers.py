"""Verification helpers and security redaction utilities for QA testing."""

import re
from typing import Any, Dict, List, Optional, Union
import json


TOKEN_PATTERNS = [
    (re.compile(r"https://discord\.com/api/webhooks/[0-9]+/[A-Za-z0-9_\-]+", re.IGNORECASE), "https://discord.com/api/webhooks/[REDACTED_WEBHOOK]"),
    (re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]+", re.IGNORECASE), "Bearer [REDACTED_TOKEN]"),
    (re.compile(r"Basic\s+[A-Za-z0-9=+/]+", re.IGNORECASE), "Basic [REDACTED_AUTH]"),
    (re.compile(r'"JIRA_API_TOKEN":\s*"[^"]+"', re.IGNORECASE), '"JIRA_API_TOKEN": "[REDACTED]"'),
    (re.compile(r'"DISCORD_BOT_TOKEN":\s*"[^"]+"', re.IGNORECASE), '"DISCORD_BOT_TOKEN": "[REDACTED]"'),
    (re.compile(r'"MATTERMOST_TOKEN":\s*"[^"]+"', re.IGNORECASE), '"MATTERMOST_TOKEN": "[REDACTED]"'),
    (re.compile(r'"api_token":\s*"[^"]+"', re.IGNORECASE), '"api_token": "[REDACTED]"'),
]


def redact_sensitive_tokens(data: Union[str, Dict[str, Any], List[Any]]) -> Union[str, Dict[str, Any], List[Any]]:
    """Recursively redact API tokens, secrets, webhook URLs, and authorization headers."""
    if isinstance(data, str):
        redacted = data
        for pattern, replacement in TOKEN_PATTERNS:
            redacted = pattern.sub(replacement, redacted)
        return redacted
    elif isinstance(data, dict):
        cleaned = {}
        for k, v in data.items():
            k_lower = str(k).lower()
            if any(secret_term in k_lower for secret_term in ["token", "secret", "password", "api_key", "authorization", "auth_header"]):
                cleaned[k] = "[REDACTED]"
            elif "webhook" in k_lower and isinstance(v, str) and "http" in v:
                cleaned[k] = "https://discord.com/api/webhooks/[REDACTED_WEBHOOK]"
            else:
                cleaned[k] = redact_sensitive_tokens(v)
        return cleaned
    elif isinstance(data, list):
        return [redact_sensitive_tokens(item) for item in data]
    return data


def verify_discord_payload(
    payload: Dict[str, Any],
    expected_issue: Optional[str] = None,
    expected_title_fragment: Optional[str] = None,
    expected_assignee: Optional[str] = None,
) -> bool:
    """Verify that a Discord webhook or bot interaction payload contains required fields and correct content."""
    if not isinstance(payload, dict):
        return False
    
    # Check embed content if present
    embeds = payload.get("embeds", [])
    content = payload.get("content", "")
    full_text = content + " " + json.dumps(embeds)
    
    if expected_issue and expected_issue.upper() not in full_text.upper():
        return False
        
    if expected_title_fragment and expected_title_fragment.lower() not in full_text.lower():
        return False
        
    if expected_assignee and expected_assignee.lower() not in full_text.lower():
        return False
        
    return True


def verify_mattermost_payload(
    payload: Dict[str, Any],
    expected_issue: Optional[str] = None,
    expected_text_fragment: Optional[str] = None,
) -> bool:
    """Verify that a Mattermost payload contains the expected issue and text."""
    if not isinstance(payload, dict):
        return False
    
    message = payload.get("message", "")
    if expected_issue and expected_issue.upper() not in message.upper():
        return False
    if expected_text_fragment and expected_text_fragment.lower() not in message.lower():
        return False
    return True


def verify_idempotency(db_path: str, idempotency_key: str) -> bool:
    """Verify whether an action or notification with the given idempotency key exists in the database."""
    import sqlite3
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM actions WHERE idempotency_key = ?", (idempotency_key,))
        count = cursor.fetchone()[0]
        conn.close()
        return count > 0
    except Exception:
        return False


def verify_jira_state(
    issue_data: Dict[str, Any],
    expected_status: Optional[str] = None,
    expected_assignee_id: Optional[str] = None,
) -> bool:
    """Verify that a Jira issue data payload has expected status and assignee."""
    if not isinstance(issue_data, dict):
        return False
    fields = issue_data.get("fields", {})
    if expected_status:
        st = fields.get("status", {}).get("name")
        if not st or st.lower() != expected_status.lower():
            return False
    if expected_assignee_id:
        assignee = fields.get("assignee")
        acc_id = assignee.get("accountId") if assignee else None
        if not acc_id or acc_id != expected_assignee_id:
            return False
    return True

