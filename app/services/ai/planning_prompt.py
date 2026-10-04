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
3. Every task proposal in task_proposals must reference an existing issue_key present in the context. Keep the list of proposed tasks focused (maximum 15 tasks).
4. Every sequencing proposal in sequencing_proposals must reference an existing issue_key present in the context.
5. Every risk signal in risk_signals must strictly use "risk_type" (a valid PlanningRiskType: CAPACITY_RISK, DEPENDENCY_RISK, DEADLINE_RISK, ESTIMATION_UNCERTAINTY, DATA_QUALITY_RISK, ARTIFACT_HANDOFF_RISK, SCHEDULE_DRIFT_RISK, OTHER) and "explanation" (non-empty string). Do NOT use fields named "type", "evidence", or "resource_name" in risk_signals. Maximum 5 risk signals.
6. Every evidence reference must cite a valid EvidenceType (RESOURCE_HISTORY, CURRENT_QUEUE, CAPACITY, TASK_ESTIMATE, TASK_COMPLEXITY, DEPENDENCY, ARTIFACT, TEAM_SCHEDULE, BOTTLENECK, DATA_QUALITY, OTHER).
7. You must NEVER convert advisory artifact relationships into confirmed HARD_BLOCK dependencies.
8. You must NEVER override or ignore HARD_BLOCK dependencies.
9. All duration estimates must be explicit numbers in units of "hours".
10. All proposed dates (proposed_start_date, proposed_due_date) are ADVISORY PROPOSALS ONLY, formatted strictly as YYYY-MM-DD. They are NOT committed Jira dates.
11. requires_human_review MUST be true on the root proposal, on every task proposal, on every risk signal, and on every assumption.
12. If information is missing or uncertain, represent it explicitly through lower confidence, assumptions, or risk signals.
13. Keep all rationales, summaries, and explanations CONCISE (1-2 sentences per item) to prevent output truncation.

OUTPUT FORMAT:
You MUST output ONLY a valid JSON object matching the PlanningProposal schema at the top level.
Do NOT wrap the response in an outer {"planning_proposal": ...} or {"type": ...} envelope.
{
  "proposal_version": "proposal-v1",
  "requires_human_review": true,
  "overall_confidence": <float between 0.0 and 1.0>,
  "summary": "<concise non-empty executive summary of proposed planning rationale and trade-offs>",
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
- Output ONLY a single valid JSON object at root level, no markdown formatting (do NOT wrap in ```json ... ```).
- Do NOT wrap inside an outer container key.
- Keep output compact. Never invent issue keys or assignments.
"""

SYSTEM_PROMPT_PLANNING_COMPACT = """You are an expert Project Management Decision Support & Planning AI Assistant.
Produce a strictly bounded, ultra-compact PlanningProposal JSON adhering strictly to the canonical PlanningProposal schema.

RULES:
1. Ground truth: Use ONLY tasks and resources in the context.
2. Canonical field names: Use "task_proposals" (max 5 items, NEVER name this "key_task_proposals"), "risk_signals" (max 3 items), "sequencing_proposals", and "assumptions".
3. For items in "risk_signals", you MUST use "risk_type" (valid PlanningRiskType) and "explanation" (string). NEVER use "type", "evidence", or "resource_name".
4. Provide a concise non-empty "summary" string (under 100 characters).
5. requires_human_review MUST be true throughout.
6. Output raw valid top-level JSON ONLY (no outer wrapping container like {"planning_proposal": ...} and NO surrounding markdown backticks).

SCHEMA SKELETON:
{
  "proposal_version": "proposal-v1",
  "requires_human_review": true,
  "overall_confidence": <float 0.0 to 1.0>,
  "summary": "<concise summary>",
  "task_proposals": [
    {
      "issue_key": "<issue_key>",
      "risk_level": "LOW" | "MEDIUM" | "HIGH",
      "requires_human_review": true
    }
  ],
  "sequencing_proposals": [],
  "risk_signals": [
    {
      "risk_type": "CAPACITY_RISK" | "DEPENDENCY_RISK" | "DEADLINE_RISK" | "ESTIMATION_UNCERTAINTY" | "DATA_QUALITY_RISK" | "ARTIFACT_HANDOFF_RISK" | "SCHEDULE_DRIFT_RISK" | "OTHER",
      "severity": "LOW" | "MEDIUM" | "HIGH",
      "explanation": "<operational explanation>",
      "requires_human_review": true
    }
  ],
  "assumptions": []
}
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

        # Retrieve empirical effort benchmarks if available
        benchmark_recs = {}
        try:
            from app.core.intelligence.effort_retrieval_service import (
                HistoricalEffortBenchmarkRetrievalService,
            )
            retrieval_svc = HistoricalEffortBenchmarkRetrievalService()
            benchmark_recs = retrieval_svc.recommend_effort_for_context(
                tasks=included_tasks,
                default_project_key=context.team_group,
            )
        except Exception:
            pass

        compact_tasks = []
        for t in included_tasks:
            summary_text = t.summary or ""
            if len(summary_text) > max_summary_chars:
                summary_text = summary_text[:max_summary_chars] + "..."
            
            task_dict = {
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
            }
            rec = benchmark_recs.get(t.issue_key)
            if rec and rec.reliability_status in ("USABLE", "LOW_CONFIDENCE"):
                task_dict["historical_effort_benchmark"] = {
                    "p50_hours": rec.p50_effort_hours,
                    "p90_hours": rec.p90_effort_hours,
                    "sample_count": rec.sample_count,
                    "reliability": rec.reliability_status.value,
                    "grouping": rec.grouping_used,
                    "is_fallback": rec.is_fallback_grouping,
                }
            elif rec and rec.reliability_status == "INSUFFICIENT_DATA":
                task_dict["historical_effort_benchmark"] = {
                    "reliability": "INSUFFICIENT_DATA",
                    "sample_count": rec.sample_count,
                    "note": "No manufactured estimate: data is below threshold",
                }

            compact_tasks.append(task_dict)
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
            # Build resource id -> display name map for resolving resource name if available
            res_id_to_name = {
                r.resource_id: r.display_name
                for r in getattr(context, "resources", [])
                if getattr(r, "resource_id", None) and getattr(r, "display_name", None)
            }
            compact_bottlenecks = []
            for b in bottlenecks[:5]:
                b_type = b.type.value if hasattr(b.type, "value") else str(b.type)
                item: Dict[str, Any] = {
                    "type": b_type,
                    "severity": getattr(b, "severity", "MEDIUM"),
                }
                res_id = getattr(b, "affected_resource_id", None)
                if res_id:
                    item["resource_id"] = res_id
                    if res_id in res_id_to_name:
                        item["resource_name"] = res_id_to_name[res_id]
                issue_k = getattr(b, "affected_issue_key", None)
                if issue_k:
                    item["issue_key"] = issue_k
                ev = getattr(b, "evidence", "")
                if ev:
                    item["evidence"] = ev[:150]
                compact_bottlenecks.append(item)
            compact["bottlenecks"] = compact_bottlenecks

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
