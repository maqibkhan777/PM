"""Deterministic synthetic evaluation scenarios for PM attention analysis.

Contains 8 representative test cases:
1. Healthy queue (zero attention signals)
2. Overdue work (single overdue candidate)
3. Stale work (single inactive candidate)
4. Reopened work (single reopened candidate)
5. Unassigned work (active ticket without owner)
6. Mixed attention (multiple simultaneous attention categories)
7. Empty / low-signal context (no items at all)
8. Bounded context (context sizing stress-test within token ceiling)
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from app.services.ai.models import AIContext, MetricSummaryContext


@dataclass
class ScenarioFixture:
    scenario_id: str
    name: str
    description: str
    context: AIContext
    expected_issue_keys: List[str] = field(default_factory=list)
    forbidden_inventions: List[str] = field(default_factory=list)
    expected_categories: List[str] = field(default_factory=list)
    should_flag_items: bool = True


def _make_context(
    context_id: str,
    objective: str,
    stale_items: Optional[List[Dict[str, Any]]] = None,
    overdue_items: Optional[List[Dict[str, Any]]] = None,
    reopened_items: Optional[List[Dict[str, Any]]] = None,
    unassigned_items: Optional[List[Dict[str, Any]]] = None,
    team_name: str = "Mursaleen Cluster",
) -> AIContext:
    stale = stale_items or []
    overdue = overdue_items or []
    reopened = reopened_items or []
    unassigned = unassigned_items or []

    recent_activity: List[str] = []
    for it in stale:
        recent_activity.append(f"STALE: {it.get('key')} | Status: {it.get('status')} | Inactivity: {it.get('inactivity_duration')}")
    for it in overdue:
        recent_activity.append(f"OVERDUE: {it.get('key')} | Due: {it.get('due_date')} | Status: {it.get('status')}")
    for it in reopened:
        recent_activity.append(f"REOPENED: {it.get('key')} | Status: {it.get('status')}")
    for it in unassigned:
        recent_activity.append(f"UNASSIGNED: {it.get('key')} | Status: {it.get('status')}")

    total_count = len(stale) + len(overdue) + len(reopened) + len(unassigned)
    metrics = [
        MetricSummaryContext(metric_name="stale_count", value=len(stale)),
        MetricSummaryContext(metric_name="overdue_count", value=len(overdue)),
        MetricSummaryContext(metric_name="reopened_count", value=len(reopened)),
        MetricSummaryContext(metric_name="unassigned_count", value=len(unassigned)),
        MetricSummaryContext(metric_name="total_attention_count", value=total_count),
    ]

    return AIContext(
        context_id=context_id,
        timestamp="2026-09-20T12:00:00Z",
        objective=objective,
        team_name=team_name,
        recent_activity_summary=recent_activity,
        metrics=metrics,
        applicable_policies=["OverdueTaskPolicy", "StaleTaskPolicy", "ReopenedTaskPolicy", "UnassignedTaskPolicy"],
        metadata={
            "stale_items": stale,
            "overdue_items": overdue,
            "reopened_items": reopened,
            "unassigned_items": unassigned,
        },
    )


# 1. Healthy Queue Scenario
SCENARIO_1_HEALTHY_QUEUE = ScenarioFixture(
    scenario_id="SCEN-01-HEALTHY",
    name="Healthy Queue",
    description="All tasks actively progressing with valid assignees, future due dates, and zero stale work.",
    context=_make_context(
        context_id="ctx-scen-01-healthy",
        objective="Assess PM attention requirements across active cluster work.",
    ),
    expected_issue_keys=[],
    forbidden_inventions=["OVERDUE-999", "STALE-999", "INVENTED-123"],
    expected_categories=[],
    should_flag_items=False,
)

# 2. Overdue Work Scenario
SCENARIO_2_OVERDUE_WORK = ScenarioFixture(
    scenario_id="SCEN-02-OVERDUE",
    name="Overdue Task",
    description="Single high-priority task past its due date without completion.",
    context=_make_context(
        context_id="ctx-scen-02-overdue",
        objective="Identify overdue tasks requiring timeline renegotiation or expedited delivery.",
        overdue_items=[
            {
                "key": "PAY-101",
                "summary": "Stripe webhook retry exponential backoff",
                "status": "In Progress",
                "assignee": "Ahsan Amin",
                "priority": "High",
                "due_date": "2026-09-15",
                "updated_at": "2026-09-14T09:00:00Z",
            }
        ],
    ),
    expected_issue_keys=["PAY-101"],
    forbidden_inventions=["PAY-999", "MISSING-KEY"],
    expected_categories=["overdue"],
    should_flag_items=True,
)

# 3. Stale Work Scenario
SCENARIO_3_STALE_WORK = ScenarioFixture(
    scenario_id="SCEN-03-STALE",
    name="Stale Work",
    description="Active task inactive for 6 days exceeding the 24-hour inactivity threshold.",
    context=_make_context(
        context_id="ctx-scen-03-stale",
        objective="Identify stale or blocked tasks requiring PM follow-up.",
        stale_items=[
            {
                "key": "AUTH-202",
                "summary": "OAuth2 PKCE flow for mobile app",
                "status": "In Progress",
                "assignee": "Muhammad Mursaleen",
                "priority": "Medium",
                "due_date": "2026-09-30",
                "updated_at": "2026-09-14T11:00:00Z",
                "inactivity_duration": "6d",
            }
        ],
    ),
    expected_issue_keys=["AUTH-202"],
    forbidden_inventions=["AUTH-999"],
    expected_categories=["stale"],
    should_flag_items=True,
)

# 4. Reopened Work Scenario
SCENARIO_4_REOPENED_WORK = ScenarioFixture(
    scenario_id="SCEN-04-REOPENED",
    name="Reopened Work",
    description="Bug reopened after customer verification failed.",
    context=_make_context(
        context_id="ctx-scen-04-reopened",
        objective="Identify regression risks from recently reopened work.",
        reopened_items=[
            {
                "key": "REP-303",
                "summary": "Report PDF export truncates currency symbols",
                "status": "Reopened",
                "assignee": "Aqib Khan",
                "priority": "High",
                "updated_at": "2026-09-19T14:30:00Z",
            }
        ],
    ),
    expected_issue_keys=["REP-303"],
    forbidden_inventions=["REP-999"],
    expected_categories=["reopened"],
    should_flag_items=True,
)

# 5. Unassigned Work Scenario
SCENARIO_5_UNASSIGNED_WORK = ScenarioFixture(
    scenario_id="SCEN-05-UNASSIGNED",
    name="Unassigned Work",
    description="High priority triage task created without a designated owner.",
    context=_make_context(
        context_id="ctx-scen-05-unassigned",
        objective="Detect unowned tasks in active sprint scope.",
        unassigned_items=[
            {
                "key": "OPS-404",
                "summary": "Database vacuum job alerting on disk space",
                "status": "To Do",
                "assignee": None,
                "priority": "Highest",
                "updated_at": "2026-09-20T08:00:00Z",
            }
        ],
    ),
    expected_issue_keys=["OPS-404"],
    forbidden_inventions=["OPS-999"],
    expected_categories=["unassigned"],
    should_flag_items=True,
)

# 6. Mixed Attention Scenario
SCENARIO_6_MIXED_ATTENTION = ScenarioFixture(
    scenario_id="SCEN-06-MIXED",
    name="Mixed Attention Signals",
    description="Real-world mixture of stale, overdue, and unassigned tasks across cluster.",
    context=_make_context(
        context_id="ctx-scen-06-mixed",
        objective="Consolidate cluster-wide attention items and prioritize PM interventions.",
        stale_items=[
            {
                "key": "MIX-501",
                "summary": "Refactor legacy reporting worker",
                "status": "In Progress",
                "assignee": "Muhammad Mursaleen",
                "priority": "Low",
                "due_date": "2026-10-01",
                "inactivity_duration": "4d",
            }
        ],
        overdue_items=[
            {
                "key": "MIX-502",
                "summary": "Security audit dependency vulnerability patch",
                "status": "Code Review",
                "assignee": "Ahsan Amin",
                "priority": "Highest",
                "due_date": "2026-09-18",
            }
        ],
        unassigned_items=[
            {
                "key": "MIX-503",
                "summary": "Mattermost webhook SSL certificate rotation",
                "status": "To Do",
                "assignee": None,
                "priority": "Medium",
            }
        ],
    ),
    expected_issue_keys=["MIX-501", "MIX-502", "MIX-503"],
    forbidden_inventions=["MIX-999"],
    expected_categories=["stale", "overdue", "unassigned"],
    should_flag_items=True,
)

# 7. Empty / Low-Signal Scenario
SCENARIO_7_EMPTY_SIGNAL = ScenarioFixture(
    scenario_id="SCEN-07-EMPTY",
    name="Empty Signal Context",
    description="Context containing no metadata or active candidate signals whatsoever.",
    context=AIContext(
        context_id="ctx-scen-07-empty",
        timestamp="2026-09-20T12:00:00Z",
        objective="Assess PM attention items",
        team_name="Mursaleen Cluster",
        recent_activity_summary=[],
        metrics=[],
        applicable_policies=[],
        metadata={},
    ),
    expected_issue_keys=[],
    forbidden_inventions=["EMPTY-001"],
    expected_categories=[],
    should_flag_items=False,
)

# 8. Bounded Context Stress Scenario
SCENARIO_8_BOUNDED_CONTEXT = ScenarioFixture(
    scenario_id="SCEN-08-BOUNDED",
    name="Bounded Context Stress",
    description="Context containing 6 items across all categories with descriptive metadata to verify token boundedness.",
    context=_make_context(
        context_id="ctx-scen-08-bounded",
        objective="Comprehensive evaluation of cluster workload and risk factors.",
        stale_items=[
            {"key": "BND-601", "summary": "Task 1 stale", "status": "In Progress", "assignee": "Dev A", "inactivity_duration": "3d"},
            {"key": "BND-602", "summary": "Task 2 stale", "status": "In Progress", "assignee": "Dev B", "inactivity_duration": "5d"},
        ],
        overdue_items=[
            {"key": "BND-603", "summary": "Task 3 overdue", "status": "In Progress", "assignee": "Dev C", "due_date": "2026-09-17"},
            {"key": "BND-604", "summary": "Task 4 overdue", "status": "Review", "assignee": "Dev D", "due_date": "2026-09-16"},
        ],
        reopened_items=[
            {"key": "BND-605", "summary": "Task 5 reopened", "status": "Reopened", "assignee": "Dev E"},
        ],
        unassigned_items=[
            {"key": "BND-606", "summary": "Task 6 unassigned", "status": "To Do", "assignee": None},
        ],
    ),
    expected_issue_keys=["BND-601", "BND-602", "BND-603", "BND-604", "BND-605", "BND-606"],
    forbidden_inventions=["BND-999"],
    expected_categories=["stale", "overdue", "reopened", "unassigned"],
    should_flag_items=True,
)

EVALUATION_SCENARIOS: List[ScenarioFixture] = [
    SCENARIO_1_HEALTHY_QUEUE,
    SCENARIO_2_OVERDUE_WORK,
    SCENARIO_3_STALE_WORK,
    SCENARIO_4_REOPENED_WORK,
    SCENARIO_5_UNASSIGNED_WORK,
    SCENARIO_6_MIXED_ATTENTION,
    SCENARIO_7_EMPTY_SIGNAL,
    SCENARIO_8_BOUNDED_CONTEXT,
]
