"""Review and rework analyzer for Phase B v1.1 Historical Intelligence Layer.

Deterministic and explainable classification of task rework and reopens.
Explicit evidence required for blocker/rework classifications; never infers employee fault.
"""

import re
from typing import Any, Dict, List, Optional

from app.core.intelligence.models import (
    ReviewReworkEvent,
    ReviewReworkProfile,
    ReviewReworkReason,
)
from app.core.models.performance import JiraIssueState


class ReviewReworkAnalyzer:
    """Analyzes task reopens and rework causes based on explicit historical evidence."""

    QA_PATTERNS = [
        re.compile(r"\bqa\b", re.IGNORECASE),
        re.compile(r"\bfailed\s+testing\b", re.IGNORECASE),
        re.compile(r"\bqa\s+rejected\b", re.IGNORECASE),
        re.compile(r"\btest\s+failure\b", re.IGNORECASE),
        re.compile(r"\breopened\s+by\s+qa\b", re.IGNORECASE),
        re.compile(r"\bfailed\s+qa\b", re.IGNORECASE),
        re.compile(r"\bverification\s+failed\b", re.IGNORECASE),
    ]

    REQUIREMENT_PATTERNS = [
        re.compile(r"\bscope\s+change\b", re.IGNORECASE),
        re.compile(r"\brequirement\s+(?:change|updated?|revision)\b", re.IGNORECASE),
        re.compile(r"\bspec\s+(?:change|revision|update)\b", re.IGNORECASE),
        re.compile(r"\bac\s+(?:change|update)\b", re.IGNORECASE),
        re.compile(r"\bacceptance\s+criteria\s+updated\b", re.IGNORECASE),
    ]

    CUSTOMER_PATTERNS = [
        re.compile(r"\bclient\s+(?:request(?:ed)?|feedback|revision|change)\b", re.IGNORECASE),
        re.compile(r"\bcustomer\s+(?:request(?:ed)?|feedback|change)\b", re.IGNORECASE),
        re.compile(r"\buser\s+feedback\b", re.IGNORECASE),
        re.compile(r"\bchange\s+request\b", re.IGNORECASE),
        re.compile(r"\bstakeholder\s+feedback\b", re.IGNORECASE),
    ]

    TECHNICAL_PATTERNS = [
        re.compile(r"\bcrash\b", re.IGNORECASE),
        re.compile(r"\bbuild\s+failure\b", re.IGNORECASE),
        re.compile(r"\bci\s+failure\b", re.IGNORECASE),
        re.compile(r"\benvironment\s+issue\b", re.IGNORECASE),
        re.compile(r"\bregression\b", re.IGNORECASE),
        re.compile(r"\bapi\s+(?:down|failure|error)\b", re.IGNORECASE),
        re.compile(r"\bserver\s+error\b", re.IGNORECASE),
    ]

    @classmethod
    def classify_rework_text(cls, text: str) -> ReviewReworkReason:
        """Deterministically classify rework text into a recognized reason."""
        if not text:
            return ReviewReworkReason.UNKNOWN

        for pattern in cls.QA_PATTERNS:
            if pattern.search(text):
                return ReviewReworkReason.QA_REWORK

        for pattern in cls.REQUIREMENT_PATTERNS:
            if pattern.search(text):
                return ReviewReworkReason.REQUIREMENT_CHANGE

        for pattern in cls.CUSTOMER_PATTERNS:
            if pattern.search(text):
                return ReviewReworkReason.CUSTOMER_CHANGE

        for pattern in cls.TECHNICAL_PATTERNS:
            if pattern.search(text):
                return ReviewReworkReason.TECHNICAL_ISSUE

        return ReviewReworkReason.UNKNOWN

    @classmethod
    def analyze_employee_rework(
        cls,
        account_id: str,
        issues: List[JiraIssueState],
        raw_events: Optional[List[Dict[str, Any]]] = None,
    ) -> ReviewReworkProfile:
        """Analyze all rework and reopen occurrences for an employee."""
        rework_events: List[ReviewReworkEvent] = []
        reopened_tasks = set()
        rework_reasons_breakdown: Dict[str, int] = {
            ReviewReworkReason.QA_REWORK.value: 0,
            ReviewReworkReason.REQUIREMENT_CHANGE.value: 0,
            ReviewReworkReason.CUSTOMER_CHANGE.value: 0,
            ReviewReworkReason.TECHNICAL_ISSUE.value: 0,
            ReviewReworkReason.UNKNOWN.value: 0,
        }

        # 1. Process structured raw events if available
        if raw_events:
            for event in raw_events:
                issue_key = event.get("issue_key", "UNKNOWN")
                text = event.get("evidence_text", "")
                reason_str = event.get("reason")
                if reason_str and reason_str in ReviewReworkReason.__members__:
                    reason = ReviewReworkReason[reason_str]
                else:
                    reason = cls.classify_rework_text(text)

                rework_events.append(
                    ReviewReworkEvent(
                        issue_key=issue_key,
                        reason=reason,
                        evidence_text=text or "Reopen event recorded in issue history",
                        timestamp=event.get("timestamp", ""),
                        author_name=event.get("author_name"),
                    )
                )
                reopened_tasks.add(issue_key)
                rework_reasons_breakdown[reason.value] = rework_reasons_breakdown.get(reason.value, 0) + 1

        # 2. Process issues with reopen counts where raw events weren't individually provided
        for issue in issues:
            reopen_count = getattr(issue, "reopen_count", 0) or 0
            if reopen_count > 0:
                reopened_tasks.add(issue.issue_key)
                # If no raw events were processed for this issue, record based on issue metadata
                already_recorded = any(e.issue_key == issue.issue_key for e in rework_events)
                if not already_recorded:
                    # Check issue summary or description for context
                    summary = getattr(issue, "summary", "") or ""
                    reason = cls.classify_rework_text(summary)
                    for i in range(reopen_count):
                        rework_events.append(
                            ReviewReworkEvent(
                                issue_key=issue.issue_key,
                                reason=reason,
                                evidence_text=f"Reopen cycle #{i+1} detected on issue status history",
                                timestamp=getattr(issue, "updated_at", "") or getattr(issue, "created_at", "") or "",
                                author_name=None,
                            )
                        )
                        rework_reasons_breakdown[reason.value] = rework_reasons_breakdown.get(reason.value, 0) + 1

        return ReviewReworkProfile(
            account_id=account_id,
            reopened_tasks_count=len(reopened_tasks),
            total_reopen_events=len(rework_events),
            rework_reasons_breakdown=rework_reasons_breakdown,
            rework_events=rework_events,
        )
