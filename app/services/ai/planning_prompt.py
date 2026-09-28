"""Deterministic Planning Prompt Builder for Phase 4B.

Formats bounded PlanningContext facts into a compact, structured prompt for AI planning reasoning.

CRITICAL INVARIANTS:
1. Pure prompt compilation: does NOT execute AI models or network requests.
2. Explicitly communicates source of truth rules: AI may reason on facts but must NOT invent facts.
3. Explicitly flags context truncation and bounded exclusions when present.
4. Requires strictly structured JSON adhering to PlanningProposal schema.
5. Injects stable prompt version: PLANNING_PROMPT_VERSION = "planning-v1".
"""

import json
from typing import Any, Dict, List
from app.config.settings import settings
from app.core.models.planning import PlanningContext
from app.utils.logger import redact_text, sanitize_dict

PLANNING_PROMPT_VERSION = "planning-v1"

SYSTEM_PROMPT_PLANNING = """You are an expert Project Management Decision Support & Planning AI Assistant.
You receive a compact, deterministic PlanningContext representing the factual operational state of a software engineering team.

CRITICAL AUTHORITY & GROUNDING RULES:
1. The PlanningContext is your EXCLUSIVE source of ground-truth facts (resources, queues, capacities, tasks, dependencies, artifacts, and projections).
2. You must NEVER invent issue keys, resources, capacity figures, dependencies, or artifacts not present in the context.
3. Every task proposal must reference an existing issue_key present in the context. Keep the list of proposed tasks focused (maximum 15 tasks).
4. Every sequencing proposal must reference an existing issue_key present in the context.
5. Every risk signal must cite a valid PlanningRiskType (CAPACITY_RISK, DEPENDENCY_RISK, DEADLINE_RISK, ESTIMATION_UNCERTAINTY, DATA_QUALITY_RISK, ARTIFACT_HANDOFF_RISK, SCHEDULE_DRIFT_RISK, OTHER). Maximum 5 risk signals.
6. Every evidence reference must cite a valid EvidenceType (RESOURCE_HISTORY, CURRENT_QUEUE, CAPACITY, TASK_ESTIMATE, TASK_COMPLEXITY, DEPENDENCY, ARTIFACT, TEAM_SCHEDULE, BOTTLENECK, DATA_QUALITY, OTHER).
7. You must NEVER convert advisory artifact relationships into confirmed HARD_BLOCK dependencies.
8. You must NEVER override or ignore HARD_BLOCK dependencies.
9. All duration estimates must be explicit numbers in units of "hours".
10. All proposed dates (proposed_start_date, proposed_due_date) are ADVISORY PROPOSALS ONLY, formatted strictly as YYYY-MM-DD. They are NOT committed Jira dates.
11. requires_human_review MUST be true on the root proposal, on every task proposal, on every risk signal, and on every assumption.
12. If information is missing or uncertain, represent it explicitly through lower confidence, assumptions, or risk signals.
13. Keep all rationales, summaries, and explanations CONCISE (1-2 sentences per item) to prevent output truncation.

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
  "summary": "<concise executive summary of proposed planning rationale and trade-offs>",
  "task_proposals": [
    {
      "issue_key": "<exact Jira issue key from context>",
      "proposed_estimate": null or {
        "value": <float hours>,
        "unit": "hours",
        "confidence": <float 0.0 to 1.0>,
        "rationale": "<grounded short rationale>",
        "evidence_references": [
          {
            "evidence_type": "<valid EvidenceType>",
            "source_identifier": "<source key e.g. issue key or resource id>",
            "description": "<short evidence detail>",
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
      "risk_reason": null or "<short risk explanation>",
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
      "rationale": "<short sequencing rationale>",
      "evidence_references": [...],
      "confidence": <float 0.0 to 1.0>
    }
  ],
  "risk_signals": [
    {
      "risk_type": "<valid PlanningRiskType>",
      "issue_key": null or "<exact issue key>",
      "severity": "LOW" | "MEDIUM" | "HIGH",
      "explanation": "<short operational risk explanation>",
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
  "evidence_references": []
}

IMPORTANT:
- Output ONLY valid JSON, no markdown formatting (do NOT wrap in ```json ... ```).
- Keep output compact. Never invent issue keys or assignments.
"""

SYSTEM_PROMPT_PLANNING_COMPACT = """You are an expert Project Management Decision Support & Planning AI Assistant.
Produce a strictly bounded, ultra-compact PlanningProposal JSON adhering to the PlanningProposal schema.

RULES:
1. Ground truth: Use ONLY tasks and resources in the context.
2. Max 5 key task proposals and max 3 risk signals.
3. Keep all summary and rationale text under 100 characters.
4. requires_human_review MUST be true throughout.
5. Output raw valid JSON ONLY with NO surrounding markdown backticks.
"""


class PlanningPromptBuilder:
    """Compiles deterministic PlanningContext into a sanitized, bounded prompt."""

    @classmethod
    def format_compact_context(cls, context: PlanningContext, max_tasks: int = 25, max_summary_chars: int = 120) -> Dict[str, Any]:
        """Convert a PlanningContext into an efficient, token-compact representation for LLM ingestion."""
        # 1. Scope and Anchor
        compact: Dict[str, Any] = {
            "context_version": context.context_version,
            "anchor_date": context.anchor_date,
            "horizon_working_days": context.planning_horizon_working_days,
            "team_group": context.team_group,
            "summary": {
                "total_tasks": len(context.tasks),
                "total_resources": len(context.resources),
            }
        }

        # 2. Resources (compact summary)
        compact_resources = []
        for r in context.resources[:getattr(settings, "PLANNING_MAX_CONTEXT_RESOURCES", 10)]:
            compact_resources.append({
                "resource_id": r.resource_id,
                "display_name": r.display_name,
                "role": r.role,
                "available_capacity_hours": r.available_capacity_hours,
                "remaining_effort_hours": r.remaining_effort_hours,
                "capacity_state": r.capacity_state.value if hasattr(r.capacity_state, "value") else str(r.capacity_state),
                "workload_pressure": r.workload_pressure,
            })
        compact["resources"] = compact_resources

        # 3. Tasks (compact representation with bounded summary and explicit exclusion counts)
        total_tasks_count = len(context.tasks)
        limit_tasks = max(1, min(max_tasks, getattr(settings, "PLANNING_MAX_CONTEXT_TASKS", 25)))
        included_tasks = context.tasks[:limit_tasks]
        excluded_count = max(0, total_tasks_count - len(included_tasks))

        compact["tasks_included_count"] = len(included_tasks)
        compact["tasks_excluded_count"] = excluded_count
        if excluded_count > 0:
            compact["tasks_exclusion_note"] = (
                f"{excluded_count} additional active backlog tasks were excluded to fit bounded context limit of {limit_tasks} tasks."
            )

        compact_tasks = []
        for t in included_tasks:
            summary_text = t.summary or ""
            if len(summary_text) > max_summary_chars:
                summary_text = summary_text[:max_summary_chars] + "..."
            
            compact_tasks.append({
                "issue_key": t.issue_key,
                "summary": summary_text,
                "status": t.status,
                "priority": t.priority,
                "assignee": t.assigned_resource_name or t.assigned_resource_id,
                "estimated_remaining_hours": t.estimated_remaining_hours,
                "due_date": t.due_date,
                "is_overdue": t.is_overdue,
                "is_blocked": t.is_blocked,
                "predecessors": t.predecessor_keys if hasattr(t, "predecessor_keys") else [],
                "successors": t.successor_keys if hasattr(t, "successor_keys") else [],
            })
        compact["tasks"] = compact_tasks

        # 4. Dependencies
        if context.dependencies:
            compact["hard_dependencies"] = [
                f"{d.source_issue_key} -> {d.target_issue_key}"
                for d in context.dependencies[:20]
                if getattr(d, "classification", None) == "HARD_BLOCK" or getattr(d, "classification", None) is None
            ]

        # 5. Bottlenecks
        bottlenecks = getattr(context, "bottlenecks", None)
        if not bottlenecks and hasattr(context, "schedule") and hasattr(context.schedule, "bottlenecks"):
            bottlenecks = context.schedule.bottlenecks

        if bottlenecks:
            compact["bottlenecks"] = [
                {"resource": b.resource_display_name, "severity": b.severity, "reason": b.reason}
                for b in bottlenecks[:5]
            ]

        return compact

    @classmethod
    def build_messages(cls, context: PlanningContext, compact_mode: bool = False) -> List[Dict[str, str]]:
        """Build chat completion messages from PlanningContext."""
        max_summary_len = getattr(settings, "PLANNING_MAX_TASK_SUMMARY_CHARS", 120)
        max_tasks = getattr(settings, "PLANNING_MAX_CONTEXT_TASKS", 25)
        
        compact_dict = cls.format_compact_context(context, max_tasks=max_tasks, max_summary_chars=max_summary_len)
        sanitized_context = sanitize_dict(compact_dict)

        user_content_parts = []
        
        # Explicit truncation warning if applicable
        if context.truncation and context.truncation.is_truncated:
            reasons_str = "; ".join(context.truncation.truncation_reasons)
            user_content_parts.append(
                f"NOTE: The provided PlanningContext was bounded/truncated: {reasons_str}. "
                "Do NOT assume omitted tasks or resources do not exist."
            )

        if compact_dict.get("tasks_excluded_count", 0) > 0:
            user_content_parts.append(
                f"NOTE: {compact_dict['tasks_excluded_count']} additional active tasks outside top priority "
                f"were excluded from prompt context (total active: {compact_dict['summary']['total_tasks']})."
            )

        user_content_parts.append(
            f"Operational Planning Context (anchor_date: {context.anchor_date}, horizon: {context.planning_horizon_working_days} working days, team/scope: {context.team_group or 'Cluster'}):\n"
            f"{json.dumps(sanitized_context, indent=2)}"
        )

        sys_prompt = SYSTEM_PROMPT_PLANNING_COMPACT if compact_mode else SYSTEM_PROMPT_PLANNING

        return [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": "\n\n".join(user_content_parts)},
        ]
