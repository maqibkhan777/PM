"""Audit logging service for tracking all external actions with credential redaction."""

from typing import Any, Dict, List, Optional
from app.database.repositories import AuditRepository
from app.database.connection import db_manager, DatabaseManager
from app.utils.logger import logger, sanitize_dict


class AuditService:
    """Records audit logs for all domain and external actions with credential redaction."""

    def __init__(self, manager: Optional[DatabaseManager] = None):
        self.mgr = manager or db_manager
        self.repo = AuditRepository(self.mgr)

    def log_action(
        self,
        actor: str,
        action: str,
        target: str,
        result: str,
        details: Optional[Dict[str, Any]] = None
    ) -> str:
        """Record an action in the audit log table."""
        sanitized_details = sanitize_dict(details) if details else {}
        log_id = self.repo.insert(
            actor=actor,
            action=action,
            target=target,
            result=result,
            details=sanitized_details
        )
        logger.info(f"AUDIT | Actor: {actor} | Action: {action} | Target: {target} | Result: {result}")
        return log_id

    def list_logs(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        return self.repo.list_logs(limit=limit, offset=offset)


# Global audit service instance
audit_service = AuditService()
