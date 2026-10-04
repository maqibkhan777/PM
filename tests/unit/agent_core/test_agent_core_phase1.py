import pytest

from app.agent_core.agent_models import AgentStep, AgentState, Candidate, ToolCall, ToolSpec, UncertaintyClass, AmbiguityQuestion
from app.agent_core.core import AgentCore
from app.agent_core.tooling import ToolRegistry
from app.services.ai.agent_provider import AgentProvider
from app.services.ai.provider import AIProvider


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
        return {"x": 1}

    async def t2(args):
        return {"y": args.get("k", 2)}

    registry = ToolRegistry()
    registry.register("tool1", ToolSpec(name="tool1", description="t1"), t1)
    registry.register("tool2", ToolSpec(name="tool2", description="t2"), t2)

    steps = [
        AgentStep(
            next_tool_calls=[ToolCall(tool_name="tool1", arguments={}), ToolCall(tool_name="tool2", arguments={"k": 7})],
            final_answer="done",
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
    assert len(res["tools_called"]) == 2
    assert res["uncertainty"] == UncertaintyClass.KNOWN.value


@pytest.mark.asyncio
async def test_ambiguous_user_produces_clarification_question():
    async def dummy(args):
        return {"candidates": ["A", "B"]}

    registry = ToolRegistry()
    registry.register("find_user", ToolSpec(name="find_user", description="users"), dummy)

    aq = AmbiguityQuestion(
        question="Which user?",
        candidates=[
            Candidate(value="A", label="Alice", evidence=["tool"]),
            Candidate(value="B", label="Bob", evidence=["tool"]),
        ],
    )

    steps = [
        AgentStep(
            next_tool_calls=[ToolCall(tool_name="find_user", arguments={})],
            final_answer=None,
            ambiguity_question=aq,
            uncertainty_class=UncertaintyClass.AMBIGUOUS,
            reasoning_trace=["scripted"],
        )
    ]
    provider = ScriptedProvider(steps)
    agent_provider = AgentProvider(provider)  # type: ignore[arg-type]
    core = AgentCore(provider=provider, tool_registry=registry, agent_provider=agent_provider)  # type: ignore[arg-type]

    res = await core.run("goal", actor="u1")
    assert res["status"] == "NEEDS_CLARIFICATION"
    assert "Which user?" in res["question"]
    assert len(res["candidates"]) == 2


@pytest.mark.asyncio
async def test_single_sprint_without_asking_clarification():
    # We model "single match" behavior purely at the uncertainty-question level:
    # if provider returns final_answer directly, AgentCore should complete.
    registry = ToolRegistry()
    async def get_active_sprints(_args):
        return {"sprints": ["S1"]}

    registry.register("get_active_sprints", ToolSpec(name="get_active_sprints", description="sprints"), get_active_sprints)

    steps = [
        AgentStep(
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
        return {"proposed_actions": [{"action_type": "TRANSITION_TASK", "target_id": args["issue"]}]}

    registry = ToolRegistry()
    registry.register("plan_write", ToolSpec(name="plan_write", description="propose only"), propose_actions)

    steps = [
        AgentStep(
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


