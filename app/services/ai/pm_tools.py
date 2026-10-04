from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.agent_core.agent_models import ToolSpec
from app.agent_core.tooling import ToolRegistry
from app.database.connection import DatabaseManager
from app.database.repositories import JiraIssueStateRepository, JiraIssueLinkRepository, JiraWorklogRepository, JiraIssueStateRepository

from app.core.planning.queue_composer import ResourceQueueComposer
from app.core.planning.forecaster import TeamScheduleForecaster
from app.core.planning.dag import DependencyGraph
from app.core.planning.context import PlanningContextBuilder


def build_tool_registry(manager: Optional[DatabaseManager] = None) -> ToolRegistry:
    """
    Minimal tool registry for Phase 1.

    Tools are read-only wrappers around existing deterministic repositories/engines.
    Write-capable tools are intentionally NOT included in Phase 1.
    """
    mgr = manager
    issue_repo = JiraIssueStateRepository(mgr)
    link_repo = JiraIssueLinkRepository(mgr)
    worklog_repo = JiraWorklogRepository(mgr)

    registry = ToolRegistry()

    async def get_issue(args: Dict[str, Any]) -> Dict[str, Any]:
        key = str(args.get("issue_key") or "").upper().strip()
        if not key:
            return {"status": "ERROR", "tool": "get_issue", "reason": "issue_key_required"}
        issue = issue_repo.get_by_key(key)
        if not issue:
            return {"status": "NOT_AVAILABLE", "tool": "get_issue", "reason": "No projected issue for key."}
        # Return only bounded metadata; no raw DB blob.
        return {
            "status": "AVAILABLE",
            "tool": "get_issue",
            "value": {
                "key": issue.get("jira_issue_key"),
                "summary": issue.get("summary"),
                "status": issue.get("status"),
                "priority": issue.get("priority"),
                "assignee": issue.get("assignee"),
                "reporter": issue.get("creator_id"),
                "type": issue.get("issue_type"),
                "project": issue.get("project_key"),
                "sprint": None,
                "due_date": issue.get("due_date"),
                "labels": issue.get("labels"),
                "links": [],  # keep lightweight in Phase 2
                "updated_at": issue.get("updated_at"),
                "last_activity_at": issue.get("last_activity_at"),
                "workflow_metadata": {"team_group": issue.get("team_group")},
            },
        }

    registry.register(
        "get_issue",
        ToolSpec(name="get_issue", description="Get projected Jira issue state by key.", parameters_schema={"issue_key": "string"}),
        get_issue,
    )

    async def search_issues(args: Dict[str, Any]) -> Dict[str, Any]:
        q = str(args.get("query") or "").strip().lower()
        if not q:
            return {"status": "ERROR", "tool": "search_issues", "reason": "query_required"}
        # Projection-only search (token-efficient): scan recent local issues and filter.
        all_issues = issue_repo.list_all(limit=200)
        matches: List[Dict[str, Any]] = []
        for it in all_issues:
            key = str(it.get("jira_issue_key") or "").upper()
            summary = str(it.get("summary") or "").lower()
            if q in key.lower() or q in summary:
                matches.append(it)
        if not matches:
            return {"status": "EMPTY", "tool": "search_issues", "value": []}
        # Provide bounded match objects only.
        bounded = []
        for m in matches:
            bounded.append(
                {
                    "key": m.get("jira_issue_key"),
                    "summary": m.get("summary"),
                    "status": m.get("status"),
                    "priority": m.get("priority"),
                    "assignee": m.get("assignee"),
                    "project": m.get("project_key"),
                    "due_date": m.get("due_date"),
                }
            )
        return {"status": "AVAILABLE", "tool": "search_issues", "value": bounded}

    registry.register(
        "search_issues",
        ToolSpec(name="search_issues", description="Search projected issues by key/title substring.", parameters_schema={"query": "string"}),
        search_issues,
    )

    async def get_active_sprints(args: Dict[str, Any]) -> Dict[str, Any]:
        # Projection-based approximation: parse sprint customfield from stored raw_reference where available.
        team_group = args.get("team_group")
        all_issues = issue_repo.list_all(limit=500)
        sprints = set()
        for it in all_issues:
            if team_group and it.get("team_group") != team_group:
                continue
            raw = it.get("raw_reference")
            if isinstance(raw, str):
                # raw_reference is stored as JSON string in schema; keep conservative.
                continue
            if isinstance(raw, dict):
                sprint_val = raw.get("fields", {}).get("sprint") or raw.get("sprints")
                if sprint_val:
                    sprints.add(str(sprint_val))
        return {"active_sprints": sorted(sprints)}

    registry.register(
        "get_active_sprints",
        ToolSpec(name="get_active_sprints", description="Return active sprint candidates from projections.", parameters_schema={"team_group": "string"}),
        get_active_sprints,
    )

    async def get_sprint_issues(args: Dict[str, Any]) -> Dict[str, Any]:
        sprint = str(args.get("sprint_name") or "").strip()
        if not sprint:
            return {"issues": []}
        all_issues = issue_repo.list_all(limit=500)
        # Minimal projection filter; full Jira sprint membership is a later phase.
        issues = [it for it in all_issues if sprint in str(it.get("raw_reference") or "")]
        return {"issues": issues}

    registry.register(
        "get_sprint_issues",
        ToolSpec(name="get_sprint_issues", description="Issues belonging to a sprint (projection approximation).", parameters_schema={"sprint_name": "string"}),
        get_sprint_issues,
    )

    async def find_user(args: Dict[str, Any]) -> Dict[str, Any]:
        from app.database.repositories import EmployeeRoleRepository

        repo = EmployeeRoleRepository(mgr)
        query = str(args.get("query") or "").strip()
        if not query:
            return {"status": "ERROR", "tool": "find_user", "reason": "query_required"}

        # Deterministic mapping by display name substring (projection only).
        matches = []
        for rec in repo.list_assignments():
            dn = str(rec.get("display_name") or "").strip()
            if query.lower() in dn.lower():
                matches.append(
                    {
                        "account_id": rec.get("account_id"),
                        "display_name": dn,
                        "role": rec.get("role"),
                        "designation": rec.get("designation"),
                        "role_category": rec.get("role_category"),
                    }
                )

        if not matches:
            return {"status": "NOT_AVAILABLE", "tool": "find_user", "reason": "No projection match."}
        if len(matches) == 1:
            return {"status": "AVAILABLE", "tool": "find_user", "value": matches[0]}
        return {"status": "INSUFFICIENT_DATA", "tool": "find_user", "reason": "Multiple matches.", "candidates": matches}

    registry.register(
        "find_user",
        ToolSpec(name="find_user", description="Find users (Phase 1 placeholder; must be backed by deterministic mapping later).", parameters_schema={"query": "string"}),
        find_user,
    )

    async def get_linked_issues(args: Dict[str, Any]) -> Dict[str, Any]:
        issue_key = str(args.get("issue_key") or "").upper().strip()
        if not issue_key:
            return {"links": []}
        links = link_repo.list_links_for_source(issue_key, active_only=True)
        return {"links": links}

    registry.register(
        "get_linked_issues",
        ToolSpec(name="get_linked_issues", description="Get linked issues from projected link table.", parameters_schema={"issue_key": "string"}),
        get_linked_issues,
    )

    async def get_comments(args: Dict[str, Any]) -> Dict[str, Any]:
        # Comments are not stored in projections in Phase 1; return empty to keep read-only correctness.
        return {"comments": []}

    registry.register(
        "get_comments",
        ToolSpec(name="get_comments", description="Get issue comments (Phase 1 placeholder).", parameters_schema={"issue_key": "string"}),
        get_comments,
    )

    async def capacity_workload_summary(args: Dict[str, Any]) -> Dict[str, Any]:
        # Use existing deterministic planning queue composer snapshot.
        account_id = args.get("account_id")
        display_name = args.get("display_name")
        team_group = args.get("team_group")
        project_key = args.get("project_key")
        composer = ResourceQueueComposer(manager=mgr)
        snap = composer.compose_snapshot(
            account_id=str(account_id) if account_id else None,
            display_name=str(display_name) if display_name else None,
            team_group=team_group,
            project_key=project_key,
        )
        return {"snapshot": snap.model_dump() if hasattr(snap, "model_dump") else snap}

    registry.register(
        "capacity_workload_summary",
        ToolSpec(name="capacity_workload_summary", description="Compute capacity/workload snapshot for one resource.", parameters_schema={}),
        capacity_workload_summary,
    )

    async def forecast_schedule(args: Dict[str, Any]) -> Dict[str, Any]:
        # Minimal wrapper: expects caller to provide serialized snapshots is a later phase.
        return {"forecast": None}

    registry.register(
        "forecast_schedule",
        ToolSpec(name="forecast_schedule", description="Forecast schedule for planning horizon (stub in Phase 1).", parameters_schema={}),
        forecast_schedule,
    )

    async def analyze_dependencies(args: Dict[str, Any]) -> Dict[str, Any]:
        # Dependency analysis from link table is already available via planning graph in later phase.
        return {"dependency_graph": None}

    registry.register(
        "analyze_dependencies",
        ToolSpec(name="analyze_dependencies", description="Analyze dependency DAG (stub in Phase 1).", parameters_schema={}),
        analyze_dependencies,
    )

    return registry

