"""Time and datetime utility helpers."""

from datetime import datetime, timezone, timedelta
from typing import Optional


def utc_now() -> datetime:
    """Return current timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """Return current UTC datetime formatted as ISO 8601 string."""
    return utc_now().isoformat()


def parse_iso_datetime(iso_str: Optional[str]) -> Optional[datetime]:
    """Parse ISO 8601 string into timezone-aware datetime."""
    if not iso_str:
        return None
    try:
        # Normalize trailing Z to +00:00
        normalized = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def format_iso(dt: Optional[datetime]) -> Optional[str]:
    """Format datetime to standard ISO string."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def hours_between(start_dt: datetime, end_dt: Optional[datetime] = None) -> float:
    """Calculate elapsed hours between two datetimes."""
    if end_dt is None:
        end_dt = utc_now()
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=timezone.utc)
    diff = end_dt - start_dt
    return diff.total_seconds() / 3600.0
