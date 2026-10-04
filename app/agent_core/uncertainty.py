from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.agent_core.agent_models import AmbiguityQuestion, Candidate, UncertaintyClass


class UncertaintyModel:
    """
    Minimal Known / Inferable / Unknown / Ambiguous model.

    The semantic meaning is enforced by AgentCore using tool results:
    - If tool results provide exactly one match for an entity: Known.
    - If tool results provide multiple matches: Ambiguous -> clarification.
    - If required fields are absent: Unknown -> clarification.
    - Inferable is allowed only when derivation is explicitly recorded in evidence.
    """

    @staticmethod
    def classify_single_match(
        entity_name: str,
        candidates: List[Candidate],
        unknown_question: str,
    ) -> Tuple[UncertaintyClass, Optional[AmbiguityQuestion]]:
        if len(candidates) == 1:
            return UncertaintyClass.KNOWN, None
        if len(candidates) > 1:
            aq = AmbiguityQuestion(question=unknown_question, candidates=candidates)
            return UncertaintyClass.AMBIGUOUS, aq
        # zero candidates => unknown
        aq = AmbiguityQuestion(question=unknown_question, candidates=[])
        return UncertaintyClass.UNKNOWN, aq

    @staticmethod
    def record_inferable(key: str, value: Any, derivation: List[str]) -> Dict[str, Any]:
        return {"value": value, "derivation": derivation}

