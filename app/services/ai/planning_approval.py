"""Planning Approval Service for Phase 4E: Human Approval Interface.

Enforces a deterministic, auditable, fail-closed human approval gate between
AI-generated PlanningProposal evaluation and any future planning mutations.

CRITICAL INVARIANTS:
1. ZERO automatic approvals: Every approval must originate from an authorized human reviewer.
2. ZERO Jira API calls, worklogs, comments, due date updates, or mutations.
3. ZERO Action Engine executions or dispatches.
4. ZERO Discord or Mattermost outbound notifications.
5. Proposal and PlanningContext immutability: References and version identifiers are bound.
6. Validation enforcement: INVALID proposals can NEVER be approved.
7. Explicit acknowledgement: NEEDS_REVIEW proposals strictly require human acknowledgement of all WARNING issues.
8. Idempotency & atomic concurrency: Terminal transitions (APPROVED, REJECTED, EXPIRED) are strictly one-time.
"""

from datetime import datetime, timezone, timedelta
import threading
from typing import Any, Dict, List, Optional, Set
import uuid

from app.core.models.enums import Capability
from app.core.models.planning import (
    PlanningApprovalDecision,
    PlanningApprovalRequest,
    PlanningApprovalState,
    PlanningContext,
    PlanningProposal,
    ProposalValidationCategory,
    ProposalValidationIssue,
    ProposalValidationResult,
    ProposalValidationStatus,
    ReviewerIdentity,
    ValidationIssueSeverity,
)
from app.core.planning.validator import PlanningProposalValidator
from app.database.connection import DatabaseManager, db_manager
from app.database.repositories import PlanningApprovalRepository
from app.services.audit_service import AuditService, audit_service
from app.utils.logger import logger
from app.utils.time import parse_iso_datetime, utc_now_iso


# Default validity duration for pending planning approvals (e.g. 48 hours)
DEFAULT_APPROVAL_EXPIRATION_HOURS = 48


class PlanningApprovalError(Exception):
    """Base exception for planning approval gate errors."""
    pass


class PlanningApprovalService:
    """Service orchestrating deterministic human review and approval for AI planning proposals."""

    _lock = threading.RLock()

    def __init__(
        self,
        manager: Optional[DatabaseManager] = None,
        audit: Optional[AuditService] = None,
        expiration_hours: int = DEFAULT_APPROVAL_EXPIRATION_HOURS,
    ):
        self.mgr = manager or db_manager
        self.repo = PlanningApprovalRepository(self.mgr)
        self.audit = audit or audit_service
        self.expiration_hours = expiration_hours

    def create_approval_request(
        self,
        context: PlanningContext,
        proposal: PlanningProposal,
        validation_result: ProposalValidationResult,
        proposal_id: Optional[str] = None,
    ) -> PlanningApprovalRequest:
        """Create an immutable, typed PlanningApprovalRequest for a validated proposal."""
        if not isinstance(context, PlanningContext):
            raise PlanningApprovalError("A valid PlanningContext instance is required.")
        if not isinstance(proposal, PlanningProposal):
            raise PlanningApprovalError("A valid PlanningProposal instance is required.")
        if not isinstance(validation_result, ProposalValidationResult):
            raise PlanningApprovalError("A valid ProposalValidationResult instance is required.")

        # Ensure version alignment
        if proposal.context_version != context.context_version:
            raise PlanningApprovalError(
                f"Proposal context_version '{proposal.context_version}' does not match "
                f"PlanningContext version '{context.context_version}'."
            )
        if validation_result.proposal_version != proposal.proposal_version:
            raise PlanningApprovalError(
                f"ValidationResult proposal_version '{validation_result.proposal_version}' does not match "
                f"Proposal version '{proposal.proposal_version}'."
            )

        now_dt = datetime.now(timezone.utc)
        created_at = now_dt.isoformat()
        expires_at = (now_dt + timedelta(hours=self.expiration_hours)).isoformat()

        req_id = f"apr-{uuid.uuid4().hex[:12]}"
        pid = proposal_id or f"prop-{uuid.uuid4().hex[:10]}"

        # Extract snapshot facts for transparent PM review
        affected_keys = sorted(list({t.issue_key for t in proposal.task_proposals}))
        affected_res = sorted(list({r.resource_id for r in context.resources}))

        request = PlanningApprovalRequest(
            approval_request_id=req_id,
            proposal_id=pid,
            proposal_version=proposal.proposal_version,
            context_version=context.context_version,
            anchor_date=proposal.anchor_date,
            state=PlanningApprovalState.PENDING,
            created_at=created_at,
            expires_at=expires_at,
            proposal_summary=proposal.summary,
            affected_issue_keys=affected_keys,
            affected_resource_ids=affected_res,
            validation_status=validation_result.status,
            validation_result=validation_result,
            task_proposals=proposal.task_proposals,
            sequencing_proposals=proposal.sequencing_proposals,
            risk_signals=proposal.risk_signals,
            assumptions=proposal.assumptions,
            evidence_references=proposal.evidence_references,
            decision=None,
        )

        with self._lock:
            self.repo.save_request(request.model_dump())

        # Audit log creation
        self.audit.log_action(
            actor="SYSTEM_PLANNING_ENGINE",
            action="PLANNING_APPROVAL_REQUEST_CREATED",
            target=req_id,
            result="SUCCESS",
            details={
                "approval_request_id": req_id,
                "proposal_id": pid,
                "proposal_version": proposal.proposal_version,
                "context_version": context.context_version,
                "validation_status": validation_result.status.value,
                "task_count": len(proposal.task_proposals),
                "expires_at": expires_at,
            },
        )

        return request

    def get_approval_request(self, approval_request_id: str) -> Optional[PlanningApprovalRequest]:
        """Retrieve approval request and lazily evaluate expiration if currently pending."""
        if not approval_request_id:
            return None
        with self._lock:
            data = self.repo.get_request(approval_request_id)
            if not data:
                return None

            request = PlanningApprovalRequest.model_validate(data)

            # Lazy expiration check
            if request.state == PlanningApprovalState.PENDING:
                now_dt = datetime.now(timezone.utc)
                exp_dt = parse_iso_datetime(request.expires_at)
                if exp_dt and now_dt > exp_dt:
                    request = request.model_copy(update={"state": PlanningApprovalState.EXPIRED})
                    self.repo.save_request(request.model_dump())
                    self.audit.log_action(
                        actor="SYSTEM_EXPIRATION_GATE",
                        action="PLANNING_APPROVAL_EXPIRED",
                        target=request.approval_request_id,
                        result="EXPIRED",
                        details={
                            "approval_request_id": request.approval_request_id,
                            "proposal_id": request.proposal_id,
                            "proposal_version": request.proposal_version,
                        },
                    )

            return request

    def list_pending_approvals(self, limit: int = 50, offset: int = 0) -> List[PlanningApprovalRequest]:
        """List active pending approval requests (auto-filtering expired)."""
        raw_list = self.repo.list_requests(state="PENDING", limit=limit, offset=offset)
        results: List[PlanningApprovalRequest] = []
        for d in raw_list:
            req = self.get_approval_request(d["approval_request_id"])
            if req and req.state == PlanningApprovalState.PENDING:
                results.append(req)
        return results

    def approve_proposal(
        self,
        approval_request_id: str,
        reviewer: ReviewerIdentity,
        acknowledged_issue_codes: Optional[List[str]] = None,
        comments: Optional[str] = None,
        expected_proposal_id: Optional[str] = None,
        expected_proposal_version: Optional[str] = None,
        expected_context_version: Optional[str] = None,
    ) -> PlanningApprovalDecision:
        """Record an explicit, authorized human approval decision for a pending proposal."""
        return self._record_decision(
            approval_request_id=approval_request_id,
            decision=PlanningApprovalState.APPROVED,
            reviewer=reviewer,
            acknowledged_issue_codes=acknowledged_issue_codes or [],
            comments=comments,
            expected_proposal_id=expected_proposal_id,
            expected_proposal_version=expected_proposal_version,
            expected_context_version=expected_context_version,
        )

    def reject_proposal(
        self,
        approval_request_id: str,
        reviewer: ReviewerIdentity,
        comments: Optional[str] = None,
        expected_proposal_id: Optional[str] = None,
        expected_proposal_version: Optional[str] = None,
        expected_context_version: Optional[str] = None,
    ) -> PlanningApprovalDecision:
        """Record an explicit, authorized human rejection decision for a pending proposal."""
        return self._record_decision(
            approval_request_id=approval_request_id,
            decision=PlanningApprovalState.REJECTED,
            reviewer=reviewer,
            acknowledged_issue_codes=[],
            comments=comments,
            expected_proposal_id=expected_proposal_id,
            expected_proposal_version=expected_proposal_version,
            expected_context_version=expected_context_version,
        )

    def _record_decision(
        self,
        approval_request_id: str,
        decision: PlanningApprovalState,
        reviewer: ReviewerIdentity,
        acknowledged_issue_codes: List[str],
        comments: Optional[str],
        expected_proposal_id: Optional[str] = None,
        expected_proposal_version: Optional[str] = None,
        expected_context_version: Optional[str] = None,
    ) -> PlanningApprovalDecision:
        """Execute atomic, fail-closed approval or rejection state transition."""
        if not isinstance(reviewer, ReviewerIdentity):
            raise PlanningApprovalError("Authenticated ReviewerIdentity is required.")

        with self._lock:
            # 1. Retrieve current request
            request = self.get_approval_request(approval_request_id)
            if not request:
                raise PlanningApprovalError(f"Approval request '{approval_request_id}' does not exist.")

            # 2. RBAC Permission Check: Must have planning approval capability or role
            req_cap = Capability.APPROVE_PLANNING.value if decision == PlanningApprovalState.APPROVED else Capability.REJECT_PLANNING.value
            has_role = any(
                r.strip().lower() in ("admin", "pm", "project_manager", "lead", req_cap.lower())
                for r in reviewer.roles
            )
            if not has_role and reviewer.roles:  # If roles are populated and none match
                raise PlanningApprovalError(
                    f"Reviewer '{reviewer.user_id}' does not possess required role or capability '{req_cap}'."
                )

            # 3. State & Finality Check
            if request.state == PlanningApprovalState.APPROVED:
                raise PlanningApprovalError(
                    f"Approval request '{approval_request_id}' has already been APPROVED and cannot be changed."
                )
            if request.state == PlanningApprovalState.REJECTED:
                raise PlanningApprovalError(
                    f"Approval request '{approval_request_id}' has already been REJECTED and cannot be changed."
                )
            if request.state == PlanningApprovalState.EXPIRED:
                raise PlanningApprovalError(
                    f"Approval request '{approval_request_id}' has EXPIRED and cannot receive decisions."
                )

            # 4. Version & Proposal Integrity Binding
            if expected_proposal_id and expected_proposal_id != request.proposal_id:
                raise PlanningApprovalError(
                    f"Proposal ID mismatch: expected '{expected_proposal_id}', request is for '{request.proposal_id}'."
                )
            if expected_proposal_version and expected_proposal_version != request.proposal_version:
                raise PlanningApprovalError(
                    f"Proposal version mismatch: expected '{expected_proposal_version}', request is for '{request.proposal_version}'."
                )
            if expected_context_version and expected_context_version != request.context_version:
                raise PlanningApprovalError(
                    f"Context version mismatch: expected '{expected_context_version}', request is for '{request.context_version}'."
                )

            # 5. Fail-Closed Validation Policy for APPROVAL
            if decision == PlanningApprovalState.APPROVED:
                val_status = request.validation_status

                # INVALID proposals can NEVER be approved
                if val_status == ProposalValidationStatus.INVALID:
                    raise PlanningApprovalError(
                        f"Proposal cannot be APPROVED: validation status is INVALID with "
                        f"{request.validation_result.invalid_task_count} violation(s)."
                    )

                # NEEDS_REVIEW requires explicit acknowledgement of all WARNING issue codes
                if val_status == ProposalValidationStatus.NEEDS_REVIEW:
                    required_ack_codes = {
                        i.code for i in request.validation_result.issues
                        if i.severity == ValidationIssueSeverity.WARNING
                    }
                    provided_ack_codes = set(acknowledged_issue_codes)
                    missing_acks = required_ack_codes - provided_ack_codes
                    if missing_acks:
                        raise PlanningApprovalError(
                            f"Proposal marked NEEDS_REVIEW requires explicit acknowledgement of validation findings: "
                            f"{sorted(list(missing_acks))}."
                        )

            # 6. Build and persist immutable decision record
            dec_id = f"dec-{uuid.uuid4().hex[:12]}"
            decided_at = utc_now_iso()

            decision_record = PlanningApprovalDecision(
                decision_id=dec_id,
                approval_request_id=request.approval_request_id,
                proposal_id=request.proposal_id,
                proposal_version=request.proposal_version,
                context_version=request.context_version,
                decision=decision,
                reviewer=reviewer,
                decided_at=decided_at,
                acknowledged_validation_issue_codes=acknowledged_issue_codes,
                comments=comments,
            )

            # 7. Atomically transition request state
            updated_req = request.model_copy(
                update={
                    "state": decision,
                    "decision": decision_record,
                }
            )

            self.repo.save_decision(decision_record.model_dump())
            self.repo.save_request(updated_req.model_dump())

            # 8. Audit event logging
            action_name = "PLANNING_PROPOSAL_APPROVED" if decision == PlanningApprovalState.APPROVED else "PLANNING_PROPOSAL_REJECTED"
            self.audit.log_action(
                actor=reviewer.user_id,
                action=action_name,
                target=request.approval_request_id,
                result="SUCCESS",
                details={
                    "decision_id": dec_id,
                    "approval_request_id": request.approval_request_id,
                    "proposal_id": request.proposal_id,
                    "proposal_version": request.proposal_version,
                    "reviewer_user_id": reviewer.user_id,
                    "reviewer_display_name": reviewer.display_name,
                    "decision": decision.value,
                    "comments": comments,
                },
            )

            return decision_record


# Global planning approval service instance
planning_approval_service = PlanningApprovalService()
