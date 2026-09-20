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

