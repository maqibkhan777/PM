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
        requested_by: str = "RulesEngine",
        requires_approval: bool = False,
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
                    preview, requested_by, requires_approval, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)
                """,
                (
                    aid, action_id, idempotency_key, action_type, target_system,
                    target_id, json.dumps(parameters), status, 1 if dry_run else 0,
                    json.dumps(preview) if preview else None, requested_by,
                    1 if requires_approval else 0, created_at
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
                d["parameters"] = json.loads(d["parameters"]) if d.get("parameters") else {}
                d["preview"] = json.loads(d["preview"]) if d.get("preview") else None
                if d.get("result_data"):
                    try:
                        d["result_data"] = json.loads(d["result_data"])
                    except Exception:
                        pass
                return d
            return None

    def get_by_action_id(self, action_id: str) -> Optional[Dict[str, Any]]:
        with self.mgr.session() as conn:
            cursor = conn.execute("SELECT * FROM actions WHERE action_id = ? OR id = ?", (action_id, action_id))
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d["parameters"] = json.loads(d["parameters"]) if d.get("parameters") else {}
                d["preview"] = json.loads(d["preview"]) if d.get("preview") else None
                if d.get("result_data"):
                    try:
                        d["result_data"] = json.loads(d["result_data"])
                    except Exception:
                        pass
                return d
            return None

    def get_by_id(self, action_id: str) -> Optional[Dict[str, Any]]:
        return self.get_by_action_id(action_id)

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

    def update_approval(
        self,
        action_id: str,
        approved_by: str,
        approved_at: Optional[str] = None,
        status: str = "APPROVED"
    ) -> None:
        """Mark action as approved with approver identity and timestamp."""
        at = approved_at or utc_now_iso()
        with self.mgr.session() as conn:
            conn.execute(
                """
                UPDATE actions
                SET status = ?,
                    approved_by = ?,
                    approved_at = ?
                WHERE action_id = ? OR id = ?
                """,
                (status, approved_by, at, action_id, action_id)
            )

    def update_rejection(
        self,
        action_id: str,
        rejected_by: str,
        rejection_reason: Optional[str] = None,
        rejected_at: Optional[str] = None,
        status: str = "REJECTED"
    ) -> None:
        """Mark action as rejected with rejector identity, reason, and timestamp."""
        at = rejected_at or utc_now_iso()
        with self.mgr.session() as conn:
            conn.execute(
                """
                UPDATE actions
                SET status = ?,
                    rejected_by = ?,
                    rejected_at = ?,
                    rejection_reason = ?,
                    last_error = ?
                WHERE action_id = ? OR id = ?
                """,
                (status, rejected_by, at, rejection_reason, rejection_reason, action_id, action_id)
            )

    def update_result(
        self,
        action_id: str,
        status: str,
        result_data: Optional[Dict[str, Any]] = None,
        last_error: Optional[str] = None,
        increment_attempt: bool = True
    ) -> None:
        """Update action with final execution status and result_data payload."""
        executed_at = utc_now_iso() if status in ("COMPLETED", "FAILED", "DRY_RUN_SIMULATED") else None
        res_json = json.dumps(result_data) if result_data is not None else None
        dry_run_val = 1 if status == "DRY_RUN_SIMULATED" else (0 if status == "COMPLETED" else None)
        with self.mgr.session() as conn:
            if increment_attempt:
                conn.execute(
                    """
                    UPDATE actions
                    SET status = ?,
                        result_data = COALESCE(?, result_data),
                        last_error = ?,
                        dry_run = COALESCE(?, dry_run),
                        attempt_count = attempt_count + 1,
                        executed_at = COALESCE(?, executed_at)
                    WHERE action_id = ? OR id = ?
                    """,
                    (status, res_json, last_error, dry_run_val, executed_at, action_id, action_id)
                )
            else:
                conn.execute(
                    """
                    UPDATE actions
                    SET status = ?,
                        result_data = COALESCE(?, result_data),
                        last_error = ?,
                        dry_run = COALESCE(?, dry_run),
                        executed_at = COALESCE(?, executed_at)
                    WHERE action_id = ? OR id = ?
                    """,
                    (status, res_json, last_error, dry_run_val, executed_at, action_id, action_id)
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
                d["parameters"] = json.loads(d["parameters"]) if d.get("parameters") else {}
                d["preview"] = json.loads(d["preview"]) if d.get("preview") else None
                if d.get("result_data"):
                    try:
                        d["result_data"] = json.loads(d["result_data"])
                    except Exception:
                        pass
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


class JiraPollingStateRepository:
    """Repository for persisting Jira polling checkpoints across application restarts."""

    def __init__(self, manager: Optional[DatabaseManager] = None):
        self.mgr = manager or db_manager

    def get_checkpoint(self, connector: str = "jira") -> Optional[str]:
        """Fetch the last successful polling timestamp for a connector."""
        from app.database.schema import init_db
        try:
            with self.mgr.session() as conn:
                cursor = conn.execute(
                    "SELECT last_successful_poll FROM jira_polling_state WHERE connector = ?",
                    (connector,)
                )
                row = cursor.fetchone()
                if row and row["last_successful_poll"]:
                    return str(row["last_successful_poll"])
                return None
        except sqlite3.OperationalError:
            init_db(self.mgr)
            return None

    def update_checkpoint(self, connector: str, checkpoint_iso: str) -> None:
        """Atomically record the latest successful poll checkpoint."""
        from app.database.schema import init_db
        sid = str(uuid.uuid4())
        now_str = utc_now_iso()
        try:
            with self.mgr.session() as conn:
                conn.execute(
                    """
                    INSERT INTO jira_polling_state (id, connector, last_successful_poll, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(connector) DO UPDATE SET
                        last_successful_poll = excluded.last_successful_poll,
                        updated_at = excluded.updated_at
                    """,
                    (sid, connector, checkpoint_iso, now_str)
                )
        except sqlite3.OperationalError:
            init_db(self.mgr)
            with self.mgr.session() as conn:
                conn.execute(
                    """
                    INSERT INTO jira_polling_state (id, connector, last_successful_poll, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(connector) DO UPDATE SET
                        last_successful_poll = excluded.last_successful_poll,
                        updated_at = excluded.updated_at
                    """,
                    (sid, connector, checkpoint_iso, now_str)
                )


class JiraIssueStateRepository:
    """Repository for local Jira issue state projection (read cache of Jira).

    Note: Jira remains the authoritative source of truth. This repository stores a
    local projection used for change detection, stale task evaluation, and overdue monitoring.
    """

    def __init__(self, manager: Optional[DatabaseManager] = None):
        self.mgr = manager or db_manager

    def get(self, issue_key: str) -> Optional[Dict[str, Any]]:
        """Retrieve latest known state for a specific Jira issue."""
        from app.database.schema import init_db
        try:
            with self.mgr.session() as conn:
                cursor = conn.execute(
                    "SELECT * FROM jira_issue_state WHERE jira_issue_key = ?",
                    (issue_key,)
                )
                row = cursor.fetchone()
                if row:
                    d = dict(row)
                    if d.get("raw_reference"):
                        try:
                            d["raw_reference"] = json.loads(d["raw_reference"])
                        except Exception:
                            pass
                    return d
                return None
        except sqlite3.OperationalError:
            init_db(self.mgr)
            return None

    def get_by_key(self, jira_issue_key: str) -> Optional[Dict[str, Any]]:
        """Alias for get(jira_issue_key)."""
        return self.get(jira_issue_key)

    def upsert(
        self,
        jira_issue_key: str,
        summary: Optional[str],
        status: str,
        assignee: Optional[str] = None,
        priority: Optional[str] = None,
        due_date: Optional[str] = None,
        updated_at: Optional[str] = None,
        last_seen_at: Optional[str] = None,
        last_activity_at: Optional[str] = None,
        project_key: Optional[str] = None,
        raw_reference: Optional[Dict[str, Any]] = None,
        team_group: Optional[str] = None,
    ) -> None:
        """Upsert an issue state projection."""
        now_str = utc_now_iso()
        seen_str = last_seen_at or now_str

        # If last_activity_at is not explicitly provided, preserve existing or use seen_str
        existing = self.get(jira_issue_key)
        if not last_activity_at:
            last_activity_at = existing["last_activity_at"] if existing and existing.get("last_activity_at") else seen_str

        if not team_group and existing:
            team_group = existing.get("team_group")

        raw_json = json.dumps(raw_reference) if raw_reference is not None else (json.dumps(existing.get("raw_reference")) if existing and existing.get("raw_reference") else None)

        with self.mgr.session() as conn:
            conn.execute(
                """
                INSERT INTO jira_issue_state (
                    jira_issue_key, summary, status, assignee, priority, due_date,
                    updated_at, last_seen_at, last_activity_at, project_key, raw_reference, team_group
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(jira_issue_key) DO UPDATE SET
                    summary = excluded.summary,
                    status = excluded.status,
                    assignee = excluded.assignee,
                    priority = excluded.priority,
                    due_date = excluded.due_date,
                    updated_at = excluded.updated_at,
                    last_seen_at = excluded.last_seen_at,
                    last_activity_at = excluded.last_activity_at,
                    project_key = excluded.project_key,
                    raw_reference = excluded.raw_reference,
                    team_group = excluded.team_group
                """,
                (
                    jira_issue_key,
                    summary,
                    status,
                    assignee,
                    priority,
                    due_date,
                    updated_at,
                    seen_str,
                    last_activity_at,
                    project_key,
                    raw_json,
                    team_group,
                )
            )

    def get_stale_candidates(
        self,
        threshold_hours: int,
        team_group: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Retrieve active issues whose last meaningful activity exceeds threshold_hours."""
        import datetime
        from app.utils.time import utc_now, format_iso

        cutoff = utc_now() - datetime.timedelta(hours=threshold_hours)
        cutoff_iso = format_iso(cutoff)

        with self.mgr.session() as conn:
            if team_group:
                cursor = conn.execute(
                    """
                    SELECT * FROM jira_issue_state
                    WHERE team_group = ?
                      AND lower(status) IN ('in progress', 'doing', 'active', 'in development', 'wip')
                      AND last_activity_at <= ?
                    ORDER BY last_activity_at ASC
                    """,
                    (team_group, cutoff_iso)
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT * FROM jira_issue_state
                    WHERE lower(status) IN ('in progress', 'doing', 'active', 'in development', 'wip')
                      AND last_activity_at <= ?
                    ORDER BY last_activity_at ASC
                    """,
                    (cutoff_iso,)
                )
            results = []
            for row in cursor.fetchall():
                d = dict(row)
                if d.get("raw_reference"):
                    try:
                        d["raw_reference"] = json.loads(d["raw_reference"])
                    except Exception:
                        pass
                results.append(d)
            return results

    def get_overdue_candidates(
        self,
        now_iso: Optional[str] = None,
        team_group: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Retrieve incomplete issues whose due date has passed."""
        from app.utils.time import utc_now_iso

        current_time = now_iso or utc_now_iso()
        # Jira duedates can be YYYY-MM-DD or full ISO strings
        # Extract YYYY-MM-DD for comparison if needed
        date_prefix = current_time[:10]

        with self.mgr.session() as conn:
            if team_group:
                cursor = conn.execute(
                    """
                    SELECT * FROM jira_issue_state
                    WHERE team_group = ?
                      AND due_date IS NOT NULL
                      AND trim(due_date) != ''
                      AND (due_date < ? OR (length(due_date) = 10 AND due_date < ?))
                      AND lower(status) NOT IN ('done', 'completed', 'resolved', 'closed', 'finished')
                    ORDER BY due_date ASC
                    """,
                    (team_group, current_time, date_prefix)
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT * FROM jira_issue_state
                    WHERE due_date IS NOT NULL
                      AND trim(due_date) != ''
                      AND (due_date < ? OR (length(due_date) = 10 AND due_date < ?))
                      AND lower(status) NOT IN ('done', 'completed', 'resolved', 'closed', 'finished')
                    ORDER BY due_date ASC
                    """,
                    (current_time, date_prefix)
                )
            results = []
            for row in cursor.fetchall():
                d = dict(row)
                if d.get("raw_reference"):
                    try:
                        d["raw_reference"] = json.loads(d["raw_reference"])
                    except Exception:
                        pass
                results.append(d)
            return results

    def get_reopened_candidates(
        self,
        team_group: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Retrieve active issues that have been reopened (via TaskReopened event or 'reopened' status)."""
        with self.mgr.session() as conn:
            done_clause = "lower(status) NOT IN ('done', 'completed', 'resolved', 'closed', 'cancelled', 'finished')"
            if team_group:
                cursor = conn.execute(
                    f"""
                    SELECT * FROM jira_issue_state
                    WHERE team_group = ?
                      AND {done_clause}
                      AND (
                          jira_issue_key IN (SELECT task_id FROM events WHERE event_type = 'TaskReopened')
                          OR lower(status) IN ('reopened', 're-opened')
                      )
                    ORDER BY updated_at DESC, jira_issue_key ASC
                    """,
                    (team_group,)
                )
            else:
                cursor = conn.execute(
                    f"""
                    SELECT * FROM jira_issue_state
                    WHERE {done_clause}
                      AND (
                          jira_issue_key IN (SELECT task_id FROM events WHERE event_type = 'TaskReopened')
                          OR lower(status) IN ('reopened', 're-opened')
                      )
                    ORDER BY updated_at DESC, jira_issue_key ASC
                    """
                )
            results = []
            for row in cursor.fetchall():
                d = dict(row)
                if d.get("raw_reference"):
                    try:
                        d["raw_reference"] = json.loads(d["raw_reference"])
                    except Exception:
                        pass
                results.append(d)
            return results

    def get_unassigned_candidates(
        self,
        team_group: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Retrieve active issues without an assignee."""
        with self.mgr.session() as conn:
            done_clause = "lower(status) NOT IN ('done', 'completed', 'resolved', 'closed', 'cancelled', 'finished')"
            unassigned_clause = "(assignee IS NULL OR trim(assignee) = '' OR lower(assignee) = 'unassigned')"
            if team_group:
                cursor = conn.execute(
                    f"""
                    SELECT * FROM jira_issue_state
                    WHERE team_group = ?
                      AND {done_clause}
                      AND {unassigned_clause}
                    ORDER BY updated_at DESC, jira_issue_key ASC
                    """,
                    (team_group,)
                )
            else:
                cursor = conn.execute(
                    f"""
                    SELECT * FROM jira_issue_state
                    WHERE {done_clause}
                      AND {unassigned_clause}
                    ORDER BY updated_at DESC, jira_issue_key ASC
                    """
                )
            results = []
            for row in cursor.fetchall():
                d = dict(row)
                if d.get("raw_reference"):
                    try:
                        d["raw_reference"] = json.loads(d["raw_reference"])
                    except Exception:
                        pass
                results.append(d)
            return results

    def list_all(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self.mgr.session() as conn:
            cursor = conn.execute(
                "SELECT * FROM jira_issue_state ORDER BY last_seen_at DESC LIMIT ?",
                (limit,)
            )
            return [dict(r) for r in cursor.fetchall()]


class JiraWorklogRepository:
    """Repository for persisting and querying Jira worklog records."""

    def __init__(self, manager: Optional[DatabaseManager] = None):
        self.mgr = manager or db_manager

    def upsert_worklog(
        self,
        worklog_id: str,
        jira_issue_key: str,
        time_spent_seconds: int,
        started_at: str,
        jira_issue_id: Optional[str] = None,
        author_account_id: Optional[str] = None,
        author_display_name: Optional[str] = None,
        created_at: Optional[str] = None,
        updated_at: Optional[str] = None,
        comment: Optional[str] = None,
        team_group: Optional[str] = None,
        source: str = "jira"
    ) -> None:
        """Idempotently insert or update a worklog record."""
        with self.mgr.session() as conn:
            conn.execute(
                """
                INSERT INTO jira_worklogs (
                    worklog_id, jira_issue_key, jira_issue_id, author_account_id,
                    author_display_name, time_spent_seconds, started_at, created_at,
                    updated_at, comment, team_group, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(worklog_id) DO UPDATE SET
                    jira_issue_key = excluded.jira_issue_key,
                    jira_issue_id = COALESCE(excluded.jira_issue_id, jira_worklogs.jira_issue_id),
                    author_account_id = COALESCE(excluded.author_account_id, jira_worklogs.author_account_id),
                    author_display_name = COALESCE(excluded.author_display_name, jira_worklogs.author_display_name),
                    time_spent_seconds = excluded.time_spent_seconds,
                    started_at = excluded.started_at,
                    updated_at = COALESCE(excluded.updated_at, jira_worklogs.updated_at),
                    comment = COALESCE(excluded.comment, jira_worklogs.comment),
                    team_group = COALESCE(excluded.team_group, jira_worklogs.team_group),
                    source = excluded.source
                """,
                (
                    str(worklog_id),
                    jira_issue_key,
                    jira_issue_id,
                    author_account_id,
                    author_display_name,
                    int(time_spent_seconds),
                    started_at,
                    created_at,
                    updated_at,
                    comment,
                    team_group,
                    source
                )
            )

    def get_worklogs_for_date(
        self,
        date_str: str,
        team_group: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Retrieve worklogs where started_at matches the given YYYY-MM-DD date."""
        prefix = date_str[:10]
        with self.mgr.session() as conn:
            if team_group:
                cursor = conn.execute(
                    """
                    SELECT * FROM jira_worklogs
                    WHERE substr(started_at, 1, 10) = ?
                      AND team_group = ?
                    ORDER BY started_at ASC
                    """,
                    (prefix, team_group)
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT * FROM jira_worklogs
                    WHERE substr(started_at, 1, 10) = ?
                    ORDER BY started_at ASC
                    """,
                    (prefix,)
                )
            return [dict(r) for r in cursor.fetchall()]

    def get_worklogs_for_range(
        self,
        start_date: str,
        end_date: str,
        team_group: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Retrieve worklogs started between start_date and end_date (inclusive, YYYY-MM-DD)."""
        s_pref = start_date[:10]
        e_pref = end_date[:10]
        with self.mgr.session() as conn:
            if team_group:
                cursor = conn.execute(
                    """
                    SELECT * FROM jira_worklogs
                    WHERE substr(started_at, 1, 10) >= ?
                      AND substr(started_at, 1, 10) <= ?
                      AND team_group = ?
                    ORDER BY started_at ASC
                    """,
                    (s_pref, e_pref, team_group)
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT * FROM jira_worklogs
                    WHERE substr(started_at, 1, 10) >= ?
                      AND substr(started_at, 1, 10) <= ?
                    ORDER BY started_at ASC
                    """,
                    (s_pref, e_pref)
                )
            return [dict(r) for r in cursor.fetchall()]

    def count(self, team_group: Optional[str] = None) -> int:
        with self.mgr.session() as conn:
            if team_group:
                cursor = conn.execute("SELECT COUNT(*) FROM jira_worklogs WHERE team_group = ?", (team_group,))
            else:
                cursor = conn.execute("SELECT COUNT(*) FROM jira_worklogs")
            return cursor.fetchone()[0]


class DailyReportHistoryRepository:
    """Repository for recording and checking daily report generation history for idempotency."""

    def __init__(self, manager: Optional[DatabaseManager] = None):
        self.mgr = manager or db_manager

    def has_report_been_sent(
        self,
        team_group: str,
        report_date: str,
        report_type: str = "daily_worklog"
    ) -> bool:
        """Check if report has already been dispatched to Discord."""
        clean_date = report_date[:10]
        with self.mgr.session() as conn:
            cursor = conn.execute(
                """
                SELECT sent_to_discord FROM daily_report_history
                WHERE team_group = ? AND report_date = ? AND report_type = ?
                """,
                (team_group, clean_date, report_type)
            )
            row = cursor.fetchone()
            if row:
                return bool(row["sent_to_discord"])
            return False

    def record_report_sent(
        self,
        team_group: str,
        report_date: str,
        payload: Dict[str, Any],
        report_type: str = "daily_worklog"
    ) -> None:
        """Record that report was generated and dispatched."""
        from app.utils.time import utc_now_iso
        clean_date = report_date[:10]
        rec_id = f"{report_type}:{team_group}:{clean_date}"
        with self.mgr.session() as conn:
            conn.execute(
                """
                INSERT INTO daily_report_history (
                    id, team_group, report_date, report_type, generated_at, sent_to_discord, report_payload
                ) VALUES (?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(id) DO UPDATE SET
                    generated_at = excluded.generated_at,
                    sent_to_discord = 1,
                    report_payload = excluded.report_payload
                """,
                (rec_id, team_group, clean_date, report_type, utc_now_iso(), json.dumps(payload))
            )


class PerformanceRepository:
    """Repository for managing performance foundation runs, profiles, statistics, forecasts, signals, and evidence."""

    def __init__(self, manager: Optional[DatabaseManager] = None):
        self.mgr = manager or db_manager

    def record_analysis_run(self, run: Dict[str, Any]) -> None:
        """Insert or update a performance analysis run metadata record."""
        with self.mgr.session() as conn:
            conn.execute(
                """
                INSERT INTO performance_analysis_runs (
                    analysis_run_id, calculated_at, analysis_window_start, analysis_window_end,
                    requested_history_days, actual_available_history_days,
                    algorithm_version, team_group, resources_analyzed, tasks_analyzed,
                    unresolved_employees_count, duration_ms, status, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(analysis_run_id) DO UPDATE SET
                    resources_analyzed = excluded.resources_analyzed,
                    tasks_analyzed = excluded.tasks_analyzed,
                    unresolved_employees_count = excluded.unresolved_employees_count,
                    duration_ms = excluded.duration_ms,
                    status = excluded.status,
                    error_message = excluded.error_message
                """,
                (
                    run["analysis_run_id"],
                    run["calculated_at"],
                    run["analysis_window_start"],
                    run["analysis_window_end"],
                    run.get("requested_history_days", 365),
                    run.get("actual_available_history_days", 0),
                    run.get("algorithm_version", "1.0.0"),
                    run.get("team_group"),
                    run.get("resources_analyzed", 0),
                    run.get("tasks_analyzed", 0),
                    run.get("unresolved_employees_count", 0),
                    run.get("duration_ms", 0),
                    run.get("status", "COMPLETED"),
                    run.get("error_message"),
                ),
            )

    def get_latest_analysis_run(self, team_group: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Fetch the most recent completed performance analysis run."""
        with self.mgr.session() as conn:
            if team_group:
                cursor = conn.execute(
                    """
                    SELECT * FROM performance_analysis_runs
                    WHERE team_group = ? AND status = 'COMPLETED'
                    ORDER BY calculated_at DESC LIMIT 1
                    """,
                    (team_group,),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT * FROM performance_analysis_runs
                    WHERE status = 'COMPLETED'
                    ORDER BY calculated_at DESC LIMIT 1
                    """
                )
            row = cursor.fetchone()
            return dict(row) if row else None

    def upsert_profile(self, p: Dict[str, Any]) -> None:
        """Insert or update a ResourcePerformanceProfile record."""
        rec_id = f"perf:{p['account_id']}:{p['analysis_run_id']}"
        raw_json = json.dumps(p)
        with self.mgr.session() as conn:
            conn.execute(
                """
                INSERT INTO resource_performance_profiles (
                    id, analysis_run_id, account_id, display_name, role, designation, role_category, team_group,
                    analysis_start, analysis_end, history_days, requested_history_days, actual_available_history_days,
                    completed_tasks, active_working_days, total_logged_seconds,
                    average_logged_hours_per_active_day, median_logged_hours_per_active_day,
                    tasks_due, tasks_completed_on_time, tasks_completed_late, on_time_rate,
                    average_days_late, median_days_late, average_task_hours, median_task_hours,
                    p25_task_hours, p75_task_hours, estimated_tasks, average_estimated_hours,
                    average_actual_hours, estimation_variance_percent, median_estimation_variance_percent,
                    reopened_tasks, reopen_rate, blocker_count, blocked_seconds, average_blocker_hours,
                    nominal_daily_capacity_hours, observed_daily_capacity_hours, forecast_daily_capacity_hours,
                    current_queue_task_count, current_queue_expected_hours, current_queue_review_buffer_hours,
                    current_queue_total_expected_hours, available_capacity_hours, capacity_difference_hours,
                    forecast_status, forecast_reason, projected_queue_completion_date,
                    confidence_level, raw_profile_json, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?, ?
                )
                ON CONFLICT(id) DO UPDATE SET
                    display_name = excluded.display_name,
                    role = excluded.role,
                    designation = excluded.designation,
                    role_category = excluded.role_category,
                    team_group = excluded.team_group,
                    analysis_start = excluded.analysis_start,
                    analysis_end = excluded.analysis_end,
                    history_days = excluded.history_days,
                    requested_history_days = excluded.requested_history_days,
                    actual_available_history_days = excluded.actual_available_history_days,
                    completed_tasks = excluded.completed_tasks,
                    active_working_days = excluded.active_working_days,
                    total_logged_seconds = excluded.total_logged_seconds,
                    average_logged_hours_per_active_day = excluded.average_logged_hours_per_active_day,
                    median_logged_hours_per_active_day = excluded.median_logged_hours_per_active_day,
                    tasks_due = excluded.tasks_due,
                    tasks_completed_on_time = excluded.tasks_completed_on_time,
                    tasks_completed_late = excluded.tasks_completed_late,
                    on_time_rate = excluded.on_time_rate,
                    average_days_late = excluded.average_days_late,
                    median_days_late = excluded.median_days_late,
                    average_task_hours = excluded.average_task_hours,
                    median_task_hours = excluded.median_task_hours,
                    p25_task_hours = excluded.p25_task_hours,
                    p75_task_hours = excluded.p75_task_hours,
                    estimated_tasks = excluded.estimated_tasks,
                    average_estimated_hours = excluded.average_estimated_hours,
                    average_actual_hours = excluded.average_actual_hours,
                    estimation_variance_percent = excluded.estimation_variance_percent,
                    median_estimation_variance_percent = excluded.median_estimation_variance_percent,
                    reopened_tasks = excluded.reopened_tasks,
                    reopen_rate = excluded.reopen_rate,
                    blocker_count = excluded.blocker_count,
                    blocked_seconds = excluded.blocked_seconds,
                    average_blocker_hours = excluded.average_blocker_hours,
                    nominal_daily_capacity_hours = excluded.nominal_daily_capacity_hours,
                    observed_daily_capacity_hours = excluded.observed_daily_capacity_hours,
                    forecast_daily_capacity_hours = excluded.forecast_daily_capacity_hours,
                    current_queue_task_count = excluded.current_queue_task_count,
                    current_queue_expected_hours = excluded.current_queue_expected_hours,
                    current_queue_review_buffer_hours = excluded.current_queue_review_buffer_hours,
                    current_queue_total_expected_hours = excluded.current_queue_total_expected_hours,
                    available_capacity_hours = excluded.available_capacity_hours,
                    capacity_difference_hours = excluded.capacity_difference_hours,
                    forecast_status = excluded.forecast_status,
                    forecast_reason = excluded.forecast_reason,
                    projected_queue_completion_date = excluded.projected_queue_completion_date,
                    confidence_level = excluded.confidence_level,
                    raw_profile_json = excluded.raw_profile_json,
                    updated_at = excluded.updated_at
                """,
                (
                    rec_id,
                    p["analysis_run_id"],
                    p["account_id"],
                    p["display_name"],
                    p.get("role", "Unknown"),
                    p.get("designation"),
                    p.get("role_category"),
                    p.get("team_group"),
                    p["analysis_start"],
                    p["analysis_end"],
                    p.get("history_days", 365),
                    p.get("requested_history_days", 365),
                    p.get("actual_available_history_days", 0),
                    p.get("completed_tasks", 0),
                    p.get("active_working_days", 0),
                    p.get("total_logged_seconds", 0),
                    p.get("average_logged_hours_per_active_day", 0.0),
                    p.get("median_logged_hours_per_active_day", 0.0),
                    p.get("tasks_due", 0),
                    p.get("tasks_completed_on_time", 0),
                    p.get("tasks_completed_late", 0),
                    p.get("on_time_rate", 0.0),
                    p.get("average_days_late", 0.0),
                    p.get("median_days_late", 0.0),
                    p.get("average_task_hours", 0.0),
                    p.get("median_task_hours", 0.0),
                    p.get("p25_task_hours", 0.0),
                    p.get("p75_task_hours", 0.0),
                    p.get("estimated_tasks", 0),
                    p.get("average_estimated_hours", 0.0),
                    p.get("average_actual_hours", 0.0),
                    p.get("estimation_variance_percent", 0.0),
                    p.get("median_estimation_variance_percent", 0.0),
                    p.get("reopened_tasks", 0),
                    p.get("reopen_rate", 0.0),
                    p.get("blocker_count", 0),
                    p.get("blocked_seconds", 0),
                    p.get("average_blocker_hours", 0.0),
                    p.get("nominal_daily_capacity_hours", 6.75),
                    p.get("observed_daily_capacity_hours", 6.75),
                    p.get("forecast_daily_capacity_hours", 6.75),
                    p.get("current_queue_task_count", 0),
                    p.get("current_queue_expected_hours", 0.0),
                    p.get("current_queue_review_buffer_hours", 0.0),
                    p.get("current_queue_total_expected_hours", 0.0),
                    p.get("available_capacity_hours", 0.0),
                    p.get("capacity_difference_hours", 0.0),
                    p.get("forecast_status", "GREEN"),
                    p.get("forecast_reason"),
                    p.get("projected_queue_completion_date"),
                    p.get("confidence_level", "LOW"),
                    raw_json,
                    p.get("created_at", ""),
                    p.get("updated_at", ""),
                ),
            )

    def get_profile(self, account_id: str, run_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Retrieve latest or run-specific ResourcePerformanceProfile for a resource."""
        with self.mgr.session() as conn:
            if run_id:
                cursor = conn.execute(
                    """
                    SELECT * FROM resource_performance_profiles
                    WHERE account_id = ? AND analysis_run_id = ?
                    """,
                    (account_id, run_id),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT * FROM resource_performance_profiles
                    WHERE account_id = ?
                    ORDER BY updated_at DESC LIMIT 1
                    """,
                    (account_id,),
                )
            row = cursor.fetchone()
            if not row:
                return None
            res = dict(row)
            if res.get("raw_profile_json"):
                try:
                    res["profile"] = json.loads(res["raw_profile_json"])
                except Exception:
                    pass
            return res

    def list_profiles(
        self,
        team_group: Optional[str] = None,
        run_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """List profiles for a team group or run."""
        with self.mgr.session() as conn:
            if run_id:
                if team_group:
                    cursor = conn.execute(
                        """
                        SELECT * FROM resource_performance_profiles
                        WHERE team_group = ? AND analysis_run_id = ?
                        ORDER BY display_name ASC
                        """,
                        (team_group, run_id),
                    )
                else:
                    cursor = conn.execute(
                        """
                        SELECT * FROM resource_performance_profiles
                        WHERE analysis_run_id = ?
                        ORDER BY display_name ASC
                        """,
                        (run_id,),
                    )
            else:
                # Get most recent profile per account_id
                if team_group:
                    cursor = conn.execute(
                        """
                        SELECT p.* FROM resource_performance_profiles p
                        INNER JOIN (
                            SELECT account_id, MAX(updated_at) as max_up
                            FROM resource_performance_profiles
                            WHERE team_group = ?
                            GROUP BY account_id
                        ) latest ON p.account_id = latest.account_id AND p.updated_at = latest.max_up
                        ORDER BY p.display_name ASC
                        """,
                        (team_group,),
                    )
                else:
                    cursor = conn.execute(
                        """
                        SELECT p.* FROM resource_performance_profiles p
                        INNER JOIN (
                            SELECT account_id, MAX(updated_at) as max_up
                            FROM resource_performance_profiles
                            GROUP BY account_id
                        ) latest ON p.account_id = latest.account_id AND p.updated_at = latest.max_up
                        ORDER BY p.display_name ASC
                        """
                    )
            rows = cursor.fetchall()
            out = []
            for r in rows:
                d = dict(r)
                if d.get("raw_profile_json"):
                    try:
                        d["profile"] = json.loads(d["raw_profile_json"])
                    except Exception:
                        pass
                out.append(d)
            return out

    def upsert_effort_statistics_batch(self, stats: List[Dict[str, Any]]) -> None:
        """Insert or replace effort statistics records in batch."""
        if not stats:
            return
        with self.mgr.session() as conn:
            for s in stats:
                rec_id = f"eff:{s['account_id']}:{s['segment_type']}:{s['segment_key']}:{s['analysis_run_id']}"
                conn.execute(
                    """
                    INSERT INTO resource_effort_statistics (
                        id, analysis_run_id, account_id, segment_type, segment_key,
                        sample_count, mean_hours, median_hours, p25_hours, p75_hours,
                        min_hours, max_hours, confidence, is_fallback, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        sample_count = excluded.sample_count,
                        mean_hours = excluded.mean_hours,
                        median_hours = excluded.median_hours,
                        p25_hours = excluded.p25_hours,
                        p75_hours = excluded.p75_hours,
                        min_hours = excluded.min_hours,
                        max_hours = excluded.max_hours,
                        confidence = excluded.confidence,
                        is_fallback = excluded.is_fallback,
                        updated_at = excluded.updated_at
                    """,
                    (
                        rec_id,
                        s["analysis_run_id"],
                        s["account_id"],
                        s["segment_type"],
                        s["segment_key"],
                        s.get("sample_count", 0),
                        s.get("mean_hours", 0.0),
                        s.get("median_hours", 0.0),
                        s.get("p25_hours", 0.0),
                        s.get("p75_hours", 0.0),
                        s.get("min_hours", 0.0),
                        s.get("max_hours", 0.0),
                        s.get("confidence", "LOW"),
                        1 if s.get("is_fallback") else 0,
                        s.get("updated_at", ""),
                    ),
                )

    def get_effort_statistics(
        self,
        account_id: str,
        run_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Retrieve effort statistics for an account."""
        with self.mgr.session() as conn:
            if run_id:
                cursor = conn.execute(
                    """
                    SELECT * FROM resource_effort_statistics
                    WHERE account_id = ? AND analysis_run_id = ?
                    ORDER BY segment_type ASC, segment_key ASC
                    """,
                    (account_id, run_id),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT * FROM resource_effort_statistics
                    WHERE account_id = ?
                    ORDER BY updated_at DESC, segment_type ASC
                    """,
                    (account_id,),
                )
            return [dict(r) for r in cursor.fetchall()]

    def upsert_task_classifications_batch(self, classifications: List[Dict[str, Any]]) -> None:
        """Insert or replace task classification records."""
        if not classifications:
            return
        with self.mgr.session() as conn:
            for c in classifications:
                rec_id = f"class:{c['issue_key']}:{c['analysis_run_id']}"
                conn.execute(
                    """
                    INSERT INTO resource_task_classifications (
                        id, analysis_run_id, issue_key, issue_type, priority, project_key,
                        components, labels, complexity_score, complexity_factors,
                        complexity_confidence, estimated_seconds, actual_logged_seconds,
                        status, is_completed, reopen_count, blocker_detected,
                        blocker_hours, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        issue_type = excluded.issue_type,
                        priority = excluded.priority,
                        project_key = excluded.project_key,
                        components = excluded.components,
                        labels = excluded.labels,
                        complexity_score = excluded.complexity_score,
                        complexity_factors = excluded.complexity_factors,
                        complexity_confidence = excluded.complexity_confidence,
                        estimated_seconds = excluded.estimated_seconds,
                        actual_logged_seconds = excluded.actual_logged_seconds,
                        status = excluded.status,
                        is_completed = excluded.is_completed,
                        reopen_count = excluded.reopen_count,
                        blocker_detected = excluded.blocker_detected,
                        blocker_hours = excluded.blocker_hours,
                        updated_at = excluded.updated_at
                    """,
                    (
                        rec_id,
                        c["analysis_run_id"],
                        c["issue_key"],
                        c.get("issue_type"),
                        c.get("priority"),
                        c.get("project_key"),
                        json.dumps(c.get("components", [])) if isinstance(c.get("components"), list) else c.get("components"),
                        json.dumps(c.get("labels", [])) if isinstance(c.get("labels"), list) else c.get("labels"),
                        c.get("complexity_score", 3),
                        json.dumps(c.get("complexity_factors", [])) if isinstance(c.get("complexity_factors"), list) else c.get("complexity_factors"),
                        c.get("complexity_confidence", "MEDIUM"),
                        c.get("estimated_seconds"),
                        c.get("actual_logged_seconds", 0),
                        c.get("status"),
                        1 if c.get("is_completed") else 0,
                        c.get("reopen_count", 0),
                        1 if c.get("blocker_detected") else 0,
                        c.get("blocker_hours", 0.0),
                        c.get("updated_at", ""),
                    ),
                )

    def upsert_task_forecasts_batch(self, forecasts: List[Dict[str, Any]]) -> None:
        """Insert or replace task delivery forecasts."""
        if not forecasts:
            return
        with self.mgr.session() as conn:
            for f in forecasts:
                rec_id = f"fcst:{f['issue_key']}:{f['analysis_run_id']}"
                conn.execute(
                    """
                    INSERT INTO task_delivery_forecasts (
                        id, analysis_run_id, issue_key, account_id, due_date,
                        jira_remaining_hours, inferred_expected_hours, inferred_remaining_hours,
                        expected_base_hours, review_buffer_hours, total_expected_hours,
                        logged_hours, remaining_hours, expected_effort_source,
                        expected_effort_confidence, sample_size, designation, role_category,
                        projected_completion_date, slack_hours, risk_level,
                        risk_reason, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        account_id = excluded.account_id,
                        due_date = excluded.due_date,
                        jira_remaining_hours = excluded.jira_remaining_hours,
                        inferred_expected_hours = excluded.inferred_expected_hours,
                        inferred_remaining_hours = excluded.inferred_remaining_hours,
                        expected_base_hours = excluded.expected_base_hours,
                        review_buffer_hours = excluded.review_buffer_hours,
                        total_expected_hours = excluded.total_expected_hours,
                        logged_hours = excluded.logged_hours,
                        remaining_hours = excluded.remaining_hours,
                        expected_effort_source = excluded.expected_effort_source,
                        expected_effort_confidence = excluded.expected_effort_confidence,
                        sample_size = excluded.sample_size,
                        designation = excluded.designation,
                        role_category = excluded.role_category,
                        projected_completion_date = excluded.projected_completion_date,
                        slack_hours = excluded.slack_hours,
                        risk_level = excluded.risk_level,
                        risk_reason = excluded.risk_reason,
                        updated_at = excluded.updated_at
                    """,
                    (
                        rec_id,
                        f["analysis_run_id"],
                        f["issue_key"],
                        f.get("account_id"),
                        f.get("due_date"),
                        f.get("jira_remaining_hours"),
                        f.get("inferred_expected_hours", 0.0),
                        f.get("inferred_remaining_hours", 0.0),
                        f.get("expected_base_hours", 0.0),
                        f.get("review_buffer_hours", 0.0),
                        f.get("total_expected_hours", 0.0),
                        f.get("logged_hours", 0.0),
                        f.get("remaining_hours", 0.0),
                        f.get("expected_effort_source", "deterministic_fallback"),
                        f.get("expected_effort_confidence", "medium"),
                        f.get("sample_size", 0),
                        f.get("designation"),
                        f.get("role_category"),
                        f.get("projected_completion_date"),
                        f.get("slack_hours", 0.0),
                        f.get("risk_level", "GREEN"),
                        f.get("risk_reason"),
                        f.get("updated_at", ""),
                    ),
                )

    def get_task_forecasts(
        self,
        account_id: Optional[str] = None,
        run_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Retrieve task delivery forecasts."""
        with self.mgr.session() as conn:
            if account_id and run_id:
                cursor = conn.execute(
                    """
                    SELECT * FROM task_delivery_forecasts
                    WHERE account_id = ? AND analysis_run_id = ?
                    ORDER BY projected_completion_date ASC
                    """,
                    (account_id, run_id),
                )
            elif account_id:
                cursor = conn.execute(
                    """
                    SELECT * FROM task_delivery_forecasts
                    WHERE account_id = ?
                    ORDER BY updated_at DESC, projected_completion_date ASC
                    """,
                    (account_id,),
                )
            elif run_id:
                cursor = conn.execute(
                    """
                    SELECT * FROM task_delivery_forecasts
                    WHERE analysis_run_id = ?
                    ORDER BY projected_completion_date ASC
                    """,
                    (run_id,),
                )
            else:
                cursor = conn.execute("SELECT * FROM task_delivery_forecasts ORDER BY updated_at DESC")
            return [dict(r) for r in cursor.fetchall()]

    def upsert_signals_batch(self, signals: List[Dict[str, Any]]) -> None:
        """Insert or replace performance signals."""
        if not signals:
            return
        with self.mgr.session() as conn:
            for s in signals:
                sig_type = s["signal_type"] if isinstance(s["signal_type"], str) else s["signal_type"].value
                rec_id = f"sig:{s['account_id']}:{sig_type}:{s['analysis_run_id']}"
                conn.execute(
                    """
                    INSERT INTO performance_signals (
                        id, analysis_run_id, account_id, signal_type, signal_value,
                        threshold_value, evidence_text, confidence, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        signal_value = excluded.signal_value,
                        threshold_value = excluded.threshold_value,
                        evidence_text = excluded.evidence_text,
                        confidence = excluded.confidence,
                        created_at = excluded.created_at
                    """,
                    (
                        rec_id,
                        s["analysis_run_id"],
                        s["account_id"],
                        sig_type,
                        s.get("signal_value"),
                        s.get("threshold_value"),
                        s.get("evidence_text", ""),
                        s.get("confidence", "MEDIUM"),
                        s.get("created_at", ""),
                    ),
                )

    def get_signals(
        self,
        account_id: Optional[str] = None,
        run_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Retrieve performance signals."""
        with self.mgr.session() as conn:
            if account_id and run_id:
                cursor = conn.execute(
                    """
                    SELECT * FROM performance_signals
                    WHERE account_id = ? AND analysis_run_id = ?
                    ORDER BY created_at DESC
                    """,
                    (account_id, run_id),
                )
            elif account_id:
                cursor = conn.execute(
                    """
                    SELECT * FROM performance_signals
                    WHERE account_id = ?
                    ORDER BY created_at DESC
                    """,
                    (account_id,),
                )
            else:
                cursor = conn.execute("SELECT * FROM performance_signals ORDER BY created_at DESC")
            return [dict(r) for r in cursor.fetchall()]

    def insert_evidence_batch(self, evidence_list: List[Dict[str, Any]]) -> None:
        """Insert evidence records idempotently."""
        if not evidence_list:
            return
        with self.mgr.session() as conn:
            for e in evidence_list:
                conn.execute(
                    """
                    INSERT INTO performance_evidence (
                        evidence_id, analysis_run_id, account_id, issue_key, evidence_type,
                        observed_value, expected_value, difference, source,
                        confidence, explanation, timestamp, snapshot_date
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(evidence_id) DO NOTHING
                    """,
                    (
                        e["evidence_id"],
                        e["analysis_run_id"],
                        e["account_id"],
                        e.get("issue_key"),
                        e["evidence_type"],
                        e.get("observed_value"),
                        e.get("expected_value"),
                        e.get("difference"),
                        e.get("source", "jira"),
                        e.get("confidence", "MEDIUM"),
                        e.get("explanation", ""),
                        e.get("timestamp", ""),
                        e.get("snapshot_date", ""),
                    ),
                )

    def get_evidence(
        self,
        account_id: Optional[str] = None,
        issue_key: Optional[str] = None,
        run_id: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Query evidence ledger."""
        with self.mgr.session() as conn:
            clauses = []
            params = []
            if account_id:
                clauses.append("account_id = ?")
                params.append(account_id)
            if issue_key:
                clauses.append("issue_key = ?")
                params.append(issue_key)
            if run_id:
                clauses.append("analysis_run_id = ?")
                params.append(run_id)

            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            params.append(limit)
            cursor = conn.execute(
                f"SELECT * FROM performance_evidence {where} ORDER BY timestamp DESC LIMIT ?",
                params,
            )
            return [dict(r) for r in cursor.fetchall()]


class EmployeeRoleRepository:
    """Repository for authoritative employee designations and role category assignments."""

    def __init__(self, manager: Optional[DatabaseManager] = None):
        self.mgr = manager or db_manager

    def get_by_account_id(self, account_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve authoritative designation/role assignment by Jira account_id (or legacy alias)."""
        if not account_id:
            return None
        # Canonical legacy aliases mapping
        legacy_map = {
            "ahsan.amin": "712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de",
            "jira-user-ahsan": "712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de",
        }
        target_id = legacy_map.get(str(account_id).strip().lower(), str(account_id).strip())

        with self.mgr.session() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM employee_role_assignments
                WHERE account_id = ?
                LIMIT 1
                """,
                (target_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_by_display_name(self, display_name: str) -> Optional[Dict[str, Any]]:
        """Retrieve authoritative designation/role assignment by exact or normalized display_name."""
        with self.mgr.session() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM employee_role_assignments
                WHERE LOWER(display_name) = LOWER(?)
                LIMIT 1
                """,
                (display_name.strip(),),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def list_assignments(self) -> List[Dict[str, Any]]:
        """List all authoritative employee role assignments."""
        with self.mgr.session() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM employee_role_assignments
                ORDER BY display_name ASC
                """
            )
            return [dict(r) for r in cursor.fetchall()]

    def upsert_assignment(
        self,
        account_id: str,
        display_name: str,
        designation: str,
        role_category: str,
        effective_from: Optional[str] = None,
        effective_to: Optional[str] = None,
        source: str = "manual_admin",
    ) -> None:
        """Insert or update an employee role assignment."""
        now_str = utc_now_iso()
        rec_id = f"role:{account_id}"
        with self.mgr.session() as conn:
            conn.execute(
                """
                INSERT INTO employee_role_assignments (
                    id, account_id, display_name, designation, role_category,
                    effective_from, effective_to, source, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    display_name = excluded.display_name,
                    designation = excluded.designation,
                    role_category = excluded.role_category,
                    effective_from = excluded.effective_from,
                    effective_to = excluded.effective_to,
                    source = excluded.source,
                    updated_at = excluded.updated_at
                """,
                (
                    rec_id,
                    account_id,
                    display_name,
                    designation,
                    role_category,
                    effective_from,
                    effective_to,
                    source,
                    now_str,
                    now_str,
                ),
            )

    def get_unresolved_employees(
        self, active_resources: Optional[List[Dict[str, Any]]] = None
    ) -> List[Dict[str, Any]]:
        """Find active team members who lack an authoritative role assignment."""
        legacy_map = {
            "ahsan.amin": "712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de",
            "jira-user-ahsan": "712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de",
        }
        with self.mgr.session() as conn:
            cursor = conn.execute("SELECT account_id, display_name FROM employee_role_assignments")
            rows = cursor.fetchall()
            known_ids = {row["account_id"] for row in rows}
            known_names = {row["display_name"].strip().lower() for row in rows}

        if active_resources is None:
            active_resources = []
            with self.mgr.session() as conn:
                cur = conn.execute(
                    """
                    SELECT DISTINCT author_account_id as account_id, author_display_name as display_name, team_group
                    FROM jira_worklogs
                    WHERE author_account_id IS NOT NULL
                    """
                )
                active_resources.extend([dict(r) for r in cur.fetchall()])
                cur2 = conn.execute(
                    """
                    SELECT DISTINCT raw_reference, assignee, team_group
                    FROM jira_issue_state
                    WHERE assignee IS NOT NULL
                    """
                )
                for r in cur2.fetchall():
                    raw = json.loads(r["raw_reference"]) if r["raw_reference"] else {}
                    aid = raw.get("fields", {}).get("assignee", {}).get("accountId") or raw.get("assignee_account_id") or r["assignee"]
                    active_resources.append({"account_id": aid, "display_name": r["assignee"], "team_group": r["team_group"]})

        unresolved = []
        seen = set()
        for res in active_resources:
            raw_id = res.get("account_id")
            d_name = res.get("display_name", "").strip()
            if not raw_id:
                continue

            can_id = legacy_map.get(str(raw_id).strip().lower(), str(raw_id).strip())
            if can_id in known_ids or (d_name and d_name.lower() in known_names) or can_id in seen or raw_id in seen:
                continue

            seen.add(can_id)
            seen.add(raw_id)
            unresolved.append(
                {
                    "account_id": raw_id,
                    "display_name": d_name or "Unknown",
                    "team_group": res.get("team_group"),
                    "resolved": False,
                    "reason": "No authoritative designation found in employee_role_assignments",
                }
            )
        return unresolved


class PerformanceValidationRepository:
    """Repository for persisting and retrieving Data Quality & Analytics Validation reports."""

    def __init__(self, manager: Optional[DatabaseManager] = None):
        self.mgr = manager or db_manager

    def upsert_report(
        self,
        validation_id: str,
        analysis_run_id: str,
        recommendation: str,
        summary: Dict[str, Any],
        raw_report: Dict[str, Any],
        team_group: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> None:
        """Persist a validation report snapshot."""
        now_str = created_at or utc_now_iso()
        summary_json = json.dumps(summary)
        raw_report_json = json.dumps(raw_report)
        with self.mgr.session() as conn:
            conn.execute(
                """
                INSERT INTO performance_validation_reports (
                    id, analysis_run_id, team_group, recommendation, summary_json, raw_report_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    analysis_run_id = excluded.analysis_run_id,
                    team_group = excluded.team_group,
                    recommendation = excluded.recommendation,
                    summary_json = excluded.summary_json,
                    raw_report_json = excluded.raw_report_json
                """,
                (
                    validation_id,
                    analysis_run_id,
                    team_group,
                    recommendation,
                    summary_json,
                    raw_report_json,
                    now_str,
                ),
            )

    def get_by_id(self, validation_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a validation report by its unique ID."""
        with self.mgr.session() as conn:
            cursor = conn.execute(
                "SELECT * FROM performance_validation_reports WHERE id = ?",
                (validation_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            d = dict(row)
            if d.get("summary_json"):
                d["summary"] = json.loads(d["summary_json"])
            if d.get("raw_report_json"):
                d["report"] = json.loads(d["raw_report_json"])
            return d

    def get_latest(self, team_group: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Retrieve the latest validation report, optionally filtered by team group."""
        with self.mgr.session() as conn:
            if team_group:
                cursor = conn.execute(
                    """
                    SELECT * FROM performance_validation_reports
                    WHERE team_group = ?
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (team_group,),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT * FROM performance_validation_reports
                    ORDER BY created_at DESC LIMIT 1
                    """
                )
            row = cursor.fetchone()
            if not row:
                return None
            d = dict(row)
            if d.get("summary_json"):
                d["summary"] = json.loads(d["summary_json"])
            if d.get("raw_report_json"):
                d["report"] = json.loads(d["raw_report_json"])
            return d

    def list_reports(self, limit: int = 10, team_group: Optional[str] = None) -> List[Dict[str, Any]]:
        """List validation reports in reverse chronological order."""
        with self.mgr.session() as conn:
            if team_group:
                cursor = conn.execute(
                    """
                    SELECT id, analysis_run_id, team_group, recommendation, summary_json, created_at
                    FROM performance_validation_reports
                    WHERE team_group = ?
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (team_group, limit),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT id, analysis_run_id, team_group, recommendation, summary_json, created_at
                    FROM performance_validation_reports
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (limit,),
                )
            rows = cursor.fetchall()
            out = []
            for r in rows:
                d = dict(r)
                if d.get("summary_json"):
                    try:
                        d["summary"] = json.loads(d["summary_json"])
                    except Exception:
                        pass
                out.append(d)
            return out



