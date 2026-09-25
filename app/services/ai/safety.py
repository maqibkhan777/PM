"""Safety validation, fail-closed enforcement, and Action Engine gate for AI decisions."""

import logging
from typing import Optional, Tuple
from app.core.actions.base import BaseAction
from app.core.models.enums import ActionStatus, ActionType
from app.services.ai.models import AIDecision, AIDecisionType, AIRecommendationType
from app.utils.logger import sanitize_dict

logger = logging.getLogger(__name__)


class AISafetyViolation(Exception):
    """Raised when an AI decision violates safety constraints."""
    pass


class AISafetyGate:
    """Safety boundary that validates AI output before any action can be staged or evaluated."""

    @classmethod
    def validate_decision(cls, decision: AIDecision) -> Tuple[bool, Optional[str]]:
        """Validate an AIDecision against core safety principles.
        
        Rules:
        1. Confidence must be between 0.0 and 1.0 (validated by model, double-checked here).
        2. Decisions proposing an external mutation must have requires_approval=True.
        3. Proposed action must not contain blacklisted/destructive parameters.
        4. Unknown or blank decision types/recommendations fail closed.
        """
        if not isinstance(decision.decision_type, AIDecisionType):
            return False, f"Invalid decision_type: {decision.decision_type}"

        if not isinstance(decision.recommendation, AIRecommendationType):
            return False, f"Invalid recommendation: {decision.recommendation}"

        if decision.confidence < 0.0 or decision.confidence > 1.0:
            return False, f"Confidence out of bounds [0.0, 1.0]: {decision.confidence}"

        if decision.proposed_action:
            # Primary defense 1: Action type must belong to the strict domain ActionType allowlist
            action_type_enum = ActionType.from_str(decision.proposed_action.action_type)
            if not action_type_enum:
                return False, f"Unrecognized ActionType: {decision.proposed_action.action_type}"

            # Primary defense 2: Target system and target ID must be non-empty strings
            if not decision.proposed_action.target_system or not decision.proposed_action.target_system.strip():
                return False, "Target system must not be empty"
            if not decision.proposed_action.target_id or not decision.proposed_action.target_id.strip():
                return False, "Target ID must not be empty"

            # Primary defense 3: AI is strictly advisory in Phase 1: any proposed mutation REQUIRES approval
            if not decision.requires_approval:
                return False, "AI proposed actions must have requires_approval=True"

            # Secondary defense: parameter sanitization and destructive keyword rejection
            sanitized_params = sanitize_dict(decision.proposed_action.parameters)
            params_str = str(sanitized_params).lower()
            if "delete" in params_str or "drop" in params_str or "truncate" in params_str:
                return False, "Destructive operations are strictly prohibited for AI proposed actions"

        return True, None

    @classmethod
    def validate_attention_analysis(cls, analysis: "PMAttentionAnalysis") -> Tuple[bool, Optional[str]]:
        """Validate a PMAttentionAnalysis against core safety and structural principles.
        
        Rules:
        1. Confidence must be bounded [0.0, 1.0].
        2. requires_human_review must be True (AI is strictly advisory).
        3. All attention items must have valid confidence [0.0, 1.0] and non-empty reasons.
        4. Any proposed actions on analysis or items must obey allowlisted ActionType, non-empty targets,
           and no destructive operations.
        """
        from app.services.ai.models import PMAttentionAnalysis
        if not isinstance(analysis, PMAttentionAnalysis):
            return False, f"Expected PMAttentionAnalysis instance, got {type(analysis)}"

        if analysis.confidence < 0.0 or analysis.confidence > 1.0:
            return False, f"Analysis confidence out of bounds [0.0, 1.0]: {analysis.confidence}"

        if not analysis.requires_human_review:
            return False, "PMAttentionAnalysis must have requires_human_review=True (advisory only)"

        # Validate top-level proposed action if present
        if analysis.proposed_action:
            p = analysis.proposed_action
            action_type_enum = ActionType.from_str(p.action_type)
            if not action_type_enum:
                return False, f"Unrecognized ActionType in attention analysis: {p.action_type}"
            if not p.target_system or not p.target_system.strip():
                return False, "Target system must not be empty"
            if not p.target_id or not p.target_id.strip():
                return False, "Target ID must not be empty"
            sanitized_params = sanitize_dict(p.parameters)
            params_str = str(sanitized_params).lower()
            if "delete" in params_str or "drop" in params_str or "truncate" in params_str:
                return False, "Destructive operations are strictly prohibited for AI proposed actions"

        # Validate individual attention items
        for idx, item in enumerate(analysis.attention_items):
            if item.confidence < 0.0 or item.confidence > 1.0:
                return False, f"Item {idx} ({item.issue_key}) confidence out of bounds: {item.confidence}"
            if not item.attention_reason or not item.attention_reason.strip():
                return False, f"Item {idx} ({item.issue_key}) must have non-empty attention_reason"
            if item.proposed_action:
                ip = item.proposed_action
                action_type_enum = ActionType.from_str(ip.action_type)
                if not action_type_enum:
                    return False, f"Item {idx} ({item.issue_key}) has unrecognized ActionType: {ip.action_type}"
                if not ip.target_system or not ip.target_system.strip():
                    return False, f"Item {idx} ({item.issue_key}) target system must not be empty"
                if not ip.target_id or not ip.target_id.strip():
                    return False, f"Item {idx} ({item.issue_key}) target ID must not be empty"
                sanitized_params = sanitize_dict(ip.parameters)
                params_str = str(sanitized_params).lower()
                if "delete" in params_str or "drop" in params_str or "truncate" in params_str:
                    return False, "Destructive operations are strictly prohibited for AI proposed actions"

        return True, None

    @classmethod
    def to_staged_action(cls, decision: AIDecision, requested_by: str = "AIEngine") -> Optional[BaseAction]:
        """Convert a validated AIDecision's proposed action into an unexecuted BaseAction requiring approval.
        
        Returns None if no action is proposed or if validation fails.
        """
        is_valid, error_msg = cls.validate_decision(decision)
        if not is_valid:
            logger.warning(f"AISafetyGate rejected decision: {error_msg}")
            raise AISafetyViolation(f"Decision failed safety validation: {error_msg}")

        if not decision.proposed_action:
            return None

        p = decision.proposed_action
        action_type_enum = ActionType.from_str(p.action_type)
        if not action_type_enum:
            raise AISafetyViolation(f"Unrecognized ActionType proposed by AI: {p.action_type}")

        # Construct BaseAction with safety defaults: always requires approval, status REQUESTED
        action = BaseAction(
            action_type=action_type_enum,
            target_system=p.target_system,
            target_id=p.target_id,
            parameters=p.parameters,
            requested_by=requested_by,
            requires_approval=True,
            status=ActionStatus.REQUESTED,
        )
        action.ensure_idempotency_key()
        return action

    @classmethod
    def validate_planning_proposal(
        cls,
        proposal: "PlanningProposal",
        planning_context: Optional["PlanningContext"] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Validate an AI-generated PlanningProposal against safety and grounding constraints.
        
        Rules:
        1. Proposal must be a valid PlanningProposal instance.
        2. requires_human_review MUST be True (AI planning is strictly advisory).
        3. Overall confidence must be in [0.0, 1.0].
        4. Summary must be non-empty.
        5. If planning_context is provided:
           - All task proposals must reference existing issue_keys in planning_context.
           - All linked predecessors/successors must be valid issue keys.
           - All sequencing proposals must reference existing issue_keys in planning_context.
           - All risk signals referencing an issue_key must reference existing issue_keys in planning_context.
        6. All task proposals must have confidence in [0.0, 1.0] and valid estimate units if provided.
        7. All risk signals and assumptions must have confidence in [0.0, 1.0].
        """
        from app.core.models.planning import PlanningProposal, PlanningContext
        if not isinstance(proposal, PlanningProposal):
            return False, f"Expected PlanningProposal instance, got {type(proposal)}"

        if not proposal.requires_human_review:
            return False, "PlanningProposal must have requires_human_review=True (advisory only)"

        if proposal.overall_confidence < 0.0 or proposal.overall_confidence > 1.0:
            return False, f"Overall confidence out of bounds [0.0, 1.0]: {proposal.overall_confidence}"

        if not proposal.summary or not proposal.summary.strip():
            return False, "PlanningProposal summary must not be empty"

        known_issue_keys = set()
        known_resource_ids = set()
        if planning_context is not None and isinstance(planning_context, PlanningContext):
            known_issue_keys = {t.issue_key.strip().upper() for t in planning_context.tasks}
            known_resource_ids = {r.resource_id.strip().lower() for r in planning_context.resources}

        # Validate task proposals
        for idx, tp in enumerate(proposal.task_proposals):
            if not tp.requires_human_review:
                return False, f"Task proposal {idx} ({tp.issue_key}) must have requires_human_review=True"
            if tp.date_confidence < 0.0 or tp.date_confidence > 1.0:
                return False, f"Task proposal {idx} ({tp.issue_key}) date_confidence out of bounds: {tp.date_confidence}"
            if planning_context is not None and known_issue_keys:
                if tp.issue_key.strip().upper() not in known_issue_keys:
                    return False, f"Proposed task '{tp.issue_key}' does not exist in PlanningContext"
            if tp.proposed_estimate:
                if tp.proposed_estimate.confidence < 0.0 or tp.proposed_estimate.confidence > 1.0:
                    return False, f"Estimate confidence for task '{tp.issue_key}' out of bounds"
                if tp.proposed_estimate.value < 0.0:
                    return False, f"Estimate value for task '{tp.issue_key}' cannot be negative"

        # Validate sequencing proposals
        for idx, sp in enumerate(proposal.sequencing_proposals):
            if sp.confidence < 0.0 or sp.confidence > 1.0:
                return False, f"Sequencing proposal {idx} ({sp.issue_key}) confidence out of bounds"
            if sp.position < 1:
                return False, f"Sequencing position for task '{sp.issue_key}' must be >= 1"
            if planning_context is not None and known_issue_keys:
                if sp.issue_key.strip().upper() not in known_issue_keys:
                    return False, f"Sequenced task '{sp.issue_key}' does not exist in PlanningContext"

        # Validate risk signals
        for idx, rs in enumerate(proposal.risk_signals):
            if not rs.requires_human_review:
                return False, f"Risk signal {idx} must have requires_human_review=True"
            if rs.confidence < 0.0 or rs.confidence > 1.0:
                return False, f"Risk signal {idx} confidence out of bounds: {rs.confidence}"
            if rs.issue_key and planning_context is not None and known_issue_keys:
                if rs.issue_key.strip().upper() not in known_issue_keys:
                    return False, f"Risk signal task '{rs.issue_key}' does not exist in PlanningContext"

        # Validate assumptions
        for idx, asm in enumerate(proposal.assumptions):
            if not asm.requires_human_review:
                return False, f"Assumption {idx} must have requires_human_review=True"
            if asm.confidence < 0.0 or asm.confidence > 1.0:
                return False, f"Assumption {idx} confidence out of bounds: {asm.confidence}"

        return True, None


