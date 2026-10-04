from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional

from app.agent_core.agent_models import ToolSpec


ToolFn = Callable[[Dict[str, Any]], Awaitable[Any]]


@dataclass(frozen=True)
class RegisteredTool:
    spec: ToolSpec
    fn: ToolFn


class ToolRegistry:
    """Registry of read-only PM tools with deterministic wrappers."""

    def __init__(self) -> None:
        self._tools: Dict[str, RegisteredTool] = {}

    def register(self, tool_name: str, spec: ToolSpec, fn: ToolFn) -> None:
        self._tools[tool_name] = RegisteredTool(spec=spec, fn=fn)

    def get(self, tool_name: str) -> Optional[RegisteredTool]:
        return self._tools.get(tool_name)

    def list_specs(self) -> Dict[str, ToolSpec]:
        return {name: rt.spec for name, rt in self._tools.items()}

