"""Deterministic Task Complexity Calculator for Phase A Performance Data Foundation.

Refinement Rule 1: Complexity is determined primarily by intrinsic task characteristics
(issue type, priority, project, components, labels, subtasks, explicit estimates) to prevent
the feedback loop: actual hours -> complexity -> expected hours -> performance evaluation.
"""

from typing import Any, Dict, List, Optional
from app.core.models.performance import TaskComplexity, ConfidenceLevel


class TaskComplexityCalculator:
    """Calculates explainable task complexity score (1 to 5) from task characteristics."""

    @classmethod
    def calculate_complexity(
        cls,
        issue_type: Optional[str] = None,
        priority: Optional[str] = None,
        project_key: Optional[str] = None,
        components: Optional[List[str]] = None,
        labels: Optional[List[str]] = None,
        subtask_count: int = 0,
        original_estimate_seconds: Optional[int] = None,
        summary: Optional[str] = None
    ) -> TaskComplexity:
        """Calculate task complexity (1=Trivial, 2=Small, 3=Medium, 4=Large, 5=Very Large)."""
        score = 3.0  # Baseline Medium
        factors: List[str] = []
        known_attributes = 0

        # 1. Issue Type Evaluation
        itype = (issue_type or "Task").strip().lower()
        if itype:
            known_attributes += 1
            if itype in ("epic", "initiative"):
                score += 2.0
                factors.append(f"issue_type={issue_type} (+2.0: macro initiative)")
            elif itype in ("story", "new feature", "feature"):
                score += 1.0
                factors.append(f"issue_type={issue_type} (+1.0: feature scope)")
            elif itype in ("sub-task", "subtask"):
                score -= 1.0
                factors.append(f"issue_type={issue_type} (-1.0: subtask scope)")
            elif itype in ("bug", "defect"):
                factors.append(f"issue_type={issue_type} (0.0: standard defect baseline)")
            else:
                factors.append(f"issue_type={issue_type} (0.0: standard task baseline)")

        # 2. Priority Evaluation
        prio = (priority or "Medium").strip().lower()
        if prio:
            known_attributes += 1
            if prio in ("highest", "blocker", "critical"):
                score += 1.0
                factors.append(f"priority={priority} (+1.0: critical urgency/complexity)")
            elif prio in ("high", "major"):
                score += 0.5
                factors.append(f"priority={priority} (+0.5: elevated priority)")
            elif prio in ("low", "minor"):
                score -= 0.5
                factors.append(f"priority={priority} (-0.5: low priority)")
            elif prio in ("lowest", "trivial"):
                score -= 1.0
                factors.append(f"priority={priority} (-1.0: trivial priority)")
            else:
                factors.append(f"priority={priority} (0.0: normal priority)")

        # 3. Component Count Evaluation
        comp_list = components or []
        if isinstance(comp_list, list) and comp_list:
            known_attributes += 1
            if len(comp_list) >= 3:
                score += 1.0
                factors.append(f"components_count={len(comp_list)} (+1.0: cross-system scope)")
            elif len(comp_list) == 2:
                score += 0.5
                factors.append(f"components_count=2 (+0.5: multi-component scope)")

        # 4. Labels Keyword Evaluation
        lbl_list = [str(l).strip().lower() for l in (labels or []) if l]
        if lbl_list:
            known_attributes += 1
            complex_keywords = {"refactor", "migration", "architecture", "security", "infra", "performance", "database"}
            trivial_keywords = {"typo", "docs", "documentation", "copy", "quick-fix", "trivial", "minor", "cleanup"}

            has_complex = any(k in lbl_list or any(k in lbl for lbl in lbl_list) for k in complex_keywords)
            has_trivial = any(k in lbl_list or any(k in lbl for lbl in lbl_list) for k in trivial_keywords)

            if has_complex:
                score += 0.5
                factors.append("labels_contain_complex_keywords (+0.5)")
            elif has_trivial:
                score -= 0.5
                factors.append("labels_contain_trivial_keywords (-0.5)")

        # 5. Subtask Count Evaluation
        if subtask_count >= 4:
            score += 1.0
            factors.append(f"subtask_count={subtask_count} (+1.0: high subtask count)")
        elif subtask_count >= 2:
            score += 0.5
            factors.append(f"subtask_count={subtask_count} (+0.5: composite task)")

        # 6. Explicit Jira Estimate Evaluation (if provided by project manager/team)
        if original_estimate_seconds is not None and original_estimate_seconds > 0:
            known_attributes += 1
            est_hours = original_estimate_seconds / 3600.0
            if est_hours >= 40.0:
                score += 1.5
                factors.append(f"jira_estimate={est_hours:.1f}h (+1.5: multi-day/week estimate)")
            elif est_hours >= 16.0:
                score += 0.5
                factors.append(f"jira_estimate={est_hours:.1f}h (+0.5: substantial estimate)")
            elif est_hours <= 2.0:
                score -= 0.5
                factors.append(f"jira_estimate={est_hours:.1f}h (-0.5: quick estimate)")

        # Bounding to integer range 1 to 5
        final_score = int(max(1, min(5, round(score))))

        # Determine confidence
        if known_attributes >= 3:
            confidence = ConfidenceLevel.HIGH
        elif known_attributes >= 2:
            confidence = ConfidenceLevel.MEDIUM
        else:
            confidence = ConfidenceLevel.LOW

        return TaskComplexity(
            complexity_score=final_score,
            factors=factors,
            confidence=confidence
        )
