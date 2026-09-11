"""Conservative Blocker Detection & Accounting for Phase A Performance Data Foundation.

Refinement Rule 3:
Blocker detection must be strictly evidence-based.
Never infer a blocker merely because status has not changed, ticket is old, worklog is low,
or due date was missed.
Explicit blocker evidence requires:
1. Status transition into an explicit blocker status ('Blocked', 'Waiting on dependency', 'On Hold', 'Impediment')
2. Explicit changelog items (e.g. 'Flagged' = 'Impediment')
3. Explicit structured blocker comments ('[BLOCKER]', 'Blocker:', 'Blocked by:')
If evidence is ambiguous, mark as UNKNOWN rather than assuming delay was excused.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from app.core.models.performance import ConfidenceLevel
from app.utils.time import parse_iso_datetime, hours_between, utc_now


BLOCKER_STATUS_KEYWORDS = {
    "blocked",
    "waiting on dependency",
    "waiting for dependency",
    "waiting for client",
    "waiting for customer",
    "on hold",
    "impediment",
    "external dependency",
}

BLOCKER_COMMENT_PREFIXES = (
    "[blocker]",
    "[blocked]",
    "blocker:",
    "blocked by:",
    "impediment:",
    "waiting on external",
)


class BlockerAnalyzer:
    """Analyzes Jira changelogs, status transitions, and comments for explicit external blockers."""

    @classmethod
    def analyze_issue_blockers(
        cls,
        issue_raw: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Analyze issue data for explicit documented blockers and calculate blocked duration."""
        blocker_periods: List[Dict[str, Any]] = []
        evidence_items: List[Dict[str, Any]] = []
        total_blocked_seconds = 0

        fields = issue_raw.get("fields", {})
        changelog = issue_raw.get("changelog", {})
        histories = changelog.get("histories", []) if isinstance(changelog, dict) else []

        # 1. Inspect Status Changelog for Explicit Blocker Status Transitions
        current_blocker_start: Optional[datetime] = None
        current_blocker_status: Optional[str] = None

        for hist in sorted(histories, key=lambda h: h.get("created", "")):
            created_str = hist.get("created")
            hist_dt = parse_iso_datetime(created_str)
            if not hist_dt:
                continue

            for item in hist.get("items", []):
                field_name = (item.get("field") or "").lower()
                from_str = (item.get("fromString") or "").strip()
                to_str = (item.get("toString") or "").strip()

                if field_name == "status":
                    to_lower = to_str.lower()
                    from_lower = from_str.lower()

                    # Entered blocker status
                    if any(kw in to_lower for kw in BLOCKER_STATUS_KEYWORDS):
                        if not current_blocker_start:
                            current_blocker_start = hist_dt
                            current_blocker_status = to_str

                    # Left blocker status
                    elif current_blocker_start and not any(kw in to_lower for kw in BLOCKER_STATUS_KEYWORDS):
                        duration_hours = max(0.0, (hist_dt - current_blocker_start).total_seconds() / 3600.0)
                        duration_secs = int((hist_dt - current_blocker_start).total_seconds())
                        total_blocked_seconds += duration_secs
                        blocker_periods.append({
                            "type": "status_transition",
                            "status": current_blocker_status,
                            "start": current_blocker_start.isoformat(),
                            "end": hist_dt.isoformat(),
                            "duration_hours": round(duration_hours, 2),
                        })
                        evidence_items.append({
                            "evidence_type": "DOCUMENTED_BLOCKER",
                            "explanation": f"Status was '{current_blocker_status}' for {duration_hours:.1f}h ({current_blocker_start.strftime('%Y-%m-%d')} to {hist_dt.strftime('%Y-%m-%d')})",
                            "duration_hours": round(duration_hours, 2),
                            "confidence": ConfidenceLevel.HIGH,
                        })
                        current_blocker_start = None
                        current_blocker_status = None

                # Flagged as impediment
                elif field_name in ("flagged", "impediment"):
                    if "impediment" in to_str.lower() or "blocked" in to_str.lower():
                        if not current_blocker_start:
                            current_blocker_start = hist_dt
                            current_blocker_status = f"Flagged: {to_str}"
                    elif current_blocker_start:
                        duration_hours = max(0.0, (hist_dt - current_blocker_start).total_seconds() / 3600.0)
                        total_blocked_seconds += int((hist_dt - current_blocker_start).total_seconds())
                        evidence_items.append({
                            "evidence_type": "DOCUMENTED_BLOCKER",
                            "explanation": f"Task was flagged as impediment for {duration_hours:.1f}h",
                            "duration_hours": round(duration_hours, 2),
                            "confidence": ConfidenceLevel.HIGH,
                        })
                        current_blocker_start = None

        # Check if issue is currently in a blocker status
        curr_status_name = (fields.get("status", {}).get("name") or "").strip()
        if current_blocker_start:
            now_dt = utc_now()
            duration_hours = max(0.0, (now_dt - current_blocker_start).total_seconds() / 3600.0)
            total_blocked_seconds += int((now_dt - current_blocker_start).total_seconds())
            blocker_periods.append({
                "type": "active_blocker_status",
                "status": current_blocker_status or curr_status_name,
                "start": current_blocker_start.isoformat(),
                "end": None,
                "duration_hours": round(duration_hours, 2),
            })
            evidence_items.append({
                "evidence_type": "ACTIVE_BLOCKER",
                "explanation": f"Task currently in '{current_blocker_status or curr_status_name}' status since {current_blocker_start.strftime('%Y-%m-%d')} ({duration_hours:.1f}h)",
                "duration_hours": round(duration_hours, 2),
                "confidence": ConfidenceLevel.HIGH,
            })

        # 2. Inspect Comments for Explicit Blocker Prefixes
        comment_section = fields.get("comment", {})
        comments_list = comment_section.get("comments", []) if isinstance(comment_section, dict) else []
        for c in comments_list:
            body_val = c.get("body", "")
            # Extract plain text if ADF or str
            body_str = str(body_val).lower() if isinstance(body_val, str) else ""
            if any(body_str.strip().startswith(prefix) for prefix in BLOCKER_COMMENT_PREFIXES):
                c_created = c.get("created")
                evidence_items.append({
                    "evidence_type": "EXPLICIT_BLOCKER_COMMENT",
                    "explanation": f"Comment at {c_created[:10] if c_created else 'N/A'}: explicit blocker stated",
                    "confidence": ConfidenceLevel.MEDIUM,
                })

        blocker_detected = len(blocker_periods) > 0 or len(evidence_items) > 0
        total_blocked_hours = round(total_blocked_seconds / 3600.0, 2)

        return {
            "blocker_detected": blocker_detected,
            "blocker_count": len(blocker_periods),
            "blocked_seconds": total_blocked_seconds,
            "blocked_hours": total_blocked_hours,
            "blocker_periods": blocker_periods,
            "evidence_items": evidence_items,
            "confidence": ConfidenceLevel.HIGH if blocker_periods else (ConfidenceLevel.MEDIUM if evidence_items else ConfidenceLevel.LOW),
        }
