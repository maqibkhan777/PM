import time

import pytest

from app.agent_core.agent_models import AgentStep, AgentState, Candidate, ToolCall, ToolSpec, UncertaintyClass, AmbiguityQuestion, ToolResultStatus
from app.agent_core.core import AgentCore
from app.agent_core.tooling import ToolRegistry
from app.services.ai.agent_provider import AgentProvider
from app.services.ai.provider import AIProvider
from app.services.ai.pm_tools import build_tool_registry


class ScriptedProvider:
    """Deterministic provider for tool-loop tests."""

    def __init__(self, scripted_steps):
        self.scripted_steps = list(scripted_steps)
        self.calls = 0

    async def next_agent_step(self, user_goal: str, actor: str, state: AgentState, tools):
        if self.calls >= len(self.scripted_steps):
            raise AssertionError("Provider received more iterations than scripted.")
        step = self.scripted_steps[self.calls]
        self.calls += 1
        return step


@pytest.mark.asyncio
async def test_tool_selection_and_multi_tool_accumulation():
    async def t1(args):
        return {"status": ToolResultStatus.AVAILABLE.value, "tool": "tool1", "value": {"x": 1}}

    async def t2(args):
        return {"status": ToolResultStatus.AVAILABLE.value, "tool": "tool2", "value": {"y": args.get("k", 2)}}

    registry = ToolRegistry()
    registry.register("tool1", ToolSpec(name="tool1", description="t1"), t1)
    registry.register("tool2", ToolSpec(name="tool2", description="t2"), t2)

    steps = [
        AgentStep(
            kind="TOOL_CALL",
            next_tool_calls=[ToolCall(tool_name="tool1", arguments={}), ToolCall(tool_name="tool2", arguments={"k": 7})],
            uncertainty_class=UncertaintyClass.KNOWN,
            reasoning_trace=["scripted"],
            final_answer="done",
        )
    ]

    provider = ScriptedProvider(steps)
    agent_provider = AgentProvider(provider)  # type: ignore[arg-type]
    core = AgentCore(provider=provider, tool_registry=registry, agent_provider=agent_provider)  # type: ignore[arg-type]

    res = await core.run("goal", actor="u1")
    assert res["status"] == "COMPLETED"
    assert len(res["tools_called"]) == 2
    assert res["uncertainty"] == UncertaintyClass.KNOWN.value


@pytest.mark.asyncio
async def test_ambiguous_user_produces_clarification_question():
    async def dummy(args):
        return {
            "status": ToolResultStatus.AMBIGUOUS.value,
            "tool": "find_user",
            "candidates": [
                {"value": "A", "label": "Alice (Developer)", "evidence": ["role"]},
                {"value": "B", "label": "Bob (QA)", "evidence": ["role"]},
            ],
        }

    registry = ToolRegistry()
    registry.register("find_user", ToolSpec(name="find_user", description="users"), dummy)

    steps = [
        AgentStep(
            kind="TOOL_CALL",
            next_tool_calls=[ToolCall(tool_name="find_user", arguments={})],
            final_answer=None,
            uncertainty_class=UncertaintyClass.AMBIGUOUS,
            reasoning_trace=["scripted"],
        )
    ]
    provider = ScriptedProvider(steps)
    agent_provider = AgentProvider(provider)  # type: ignore[arg-type]
    core = AgentCore(provider=provider, tool_registry=registry, agent_provider=agent_provider)  # type: ignore[arg-type]

    res = await core.run("goal", actor="u1")
    assert res["status"] == "NEEDS_CLARIFICATION"
    assert "Which user?" in res["question"] or "Alice" in str(res["candidates"])
    assert len(res["candidates"]) == 2


@pytest.mark.asyncio
async def test_single_user_match_resolves_without_clarification():
    async def single(args):
        return {
            "status": ToolResultStatus.AVAILABLE.value,
            "tool": "find_user",
            "value": {"account_id": "acc-1", "display_name": "Ali Raza", "role": "Developer", "designation": "Developer", "role_category": "Engineering"},
        }

    registry = ToolRegistry()
    registry.register("find_user", ToolSpec(name="find_user", description="users"), single)
    provider = ScriptedProvider([AgentStep.tool_calls([ToolCall(tool_name="find_user", arguments={"query": "Ali"})]), AgentStep.final("Ali Raza")])
    core = AgentCore(provider=provider, tool_registry=registry, agent_provider=AgentProvider(provider))  # type: ignore[arg-type]
    res = await core.run("Who is Ali?", actor="u1")
    assert res["status"] == "COMPLETED"
    assert "Ali Raza" in res["answer"]


@pytest.mark.asyncio
async def test_single_sprint_without_asking_clarification():
    # We model "single match" behavior purely at the uncertainty-question level:
    # if provider returns final_answer directly, AgentCore should complete.
    registry = ToolRegistry()
    async def get_active_sprints(_args):
        return {"status": ToolResultStatus.AVAILABLE.value, "tool": "get_active_sprints", "value": {"sprints": ["S1"]}}

    registry.register("get_active_sprints", ToolSpec(name="get_active_sprints", description="sprints"), get_active_sprints)

    steps = [
        AgentStep(
            kind="TOOL_CALL",
            next_tool_calls=[ToolCall(tool_name="get_active_sprints", arguments={})],
            final_answer="single sprint chosen",
            ambiguity_question=None,
            uncertainty_class=UncertaintyClass.KNOWN,
            reasoning_trace=["scripted"],
        )
    ]
    provider = ScriptedProvider(steps)
    agent_provider = AgentProvider(provider)  # type: ignore[arg-type]
    core = AgentCore(provider=provider, tool_registry=registry, agent_provider=agent_provider)  # type: ignore[arg-type]

    res = await core.run("goal", actor="u1")
    assert res["status"] == "COMPLETED"
    assert "question" not in res


@pytest.mark.asyncio
async def test_write_tools_only_emit_proposed_actions():
    # Tool results should not directly be "executed actions".
    async def propose_actions(args):
        return {
            "status": ToolResultStatus.AVAILABLE.value,
            "tool": "plan_write",
            "value": {"proposed_actions": [{"action_type": "TRANSITION_TASK", "target_id": args["issue"]}]},
        }

    registry = ToolRegistry()
    registry.register("plan_write", ToolSpec(name="plan_write", description="propose only"), propose_actions)

    steps = [
        AgentStep(
            kind="TOOL_CALL",
            next_tool_calls=[ToolCall(tool_name="plan_write", arguments={"issue": "WSSS-1"})],
            final_answer="proposal ready",
            ambiguity_question=None,
            uncertainty_class=UncertaintyClass.INFERABLE,
            reasoning_trace=["scripted"],
        )
    ]
    provider = ScriptedProvider(steps)
    agent_provider = AgentProvider(provider)  # type: ignore[arg-type]
    core = AgentCore(provider=provider, tool_registry=registry, agent_provider=agent_provider)  # type: ignore[arg-type]

    res = await core.run("goal", actor="u1")
    assert res["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_ambiguous_active_sprints_become_clarification():
    async def sprints(_args):
        return {
            "status": ToolResultStatus.AMBIGUOUS.value,
            "tool": "get_active_sprints",
            "candidates": [
                {"value": "Sprint 42", "label": "Sprint 42", "evidence": ["active sprint"]},
                {"value": "Sprint 43", "label": "Sprint 43", "evidence": ["active sprint"]},
            ],
        }

    registry = ToolRegistry()
    registry.register("get_active_sprints", ToolSpec(name="get_active_sprints", description="sprints"), sprints)
    provider = ScriptedProvider([AgentStep.tool_calls([ToolCall(tool_name="get_active_sprints", arguments={})])])
    core = AgentCore(provider=provider, tool_registry=registry, agent_provider=AgentProvider(provider))  # type: ignore[arg-type]
    res = await core.run("Which sprint should I use?", actor="u1")
    assert res["status"] == "NEEDS_CLARIFICATION"
    assert "Sprint 42" in str(res["candidates"]) and "Sprint 43" in str(res["candidates"])


@pytest.mark.asyncio
async def test_zero_matches_returns_truthful_failure():
    async def missing(_args):
        return {"status": ToolResultStatus.EMPTY.value, "tool": "find_user", "reason": "No matching user found."}

    registry = ToolRegistry()
    registry.register("find_user", ToolSpec(name="find_user", description="users"), missing)
    provider = ScriptedProvider([AgentStep.tool_calls([ToolCall(tool_name="find_user", arguments={"query": "Nobody"})])])
    core = AgentCore(provider=provider, tool_registry=registry, agent_provider=AgentProvider(provider))  # type: ignore[arg-type]
    res = await core.run("Who is Nobody?", actor="u1")
    assert res["status"] == "FAILURE"
    assert "couldn't verify" in res["error"].lower()


@pytest.mark.asyncio
async def test_inferable_carries_derivation_in_state():
    captured = []

    class InferableProvider:
        async def next_agent_step(self, user_goal: str, actor: str, state: AgentState, tools):
            if state.last_tool_results.get("tools_called"):
                captured.append(state.inferable_facts.copy())
                return AgentStep.final("done")
            return AgentStep.tool_calls([ToolCall(tool_name="capacity_workload_summary", arguments={})])

    async def infer_hours(_args):
        return {
            "status": ToolResultStatus.INSUFFICIENT_DATA.value,
            "tool": "capacity_workload_summary",
            "value": {"remaining_hours": 10},
            "derivation": ["capacity 40h - committed 30h = 10h remaining"],
            "reason": "Derived remaining capacity",
        }

    registry = ToolRegistry()
    registry.register("capacity_workload_summary", ToolSpec(name="capacity_workload_summary", description="capacity"), infer_hours)
    provider = InferableProvider()
    core = AgentCore(provider=provider, tool_registry=registry, agent_provider=AgentProvider(provider))  # type: ignore[arg-type]
    res = await core.run("How much time is left?", actor="u1", session_id="cap1")
    assert res["status"] == "COMPLETED"
    assert captured
    assert captured[0]["capacity_workload_summary"]["value"]["remaining_hours"] == 10
    assert captured[0]["capacity_workload_summary"]["derivation"] == ["capacity 40h - committed 30h = 10h remaining"]


@pytest.mark.asyncio
async def test_clarification_continuity_resumes_original_goal():
    class ContinuityProvider:
        async def next_agent_step(self, user_goal: str, actor: str, state: AgentState, tools):
            if state.selected_sprint:
                return AgentStep.final(f"Resumed goal for {state.selected_sprint}.")
            return AgentStep.tool_calls([ToolCall(tool_name="get_active_sprints", arguments={})], uncertainty=UncertaintyClass.AMBIGUOUS)

    async def sprints(_args):
        return {
            "status": ToolResultStatus.AMBIGUOUS.value,
            "tool": "get_active_sprints",
            "clarification_kind": "sprint",
            "candidates": [
                {"value": "Sprint A", "label": "Sprint A", "evidence": ["active sprint"]},
                {"value": "Sprint B", "label": "Sprint B", "evidence": ["active sprint"]},
            ],
        }

    registry = ToolRegistry()
    registry.register("get_active_sprints", ToolSpec(name="get_active_sprints", description="sprints"), sprints)
    provider = ContinuityProvider()
    core = AgentCore(provider=provider, tool_registry=registry, agent_provider=AgentProvider(provider))  # type: ignore[arg-type]
    first = await core.run("Which sprint?", actor="u1", session_id="t1")
    assert first["status"] == "NEEDS_CLARIFICATION"
    second = await core.run("Sprint B", actor="u1", session_id="t1")
    assert second["status"] == "COMPLETED"
    assert "Sprint B" in second["answer"]


@pytest.mark.asyncio
async def test_provider_clarification_rejected_without_prior_tool_evidence():
    class ClarificationProvider:
        async def next_agent_step(self, user_goal: str, actor: str, state: AgentState, tools):
            return AgentStep.clarification(AmbiguityQuestion(question="Which sprint?", candidates=[Candidate(value="Sprint B", label="Sprint B")]), uncertainty=UncertaintyClass.UNKNOWN)

    core = AgentCore(provider=ClarificationProvider(), tool_registry=ToolRegistry(), agent_provider=AgentProvider(ClarificationProvider()))  # type: ignore[arg-type]
    res = await core.run("Create a plan for the WPEPSUP work.", actor="u1", session_id="clarify-accept")
    assert res["status"] == "FAILED"
    assert "tool loop exceeded budget" in res["error"].lower()


@pytest.mark.asyncio
async def test_provider_clarification_rejected_after_grounded_tool_result():
    class GroundedClarificationProvider:
        async def next_agent_step(self, user_goal: str, actor: str, state: AgentState, tools):
            if state.context.get("clarification_rejected"):
                return AgentStep.final("Grounded answer after continuing to retrieve tools.")
            if not state.last_tool_results.get("tools_called"):
                return AgentStep.tool_calls([ToolCall(tool_name="get_issue", arguments={"issue_key": "WSSS-326"})], uncertainty=UncertaintyClass.KNOWN)
            return AgentStep.clarification(AmbiguityQuestion(question="Which sprint?", candidates=[]), uncertainty=UncertaintyClass.UNKNOWN)

    async def get_issue(_args):
        return {
            "status": ToolResultStatus.AVAILABLE.value,
            "tool": "get_issue",
            "value": {"key": "WSSS-326", "summary": "Blocked ticket", "status": "In Progress"},
        }

    registry = ToolRegistry()
    registry.register("get_issue", ToolSpec(name="get_issue", description="issue"), get_issue)
    provider = GroundedClarificationProvider()
    core = AgentCore(provider=provider, tool_registry=registry, agent_provider=AgentProvider(provider))  # type: ignore[arg-type]
    res = await core.run("What is blocking WSSS-326?", actor="u1", session_id="clarify-reject")
    assert res["status"] == "COMPLETED"
    assert "Grounded answer" in res["answer"]


@pytest.mark.asyncio
async def test_provider_clarification_accepted_after_ambiguous_tool_result():
    class AmbiguousClarificationProvider:
        async def next_agent_step(self, user_goal: str, actor: str, state: AgentState, tools):
            return AgentStep.clarification(
                AmbiguityQuestion(
                    question="Which Ali do you mean?",
                    candidates=[
                        Candidate(value="acc-1", label="Ali Raza (Developer)", evidence=["developer"]),
                        Candidate(value="acc-2", label="Ali Khan (QA)", evidence=["qa"]),
                    ],
                ),
                uncertainty=UncertaintyClass.AMBIGUOUS,
            )

    registry = ToolRegistry()
    provider = AmbiguousClarificationProvider()
    core = AgentCore(provider=provider, tool_registry=registry, agent_provider=AgentProvider(provider))  # type: ignore[arg-type]
    session_id = "clarify-ambiguous"
    core._sessions[session_id] = {
        "ts": time.time(),
        "state": AgentState(
            user_goal="Who is Ali?",
            current_input="Ali Raza",
            pending_clarification=AmbiguityQuestion(
                question="Which Ali?",
                kind="user",
                candidates=[
                    Candidate(value="acc-1", label="Ali Raza (Developer)", evidence=["developer"]),
                    Candidate(value="acc-2", label="Ali Khan (QA)", evidence=["qa"]),
                ],
            ),
            last_tool_results={
                "tools_called": [{"tool": "find_user", "args": {"query": "Ali"}, "result_type": "dict"}],
                "find_user": {
                    "status": ToolResultStatus.AMBIGUOUS.value,
                    "tool": "find_user",
                    "candidates": [
                        {"value": "acc-1", "label": "Ali Raza (Developer)", "evidence": ["developer"]},
                        {"value": "acc-2", "label": "Ali Khan (QA)", "evidence": ["qa"]},
                    ],
                },
            },
        ),
    }
    res = await core.run("Ali Raza", actor="u1", session_id=session_id)
    assert res["status"] == "NEEDS_CLARIFICATION"
    assert "Which Ali" in res["question"]


@pytest.mark.asyncio
async def test_unregistered_tool_rejected_and_loop_limit_safe():
    class BadProvider:
        async def next_agent_step(self, user_goal: str, actor: str, state: AgentState, tools):
            return AgentStep.tool_calls([ToolCall(tool_name="not_registered", arguments={})])

    registry = ToolRegistry()
    provider = BadProvider()
    core = AgentCore(provider=provider, tool_registry=registry, agent_provider=AgentProvider(provider))  # type: ignore[arg-type]
    res = await core.run("goal", actor="u1", session_id="bad1")
    assert res["status"] == "FAILURE"
    assert "Unknown tool" in res["error"]

    class LoopProvider:
        async def next_agent_step(self, user_goal: str, actor: str, state: AgentState, tools):
            return AgentStep.tool_calls([ToolCall(tool_name="noop", arguments={})])

    async def noop(_args):
        return {"status": ToolResultStatus.AVAILABLE.value, "tool": "noop", "value": {"ok": True}}

    registry = ToolRegistry()
    registry.register("noop", ToolSpec(name="noop", description="noop"), noop)
    loop_core = AgentCore(provider=LoopProvider(), tool_registry=registry, agent_provider=AgentProvider(LoopProvider()))  # type: ignore[arg-type]
    res2 = await loop_core.run("loop", actor="u1", session_id="loop1")
    assert res2["status"] == "FAILED"


@pytest.mark.asyncio
async def test_not_available_comments_path_returns_not_available(monkeypatch):
    import app.services.ai.pm_tools as pm_tools

    monkeypatch.setattr(type(pm_tools.settings), "is_jira_configured", lambda self: False, raising=False)
    registry = build_tool_registry()
    tool = registry.get("get_comments")
    assert tool is not None
    result = await tool.fn({"issue_key": "WSSS-1"})
    assert result["status"] == "NOT_AVAILABLE"


@pytest.mark.asyncio
async def test_get_sprint_issues_returns_deterministic_aggregates(temp_db):
    from app.database.repositories import JiraIssueStateRepository

    issue_repo = JiraIssueStateRepository(temp_db)
    def sprint_ref(status_name: str, status_category: str | None):
        status = {"name": status_name}
        if status_category is not None:
            status["statusCategory"] = {"key": status_category}
        return {"fields": {"sprint": [{"name": "Sprint 42"}], "status": status}}

    issue_repo.upsert(
        jira_issue_key="SPR-1",
        summary="Done task",
        status="Done",
        assignee="acc-a",
        project_key="SPR",
        raw_reference=sprint_ref("Done", "done"),
    )
    issue_repo.upsert(
        jira_issue_key="SPR-2",
        summary="Active task",
        status="In Progress",
        assignee="acc-b",
        project_key="SPR",
        raw_reference=sprint_ref("In Progress", "indeterminate"),
    )
    issue_repo.upsert(
        jira_issue_key="SPR-3",
        summary="Queued task",
        status="New",
        assignee="acc-a",
        project_key="SPR",
        raw_reference=sprint_ref("New", "new"),
    )
    issue_repo.upsert(
        jira_issue_key="SPR-4",
        summary="Cancelled task",
        status="Cancelled",
        assignee="acc-c",
        project_key="SPR",
        raw_reference=sprint_ref("Cancelled", "done"),
    )
    issue_repo.upsert(
        jira_issue_key="SPR-5",
        summary="Mystery task",
        status="Mystery",
        assignee="acc-d",
        project_key="SPR",
        raw_reference=sprint_ref("Mystery", None),
    )

    registry = build_tool_registry(manager=temp_db)
    tool = registry.get("get_sprint_issues")
    assert tool is not None
    result = await tool.fn({"sprint_name": "Sprint 42"})
    assert result["status"] == "AVAILABLE"
    value = result["value"]
    assert value["total"] == 5
    assert value["status_counts"] == {"Done": 1, "In Progress": 1, "To Do": 1, "cancelled": 1, "unknown": 1}
    assert value["assignee_counts"] == {"acc-a": 2, "acc-b": 1, "acc-c": 1, "acc-d": 1}
    assert any("statusCategory=done" in line for line in result["derivation"])
    assert any("statusCategory=indeterminate" in line for line in result["derivation"])
    assert any("fallback-name=unknown" in line for line in result["derivation"])
    assert any("cancelled" in line.lower() for line in result["derivation"])


@pytest.mark.asyncio
async def test_get_sprint_issues_pages_through_results_and_populates_project(temp_db):
    class FakeJiraClient:
        def __init__(self):
            self.calls = []

        async def search_issues(self, jql, next_page_token=None, max_results=100, expand=None, fields=None):
            self.calls.append(
                {
                    "jql": jql,
                    "next_page_token": next_page_token,
                    "max_results": max_results,
                    "expand": expand,
                    "fields": list(fields or []),
                }
            )
            assert "project" in (fields or [])
            assert expand is None
            if next_page_token is None:
                return {
                    "issues": [
                        {
                            "key": "SPR-1",
                            "fields": {
                                "summary": "First page issue",
                                "status": {"name": "Done", "statusCategory": {"key": "done"}},
                                "priority": {"name": "High"},
                                "assignee": {"accountId": "acc-a", "displayName": "Alice"},
                                "project": {"key": "SPR"},
                                "duedate": "2026-10-01",
                            },
                        }
                    ],
                    "nextPageToken": "page-2",
                    "isLast": False,
                    "total": 2,
                }
            assert next_page_token == "page-2"
            return {
                "issues": [
                    {
                        "key": "SPR-2",
                        "fields": {
                            "summary": "Second page issue",
                            "status": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}},
                            "priority": {"name": "Medium"},
                            "assignee": {"accountId": "acc-b", "displayName": "Bob"},
                            "project": {"key": "SPR"},
                            "duedate": "2026-10-02",
                        },
                    }
                ],
                "isLast": True,
                "total": 2,
            }

    registry = build_tool_registry(manager=temp_db, jira_client=FakeJiraClient())
    tool = registry.get("get_sprint_issues")
    assert tool is not None
    result = await tool.fn({"sprint_name": "Sprint 42"})
    assert result["status"] == "AVAILABLE"
    value = result["value"]
    assert value["total"] == 2
    assert value["issues"][0]["project"] == "SPR"
    assert value["issues"][1]["project"] == "SPR"
    assert value["status_counts"] == {"Done": 1, "In Progress": 1, "To Do": 0, "cancelled": 0, "unknown": 0}


@pytest.mark.asyncio
async def test_get_sprint_issues_marks_truncation_when_cap_hit(temp_db):
    class FakeJiraClient:
        def __init__(self):
            self.pages = []
            for page_idx in range(11):
                page = []
                for item_idx in range(100):
                    global_idx = page_idx * 100 + item_idx
                    if global_idx >= 1001:
                        break
                    page.append(
                        {
                            "key": f"SPR-{global_idx + 1}",
                            "fields": {
                                "summary": f"Issue {global_idx + 1}",
                                "status": {"name": "To Do", "statusCategory": {"key": "new"}},
                                "priority": {"name": "Low"},
                                "assignee": {"accountId": f"acc-{global_idx % 3}"},
                                "project": {"key": "SPR"},
                                "duedate": "2026-10-03",
                            },
                        }
                    )
                self.pages.append(page)

        async def search_issues(self, jql, next_page_token=None, max_results=100, expand=None, fields=None):
            page_idx = int(next_page_token or 0)
            page = self.pages[page_idx]
            return {
                "issues": page,
                "nextPageToken": str(page_idx + 1) if page_idx + 1 < len(self.pages) else None,
                "isLast": page_idx + 1 >= len(self.pages),
                "total": 1001,
            }

    registry = build_tool_registry(manager=temp_db, jira_client=FakeJiraClient())
    tool = registry.get("get_sprint_issues")
    assert tool is not None
    result = await tool.fn({"sprint_name": "Sprint 42"})
    assert result["status"] == "INSUFFICIENT_DATA"
    assert result["truncated"] is True
    assert result["value"]["truncated"] is True
    assert result["value"]["partial_total"] == 1000
    assert any("partial_total=1000" in line for line in result["derivation"])


def _seed_active_sprint_issues(temp_db, rows):
    from app.database.repositories import JiraIssueStateRepository

    issue_repo = JiraIssueStateRepository(temp_db)
    for row in rows:
        issue_repo.upsert(
            jira_issue_key=row["key"],
            summary=row["summary"],
            status=row["status"],
            assignee=row.get("assignee"),
            project_key=row["project_key"],
            raw_reference={
                "fields": {
                    "project": {"key": row["project_key"], "name": row.get("project_name") or row["project_key"]},
                    "sprint": [
                        {
                            "id": row["sprint_id"],
                            "name": row["sprint_name"],
                            "state": "active",
                        }
                    ],
                }
            },
        )


@pytest.mark.asyncio
async def test_get_active_sprints_multiple_projects_prompts_for_project(temp_db):
    _seed_active_sprint_issues(
        temp_db,
        [
            {"key": "SPR-1", "summary": "A", "status": "In Progress", "project_key": "SPR", "project_name": "Sprint Project", "sprint_id": 11, "sprint_name": "SPR Sprint 1"},
            {"key": "TST-1", "summary": "B", "status": "In Progress", "project_key": "TST", "project_name": "Test Project", "sprint_id": 21, "sprint_name": "TST Sprint 1"},
        ],
    )
    registry = build_tool_registry(manager=temp_db)
    tool = registry.get("get_active_sprints")
    assert tool is not None
    result = await tool.fn({})
    assert result["status"] == "AMBIGUOUS"
    assert result["clarification_kind"] == "project"
    assert {candidate["project_key"] for candidate in result["candidates"]} == {"SPR", "TST"}
    assert {candidate["label"] for candidate in result["candidates"]} == {"Sprint Project", "Test Project"}
    assert any("active_sprint_count" in str(candidate["evidence"][0]).lower() for candidate in result["candidates"])


@pytest.mark.asyncio
async def test_get_active_sprints_named_project_skips_project_question(temp_db):
    _seed_active_sprint_issues(
        temp_db,
        [
            {"key": "SPR-1", "summary": "A", "status": "In Progress", "project_key": "SPR", "project_name": "Sprint Project", "sprint_id": 11, "sprint_name": "SPR Sprint 1"},
            {"key": "SPR-2", "summary": "B", "status": "In Progress", "project_key": "SPR", "project_name": "Sprint Project", "sprint_id": 12, "sprint_name": "SPR Sprint 2"},
            {"key": "TST-1", "summary": "C", "status": "In Progress", "project_key": "TST", "project_name": "Test Project", "sprint_id": 21, "sprint_name": "TST Sprint 1"},
        ],
    )
    registry = build_tool_registry(manager=temp_db)
    tool = registry.get("get_active_sprints")
    assert tool is not None
    result = await tool.fn({"project_key": "SPR"})
    assert result["status"] == "AMBIGUOUS"
    assert result["clarification_kind"] == "sprint"
    assert all(candidate["project_key"] == "SPR" for candidate in result["candidates"])
    assert all(candidate["label"].startswith("SPR Sprint") for candidate in result["candidates"])


@pytest.mark.asyncio
async def test_get_active_sprints_single_project_single_sprint_auto_selects(temp_db):
    _seed_active_sprint_issues(
        temp_db,
        [
            {"key": "SPR-1", "summary": "A", "status": "In Progress", "project_key": "SPR", "project_name": "Sprint Project", "sprint_id": 11, "sprint_name": "SPR Sprint 1"},
        ],
    )
    registry = build_tool_registry(manager=temp_db)
    tool = registry.get("get_active_sprints")
    assert tool is not None
    result = await tool.fn({"project_key": "SPR"})
    assert result["status"] == "AVAILABLE"
    assert result["value"]["name"] == "SPR Sprint 1"
    assert len(result["value"]["candidates"]) == 1


@pytest.mark.asyncio
async def test_get_active_sprints_two_stage_resume_project_then_sprint(temp_db):
    _seed_active_sprint_issues(
        temp_db,
        [
            {"key": "SPR-1", "summary": "A", "status": "In Progress", "project_key": "SPR", "project_name": "Sprint Project", "sprint_id": 11, "sprint_name": "SPR Sprint 1"},
            {"key": "SPR-2", "summary": "B", "status": "In Progress", "project_key": "SPR", "project_name": "Sprint Project", "sprint_id": 12, "sprint_name": "SPR Sprint 2"},
            {"key": "TST-1", "summary": "C", "status": "In Progress", "project_key": "TST", "project_name": "Test Project", "sprint_id": 21, "sprint_name": "TST Sprint 1"},
        ],
    )

    class ProjectSprintProvider:
        async def next_agent_step(self, user_goal: str, actor: str, state: AgentState, tools):
            if state.selected_sprint:
                return AgentStep.final(f"Project {state.selected_project} sprint {state.selected_sprint} selected.")
            return AgentStep.tool_calls([ToolCall(tool_name="get_active_sprints", arguments={})], uncertainty=UncertaintyClass.AMBIGUOUS)

    registry = build_tool_registry(manager=temp_db)
    provider = ProjectSprintProvider()
    core = AgentCore(provider=provider, tool_registry=registry, agent_provider=AgentProvider(provider))  # type: ignore[arg-type]
    session_id = "project-sprint-flow"

    first = await core.run("Which sprint should I use?", actor="u1", session_id=session_id)
    assert first["status"] == "NEEDS_CLARIFICATION"
    assert "project" in first["question"].lower()

    second = await core.run("SPR", actor="u1", session_id=session_id)
    assert second["status"] == "NEEDS_CLARIFICATION"
    assert "sprint" in second["question"].lower()

    third = await core.run("SPR Sprint 2", actor="u1", session_id=session_id)
    assert third["status"] == "COMPLETED"
    assert "Project SPR sprint" in third["answer"]


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ["2", "no", "banana"])
async def test_project_clarification_reasks_on_unrecognised_reply(temp_db, reply):
    registry = ToolRegistry()

    class NoOpProvider:
        async def next_agent_step(self, user_goal: str, actor: str, state: AgentState, tools):
            raise AssertionError("Provider should not be called when clarification cannot be matched.")

    core = AgentCore(provider=NoOpProvider(), tool_registry=registry, agent_provider=AgentProvider(NoOpProvider()))  # type: ignore[arg-type]
    session_id = "project-no-match"
    core._sessions[session_id] = {
        "ts": time.time(),
        "state": AgentState(
            user_goal="Which sprint?",
            current_input=reply,
            pending_clarification=AmbiguityQuestion(
                question="Which project do you mean?",
                kind="project",
                candidates=[
                    Candidate(value="TREN", label="Tren Project", evidence=["active_sprint_count=1"]),
                    Candidate(value="TEST", label="Test Project", evidence=["active_sprint_count=1"]),
                ],
            ),
            last_uncertainty=UncertaintyClass.AMBIGUOUS,
        ),
    }

    result = await core.run(reply, actor="u1", session_id=session_id)
    assert result["status"] == "NEEDS_CLARIFICATION"
    assert "didn't recognise" in result["question"].lower()
    assert result["clarification_kind"] == "project"


@pytest.mark.asyncio
async def test_clarification_escapes_to_fresh_issue_request(temp_db):
    async def get_issue(args):
        assert args["issue_key"] == "WSSS-326"
        return {
            "status": ToolResultStatus.AVAILABLE.value,
            "tool": "get_issue",
            "value": {"key": "WSSS-326", "summary": "Assigned work", "assignee": "Alice"},
        }

    class IssueProvider:
        async def next_agent_step(self, user_goal: str, actor: str, state: AgentState, tools):
            if state.known_facts.get("get_issue"):
                issue = state.known_facts["get_issue"]
                return AgentStep.final(f"{issue['key']} assigned to {issue.get('assignee')}.")
            if "WSSS-326" in (state.current_input or ""):
                return AgentStep.tool_calls([ToolCall(tool_name="get_issue", arguments={"issue_key": "WSSS-326"})], uncertainty=UncertaintyClass.KNOWN)
            return AgentStep.tool_calls([ToolCall(tool_name="get_active_sprints", arguments={})], uncertainty=UncertaintyClass.AMBIGUOUS)

    registry = ToolRegistry()
    registry.register("get_issue", ToolSpec(name="get_issue", description="issue"), get_issue)
    async def get_active_sprints(_args):
        return {
            "status": ToolResultStatus.AMBIGUOUS.value,
            "tool": "get_active_sprints",
            "clarification_kind": "sprint",
            "candidates": [
                {"value": "Sprint A", "label": "Sprint A", "evidence": ["active sprint"]},
                {"value": "Sprint B", "label": "Sprint B", "evidence": ["active sprint"]},
            ],
        }

    registry.register("get_active_sprints", ToolSpec(name="get_active_sprints", description="sprints"), get_active_sprints)

    provider = IssueProvider()
    core = AgentCore(provider=provider, tool_registry=registry, agent_provider=AgentProvider(provider))  # type: ignore[arg-type]
    session_id = "escape-new-goal"
    core._sessions[session_id] = {
        "ts": time.time(),
        "state": AgentState(
            user_goal="Is the sprint on track?",
            current_input="Who is assigned WSSS-326?",
            pending_clarification=AmbiguityQuestion(
                question="Which sprint do you mean?",
                kind="sprint",
                candidates=[
                    Candidate(value="Sprint A", label="Sprint A", evidence=["active sprint"]),
                    Candidate(value="Sprint B", label="Sprint B", evidence=["active sprint"]),
                ],
            ),
            last_uncertainty=UncertaintyClass.AMBIGUOUS,
        ),
    }

    result = await core.run("Who is assigned WSSS-326?", actor="u1", session_id=session_id)
    assert result["status"] == "COMPLETED"
    assert any(call["tool"] == "get_issue" for call in result["tools_called"])
    assert "WSSS-326 assigned to Alice." == result["answer"]


@pytest.mark.asyncio
async def test_project_clarification_lowercase_key_matches_without_setting_issue(temp_db):
    _seed_active_sprint_issues(
        temp_db,
        [
            {"key": "TREN-1", "summary": "A", "status": "In Progress", "project_key": "TREN", "project_name": "Tren Project", "sprint_id": 301, "sprint_name": "Tren Sprint 1"},
            {"key": "TEST-1", "summary": "B", "status": "In Progress", "project_key": "TEST", "project_name": "Test Project", "sprint_id": 401, "sprint_name": "Test Sprint 1"},
        ],
    )

    class LowercaseProjectProvider:
        async def next_agent_step(self, user_goal: str, actor: str, state: AgentState, tools):
            if state.selected_project:
                return AgentStep.final(f"project={state.selected_project}; issue={state.selected_issue}; sprint={state.selected_sprint}")
            return AgentStep.tool_calls([ToolCall(tool_name="get_active_sprints", arguments={})], uncertainty=UncertaintyClass.AMBIGUOUS)

    registry = build_tool_registry(manager=temp_db)
    provider = LowercaseProjectProvider()
    core = AgentCore(provider=provider, tool_registry=registry, agent_provider=AgentProvider(provider))  # type: ignore[arg-type]
    session_id = "project-lowercase"
    core._sessions[session_id] = {
        "ts": time.time(),
        "state": AgentState(
            user_goal="Which sprint?",
            current_input="tren",
            pending_clarification=AmbiguityQuestion(
                question="Which project do you mean?",
                kind="project",
                candidates=[
                    Candidate(value="TREN", label="Tren Project", evidence=["active_sprint_count=1"], metadata={"project_key": "TREN", "project_name": "Tren Project"}),
                    Candidate(value="TEST", label="Test Project", evidence=["active_sprint_count=1"], metadata={"project_key": "TEST", "project_name": "Test Project"}),
                ],
            ),
            last_uncertainty=UncertaintyClass.AMBIGUOUS,
        ),
    }

    result = await core.run("tren", actor="u1", session_id=session_id)
    assert result["status"] == "COMPLETED"
    assert "project=TREN" in result["answer"]
    assert "sprint=Tren Sprint 1" in result["answer"]
    assert "issue=None" in result["answer"]


@pytest.mark.asyncio
async def test_get_active_sprints_filters_active_state_and_dedupes_by_sprint_id(temp_db):
    class FakeJiraClient:
        async def search_issues(self, jql, next_page_token=None, max_results=100, expand=None, fields=None):
            assert expand is None
            assert "sprint" in (fields or [])
            if next_page_token is None:
                return {
                    "issues": [
                        {
                            "key": "SPR-1",
                            "fields": {
                                "sprint": [
                                    {"id": 101, "name": "Sprint 10", "state": "active"},
                                    {"id": 102, "name": "Sprint 11", "state": "active"},
                                    {"id": 101, "name": "Sprint 10", "state": "active"},
                                    {"id": 103, "name": "Sprint 12", "state": "future"},
                                ],
                            },
                        }
                    ],
                    "nextPageToken": "page-2",
                    "isLast": False,
                    "total": 1,
                }
            assert next_page_token == "page-2"
            return {"issues": [], "isLast": True, "total": 1}

    registry = build_tool_registry(manager=temp_db, jira_client=FakeJiraClient())
    tool = registry.get("get_active_sprints")
    assert tool is not None
    result = await tool.fn({})
    assert result["status"] == "AMBIGUOUS"
    labels = [candidate["label"] for candidate in result["candidates"]]
    assert labels == ["Sprint 10", "Sprint 11"]
    assert all(candidate["label"] != "Sprint 12" for candidate in result["candidates"])


@pytest.mark.asyncio
async def test_get_active_sprints_truncated_page_cap_returns_ambiguous_candidates(temp_db):
    class FakeJiraClient:
        def __init__(self):
            self.pages = []
            for idx in range(51):
                self.pages.append(
                    {
                        "issues": [
                            {
                                "key": f"SPR-{idx + 1}",
                                "fields": {
                                    "project": {"key": f"PRJ{idx + 1}", "name": f"Project {idx + 1}"},
                                    "sprint": [{"id": idx + 1, "name": f"Sprint {idx + 1}", "state": "active"}],
                                },
                            }
                        ],
                        "nextPageToken": str(idx + 1) if idx < 50 else None,
                        "isLast": idx == 50,
                        "total": 51,
                    }
                )

        async def search_issues(self, jql, next_page_token=None, max_results=100, expand=None, fields=None):
            page_idx = int(next_page_token or 0)
            return self.pages[page_idx]

    registry = build_tool_registry(manager=temp_db, jira_client=FakeJiraClient())
    tool = registry.get("get_active_sprints")
    assert tool is not None
    result = await tool.fn({})
    assert result["status"] == "AMBIGUOUS"
    assert result["truncated"] is True
    assert len(result["candidates"]) == 50


@pytest.mark.asyncio
async def test_get_sprint_issues_returns_partial_data_on_page_error(temp_db):
    class FakeJiraClient:
        async def search_issues(self, jql, next_page_token=None, max_results=100, expand=None, fields=None):
            if next_page_token is None:
                return {
                    "issues": [
                        {
                            "key": "SPR-1",
                            "fields": {
                                "summary": "First page issue",
                                "status": {"name": "Done", "statusCategory": {"key": "done"}},
                                "priority": {"name": "High"},
                                "assignee": {"accountId": "acc-a", "displayName": "Alice"},
                                "project": {"key": "SPR"},
                            },
                        }
                    ],
                    "nextPageToken": "page-2",
                    "isLast": False,
                    "total": 2,
                }
            raise RuntimeError("page fetch failed")

    registry = build_tool_registry(manager=temp_db, jira_client=FakeJiraClient())
    tool = registry.get("get_sprint_issues")
    assert tool is not None
    result = await tool.fn({"sprint_name": "Sprint 42"})
    assert result["status"] == "INSUFFICIENT_DATA"
    assert result["value"]["partial_total"] == 1
    assert result["value"]["issues"][0]["project"] == "SPR"
    assert "page fetch failed" in result["reason"].lower()
    assert any("partial_total=1" in line for line in result["derivation"])


@pytest.mark.asyncio
async def test_inferable_requires_evidence_and_retains_derivation():
    captured = []

    class InferableProvider:
        async def next_agent_step(self, user_goal: str, actor: str, state: AgentState, tools):
            if state.last_tool_results.get("tools_called"):
                captured.append(state.inferable_facts.copy())
                return AgentStep.final("done")
            return AgentStep.tool_calls([ToolCall(tool_name="capacity_workload_summary", arguments={})])

    async def infer_hours(_args):
        return {
            "status": ToolResultStatus.INFERABLE.value,
            "tool": "capacity_workload_summary",
            "value": {"remaining_hours": 10},
            "derivation": ["capacity 40h - committed 30h = 10h remaining"],
            "reason": "Derived remaining capacity",
        }

    registry = ToolRegistry()
    registry.register("capacity_workload_summary", ToolSpec(name="capacity_workload_summary", description="capacity"), infer_hours)
    provider = InferableProvider()
    core = AgentCore(provider=provider, tool_registry=registry, agent_provider=AgentProvider(provider))  # type: ignore[arg-type]

    res = await core.run("How much time is left?", actor="u1", session_id="infer1")
    assert res["status"] == "COMPLETED"
    assert captured
    inferable = captured[0]["capacity_workload_summary"]
    assert inferable["value"]["remaining_hours"] == 10
    assert inferable["derivation"] == ["capacity 40h - committed 30h = 10h remaining"]


@pytest.mark.asyncio
async def test_inferable_without_derivation_fails_closed():
    async def bad_infer(_args):
        return {
            "status": ToolResultStatus.INFERABLE.value,
            "tool": "capacity_workload_summary",
            "value": {"remaining_hours": 10},
        }

    registry = ToolRegistry()
    registry.register("capacity_workload_summary", ToolSpec(name="capacity_workload_summary", description="capacity"), bad_infer)
    provider = ScriptedProvider([AgentStep.tool_calls([ToolCall(tool_name="capacity_workload_summary", arguments={})])])
    core = AgentCore(provider=provider, tool_registry=registry, agent_provider=AgentProvider(provider))  # type: ignore[arg-type]

    res = await core.run("How much time is left?", actor="u1", session_id="infer2")
    assert res["status"] == "FAILURE"
    assert "derivation" in res["error"].lower()


@pytest.mark.asyncio
async def test_issue_search_zero_and_multiple_matches_behave_deterministically():
    async def zero(_args):
        return {"status": ToolResultStatus.EMPTY.value, "tool": "search_issues", "reason": "No matches."}

    async def many(_args):
        return {
            "status": ToolResultStatus.AMBIGUOUS.value,
            "tool": "search_issues",
            "candidates": [
                {"value": "WSSS-1", "label": "Login bug", "evidence": ["summary"]},
                {"value": "WSSS-2", "label": "Login timeout", "evidence": ["summary"]},
            ],
        }

    zero_registry = ToolRegistry()
    zero_registry.register("search_issues", ToolSpec(name="search_issues", description="search"), zero)
    zero_provider = ScriptedProvider([AgentStep.tool_calls([ToolCall(tool_name="search_issues", arguments={"query": "login"})])])
    zero_core = AgentCore(provider=zero_provider, tool_registry=zero_registry, agent_provider=AgentProvider(zero_provider))  # type: ignore[arg-type]
    zero_res = await zero_core.run("Find login issue", actor="u1")
    assert zero_res["status"] == "FAILURE"
    assert "couldn't verify" in zero_res["error"].lower()

    many_registry = ToolRegistry()
    many_registry.register("search_issues", ToolSpec(name="search_issues", description="search"), many)
    many_provider = ScriptedProvider([AgentStep.tool_calls([ToolCall(tool_name="search_issues", arguments={"query": "login"})])])
    many_core = AgentCore(provider=many_provider, tool_registry=many_registry, agent_provider=AgentProvider(many_provider))  # type: ignore[arg-type]
    many_res = await many_core.run("Find login issue", actor="u1")
    assert many_res["status"] == "NEEDS_CLARIFICATION"
    assert "Login bug" in str(many_res["candidates"]) and "Login timeout" in str(many_res["candidates"])


@pytest.mark.asyncio
async def test_provider_exception_and_malformed_output_fail_closed():
    class ExplodingProvider:
        async def next_agent_step(self, user_goal: str, actor: str, state: AgentState, tools):
            raise RuntimeError("boom")

    core = AgentCore(provider=ExplodingProvider(), tool_registry=ToolRegistry(), agent_provider=AgentProvider(ExplodingProvider()))  # type: ignore[arg-type]
    res = await core.run("goal", actor="u1")
    assert res["status"] == "FAILURE"
    assert "provider failure" in res["error"].lower()

    class MalformedProvider:
        async def next_agent_step(self, user_goal: str, actor: str, state: AgentState, tools):
            return {"kind": "TOOL_CALL", "next_tool_calls": []}  # not an AgentStep

    core2 = AgentCore(provider=MalformedProvider(), tool_registry=ToolRegistry(), agent_provider=AgentProvider(MalformedProvider()))  # type: ignore[arg-type]
    res2 = await core2.run("goal", actor="u1")
    assert res2["status"] == "FAILURE"
    assert "malformed agent step" in res2["error"].lower()


@pytest.mark.asyncio
async def test_invalid_args_and_tool_exception_fail_closed():
    async def bad_args(args):
        if "required" not in args:
            raise ValueError("required argument missing")
        raise RuntimeError("tool error")

    registry = ToolRegistry()
    registry.register("bad_tool", ToolSpec(name="bad_tool", description="bad"), bad_args)
    provider = ScriptedProvider([AgentStep.tool_calls([ToolCall(tool_name="bad_tool", arguments={})])])
    core = AgentCore(provider=provider, tool_registry=registry, agent_provider=AgentProvider(provider))  # type: ignore[arg-type]
    res = await core.run("goal", actor="u1")
    assert res["status"] == "FAILURE"
    assert "failed" in res["error"].lower()


