"""Deterministic role, job title, and canonical identity resolution for Phase A Performance Data Foundation."""

from typing import Any, Dict, List, Optional, Tuple
from app.config.settings import settings
from app.core.models.performance import ResourceRole, RoleCategory
from app.database.repositories import EmployeeRoleRepository
from app.utils.logger import logger


ROLE_KEYWORD_MAP = {
    "developer": ResourceRole.DEVELOPER,
    "software engineer": ResourceRole.DEVELOPER,
    "frontend": ResourceRole.DEVELOPER,
    "backend": ResourceRole.DEVELOPER,
    "fullstack": ResourceRole.DEVELOPER,
    "engineer": ResourceRole.DEVELOPER,
    "programmer": ResourceRole.DEVELOPER,
    "wordpress": ResourceRole.DEVELOPER,
    "qa": ResourceRole.QA,
    "quality assurance": ResourceRole.QA,
    "tester": ResourceRole.QA,
    "sdet": ResourceRole.QA,
    "designer": ResourceRole.DESIGNER,
    "ui/ux": ResourceRole.DESIGNER,
    "product designer": ResourceRole.DESIGNER,
    "support": ResourceRole.SUPPORT,
    "customer support": ResourceRole.SUPPORT,
    "helpdesk": ResourceRole.SUPPORT,
    "project manager": ResourceRole.PROJECT_MANAGER,
    "product manager": ResourceRole.PROJECT_MANAGER,
    "scrum master": ResourceRole.PROJECT_MANAGER,
    "ba": ResourceRole.PROJECT_MANAGER,
    "business analyst": ResourceRole.PROJECT_MANAGER,
    "tech lead": ResourceRole.DEVELOPER,
}

CATEGORY_TO_RESOURCE_ROLE = {
    RoleCategory.WORDPRESS_DEVELOPMENT.value: ResourceRole.DEVELOPER,
    RoleCategory.FRONTEND_DEVELOPMENT.value: ResourceRole.DEVELOPER,
    RoleCategory.BUSINESS_ANALYSIS.value: ResourceRole.PROJECT_MANAGER,
    RoleCategory.QA.value: ResourceRole.QA,
    RoleCategory.CONTENT.value: ResourceRole.UNKNOWN,
    RoleCategory.CONTENT_MARKETING.value: ResourceRole.UNKNOWN,
    RoleCategory.SEO.value: ResourceRole.UNKNOWN,
    RoleCategory.CUSTOMER_SUPPORT.value: ResourceRole.SUPPORT,
    RoleCategory.DESIGN.value: ResourceRole.DESIGNER,
    RoleCategory.UNKNOWN.value: ResourceRole.UNKNOWN,
}

# Explicit authoritative mapping of legacy Jira usernames / alternate identifiers to canonical Atlassian account IDs
AUTHORITATIVE_JIRA_LEGACY_ACCOUNT_MAP: Dict[str, str] = {
    "ahsan.amin": "712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de",
    "jira-user-ahsan": "712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de",
}


def resolve_canonical_account_id(
    identifier: Optional[str],
    display_name: Optional[str] = None,
    role_repo: Optional[EmployeeRoleRepository] = None,
) -> Optional[str]:
    """Deterministically resolve a Jira account_id, legacy username, or alias to its canonical Atlassian account ID.

    Resolution rules (no guessing, no fuzzy matching):
    1. Check explicit AUTHORITATIVE_JIRA_LEGACY_ACCOUNT_MAP (e.g. ahsan.amin -> 712020:8bc58bcd...)
    2. Check if identifier is already a known canonical account_id in employee_role_assignments
    3. Check if display_name matches a unique seeded employee in employee_role_assignments
    4. Fallback to original identifier untouched
    """
    if not identifier and not display_name:
        return None

    # 1. Check explicit legacy map
    if identifier:
        clean_id = str(identifier).strip().lower()
        if clean_id in AUTHORITATIVE_JIRA_LEGACY_ACCOUNT_MAP:
            return AUTHORITATIVE_JIRA_LEGACY_ACCOUNT_MAP[clean_id]

    repo = role_repo or EmployeeRoleRepository()

    # 2. Check if identifier is already in authoritative table
    if identifier:
        assignment = repo.get_by_account_id(str(identifier).strip())
        if assignment:
            return assignment["account_id"]

    # 3. Check exact display name match against authoritative table
    if display_name:
        assignment = repo.get_by_display_name(str(display_name).strip())
        if assignment:
            return assignment["account_id"]

    return str(identifier).strip() if identifier else None


def get_account_aliases(account_id: str) -> List[str]:
    """Return all known legacy identifiers and aliases for a canonical account ID."""
    if not account_id:
        return []
    clean_id = str(account_id).strip()
    aliases = [clean_id]
    for alias, can_id in AUTHORITATIVE_JIRA_LEGACY_ACCOUNT_MAP.items():
        if can_id.lower() == clean_id.lower() and alias not in aliases:
            aliases.append(alias)
    return aliases


def get_employee_designation_and_category(
    account_id: Optional[str],
    display_name: Optional[str] = None,
    role_repo: Optional[EmployeeRoleRepository] = None,
) -> Tuple[str, str, bool]:
    """Look up authoritative designation and normalized role category from SQLite.

    Returns:
        (exact_designation, normalized_role_category, is_resolved)
    """
    if not account_id and not display_name:
        return "Unknown", RoleCategory.UNKNOWN.value, False

    repo = role_repo or EmployeeRoleRepository()
    canonical_id = resolve_canonical_account_id(account_id, display_name, role_repo=repo)

    # 1. Primary lookup by canonical account_id
    if canonical_id:
        assignment = repo.get_by_account_id(canonical_id)
        if assignment:
            return (
                assignment.get("designation", "Unknown"),
                assignment.get("role_category", RoleCategory.UNKNOWN.value),
                True,
            )

    # 2. Secondary lookup by display_name if provided
    if display_name:
        assignment = repo.get_by_display_name(display_name)
        if assignment:
            return (
                assignment.get("designation", "Unknown"),
                assignment.get("role_category", RoleCategory.UNKNOWN.value),
                True,
            )

    return "Unknown", RoleCategory.UNKNOWN.value, False


def resolve_resource_role(
    account_id: str,
    display_name: Optional[str] = None,
    job_title: Optional[str] = None,
    user_metadata: Optional[Dict[str, Any]] = None,
    role_repo: Optional[EmployeeRoleRepository] = None,
) -> ResourceRole:
    """Resolve a resource's role deterministically using authoritative table, config, or metadata.

    Order of precedence:
    1. Authoritative employee_role_assignments table in SQLite (with legacy alias resolution)
    2. Explicit configured mapping in PERFORMANCE_ROLE_MAPPINGS (by account_id or display_name)
    3. Explicit job_title parameter
    4. User metadata dictionary fields (title, jobTitle, role)
    5. Fallback to ResourceRole.UNKNOWN
    """
    # 1. Authoritative SQLite table lookup
    designation, category, resolved = get_employee_designation_and_category(
        account_id, display_name, role_repo=role_repo
    )
    if resolved and category in CATEGORY_TO_RESOURCE_ROLE:
        return CATEGORY_TO_RESOURCE_ROLE[category]

    mappings = settings.get_performance_role_mappings()

    # 2. Check configured explicit mappings
    if account_id and account_id.lower() in mappings:
        val = mappings[account_id.lower()].lower()
        for k, role_enum in ROLE_KEYWORD_MAP.items():
            if k in val:
                return role_enum
        return (
            ResourceRole(mappings[account_id.lower()])
            if mappings[account_id.lower()] in [r.value for r in ResourceRole]
            else ResourceRole.UNKNOWN
        )

    if display_name and display_name.lower() in mappings:
        val = mappings[display_name.lower()].lower()
        for k, role_enum in ROLE_KEYWORD_MAP.items():
            if k in val:
                return role_enum
        return (
            ResourceRole(mappings[display_name.lower()])
            if mappings[display_name.lower()] in [r.value for r in ResourceRole]
            else ResourceRole.UNKNOWN
        )

    # 3. Check explicit job title
    if job_title and str(job_title).strip():
        jt_lower = str(job_title).strip().lower()
        for k, role_enum in ROLE_KEYWORD_MAP.items():
            if k in jt_lower:
                return role_enum

    # 4. Check user metadata dictionary
    if user_metadata and isinstance(user_metadata, dict):
        raw_title = (
            user_metadata.get("title")
            or user_metadata.get("jobTitle")
            or user_metadata.get("role")
        )
        if raw_title and isinstance(raw_title, str):
            t_lower = raw_title.strip().lower()
            for k, role_enum in ROLE_KEYWORD_MAP.items():
                if k in t_lower:
                    return role_enum

    # 5. Deterministic fallback
    return ResourceRole.UNKNOWN
