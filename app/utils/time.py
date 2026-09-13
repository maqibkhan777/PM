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


def calculate_business_days(
    start_dt: datetime,
    end_dt: Optional[datetime] = None,
    tz_name: str = "Asia/Karachi",
) -> float:
    """Calculate elapsed business days (Monday-Friday) between two datetimes in Asia/Karachi timezone.

    Weekends (Saturday and Sunday) are strictly excluded.
    Public holidays are NOT yet modeled as no holiday calendar exists in the repository.

    Returns:
        float: Total business days elapsed (e.g. 3.0 = 3 completed business days).
    """
    import zoneinfo

    if end_dt is None:
        end_dt = utc_now()

    tz = zoneinfo.ZoneInfo(tz_name)
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=timezone.utc)

    s = start_dt.astimezone(tz)
    e = end_dt.astimezone(tz)

    if e <= s:
        return 0.0

    total_business_seconds = 0.0
    cur = s
    while cur < e:
        # End of current calendar day in the target timezone
        next_day = datetime(cur.year, cur.month, cur.day, tzinfo=tz) + timedelta(days=1)
        day_end = min(next_day, e)
        # Weekday: 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
        if cur.weekday() < 5:
            total_business_seconds += (day_end - cur).total_seconds()
        cur = day_end

    return total_business_seconds / 86400.0


def is_business_day(dt: datetime, tz_name: str = "Asia/Karachi") -> bool:
    """Check if a datetime falls on a business day (Monday through Friday) in target timezone."""
    import zoneinfo
    tz = zoneinfo.ZoneInfo(tz_name)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz).weekday() < 5

