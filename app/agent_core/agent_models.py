from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence


class UncertaintyClass(str, Enum):
    KNOWN = "KNOWN"
    INFERABLE = "INFERABLE"
    UNKNOWN = "UNKNOWN"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True)
class Candidate:
    """Represents a possible entity match derived from deterministic tool reads."""

    value: str
    label: str
    evidence: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class AmbiguityQuestion:
    """A narrow clarification question plus the candidates to choose from."""

    question: str
    candidates: List[Candidate]


@dataclass
class AgentState:
    """Short-term, request-scoped state for iterative tool loops."""

    user_goal: str
    context: Dict[str, Any] = field(default_factory=dict)
    known_facts: Dict[str, Any] = field(default_factory=dict)
    inferable_facts: Dict[str, Any] = field(default_factory=dict)
    last_tool_results: Dict[str, Any] = field(default_factory=dict)
    last_uncertainty: Optional[UncertaintyClass] = None
    pending_clarification: Optional[AmbiguityQuestion] = None
    proposed_actions: List[Dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    # JSON-schema-like dict for tool parameters; agent core treats as opaque for now.
    parameters_schema: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolCall:
    tool_name: str
    arguments: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentStep:
    """
    Provider-generated step for the tool loop.

    - If next_tool_calls is non-empty, AgentCore should call those tools and iterate.
    - If final_answer is non-empty, AgentCore should return it to the user.
    - If ambiguity_question is non-null, AgentCore should ask clarification.
    """

    next_tool_calls: List[ToolCall] = field(default_factory=list)
    final_answer: Optional[str] = None
    ambiguity_question: Optional[AmbiguityQuestion] = None

    # Audit/debug fields (no secrets).
    uncertainty_class: Optional[UncertaintyClass] = None
    reasoning_trace: List[str] = field(default_factory=list)

