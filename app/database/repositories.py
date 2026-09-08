"""Database repositories for typed query operations."""

import json
import sqlite3
import uuid
from typing import Any, Dict, List, Optional
from app.database.connection import db_manager, DatabaseManager
from app.utils.time import utc_now_iso


class EventRepository:
    """Repository for managing events in SQLite."""

    def __init__(self, manager: Optional[DatabaseManager] = None):
        self.mgr = manager or db_manager

    def insert(
        self,
        event_type: str,
        source: str,
        external_event_id: Optional[str],
        timestamp: str,
        payload: Dict[str, Any],
        actor_id: Optional[str] = None,
        actor_name: Optional[str] = None,
        project_id: Optional[str] = None,
        task_id: Optional[str] = None,
        processing_status: str = "RECEIVED",
        event_id: Optional[str] = None,
    ) -> str:
        eid = event_id or str(uuid.uuid4())
        created_at = utc_now_iso()
        with self.mgr.session() as conn:
            conn.execute(
                """
                INSERT INTO events (
                    id, event_type, source, external_event_id, timestamp,
                    actor_id, actor_name, project_id, task_id, payload,
                    processing_status, processing_attempts, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    eid, event_type, source, external_event_id, timestamp,
                    actor_id, actor_name, project_id, task_id, json.dumps(payload),
                    processing_status, created_at
                )
            )
        return eid

    def exists_by_external_id(self, source: str, external_event_id: str) -> bool:
        """Check if an event from source with external_event_id was already received."""
        if not external_event_id:
            return False
        with self.mgr.session() as conn:
            cursor = conn.execute(
                "SELECT 1 FROM events WHERE source = ? AND external_event_id = ? LIMIT 1",
                (source, external_event_id)
            )
            return cursor.fetchone() is not None

    def get_by_id(self, event_id: str) -> Optional[Dict[str, Any]]:
        with self.mgr.session() as conn:
            cursor = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d["payload"] = json.loads(d["payload"]) if d["payload"] else {}
                return d
            return None

    def update_status(
        self,
        event_id: str,
        status: str,
        last_error: Optional[str] = None,
        increment_attempts: bool = False
    ) -> None:
        processed_at = utc_now_iso() if status in ("PROCESSED", "FAILED") else None
        with self.mgr.session() as conn:
            if increment_attempts:
                conn.execute(
                    """
                    UPDATE events
                    SET processing_status = ?,
                        last_error = ?,
                        processing_attempts = processing_attempts + 1,
                        processed_at = COALESCE(?, processed_at)
                    WHERE id = ?
                    """,
                    (status, last_error, processed_at, event_id)
                )
            else:
                conn.execute(
                    """
                    UPDATE events
                    SET processing_status = ?,
                        last_error = ?,
                        processed_at = COALESCE(?, processed_at)
                    WHERE id = ?
                    """,
                    (status, last_error, processed_at, event_id)
                )

    def list_events(
        self,
        limit: int = 50,
        offset: int = 0,
        event_type: Optional[str] = None,
        task_id: Optional[str] = None,
        status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM events WHERE 1=1"
        params: List[Any] = []
        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)
        if task_id:
            query += " AND task_id = ?"
            params.append(task_id)
        if status:
            query += " AND processing_status = ?"
            params.append(status)
        query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self.mgr.session() as conn:
            cursor = conn.execute(query, tuple(params))
            results = []
            for row in cursor.fetchall():
                d = dict(row)
                d["payload"] = json.loads(d["payload"]) if d["payload"] else {}
                results.append(d)
            return results

    def get_events_for_date(self, date_str: str) -> List[Dict[str, Any]]:
        """Fetch all events that occurred on a specific date (YYYY-MM-DD)."""
        with self.mgr.session() as conn:
            cursor = conn.execute(
                "SELECT * FROM events WHERE timestamp LIKE ? ORDER BY timestamp ASC",
                (f"{date_str}%",)
            )
            results = []
            for row in cursor.fetchall():
                d = dict(row)
                d["payload"] = json.loads(d["payload"]) if d["payload"] else {}
                results.append(d)
            return results


class UserRepository:
    """Repository for managing external users across systems."""

    def __init__(self, manager: Optional[DatabaseManager] = None):
        self.mgr = manager or db_manager

    def upsert(
        self,
        external_system: str,
        external_user_id: str,
        display_name: str,
        email: Optional[str] = None,
        active: bool = True
    ) -> str:
        created_at = utc_now_iso()
        uid = str(uuid.uuid4())
        with self.mgr.session() as conn:
            conn.execute(
                """
                INSERT INTO users (id, external_system, external_user_id, display_name, email, active, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(external_system, external_user_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    email = COALESCE(excluded.email, users.email),
                    active = excluded.active
                """,
                (uid, external_system, external_user_id, display_name, email, 1 if active else 0, created_at)
            )
            cursor = conn.execute(
                "SELECT id FROM users WHERE external_system = ? AND external_user_id = ?",
                (external_system, external_user_id)
            )
            row = cursor.fetchone()
            return row["id"] if row else uid

    def get_by_system_and_id(self, external_system: str, external_user_id: str) -> Optional[Dict[str, Any]]:
        with self.mgr.session() as conn:
            cursor = conn.execute(
                "SELECT * FROM users WHERE external_system = ? AND external_user_id = ?",
                (external_system, external_user_id)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def find_by_email(self, email: str, external_system: Optional[str] = None) -> List[Dict[str, Any]]:
        if not email:
            return []
        query = "SELECT * FROM users WHERE LOWER(email) = LOWER(?) AND active = 1"
        params: List[Any] = [email]
        if external_system:
            query += " AND external_system = ?"
            params.append(external_system)
        with self.mgr.session() as conn:
            cursor = conn.execute(query, tuple(params))
            return [dict(r) for r in cursor.fetchall()]

    def find_by_name(self, display_name: str, external_system: Optional[str] = None) -> List[Dict[str, Any]]:
        if not display_name:
            return []
        query = "SELECT * FROM users WHERE LOWER(display_name) = LOWER(?) AND active = 1"
        params: List[Any] = [display_name]
        if external_system:
            query += " AND external_system = ?"
            params.append(external_system)
        with self.mgr.session() as conn:
            cursor = conn.execute(query, tuple(params))
            return [dict(r) for r in cursor.fetchall()]


class UserMappingRepository:
    """Repository for Jira <-> Mattermost user mappings."""

    def __init__(self, manager: Optional[DatabaseManager] = None):
        self.mgr = manager or db_manager

    def upsert_mapping(
        self,
        jira_user_id: str,
        mattermost_user_id: str,
        display_name: str,
        active: bool = True
    ) -> str:
        now_str = utc_now_iso()
        mid = str(uuid.uuid4())
        with self.mgr.session() as conn:
            conn.execute(
                """
                INSERT INTO user_mappings (id, jira_user_id, mattermost_user_id, display_name, active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(jira_user_id) DO UPDATE SET
                    mattermost_user_id = excluded.mattermost_user_id,
                    display_name = excluded.display_name,
                    active = excluded.active,
                    updated_at = excluded.updated_at
                """,
                (mid, jira_user_id, mattermost_user_id, display_name, 1 if active else 0, now_str, now_str)
            )
            cursor = conn.execute("SELECT id FROM user_mappings WHERE jira_user_id = ?", (jira_user_id,))
            row = cursor.fetchone()
            return row["id"] if row else mid

    def get_by_jira_id(self, jira_user_id: str) -> Optional[Dict[str, Any]]:
        with self.mgr.session() as conn:
            cursor = conn.execute(
                "SELECT * FROM user_mappings WHERE jira_user_id = ? AND active = 1",
                (jira_user_id,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def list_mappings(self) -> List[Dict[str, Any]]:
        with self.mgr.session() as conn:
            cursor = conn.execute("SELECT * FROM user_mappings ORDER BY display_name ASC")
            return [dict(r) for r in cursor.fetchall()]


class RuleRepository:
    """Repository for configurable rules."""

    def __init__(self, manager: Optional[DatabaseManager] = None):
        self.mgr = manager or db_manager

    def upsert_rule(
        self,
        name: str,
        description: str,
        enabled: bool,
        configuration: Dict[str, Any],
        rule_id: Optional[str] = None
    ) -> str:
        rid = rule_id or str(uuid.uuid4())
        now_str = utc_now_iso()
        with self.mgr.session() as conn:
            conn.execute(
                """
                INSERT INTO rules (id, name, description, enabled, configuration, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    description = excluded.description,
                    enabled = excluded.enabled,
                    configuration = excluded.configuration,
                    updated_at = excluded.updated_at
                """,
                (rid, name, description, 1 if enabled else 0, json.dumps(configuration), now_str, now_str)
            )
            cursor = conn.execute("SELECT id FROM rules WHERE name = ?", (name,))
            row = cursor.fetchone()
            return row["id"] if row else rid

    def get_rule_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        with self.mgr.session() as conn:
            cursor = conn.execute("SELECT * FROM rules WHERE name = ?", (name,))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d["configuration"] = json.loads(d["configuration"]) if d["configuration"] else {}
                return d
            return None

    def list_rules(self) -> List[Dict[str, Any]]:
        with self.mgr.session() as conn:
            cursor = conn.execute("SELECT * FROM rules ORDER BY name ASC")
            results = []
            for row in cursor.fetchall():
                d = dict(row)
                d["configuration"] = json.loads(d["configuration"]) if d["configuration"] else {}
                results.append(d)
            return results

    def set_enabled(self, rule_id: str, enabled: bool) -> bool:
        now_str = utc_now_iso()
        with self.mgr.session() as conn:
            cursor = conn.execute(
                "UPDATE rules SET enabled = ?, updated_at = ? WHERE id = ? OR name = ?",
                (1 if enabled else 0, now_str, rule_id, rule_id)
            )
            return cursor.rowcount > 0


class ActionRepository:
    """Repository for tracking action lifecycle and idempotency."""

    def __init__(self, manager: Optional[DatabaseManager] = None):
        self.mgr = manager or db_manager

    def insert(
        self,
        action_id: str,
        action_type: str,
        target_system: str,
        target_id: str,
        parameters: Dict[str, Any],
        status: str,
        idempotency_key: Optional[str] = None,
        dry_run: bool = False,
        preview: Optional[Dict[str, Any]] = None,
        db_id: Optional[str] = None
    ) -> str:
        aid = db_id or str(uuid.uuid4())
        created_at = utc_now_iso()
        with self.mgr.session() as conn:
            conn.execute(
                """
                INSERT INTO actions (
                    id, action_id, idempotency_key, action_type, target_system,
                    target_id, parameters, status, attempt_count, dry_run,
                    preview, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (
                    aid, action_id, idempotency_key, action_type, target_system,
                    target_id, json.dumps(parameters), status, 1 if dry_run else 0,
                    json.dumps(preview) if preview else None, created_at
                )
            )
        return aid

    def get_by_idempotency_key(self, idempotency_key: str) -> Optional[Dict[str, Any]]:
        if not idempotency_key:
            return None
        with self.mgr.session() as conn:
            cursor = conn.execute("SELECT * FROM actions WHERE idempotency_key = ?", (idempotency_key,))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d["parameters"] = json.loads(d["parameters"]) if d["parameters"] else {}
                d["preview"] = json.loads(d["preview"]) if d["preview"] else None
                return d
            return None

    def get_by_action_id(self, action_id: str) -> Optional[Dict[str, Any]]:
        with self.mgr.session() as conn:
            cursor = conn.execute("SELECT * FROM actions WHERE action_id = ? OR id = ?", (action_id, action_id))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d["parameters"] = json.loads(d["parameters"]) if d["parameters"] else {}
                d["preview"] = json.loads(d["preview"]) if d["preview"] else None
                return d
            return None

    def update_status(
        self,
        action_id: str,
        status: str,
        last_error: Optional[str] = None,
        increment_attempt: bool = True
    ) -> None:
        executed_at = utc_now_iso() if status in ("COMPLETED", "FAILED", "DRY_RUN_SIMULATED") else None
        with self.mgr.session() as conn:
            if increment_attempt:
                conn.execute(
                    """
                    UPDATE actions
                    SET status = ?,
                        last_error = ?,
                        attempt_count = attempt_count + 1,
                        executed_at = COALESCE(?, executed_at)
                    WHERE action_id = ? OR id = ?
                    """,
                    (status, last_error, executed_at, action_id, action_id)
                )
            else:
                conn.execute(
                    """
                    UPDATE actions
                    SET status = ?,
                        last_error = ?,
                        executed_at = COALESCE(?, executed_at)
                    WHERE action_id = ? OR id = ?
                    """,
                    (status, last_error, executed_at, action_id, action_id)
                )

    def list_actions(self, limit: int = 50, offset: int = 0, status: Optional[str] = None) -> List[Dict[str, Any]]:
        query = "SELECT * FROM actions WHERE 1=1"
        params: List[Any] = []
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self.mgr.session() as conn:
            cursor = conn.execute(query, tuple(params))
            results = []
            for row in cursor.fetchall():
                d = dict(row)
                d["parameters"] = json.loads(d["parameters"]) if d["parameters"] else {}
                d["preview"] = json.loads(d["preview"]) if d["preview"] else None
                results.append(d)
            return results


class NotificationRepository:
    """Repository for tracking and deduplicating notification dispatches."""

    def __init__(self, manager: Optional[DatabaseManager] = None):
        self.mgr = manager or db_manager

    def record_notification(self, rule_id: str, target_id: str, condition: str, channel: str) -> None:
        now_str = utc_now_iso()
        nid = str(uuid.uuid4())
        with self.mgr.session() as conn:
            conn.execute(
                """
                INSERT INTO notifications (id, rule_id, target_id, condition, channel, last_notified_at, notification_count)
                VALUES (?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(rule_id, target_id, condition) DO UPDATE SET
                    channel = excluded.channel,
                    last_notified_at = excluded.last_notified_at,
                    notification_count = notifications.notification_count + 1
                """,
                (nid, rule_id, target_id, condition, channel, now_str)
            )

    def get_last_notification(self, rule_id: str, target_id: str, condition: str) -> Optional[Dict[str, Any]]:
        with self.mgr.session() as conn:
            cursor = conn.execute(
                "SELECT * FROM notifications WHERE rule_id = ? AND target_id = ? AND condition = ?",
                (rule_id, target_id, condition)
            )
            row = cursor.fetchone()
            return dict(row) if row else None


class AuditRepository:
    """Repository for recording audit logs with sanitized details."""

    def __init__(self, manager: Optional[DatabaseManager] = None):
        self.mgr = manager or db_manager

    def insert(
        self,
        actor: str,
        action: str,
        target: str,
        result: str,
        details: Optional[Dict[str, Any]] = None
    ) -> str:
        aid = str(uuid.uuid4())
        now_str = utc_now_iso()
        with self.mgr.session() as conn:
            conn.execute(
                """
                INSERT INTO audit_logs (id, timestamp, actor, action, target, result, details)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (aid, now_str, actor, action, target, result, json.dumps(details) if details else None)
            )
        return aid

    def list_logs(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        with self.mgr.session() as conn:
            cursor = conn.execute("SELECT * FROM audit_logs ORDER BY timestamp DESC LIMIT ? OFFSET ?", (limit, offset))
            results = []
            for row in cursor.fetchall():
                d = dict(row)
                d["details"] = json.loads(d["details"]) if d["details"] else {}
                results.append(d)
            return results
