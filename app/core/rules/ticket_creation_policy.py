"""Deterministic policy evaluation for Jira ticket creation events."""

from enum import Enum
from typing import Any, Dict, Optional, Tuple
from app.config.settings import settings
from app.core.performance.roles import resolve_canonical_account_id
from app.database.repositories import EmployeeRoleRepository, PluginBoardRepository
from app.utils.logger import logger


class TicketCreationDecision(str, Enum):
    IGNORE = "IGNORE"
    EXPECTED = "EXPECTED"
    REVIEW = "REVIEW"


# Authoritative known account IDs for special roles
TAHIR_ALI_ACCOUNT_ID = "638855b85fce844d606bb422"
MUBASHIR_BUTT_ACCOUNT_ID = "712020:e268bcd8-d981-4b4d-992d-d5694745df8b"

QA_CANONICAL_ACCOUNT_IDS = {
    "712020:c12d2371-1e5b-4797-a888-369c0c9c5a65",  # Muhammad Sufiyan
    "712020:32e5be05-80c9-4ece-ac19-301da7c9487d",  # shoaib hassan askari
    "712020:12e1da4b-147f-4f91-9d2d-965b66e19b61",  # Muhammad Bilal Khan
}

SUPPORT_ISSUE_TYPES = {"support", "customer support", "support ticket", "helpdesk"}
QA_EXPECTED_ISSUE_TYPES = {"bug", "sub-task", "subtask", "sub task"}
QA_REVIEW_ISSUE_TYPES = {"story", "task", "user story"}


class TicketCreationPolicy:
    """Evaluates Jira ticket creation events deterministically against authoritative PM policy."""

    @staticmethod
    def is_authoritatively_customer_created(
        creator_account_type: Optional[str] = None,
        creator_email: Optional[str] = None,
        raw_payload: Optional[Dict[str, Any]] = None,
        is_internal_employee: bool = False,
    ) -> bool:
        """Determine whether there is authoritative evidence that an issue was created by an external customer.

        Strict criteria:
        1. Creator must NOT be an internal employee.
        2. At least one authoritative customer signal must be present:
           - accountType is explicitly 'customer'
           - Jira Service Management request fields present (customerRequestType, requestTypeId, portalId)
           - External non-company email combined with customer/requester payload indicator
        """
        if is_internal_employee:
            return False

        # 1. Direct Atlassian customer accountType signal
        if creator_account_type and str(creator_account_type).strip().lower() == "customer":
            return True

        if raw_payload and isinstance(raw_payload, dict):
            issue = raw_payload.get("issue", {})
            fields = issue.get("fields", {}) if isinstance(issue, dict) else {}
            user = raw_payload.get("user", {})
            creator = fields.get("creator") or user or {}

            # Check creator/user accountType in payload
            if isinstance(creator, dict) and str(creator.get("accountType", "")).lower() == "customer":
                return True
            if isinstance(user, dict) and str(user.get("accountType", "")).lower() == "customer":
                return True

            # Check JSM portal/request metadata in fields
            if any(k in fields for k in ("customerRequestType", "requestTypeId", "portalId", "requestFeedbackEnabled")):
                return True

            # Check customfield representing customer request type (e.g. customfield_10010)
            for k, v in fields.items():
                if k.startswith("customfield_") and isinstance(v, dict):
                    if "requestType" in v or ("_links" in v and "portal" in str(v.get("_links", ""))):
                        return True

        return False

    @classmethod
    def evaluate(
        cls,
        creator_account_id: Optional[str],
        creator_display_name: Optional[str] = None,
        creator_email: Optional[str] = None,
        creator_account_type: Optional[str] = None,
        issue_type: Optional[str] = None,
        issue_type_id: Optional[str] = None,
        project_key: Optional[str] = None,
        raw_payload: Optional[Dict[str, Any]] = None,
        role_repo: Optional[EmployeeRoleRepository] = None,
        plugin_repo: Optional[PluginBoardRepository] = None,
    ) -> Tuple[TicketCreationDecision, str, Optional[str], Optional[str]]:
        """Evaluate a ticket creation event.

        Returns:
            Tuple of (decision, reason, canonical_account_id, resolved_display_name)
        """
        repo = role_repo or EmployeeRoleRepository()
        p_repo = plugin_repo or PluginBoardRepository()

        # 1. Resolve canonical Atlassian account ID
        can_id = resolve_canonical_account_id(
            creator_account_id,
            display_name=creator_display_name,
            role_repo=repo
        )

        # 2. Check Canonical Global Exclusion (PM accounts, bots, automated tools)
        if settings.is_canonical_excluded(can_id, creator_display_name) or settings.is_canonical_excluded(creator_account_id, creator_display_name):
            return TicketCreationDecision.IGNORE, "Excluded account or bot identity", can_id, creator_display_name

        # 3. Look up authoritative employee record
        assignment: Optional[Dict[str, Any]] = None
        if can_id:
            assignment = repo.get_by_account_id(can_id)
        if not assignment and creator_display_name:
            assignment = repo.get_by_display_name(creator_display_name)

        is_internal_employee = bool(assignment or (can_id and can_id in QA_CANONICAL_ACCOUNT_IDS))
        if creator_email and any(dom in creator_email.lower() for dom in ("@objects.ws", "@objectsws.com")):
            is_internal_employee = True

        resolved_name = (assignment.get("display_name") if assignment else None) or creator_display_name or "Unknown Jira User"
        role_category = (assignment.get("role_category") if assignment else "").strip().lower()
        designation = (assignment.get("designation") if assignment else "").strip()

        # Check Service Management Project status
        is_sm_project = bool(project_key and p_repo.is_service_management_project(project_key))

        # Check Authoritative Customer Classification in Service Management projects
        if is_sm_project:
            if not is_internal_employee:
                if cls.is_authoritatively_customer_created(
                    creator_account_type=creator_account_type,
                    creator_email=creator_email,
                    raw_payload=raw_payload,
                    is_internal_employee=is_internal_employee,
                ):
                    return TicketCreationDecision.IGNORE, "Customer-created Service Management request", can_id, resolved_name
                else:
                    # Unverified / unknown creator in SM project without authoritative customer evidence -> REVIEW
                    return TicketCreationDecision.REVIEW, "Service Management issue without authoritative customer evidence — requires review", can_id, resolved_name

        # Unresolved creator in standard/internal project
        if not can_id and not assignment:
            return TicketCreationDecision.REVIEW, "Unknown Jira User created ticket", None, resolved_name

        # Normalize issue type
        raw_type = (issue_type or "Task").strip()
        clean_type = raw_type.lower()

        # 4. Tahir Ali Policy
        if can_id == TAHIR_ALI_ACCOUNT_ID or (assignment and assignment.get("display_name") == "Tahir Ali"):
            return TicketCreationDecision.EXPECTED, "Tahir Ali is authorized to create tickets", can_id, resolved_name

        # 5. Mubashir Butt / Support Policy
        if can_id == MUBASHIR_BUTT_ACCOUNT_ID or role_category == "customer support" or "support" in designation.lower():
            if clean_type in SUPPORT_ISSUE_TYPES:
                return TicketCreationDecision.EXPECTED, "Customer Support is authorized to create Support tickets", can_id, resolved_name
            else:
                return TicketCreationDecision.REVIEW, f"Support team member created {raw_type} ticket type", can_id, resolved_name

        # 6. QA Policy
        is_qa = (
            (can_id and can_id in QA_CANONICAL_ACCOUNT_IDS)
            or role_category == "qa"
            or "qa" in designation.lower()
            or "quality assurance" in designation.lower()
            or "tester" in designation.lower()
        )
        if is_qa:
            if clean_type in QA_EXPECTED_ISSUE_TYPES:
                return TicketCreationDecision.EXPECTED, f"QA may create {raw_type} tickets", can_id, resolved_name
            elif clean_type in QA_REVIEW_ISSUE_TYPES:
                return TicketCreationDecision.REVIEW, f"QA member created {raw_type} — exception review", can_id, resolved_name
            else:
                return TicketCreationDecision.REVIEW, f"QA member created {raw_type} — requires PM review", can_id, resolved_name

        # 7. General Team Policy
        return TicketCreationDecision.REVIEW, f"General team member created {raw_type} — requires PM review", can_id, resolved_name

