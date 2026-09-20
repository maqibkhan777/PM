"""Deterministic factual grounding checks for AI Attention Analysis.

Evaluates whether the AI:
1. Referenced only issue keys that actually existed in the input context.
2. Did not invent nonexistent issue keys, assignees, or dates.
3. Preserved unassigned status without fabricating assignees.
4. Maintained valid confidence bounds [0.0, 1.0].
5. Strictly respected human review requirement (requires_human_review = True).
"""

from dataclasses import dataclass, field
from typing import List, Optional, Set
from app.services.ai.models import PMAttentionAnalysis
from app.services.ai.evaluation.scenarios import ScenarioFixture


@dataclass
class GroundingResult:
    is_grounded: bool
    scenario_id: str
    referenced_keys: List[str] = field(default_factory=list)
    unauthorized_keys: List[str] = field(default_factory=list)
    missing_expected_keys: List[str] = field(default_factory=list)
    fabricated_assignees: List[str] = field(default_factory=list)
    confidence_valid: bool = True
    human_review_preserved: bool = True
    violations: List[str] = field(default_factory=list)


class GroundingValidator:
    """Validates that a PMAttentionAnalysis strictly adheres to the factual bounds of its fixture scenario."""

    @classmethod
    def validate(
        cls,
        analysis: PMAttentionAnalysis,
        scenario: ScenarioFixture,
    ) -> GroundingResult:
        violations: List[str] = []

        # 1. Collect all known issue keys present in input context
        context_keys: Set[str] = set()
        meta = scenario.context.metadata or {}
        for category in ("stale_items", "overdue_items", "reopened_items", "unassigned_items"):
            for item in meta.get(category, []):
                k = item.get("key")
                if k:
                    context_keys.add(k.upper().strip())

        # 2. Collect issue keys referenced in attention items
        referenced_keys: List[str] = []
        unauthorized_keys: List[str] = []
        fabricated_assignees: List[str] = []

        for item in analysis.attention_items:
            key = item.issue_key.upper().strip()
            referenced_keys.append(key)
            if key not in context_keys:
                unauthorized_keys.append(key)
                violations.append(f"AI invented issue key '{key}' not present in input context.")

            # If the original item in unassigned_items was unassigned, AI must not fabricate an assignee
            for un in meta.get("unassigned_items", []):
                if un.get("key", "").upper().strip() == key:
                    if item.assignee and item.assignee.strip().lower() not in ("null", "none", "unassigned", ""):
                        fabricated_assignees.append(f"{key}: {item.assignee}")
                        violations.append(
                            f"AI invented assignee '{item.assignee}' for unassigned issue {key}."
                        )

        # 3. Check for specific forbidden inventions
        analysis_text = f"{analysis.summary} {analysis.recommendation} {' '.join(analysis.evidence)}"
        for forbidden in scenario.forbidden_inventions:
            if forbidden.upper() in analysis_text.upper():
                violations.append(f"AI mentioned forbidden invention token '{forbidden}'.")

        # 4. If scenario had expected keys, ensure they are represented (if items should be flagged)
        missing_expected_keys: List[str] = []
        if scenario.should_flag_items:
            expected_set = {k.upper().strip() for k in scenario.expected_issue_keys}
            ref_set = set(referenced_keys)
            missing_expected_keys = list(expected_set - ref_set)
            if missing_expected_keys:
                violations.append(
                    f"AI omitted expected attention items: {', '.join(missing_expected_keys)}"
                )
        else:
            # If scenario expected 0 items, flag if AI invented items
            if len(analysis.attention_items) > 0:
                violations.append(
                    f"Scenario '{scenario.scenario_id}' expected 0 flagged items, but AI flagged {len(analysis.attention_items)} items."
                )

        # 5. Check confidence bounds
        confidence_valid = 0.0 <= analysis.confidence <= 1.0
        if not confidence_valid:
            violations.append(f"Analysis confidence {analysis.confidence} out of range [0.0, 1.0].")

        for idx, it in enumerate(analysis.attention_items):
            if not (0.0 <= it.confidence <= 1.0):
                confidence_valid = False
                violations.append(f"Item {idx} confidence {it.confidence} out of range [0.0, 1.0].")

        # 6. Check human review requirement
        human_review_preserved = bool(analysis.requires_human_review)
        if not human_review_preserved:
            violations.append("requires_human_review is False; AI output must require human review.")

        is_grounded = len(violations) == 0

        return GroundingResult(
            is_grounded=is_grounded,
            scenario_id=scenario.scenario_id,
            referenced_keys=referenced_keys,
            unauthorized_keys=unauthorized_keys,
            missing_expected_keys=missing_expected_keys,
            fabricated_assignees=fabricated_assignees,
            confidence_valid=confidence_valid,
            human_review_preserved=human_review_preserved,
            violations=violations,
        )
