"""QA Domain, Execution, and Integrity Models for PM Operations Agent."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class TestTier(str, Enum):
    """Execution tier of the QA test case."""
    __test__ = False
    UNIT = "UNIT"
    INTEGRATION = "INTEGRATION"
    DRY_RUN_E2E = "DRY_RUN_E2E"
    LIVE_E2E = "LIVE_E2E"


class QAStatus(str, Enum):
    """Result status of a QA test."""
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_EXECUTED = "NOT_EXECUTED"
    INVALID_LIVE_TEST = "INVALID_LIVE_TEST"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"
    DEPRECATED = "DEPRECATED"


class QAPriority(str, Enum):
    """Priority level of the test scenario."""
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class QAScenario(BaseModel):
    """Specification of a single QA test scenario."""
    test_id: str
    category: str
    title: str
    priority: QAPriority = QAPriority.P1
    test_tier: TestTier = TestTier.LIVE_E2E
    preconditions: str
    test_steps: List[str]
    expected_result: str
    jira_issue: str = "TREN-378"
    is_deprecated: bool = False
    deprecation_reason: Optional[str] = None


class QATestResult(BaseModel):
    """Structured result of a single QA test execution conforming to the standard QA Result Model and Integrity Gate."""
    test_id: str
    run_id: str
    test_tier: TestTier
    category: str
    title: str
    status: QAStatus
    priority: QAPriority = QAPriority.P1
    environment: str = "LIVE_JIRA"
    jira_issue: Optional[str] = "TREN-378"
    jira_assignee: Optional[str] = None
    
    # Explicit component execution characteristics (Anti-Faking Metadata)
    execution_mode: Dict[str, str] = Field(default_factory=lambda: {
        "jira": "NOT_USED",
        "discord": "NOT_USED",
        "mattermost": "NOT_USED",
        "database": "REAL",
        "webhook": "NOT_USED",
        "polling": "NOT_USED",
        "scheduler": "NOT_USED",
        "action_engine": "NOT_USED",
    })
    external_operations: List[str] = Field(default_factory=list)
    
    # Detailed lifecycle stages
    pre_state: Optional[Dict[str, Any]] = None
    mutation: Optional[Dict[str, Any]] = None
    observed_external_state: Optional[Dict[str, Any]] = None
    event_id: Optional[str] = None
    rule_result: Optional[Dict[str, Any]] = None
    action_id: Optional[str] = None
    action_result: Optional[Dict[str, Any]] = None
    idempotency_key: Optional[str] = None
    idempotency_result: Optional[Dict[str, Any]] = None
    discord_message_id: Optional[str] = None
    mattermost_message_id: Optional[str] = None
    notification_result: Optional[Dict[str, Any]] = None
    audit_result: Optional[Dict[str, Any]] = None
    
    expected: str
    actual: str
    failure_reason: Optional[str] = None
    evidence: Optional[str] = None
    evidence_file: Optional[str] = None
    defect_ref: Optional[str] = None
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_ms: float = 0.0
    payload_metadata: Optional[Dict[str, Any]] = None

    def is_genuinely_live(self) -> bool:
        """Check if test genuinely used real external Jira, Discord, or Mattermost APIs."""
        return self.test_tier == TestTier.LIVE_E2E and any(
            self.execution_mode.get(k) == "REAL" for k in ["jira", "discord", "mattermost"]
        )

    def to_qa_dict(self) -> Dict[str, Any]:
        """Convert to the standard output format required by Master QA Matrix and Evidence."""
        return {
            "test_id": self.test_id,
            "run_id": self.run_id,
            "tier": self.test_tier.value,
            "category": self.category,
            "title": self.title,
            "status": self.status.value,
            "jira_issue": self.jira_issue,
            "jira_assignee": self.jira_assignee,
            "execution_mode": self.execution_mode,
            "external_operations": self.external_operations,
            "pre_state": self.pre_state,
            "mutation": self.mutation,
            "observed_external_state": self.observed_external_state,
            "event_id": self.event_id,
            "action_id": self.action_id,
            "idempotency_key": self.idempotency_key,
            "discord_message_id": self.discord_message_id,
            "mattermost_message_id": self.mattermost_message_id,
            "rule_result": self.rule_result,
            "action_result": self.action_result,
            "notification_result": self.notification_result,
            "audit_result": self.audit_result,
            "idempotency_result": self.idempotency_result,
            "expected": self.expected,
            "actual": self.actual,
            "failure_reason": self.failure_reason,
            "evidence": self.evidence or self.evidence_file,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_ms": self.duration_ms,
            "timestamp": self.timestamp,
        }


class QARunSummary(BaseModel):
    """Summary of a QA suite execution run with live integrity auditing."""
    run_id: str
    tier: TestTier
    target_issue: str = "TREN-378"
    started_at: str
    completed_at: str
    total_tests: int = 0
    passed: int = 0
    failed: int = 0
    not_executed: int = 0
    invalid_live_tests: int = 0
    blocked: int = 0
    skipped: int = 0
    deprecated: int = 0
    
    genuinely_live_count: int = 0
    simulated_count: int = 0
    live_integrity_valid: int = 0
    live_integrity_invalid: int = 0
    
    results: List[QATestResult] = Field(default_factory=list)

    @property
    def pass_rate_percent(self) -> float:
        """Calculate pass percentage strictly on active executed tests."""
        active = self.total_tests - (self.skipped + self.deprecated + self.not_executed)
        if active <= 0:
            return 100.0 if self.passed > 0 else 0.0
        return round((self.passed / active) * 100.0, 2)
