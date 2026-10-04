from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from app.agent_core.agent_models import AgentState, AgentStep, ToolSpec
from app.services.ai.provider import AIProvider


class AgentProvider:
    """
    Bridge between Agent Core and the existing AI provider abstraction.

    AgentCore expects a provider that decides the next tool calls or returns an answer.
    """

    def __init__(self, ai_provider: AIProvider) -> None:
        self.ai_provider = ai_provider

    async def next_step(
        self,
        user_goal: str,
        actor: str,
        state: AgentState,
        tools: Dict[str, ToolSpec],
    ) -> AgentStep:
        return await self.ai_provider.next_agent_step(
            user_goal=user_goal,
            actor=actor,
            state=state,
            tools=tools,
        )

