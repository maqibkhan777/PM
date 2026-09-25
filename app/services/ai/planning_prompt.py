"""Deterministic Planning Prompt Builder for Phase 4B.

Formats bounded PlanningContext facts into a compact, structured prompt for AI planning reasoning.

CRITICAL INVARIANTS:
1. Pure prompt compilation: does NOT execute AI models or network requests.
2. Explicitly communicates source of truth rules: AI may reason on facts but must NOT invent facts.
3. Explicitly flags context truncation when present.
4. Requires strictly structured JSON adhering to PlanningProposal schema.
5. Injects stable prompt version: PLANNING_PROMPT_VERSION = "planning-v1".
"""

import json
from typing import Any, Dict
from app.core.models.planning import PlanningContext
from app.utils.logger import redact_text, sanitize_dict

PLANNING_PROMPT_VERSION = "planning-v1"

SYSTEM_PROMPT_PLANNING = """You are an expert Project Management Decision Support & Planning AI Assistant.
You receive a bounded, deterministic PlanningContext representing the factual operational state of a software engineering team.

CRITICAL AUTHORITY & GROUNDING RULES:
1. The PlanningContext is your EXCLUSIVE source of ground-truth facts (resources, queues, capacities, tasks, dependencies, artifacts, and projections).
2. You must NEVER invent issue keys, resources, capacity figures, dependencies, or artifacts not present in the context.
3. Every task proposal must reference an existing issue_key present in the context.
4. Every sequencing proposal must reference an existing issue_key present in the context.
5. Every risk signal must cite a valid PlanningRiskType (CAPACITY_RISK, DEPENDENCY_RISK, DEADLINE_RISK, ESTIMATION_UNCERTAINTY, DATA_QUALITY_RISK, ARTIFACT_HANDOFF_RISK, SCHEDULE_DRIFT_RISK, OTHER).
6. Every evidence reference must cite a valid EvidenceType (RESOURCE_HISTORY, CURRENT_QUEUE, CAPACITY, TASK_ESTIMATE, TASK_COMPLEXITY, DEPENDENCY, ARTIFACT, TEAM_SCHEDULE, BOTTLENECK, DATA_QUALITY, OTHER).
7. You must NEVER convert advisory artifact relationships into confirmed HARD_BLOCK dependencies.
8. You must NEVER override or ignore HARD_BLOCK dependencies.
9. All duration estimates must be explicit numbers in units of "hours".
10. All proposed dates (proposed_start_date, proposed_due_date) are ADVISORY PROPOSALS ONLY, formatted strictly as YYYY-MM-DD. They are NOT committed Jira dates.
11. requires_human_review MUST be true on the root proposal, on every task proposal, on every risk signal, and on every assumption.
12. If information is missing or uncertain, represent it explicitly through lower confidence, assumptions, or risk signals.

OUTPUT FORMAT:
You MUST output ONLY a valid JSON object matching the PlanningProposal schema:
{
  "proposal_version": "proposal-v1",
  "generated_at": "<ISO8601 string>",
  "context_version": "<context version e.g. planning-v1>",
  "anchor_date": "<YYYY-MM-DD matching context anchor_date>",
  "planning_horizon_working_days": <integer matching context>,
  "requires_human_review": true,
  "overall_confidence": <float between 0.0 and 1.0>,
  "summary": "<clear executive summary of proposed planning rationale and trade-offs>",
  "task_proposals": [
    {
      "issue_key": "<exact Jira issue key from context>",
      "proposed_estimate": null or {
        "value": <float hours>,
        "unit": "hours",
        "confidence": <float 0.0 to 1.0>,
        "rationale": "<grounded rationale>",
        "evidence_references": [
          {
            "evidence_type": "<valid EvidenceType>",
            "source_identifier": "<source key e.g. issue key or resource id>",
            "description": "<evidence detail>",
            "relevance": "DIRECT"
          }
        ]
      },
      "proposed_start_date": null or "<YYYY-MM-DD>",
      "proposed_due_date": null or "<YYYY-MM-DD>",
      "date_confidence": <float 0.0 to 1.0>,
      "sequencing_position": null or <integer >= 1>,
      "proposed_predecessors": ["<issue key>", ...],
      "proposed_successors": ["<issue key>", ...],
      "risk_level": "LOW" | "MEDIUM" | "HIGH",
      "risk_reason": null or "<risk explanation>",
      "evidence_references": [...],
      "assumptions": [
        {
          "statement": "<explicit assumption statement>",
          "confidence": <float 0.0 to 1.0>,
          "requires_human_review": true
        }
      ],
      "requires_human_review": true
    }
  ],
  "sequencing_proposals": [
    {
      "issue_key": "<exact issue key>",
      "position": <integer >= 1>,
      "rationale": "<sequencing rationale>",
      "evidence_references": [...],
      "confidence": <float 0.0 to 1.0>
    }
  ],
  "risk_signals": [
    {
      "risk_type": "<valid PlanningRiskType>",
      "issue_key": null or "<exact issue key>",
      "severity": "LOW" | "MEDIUM" | "HIGH",
      "explanation": "<operational risk explanation>",
      "evidence_references": [...],
      "confidence": <float 0.0 to 1.0>,
      "requires_human_review": true
    }
  ],
  "assumptions": [
    {
      "statement": "<planning assumption>",
      "evidence_references": [...],
      "confidence": <float 0.0 to 1.0>,
      "requires_human_review": true
    }
  ],
  "evidence_references": [...]
}

IMPORTANT:
- Output ONLY valid JSON, no markdown formatting (do NOT wrap in ```json ... ```).
- Never invent issue keys or assignments.
"""


class PlanningPromptBuilder:
    """Compiles deterministic PlanningContext into a sanitized, bounded prompt."""

    @classmethod
    def build_messages(cls, context: PlanningContext) -> list[dict[str, str]]:
        """Build chat completion messages from PlanningContext."""
        context_dict = context.model_dump(mode="json")
        sanitized_context = sanitize_dict(context_dict)

        user_content_parts = []
        
        # Explicit truncation warning if applicable
        if context.truncation and context.truncation.is_truncated:
            reasons_str = "; ".join(context.truncation.truncation_reasons)
            user_content_parts.append(
                f"NOTE: The provided PlanningContext was bounded/truncated: {reasons_str}. "
                "Do NOT assume omitted tasks or resources do not exist."
            )

        user_content_parts.append(
            f"Operational Planning Context (anchor_date: {context.anchor_date}, horizon: {context.planning_horizon_working_days} working days):\n"
            f"{json.dumps(sanitized_context, indent=2)}"
        )

        return [
            {"role": "system", "content": SYSTEM_PROMPT_PLANNING},
            {"role": "user", "content": "\n\n".join(user_content_parts)},
        ]
