"""User identity resolution service for personal notification targeting.

Determines 'my' identity dynamically from /rest/api/3/myself or optional
environment variable overrides (MY_JIRA_ACCOUNT_ID, MY_JIRA_EMAIL, MY_JIRA_DISPLAY_NAME).
No credentials, account IDs, or display names are hardcoded.
"""

from typing import Any, Dict, Optional
from app.config.settings import settings
from app.utils.logger import logger


class UserIdentityService:
    """Manages resolution and matching of the active PM / user's Jira identity."""

    def __init__(self):
        self._cached_account_id: Optional[str] = None
        self._cached_email: Optional[str] = None
        self._cached_display_name: Optional[str] = None
        self._initialized: bool = False

    async def get_my_identity(self, client: Optional[Any] = None) -> Dict[str, Optional[str]]:
        """Resolve the active user's Jira identity.
        
        Priority:
        1. Explicit settings overrides (MY_JIRA_ACCOUNT_ID, etc.)
        2. Cached Jira /rest/api/3/myself profile
        3. Dynamic fetch via JiraClient.get_myself()
        4. Fallback to settings.JIRA_EMAIL
        """
        # Check explicit overrides
        acc_id = settings.MY_JIRA_ACCOUNT_ID
        email = settings.MY_JIRA_EMAIL or settings.JIRA_EMAIL
        disp_name = settings.MY_JIRA_DISPLAY_NAME

        if acc_id and disp_name:
            self._cached_account_id = acc_id
            self._cached_email = email
            self._cached_display_name = disp_name
            self._initialized = True
            return {
                "account_id": acc_id,
                "email": email,
                "display_name": disp_name,
            }

        # If not already initialized from Jira API, attempt fetch
        if not self._initialized:
            try:
                from app.connectors.jira.client import JiraClient
                cli = client or JiraClient()
                if settings.is_jira_configured():
                    myself = await cli.get_myself()
                    if myself and isinstance(myself, dict):
                        self._cached_account_id = acc_id or myself.get("accountId")
                        self._cached_email = email or myself.get("emailAddress")
                        self._cached_display_name = disp_name or myself.get("displayName")
                        self._initialized = True
                        logger.info(
                            f"Resolved active Jira user identity from /rest/api/3/myself: "
                            f"displayName='{self._cached_display_name}', "
                            f"accountId='{self._cached_account_id}'"
                        )
            except Exception as e:
                logger.warning(f"Could not dynamically resolve Jira identity via /rest/api/3/myself: {e}")

        # Fallback values
        return {
            "account_id": self._cached_account_id or acc_id,
            "email": self._cached_email or email,
            "display_name": self._cached_display_name or disp_name,
        }

    def set_identity(self, account_id: Optional[str] = None, email: Optional[str] = None, display_name: Optional[str] = None) -> None:
        """Explicitly set or override cached identity (useful in testing)."""
        self._cached_account_id = account_id
        self._cached_email = email
        self._cached_display_name = display_name
        self._initialized = True

    def is_me(
        self,
        account_id: Optional[str] = None,
        email: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> bool:
        """Check if a given actor, assignee, or mention target matches 'me'."""
        my_acc = settings.MY_JIRA_ACCOUNT_ID or self._cached_account_id
        my_email = settings.MY_JIRA_EMAIL or self._cached_email or (settings.JIRA_EMAIL if settings.is_jira_configured() else None)
        my_name = settings.MY_JIRA_DISPLAY_NAME or self._cached_display_name

        if account_id and my_acc and account_id.strip() == my_acc.strip():
            return True

        if email and my_email and email.strip().lower() == my_email.strip().lower():
            return True

        if display_name and my_name and display_name.strip().lower() == my_name.strip().lower():
            return True

        return False


user_identity_service = UserIdentityService()
