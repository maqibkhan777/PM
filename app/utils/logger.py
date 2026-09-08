"""Structured logging with automatic sensitive data redaction."""

import logging
import re
import sys
from typing import Any, Dict, Union

# Patterns for sensitive keys and tokens
SENSITIVE_KEY_PATTERNS = [
    re.compile(r"api[_-]?token", re.IGNORECASE),
    re.compile(r"api[_-]?key", re.IGNORECASE),
    re.compile(r"password", re.IGNORECASE),
    re.compile(r"secret", re.IGNORECASE),
    re.compile(r"authorization", re.IGNORECASE),
    re.compile(r"bearer\s+[a-zA-Z0-9_\-\.]+", re.IGNORECASE),
    re.compile(r"webhook[_-]?url", re.IGNORECASE),
]

SENSITIVE_VALUE_PATTERNS = [
    re.compile(r"Bearer\s+([A-Za-z0-9_\-\.]{8,})", re.IGNORECASE),
    re.compile(r"(https://discord\.com/api/webhooks/[0-9]+/)([A-Za-z0-9_\-]+)"),
    re.compile(r"(token=)([A-Za-z0-9_\-]+)"),
]

REDACTED_STR = "******"


def redact_text(text: str) -> str:
    """Redact sensitive patterns from a raw log string."""
    if not isinstance(text, str):
        return text
    
    # Redact Bearer tokens
    redacted = re.sub(r"(Bearer\s+)[A-Za-z0-9_\-\.]+", r"\1" + REDACTED_STR, text, flags=re.IGNORECASE)
    # Redact Discord Webhook secrets
    redacted = re.sub(r"(https://discord\.com/api/webhooks/\d+/)[A-Za-z0-9_\-]+", r"\1" + REDACTED_STR, redacted)
    # Redact token= or password=
    redacted = re.sub(r"((?:token|password|secret|key)=)[^&\s]+", r"\1" + REDACTED_STR, redacted, flags=re.IGNORECASE)
    return redacted


def sanitize_dict(data: Union[Dict[str, Any], Any]) -> Any:
    """Recursively sanitize a dictionary or list, masking sensitive fields."""
    if isinstance(data, dict):
        sanitized = {}
        for k, v in data.items():
            k_str = str(k)
            if any(pattern.search(k_str) for pattern in SENSITIVE_KEY_PATTERNS):
                sanitized[k] = REDACTED_STR
            else:
                sanitized[k] = sanitize_dict(v)
        return sanitized
    elif isinstance(data, list):
        return [sanitize_dict(item) for item in data]
    elif isinstance(data, str):
        return redact_text(data)
    return data


class RedactingFormatter(logging.Formatter):
    """Custom logging formatter that scrubs sensitive credentials from output and handles encoding."""

    def format(self, record: logging.LogRecord) -> str:
        original = super().format(record)
        redacted = redact_text(original)
        return redacted


def setup_logger(name: str = "pm_ops", level: int = logging.INFO) -> logging.Logger:
    """Set up and configure application logger with redacting formatter and safe encoding."""
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Reconfigure sys.stdout to UTF-8 on Windows if supported
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    if not logger.handlers:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_formatter = RedactingFormatter(
            fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)

    return logger


# Global default logger
logger = setup_logger()
