from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app.agent_core.agent_models import ToolSpec
from app.agent_core.tooling import ToolRegistry
from app.config.settings import settings
from app.database.connection import DatabaseManager
from app.database.repositories import (
    ArtifactRepository,
    EmployeeRoleRepository,
    JiraIssueLinkRepository,
    JiraIssueStateRepository,
    JiraWorklogRepository,
)

from app.core.planning.queue_composer import ResourceQueueComposer
from app.core.planning.forecaster import TeamScheduleForecaster
from app.core.planning.dag import DependencyGraph
from app.core.planning.context import PlanningContextBuilder
from app.connectors.jira.client import JiraClient
from app.core.models.planning import ArtifactRecord, ArtifactRelationshipRecord, ArtifactStatus, ArtifactType, ArtifactProvenance


def build_tool_registry(manager: Optional[DatabaseManager] = None, jira_client: Optional[JiraClient] = None) -> ToolRegistry:
    """
    Minimal tool registry for Phase 1.

    Tools are read-only wrappers around existing deterministic repositories/engines.
    Write-capable tools are intentionally NOT included in Phase 1.
    """
    mgr = manager
    issue_repo = JiraIssueStateRepository(mgr)
    link_repo = JiraIssueLinkRepository(mgr)
    worklog_repo = JiraWorklogRepository(mgr)
    role_repo = EmployeeRoleRepository(mgr)
    client = jira_client or (JiraClient() if settings.is_jira_configured() else None)

    registry = ToolRegistry()

    def _issue_value(issue: Dict[str, Any]) -> Dict[str, Any]:
        key = issue.get("jira_issue_key") or issue.get("key")
        related_links = []
        if key:
            for l in (link_repo.list_links_for_source(str(key), active_only=True) + link_repo.list_links_for_target(str(key), active_only=True)):
                related_links.append({
                    "source_issue_key": l.get("source_issue_key"),
                    "target_issue_key": l.get("target_issue_key"),
                    "link_type_name": l.get("link_type_name"),
                    "classification": l.get("classification"),
                    "is_active": bool(l.get("is_active", 1)),
                })
        return {
            "key": key,
            "summary": issue.get("summary"),
            "status": issue.get("status"),
            "priority": issue.get("priority"),
            "assignee": issue.get("assignee"),
            "reporter": issue.get("reporter") or issue.get("creator_id"),
            "type": issue.get("issue_type"),
            "project": issue.get("project_key"),
            "sprint": issue.get("sprint"),
            "due_date": issue.get("due_date"),
            "labels": issue.get("labels") or [],
            "links": related_links or issue.get("links") or [],
            "updated_at": issue.get("updated_at"),
            "last_activity_at": issue.get("last_activity_at"),
            "workflow_metadata": {"team_group": issue.get("team_group")},
        }

    def _as_available(tool: str, value: Any, derivation: Optional[List[str]] = None) -> Dict[str, Any]:
        payload = {"status": "AVAILABLE", "tool": tool, "value": value}
        if derivation:
            payload["derivation"] = derivation
        return payload

    def _as_empty(tool: str, reason: str) -> Dict[str, Any]:
        return {"status": "EMPTY", "tool": tool, "reason": reason}

    def _as_not_available(tool: str, reason: str) -> Dict[str, Any]:
        return {"status": "NOT_AVAILABLE", "tool": tool, "reason": reason}

    def _as_ambiguous(tool: str, candidates: List[Dict[str, Any]], question: Optional[str] = None) -> Dict[str, Any]:
        payload = {"status": "AMBIGUOUS", "tool": tool, "candidates": candidates}
        if question:
            payload["question"] = question
        return payload

    def _status_category(issue: Dict[str, Any]) -> Dict[str, str]:
        raw_reference = issue.get("raw_reference")
        if isinstance(raw_reference, str):
            try:
                raw_reference = json.loads(raw_reference)
            except Exception:
                raw_reference = None
        fields = raw_reference.get("fields", {}) if isinstance(raw_reference, dict) else {}
        status_obj = fields.get("status", {}) if isinstance(fields, dict) else {}
        status_name = str(
            (status_obj.get("name") if isinstance(status_obj, dict) else None)
            or issue.get("status")
            or "Unknown"
        ).strip()
        status_category = str(
            (status_obj.get("statusCategory") or {}).get("key") if isinstance(status_obj, dict) and isinstance(status_obj.get("statusCategory"), dict) else ""
        ).strip().lower()
        cancelled_terms = ("cancelled", "canceled", "won't do", "wont do", "won't-do", "wont-do", "abandoned")
        name_lower = status_name.lower()

        if status_category:
            if status_category == "done":
                if any(term in name_lower for term in cancelled_terms):
                    return {"bucket": "cancelled", "method": "statusCategory=done + cancelled-name"}
                return {"bucket": "Done", "method": "statusCategory=done"}
            if status_category == "new":
                return {"bucket": "To Do", "method": "statusCategory=new"}
            if status_category == "indeterminate":
                return {"bucket": "In Progress", "method": "statusCategory=indeterminate"}
            return {"bucket": "unknown", "method": f"statusCategory={status_category or 'missing'}"}

        if any(term in name_lower for term in cancelled_terms):
            return {"bucket": "cancelled", "method": "fallback-name=cancelled"}
        if any(token in name_lower for token in ("done", "closed", "resolved", "complete")):
            return {"bucket": "Done", "method": "fallback-name=done"}
        if any(token in name_lower for token in ("progress", "review", "qa", "testing", "blocked", "started", "development")):
            return {"bucket": "In Progress", "method": "fallback-name=in-progress"}
        if any(token in name_lower for token in ("todo", "to do", "open", "backlog", "new", "ready")):
            return {"bucket": "To Do", "method": "fallback-name=to-do"}
        return {"bucket": "unknown", "method": "fallback-name=unknown"}

    def _summarize_issues(issues: List[Dict[str, Any]]) -> Dict[str, Any]:
        status_counts = {"Done": 0, "In Progress": 0, "To Do": 0, "cancelled": 0, "unknown": 0}
        assignee_counts: Dict[str, int] = {}
        derivation: List[str] = []
        for issue in issues:
            key = str(issue.get("key") or issue.get("jira_issue_key") or "unknown").strip()
            status_meta = _status_category(issue)
            category = status_meta["bucket"]
            status_counts[category] = status_counts.get(category, 0) + 1
            assignee = str(issue.get("assignee") or "Unassigned").strip() or "Unassigned"
            assignee_counts[assignee] = assignee_counts.get(assignee, 0) + 1
            derivation.append(f"{key}: {status_meta['method']} -> {category}; assignee='{assignee}'")
        return {
            "total": len(issues),
            "status_counts": status_counts,
            "assignee_counts": assignee_counts,
            "derivation": derivation,
        }

    async def _paged_search_issues(
        jql: str,
        fields: List[str],
        page_size: int = 100,
        cap: int = 1000,
    ) -> Dict[str, Any]:
        collected: List[Dict[str, Any]] = []
        next_page_token: Optional[str] = None
        reported_total: Optional[int] = None
        truncated = False

        while True:
            raw = await client.search_issues(
                jql=jql,
                next_page_token=next_page_token,
                max_results=page_size,
                expand=None,
                fields=fields,
            )
            page_issues = raw.get("issues", []) if isinstance(raw, dict) else []
            if not isinstance(page_issues, list):
                page_issues = []
            collected.extend([item for item in page_issues if isinstance(item, dict)])

            if isinstance(raw, dict) and raw.get("total") is not None:
                try:
                    reported_total = int(raw.get("total"))
                except Exception:
                    pass

            if len(collected) >= cap:
                truncated = True
                collected = collected[:cap]
                break

            if reported_total is not None and len(collected) >= reported_total:
                break

            next_page_token = str(raw.get("nextPageToken") or raw.get("next_page_token") or "").strip() if isinstance(raw, dict) else ""
            is_last = bool(raw.get("isLast")) if isinstance(raw, dict) and "isLast" in raw else False
            if is_last or not next_page_token:
                if reported_total is not None and len(collected) < reported_total:
                    truncated = True
                break

        if reported_total is not None and len(collected) < reported_total:
            truncated = True
        return {
            "issues": collected,
            "truncated": truncated,
            "total": reported_total,
        }

    def _extract_sprint_names(fields: Any) -> List[str]:
        names: List[str] = []
        if not isinstance(fields, dict):
            return names
        sprint_val = fields.get("sprint") or fields.get("customfield_10020") or fields.get("sprints")
        if isinstance(sprint_val, list):
            for sv in sprint_val:
                if isinstance(sv, dict):
                    nm = sv.get("name") or sv.get("id")
                    if nm:
                        names.append(str(nm).strip())
                elif sv:
                    names.append(str(sv).strip())
        elif isinstance(sprint_val, dict):
            nm = sprint_val.get("name") or sprint_val.get("id")
            if nm:
                names.append(str(nm).strip())
        elif sprint_val:
            names.append(str(sprint_val).strip())
        return [n for n in names if n]

    async def get_issue(args: Dict[str, Any]) -> Dict[str, Any]:
        key = str(args.get("issue_key") or "").upper().strip()
        if not key:
            return {"status": "ERROR", "tool": "get_issue", "reason": "issue_key_required"}
        issue = issue_repo.get_by_key(key)
        if not issue:
            if client:
                try:
                    raw = await client.get_issue(key)
                    fields = raw.get("fields", {}) if isinstance(raw, dict) else {}
                    if raw and isinstance(raw, dict):
                        return _as_available("get_issue", {
                            "key": raw.get("key"),
                            "summary": fields.get("summary"),
                            "status": (fields.get("status") or {}).get("name"),
                            "priority": (fields.get("priority") or {}).get("name"),
                            "assignee": ((fields.get("assignee") or {}).get("displayName") or (fields.get("assignee") or {}).get("accountId")),
                            "reporter": ((fields.get("reporter") or {}).get("displayName") or (fields.get("reporter") or {}).get("accountId")),
                            "type": (fields.get("issuetype") or {}).get("name"),
                            "project": (fields.get("project") or {}).get("key"),
                            "sprint": fields.get("sprint") or fields.get("customfield_10020"),
                            "due_date": fields.get("duedate"),
                            "labels": fields.get("labels") or [],
                            "links": fields.get("issuelinks") or [],
                            "updated_at": fields.get("updated"),
                            "last_activity_at": fields.get("updated"),
                            "workflow_metadata": {"source": "jira_api"},
                        })
                except Exception:
                    pass
            return _as_empty("get_issue", "No projected issue for key.")
        return _as_available("get_issue", _issue_value(issue))

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
        if client and not matches:
            try:
                raw = await client.search_issues(jql=q, max_results=10, fields=["summary", "status", "priority", "assignee", "reporter", "issuetype", "project", "duedate", "labels", "issuelinks"])
                issues = raw.get("issues", []) if isinstance(raw, dict) else []
                for item in issues:
                    fields = item.get("fields", {}) if isinstance(item, dict) else {}
                    matches.append({
                        "jira_issue_key": item.get("key"),
                        "summary": fields.get("summary"),
                        "status": (fields.get("status") or {}).get("name"),
                        "priority": (fields.get("priority") or {}).get("name"),
                        "assignee": ((fields.get("assignee") or {}).get("displayName") or (fields.get("assignee") or {}).get("accountId")),
                        "project_key": (fields.get("project") or {}).get("key"),
                        "due_date": fields.get("duedate"),
                    })
            except Exception:
                pass
        if not matches:
            return _as_empty("search_issues", "No matches.")
        bounded = [_issue_value(m) for m in matches]
        if len(bounded) == 1:
            return _as_available("search_issues", bounded[0])
        return _as_ambiguous("search_issues", bounded, question="I found multiple matching issues. Which issue do you mean?")

    registry.register(
        "search_issues",
        ToolSpec(name="search_issues", description="Search projected issues by key/title substring.", parameters_schema={"query": "string"}),
        search_issues,
    )

    async def get_active_sprints(args: Dict[str, Any]) -> Dict[str, Any]:
        team_group = args.get("team_group")
        sprint_names = set()
        if client:
            try:
                jql = "sprint in openSprints() ORDER BY updated DESC"
                page = await _paged_search_issues(jql, ["sprint", "customfield_10020"], page_size=100, cap=1000)
                for item in page["issues"]:
                    fields = item.get("fields", {}) if isinstance(item, dict) else {}
                    sprint_names.update(_extract_sprint_names(fields))
                if page.get("truncated"):
                    sprint_names = {s for s in sprint_names if s}
                    sprints = sorted(sprint_names)
                    return {
                        "status": "INSUFFICIENT_DATA",
                        "tool": "get_active_sprints",
                        "value": {"candidates": sprints, "truncated": True},
                        "reason": "Active sprint search hit the 1000-result cap.",
                        "truncated": True,
                    }
            except Exception:
                pass
        if not sprint_names:
            all_issues = issue_repo.list_all(limit=1000)
            for it in all_issues:
                if team_group and it.get("team_group") != team_group:
                    continue
                raw = it.get("raw_reference")
                try:
                    raw_obj = json.loads(raw) if isinstance(raw, str) else raw
                except Exception:
                    raw_obj = raw
                fields = raw_obj.get("fields", {}) if isinstance(raw_obj, dict) else {}
                sprint_names.update(_extract_sprint_names(fields))
            if len(all_issues) >= 1000:
                sprints = sorted({s for s in sprint_names if s})
                return {
                    "status": "INSUFFICIENT_DATA",
                    "tool": "get_active_sprints",
                    "value": {"candidates": sprints, "truncated": True},
                    "reason": "Active sprint search hit the 1000-result cap.",
                    "truncated": True,
                }
        sprint_names = {s for s in sprint_names if s}
        if not sprint_names:
            return _as_empty("get_active_sprints", "No active sprints found.")
        sprints = sorted(sprint_names)
        if len(sprints) == 1:
            return _as_available("get_active_sprints", {"name": sprints[0], "candidates": sprints})
        return _as_ambiguous("get_active_sprints", [{"value": s, "label": s, "evidence": ["active sprint"]} for s in sprints], question="I found multiple active sprints. Which sprint do you mean?")

    registry.register(
        "get_active_sprints",
        ToolSpec(name="get_active_sprints", description="Return active sprint candidates from projections.", parameters_schema={"team_group": "string"}),
        get_active_sprints,
    )

    async def get_sprint(args: Dict[str, Any]) -> Dict[str, Any]:
        sprint_name = str(args.get("sprint_name") or "").strip()
        if not sprint_name:
            return {"status": "ERROR", "tool": "get_sprint", "reason": "sprint_name_required"}
        issues_res = await get_sprint_issues({"sprint_name": sprint_name})
        if issues_res.get("status") == "AVAILABLE":
            val = issues_res.get("value")
            issues = val.get("issues", []) if isinstance(val, dict) else []
            summary = {
                "total": val.get("total", len(issues)) if isinstance(val, dict) else len(issues),
                "status_counts": val.get("status_counts", {}) if isinstance(val, dict) else {},
                "assignee_counts": val.get("assignee_counts", {}) if isinstance(val, dict) else {},
            }
            return _as_available(
                "get_sprint",
                {"name": sprint_name, "summary": summary, "issues_preview": issues[:10]},
                derivation=issues_res.get("derivation") or [],
            )
        if issues_res.get("status") == "AMBIGUOUS":
            return issues_res
        if issues_res.get("status") == "EMPTY":
            return _as_empty("get_sprint", f"No sprint found for {sprint_name}.")
        return issues_res

    registry.register(
        "get_sprint",
        ToolSpec(name="get_sprint", description="Get a sprint summary by sprint name.", parameters_schema={"sprint_name": "string"}),
        get_sprint,
    )

    async def get_sprint_issues(args: Dict[str, Any]) -> Dict[str, Any]:
        sprint = str(args.get("sprint_name") or "").strip()
        if not sprint:
            return {"status": "ERROR", "tool": "get_sprint_issues", "reason": "sprint_name_required"}
        issues: List[Dict[str, Any]] = []
        matched_records: List[Dict[str, Any]] = []
        if client:
            try:
                page = await _paged_search_issues(
                    jql=f'sprint = "{sprint}" ORDER BY rank ASC',
                    fields=["summary", "status", "priority", "assignee", "reporter", "issuetype", "sprint", "project", "duedate", "labels", "issuelinks"],
                    page_size=100,
                    cap=1000,
                )
                for item in page["issues"]:
                    fields = item.get("fields", {}) if isinstance(item, dict) else {}
                    issues.append({
                        "key": item.get("key"),
                        "summary": fields.get("summary"),
                        "status": (fields.get("status") or {}).get("name"),
                        "priority": (fields.get("priority") or {}).get("name"),
                        "assignee": ((fields.get("assignee") or {}).get("displayName") or (fields.get("assignee") or {}).get("accountId")),
                        "project": (fields.get("project") or {}).get("key"),
                        "due_date": fields.get("duedate"),
                    })
                    matched_records.append({
                        "key": item.get("key"),
                        "status": (fields.get("status") or {}).get("name"),
                        "assignee": ((fields.get("assignee") or {}).get("displayName") or (fields.get("assignee") or {}).get("accountId")),
                        "raw_reference": item,
                    })
                if page.get("truncated"):
                    summary = _summarize_issues(matched_records)
                    return {
                        "status": "INSUFFICIENT_DATA",
                        "tool": "get_sprint_issues",
                        "value": {
                            "sprint_name": sprint,
                            "total": summary["total"],
                            "status_counts": summary["status_counts"],
                            "assignee_counts": summary["assignee_counts"],
                            "issues": issues,
                            "truncated": True,
                        },
                        "truncated": True,
                        "reason": "Sprint search hit the 1000-result cap.",
                    }
            except Exception:
                pass
        if not issues:
            all_issues = issue_repo.list_all(limit=1000)
            for it in all_issues:
                raw = it.get("raw_reference")
                try:
                    raw_obj = json.loads(raw) if isinstance(raw, str) else raw
                except Exception:
                    raw_obj = raw
                fields = raw_obj.get("fields", {}) if isinstance(raw_obj, dict) else {}
                sprint_names = {s.lower() for s in _extract_sprint_names(fields)}
                if sprint.lower() in sprint_names:
                    issues.append(_issue_value(it))
                    matched_records.append(it)
        if not issues:
            return _as_empty("get_sprint_issues", "No issues found for sprint.")
        if len(issues) >= 1000:
            summary = _summarize_issues(matched_records)
            return {
                "status": "INSUFFICIENT_DATA",
                "tool": "get_sprint_issues",
                "value": {
                    "sprint_name": sprint,
                    "total": summary["total"],
                    "status_counts": summary["status_counts"],
                    "assignee_counts": summary["assignee_counts"],
                    "issues": issues[:1000],
                    "truncated": True,
                },
                "truncated": True,
                "reason": "Sprint search hit the 1000-result cap.",
            }
        summary = _summarize_issues(matched_records)
        return _as_available(
            "get_sprint_issues",
            {
                "sprint_name": sprint,
                "total": summary["total"],
                "status_counts": summary["status_counts"],
                "assignee_counts": summary["assignee_counts"],
                "issues": issues,
            },
            derivation=summary["derivation"],
        )

    registry.register(
        "get_sprint_issues",
        ToolSpec(name="get_sprint_issues", description="Issues belonging to a sprint (projection approximation).", parameters_schema={"sprint_name": "string"}),
        get_sprint_issues,
    )

    async def find_user(args: Dict[str, Any]) -> Dict[str, Any]:
        query = str(args.get("query") or "").strip()
        if not query:
            return {"status": "ERROR", "tool": "find_user", "reason": "query_required"}

        # Deterministic mapping by display name substring (projection only).
        matches = []
        for rec in role_repo.list_assignments():
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
            return _as_empty("find_user", "No matching user found.")
        if len(matches) == 1:
            return _as_available("find_user", matches[0])
        return _as_ambiguous("find_user", [
            {"value": m.get("account_id"), "label": f"{m.get('display_name')} ({m.get('designation') or m.get('role_category') or 'Unknown'})", "evidence": [m.get("role") or "", m.get("designation") or ""]} for m in matches
        ], question="Which user do you mean?")

    registry.register(
        "find_user",
        ToolSpec(name="find_user", description="Find users (Phase 1 placeholder; must be backed by deterministic mapping later).", parameters_schema={"query": "string"}),
        find_user,
    )

    async def get_linked_issues(args: Dict[str, Any]) -> Dict[str, Any]:
        issue_key = str(args.get("issue_key") or "").upper().strip()
        if not issue_key:
            return {"status": "ERROR", "tool": "get_linked_issues", "reason": "issue_key_required"}
        links = link_repo.list_links_for_source(issue_key, active_only=True)
        value = [{
            "source_issue_key": l.get("source_issue_key"),
            "target_issue_key": l.get("target_issue_key"),
            "link_type_name": l.get("link_type_name"),
            "classification": l.get("classification"),
            "is_active": bool(l.get("is_active", 1)),
        } for l in links]
        if not value:
            return _as_empty("get_linked_issues", "No linked issues found.")
        return _as_available("get_linked_issues", value)

    registry.register(
        "get_linked_issues",
        ToolSpec(name="get_linked_issues", description="Get linked issues from projected link table.", parameters_schema={"issue_key": "string"}),
        get_linked_issues,
    )

    async def get_comments(args: Dict[str, Any]) -> Dict[str, Any]:
        issue_key = str(args.get("issue_key") or "").upper().strip()
        if not issue_key:
            return {"status": "ERROR", "tool": "get_comments", "reason": "issue_key_required"}
        if not client:
            return _as_not_available("get_comments", "Comments are not available in the local projection.")
        try:
            comments = await client.get_issue_comments(issue_key)
            if not comments:
                return _as_empty("get_comments", "No comments found.")
            return _as_available("get_comments", [{"id": c.get("id"), "author": (c.get("author") or {}).get("displayName"), "body": c.get("body"), "updated": c.get("updated") or c.get("created")} for c in comments[:20]])
        except Exception as e:
            return {"status": "ERROR", "tool": "get_comments", "reason": str(e)}

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
        return _as_available("capacity_workload_summary", snap.model_dump() if hasattr(snap, "model_dump") else snap, derivation=["ResourceQueueComposer.compose_snapshot"])

    registry.register(
        "capacity_workload_summary",
        ToolSpec(name="capacity_workload_summary", description="Compute capacity/workload snapshot for one resource.", parameters_schema={}),
        capacity_workload_summary,
    )

    async def forecast_schedule(args: Dict[str, Any]) -> Dict[str, Any]:
        account_ids = args.get("account_ids")
        team_group = args.get("team_group")
        project_key = args.get("project_key")
        horizon_working_days = int(args.get("horizon_working_days") or 10)
        composer = ResourceQueueComposer(manager=mgr)
        forecaster = TeamScheduleForecaster(manager=mgr)
        workload = composer.compose_team_snapshots(
            account_ids=account_ids if isinstance(account_ids, list) else None,
            team_group=team_group,
            project_key=project_key,
            horizon_working_days=horizon_working_days,
        )
        if not workload.resource_snapshots:
            return _as_empty("forecast_schedule", "No snapshots available.")
        forecast = forecaster.forecast_team_schedule(
            team_snapshots=workload.resource_snapshots,
            horizon_working_days=horizon_working_days,
        )
        return _as_available("forecast_schedule", forecast.model_dump() if hasattr(forecast, "model_dump") else forecast, derivation=["ResourceQueueComposer.compose_team_snapshots", "TeamScheduleForecaster.forecast_team_schedule"])

    registry.register(
        "forecast_schedule",
        ToolSpec(name="forecast_schedule", description="Forecast schedule for planning horizon (stub in Phase 1).", parameters_schema={}),
        forecast_schedule,
    )

    async def analyze_dependencies(args: Dict[str, Any]) -> Dict[str, Any]:
        issue_key = str(args.get("issue_key") or "").upper().strip()
        links = link_repo.list_all_links(active_only=True)
        graph = DependencyGraph.from_link_records(links, include_only_hard_blocks=True)
        if issue_key:
            preds = graph.get_predecessors(issue_key)
            succs = graph.get_successors(issue_key)
            return _as_available("analyze_dependencies", {
                "issue_key": issue_key,
                "predecessors": preds,
                "successors": succs,
                "has_hard_blockers": graph.has_hard_blockers(issue_key),
                "cycle": graph.detect_cycles().model_dump() if hasattr(graph.detect_cycles(), "model_dump") else graph.detect_cycles(),
            }, derivation=["jira_issue_links", "DependencyGraph.from_link_records"])
        return _as_available("analyze_dependencies", {
            "node_count": graph.node_count,
            "edge_count": graph.edge_count,
            "has_cycle": graph.detect_cycles().has_cycle,
        }, derivation=["jira_issue_links", "DependencyGraph.from_link_records"])

    async def get_planning_context(args: Dict[str, Any]) -> Dict[str, Any]:
        team_group = args.get("team_group")
        project_key = args.get("project_key")
        horizon_working_days = int(args.get("horizon_working_days") or 10)
        composer = ResourceQueueComposer(manager=mgr)
        team = composer.compose_team_snapshots(team_group=team_group, project_key=project_key, horizon_working_days=horizon_working_days)
        if not team.resource_snapshots:
            return _as_empty("get_planning_context", "No resource snapshots available.")
        dep_graph = DependencyGraph.from_link_records(link_repo.list_all_links(active_only=True), include_only_hard_blocks=True)
        forecast = TeamScheduleForecaster(manager=mgr).forecast_team_schedule(team.resource_snapshots, dependency_graph=dep_graph, horizon_working_days=horizon_working_days)
        artifact_models = []
        for art in ArtifactRepository(mgr).list_all_artifacts(active_only=True):
            try:
                artifact_models.append(
                    ArtifactRecord(
                        id=art.get("id"),
                        name=art.get("name"),
                        project_key=art.get("project_key"),
                        artifact_type=ArtifactType(art.get("artifact_type", "GENERIC")),
                        status=ArtifactStatus(art.get("status", "PLANNED")),
                        producer_issue_key=art.get("producer_issue_key"),
                        provenance=ArtifactProvenance(art.get("provenance", "EXPLICIT_JIRA_LABEL")),
                        confidence=art.get("confidence", "HIGH"),
                        first_seen_at=art.get("first_seen_at"),
                        last_seen_at=art.get("last_seen_at"),
                        is_active=bool(art.get("is_active", 1)),
                    )
                )
            except Exception:
                continue
        rel_models = []
        # Keep relationships empty in Phase 2 unless we can safely normalize them.
        ctx = PlanningContextBuilder(max_resources=10, max_total_tasks=25).build_context(
            team_snapshots=team.resource_snapshots,
            schedule_projection=forecast,
            dependency_graph=dep_graph,
            artifact_records=artifact_models,
            artifact_relationships=rel_models,
            team_group=team_group,
            horizon_working_days=horizon_working_days,
        )
        ctx_value = ctx.model_dump() if hasattr(ctx, "model_dump") else ctx
        task_summary = _summarize_issues(
            [
                {
                    "key": getattr(task, "issue_key", None) if not isinstance(task, dict) else task.get("issue_key"),
                    "status": getattr(task, "status", None) if not isinstance(task, dict) else task.get("status"),
                    "assignee": getattr(task, "assigned_resource_id", None) if not isinstance(task, dict) else task.get("assigned_resource_id"),
                }
                for task in (getattr(ctx, "tasks", []) or [])
            ]
        )
        if isinstance(ctx_value, dict):
            ctx_value["summary"] = {
                "total": task_summary["total"],
                "status_counts": task_summary["status_counts"],
                "assignee_counts": task_summary["assignee_counts"],
            }
        return _as_available(
            "get_planning_context",
            ctx_value,
            derivation=["ResourceQueueComposer", "TeamScheduleForecaster", "PlanningContextBuilder", *task_summary["derivation"]],
        )

    registry.register(
        "analyze_dependencies",
        ToolSpec(name="analyze_dependencies", description="Analyze dependency DAG (stub in Phase 1).", parameters_schema={}),
        analyze_dependencies,
    )

    registry.register(
        "get_planning_context",
        ToolSpec(name="get_planning_context", description="Build a bounded planning context from deterministic PM services.", parameters_schema={}),
        get_planning_context,
    )

    return registry

