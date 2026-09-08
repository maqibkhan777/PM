"""User Mapping Service resolving Jira users to Mattermost accounts strictly."""

from typing import Dict, Optional, Tuple
from app.database.repositories import UserMappingRepository, UserRepository
from app.database.connection import db_manager, DatabaseManager
from app.utils.logger import logger


class UserMappingService:
    """Service to safely resolve Jira User IDs to Mattermost User IDs.

    Resolution order:
    1. Explicit Jira -> Mattermost mapping in database.
    2. Exact verified email match across systems.
    3. Exact username / display name match where unique.
    4. Unresolved (Never guesses or risks messaging wrong employee).
    """

    def __init__(self, manager: Optional[DatabaseManager] = None):
        self.mgr = manager or db_manager
        self.mapping_repo = UserMappingRepository(self.mgr)
        self.user_repo = UserRepository(self.mgr)

    def resolve_jira_to_mattermost(
        self,
        jira_user_id: str,
        display_name: Optional[str] = None,
        email: Optional[str] = None
    ) -> Tuple[Optional[str], str]:
        """Resolve a Jira user to a Mattermost user ID.

        Returns:
            Tuple of (mattermost_user_id or None, resolution_method)
            where resolution_method is one of:
            - 'EXPLICIT_MAPPING'
            - 'EXACT_EMAIL_MATCH'
            - 'EXACT_NAME_MATCH'
            - 'UNRESOLVED'
        """
        if not jira_user_id:
            return None, "UNRESOLVED"

        # 1. Explicit DB mapping
        mapping = self.mapping_repo.get_by_jira_id(jira_user_id)
        if mapping and mapping.get("mattermost_user_id"):
            mm_id = mapping["mattermost_user_id"]
            logger.debug(f"Resolved Jira user '{jira_user_id}' via EXPLICIT_MAPPING to MM ID '{mm_id}'")
            return mm_id, "EXPLICIT_MAPPING"

        # 2. Exact email match in users table
        if email:
            mm_users = self.user_repo.find_by_email(email=email, external_system="mattermost")
            if mm_users:
                mm_id = mm_users[0]["external_user_id"]
                logger.info(f"Resolved Jira user '{jira_user_id}' via EXACT_EMAIL_MATCH to MM ID '{mm_id}'")
                # Auto-cache mapping for future fast lookup
                self.mapping_repo.upsert_mapping(
                    jira_user_id=jira_user_id,
                    mattermost_user_id=mm_id,
                    display_name=display_name or mm_users[0]["display_name"]
                )
                return mm_id, "EXACT_EMAIL_MATCH"

        # 3. Exact display name match in users table
        if display_name:
            mm_users = self.user_repo.find_by_name(display_name=display_name, external_system="mattermost")
            if len(mm_users) == 1:
                mm_id = mm_users[0]["external_user_id"]
                logger.info(f"Resolved Jira user '{jira_user_id}' via EXACT_NAME_MATCH to MM ID '{mm_id}'")
                self.mapping_repo.upsert_mapping(
                    jira_user_id=jira_user_id,
                    mattermost_user_id=mm_id,
                    display_name=display_name
                )
                return mm_id, "EXACT_NAME_MATCH"

        # 4. Unresolved - Do not guess!
        logger.warning(
            f"User mapping required: Jira user '{display_name or jira_user_id}' (ID: {jira_user_id}, Email: {email}) "
            "could not be resolved to any Mattermost account."
        )
        return None, "UNRESOLVED"

    def register_mapping(
        self,
        jira_user_id: str,
        mattermost_user_id: str,
        display_name: str
    ) -> str:
        """Explicitly register or update a Jira <-> Mattermost mapping."""
        return self.mapping_repo.upsert_mapping(
            jira_user_id=jira_user_id,
            mattermost_user_id=mattermost_user_id,
            display_name=display_name
        )


# Global user mapping service instance
user_mapping_service = UserMappingService()
