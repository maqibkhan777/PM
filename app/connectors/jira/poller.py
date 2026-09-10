"""Jira Cloud Poller for periodic event ingestion, change detection, and state projection."""

import datetime
import hashlib
from typing import Any, Dict, List, Optional, Set, Tuple
from app.config.settings import settings
from app.connectors.jira.client import JiraClient
from app.connectors.jira.normalizer import JiraEventNormalizer
from app.core.events.base import BaseEvent
from app.core.events.types import (
    TaskCreated,
    TaskUpdated,
    TaskStatusChanged,
    TaskAssigned,
    TaskCommentAdded,
    TaskPriorityChanged,
    TaskCompleted,
    TaskReopened,
    TaskWorklogged,
)
from app.database.connection import db_manager, DatabaseManager
from app.database.repositories import (
    EventRepository,
    JiraPollingStateRepository,
    JiraIssueStateRepository,
    JiraWorklogRepository,
)
from app.utils.logger import logger
from app.utils.time import utc_now, utc_now_iso, parse_iso_datetime, format_iso

# Fields that represent meaningful human project activity that resets the inactivity/stale timer.
# Irrelevant/system fields (sprint, rank, label, tag, components, syncs) do NOT reset last_activity_at.
MEANINGFUL_ACTIVITY_FIELDS: Set[str] = {
    "status",
    "assignee",
    "comment",
    "worklog",
    "timespent",
    "worklogid",
    "priority",
    "summary",
    "description",
    "duedate",
}

DONE_STATUSES: Set[str] = {"done", "completed", "resolved", "closed", "finished"}


class JiraPoller:
    """Polls Jira Cloud REST API, detects changes, normalizes events, and updates local state."""

    def __init__(
        self,
        client: Optional[JiraClient] = None,
        manager: Optional[DatabaseManager] = None
    ):
        self.mgr = manager or db_manager
        self.client = client or JiraClient()
        self.event_repo = EventRepository(self.mgr)
        self.polling_state_repo = JiraPollingStateRepository(self.mgr)
        self.issue_state_repo = JiraIssueStateRepository(self.mgr)
        self.worklog_repo = JiraWorklogRepository(self.mgr)
        self.normalizer = JiraEventNormalizer()

    async def poll(self) -> Dict[str, Any]:
        """Execute a single Jira polling cycle.

        1. Determine lookback time window from persistent checkpoint.
        2. Query Jira search API using JQL with pagination.
        3. Detect changes, generate typed events with deterministic IDs.
        4. Ingest events into EventRepository and Event Bus (skipping duplicates).
        5. Update local jira_issue_state projection.
        6. Advance checkpoint in SQLite ONLY on full success.
        """
        if not settings.is_jira_configured():
            logger.info("Jira is not configured with credentials; polling skipped.")
            return {
                "status": "skipped",
                "reason": "jira_not_configured",
                "issues_scanned": 0,
                "events_generated": 0,
                "duplicates_skipped": 0,
            }

        if not settings.is_jira_team_group_configured():
            logger.warning("Jira team group is not configured (JIRA_TEAM_GROUP is empty); polling skipped to avoid ingesting unrelated issues.")
            return {
                "status": "skipped",
                "reason": "jira_team_group_not_configured",
                "issues_scanned": 0,
                "events_generated": 0,
                "duplicates_skipped": 0,
            }

        now_dt = utc_now()
        current_poll_start_iso = format_iso(now_dt)
        logger.info("Jira polling cycle started.")

        # 1. Determine query time window
        checkpoint_iso = self.polling_state_repo.get_checkpoint("jira")
        if checkpoint_iso:
            last_poll_dt = parse_iso_datetime(checkpoint_iso) or now_dt
            lookback_delta = datetime.timedelta(minutes=settings.JIRA_POLLING_LOOKBACK_MINUTES)
            query_start_dt = last_poll_dt - lookback_delta
        else:
            # First-ever run: use safe initial lookback window
            initial_lookback_delta = datetime.timedelta(minutes=settings.JIRA_POLLING_INITIAL_LOOKBACK_MINUTES)
            query_start_dt = now_dt - initial_lookback_delta

        # Format JQL query timestamp: 'YYYY-MM-DD HH:mm' and apply team group filter
        jql_time_str = query_start_dt.strftime("%Y-%m-%d %H:%M")
        team_group = settings.JIRA_TEAM_GROUP.strip()
        jql = f'updated >= "{jql_time_str}" AND assignee in membersOf("{team_group}") ORDER BY updated ASC'
        logger.info(f"Jira polling scope: assignee in membersOf(\"{team_group}\")")

        issues_scanned = 0
        events_generated = 0
        duplicates_skipped = 0

        try:
            # 2. Paginated issue search via /rest/api/3/search/jql (cursor-based)
            next_page_token: Optional[str] = None
            batch_size = settings.JIRA_POLLING_BATCH_SIZE
            seen_tokens = set()

            while True:
                data = await self.client.search_issues(
                    jql=jql,
                    next_page_token=next_page_token,
                    max_results=batch_size,
                    expand="changelog"
                )

                issues = data.get("issues", [])
                if not issues:
                    break

                for issue in issues:
                    issues_scanned += 1
                    gen, dups = await self._process_issue(issue, query_start_dt)
                    events_generated += gen
                    duplicates_skipped += dups

                is_last = data.get("isLast", True if not data.get("nextPageToken") else False)
                next_page_token = data.get("nextPageToken")

                if is_last or not next_page_token or next_page_token in seen_tokens:
                    break
                seen_tokens.add(next_page_token)

            # 3. Only advance checkpoint on complete success
            self.polling_state_repo.update_checkpoint("jira", current_poll_start_iso)
            logger.info(
                f"Jira polling completed successfully. Issues scanned: {issues_scanned}, "
                f"Events generated: {events_generated}, Duplicates skipped: {duplicates_skipped}"
            )
            return {
                "status": "completed",
                "issues_scanned": issues_scanned,
                "events_generated": events_generated,
                "duplicates_skipped": duplicates_skipped,
                "checkpoint": current_poll_start_iso
            }

        except Exception as e:
            # IMPORTANT: Do NOT advance checkpoint on failure
            logger.error(f"Jira polling cycle failed: {e}. Checkpoint not advanced.", exc_info=True)
            return {
                "status": "failed",
                "error": str(e),
                "issues_scanned": issues_scanned,
                "events_generated": events_generated,
                "duplicates_skipped": duplicates_skipped,
            }

    async def _process_issue(
        self,
        issue: Dict[str, Any],
        query_start_dt: datetime.datetime
    ) -> Tuple[int, int]:
        """Detect changes on an issue, emit normalized events, and update state projection."""
        from app.services.orchestrator import orchestrator

        task_key = issue.get("key")
        if not task_key:
            return 0, 0

        fields = issue.get("fields", {})
        summary = fields.get("summary") or "Untitled"
        status_name = fields.get("status", {}).get("name", "Unknown")
        assignee_obj = fields.get("assignee") or {}
        assignee_name = assignee_obj.get("displayName") or assignee_obj.get("name")
        priority_obj = fields.get("priority") or {}
        priority_name = priority_obj.get("name")
        duedate = fields.get("duedate")
        updated_str = fields.get("updated")
        created_str = fields.get("created")
        project_obj = fields.get("project", {})
        project_key = project_obj.get("key")

        now_str = utc_now_iso()
        cached_state = self.issue_state_repo.get(task_key)
        team_group = settings.JIRA_TEAM_GROUP.strip() if settings.is_jira_team_group_configured() else None

        events_to_emit: List[BaseEvent] = []
        new_activity_time: Optional[str] = None
        event_payload = {"issue": issue, "task_key": task_key}

        # ----------------------------------------------------------------------
        # Change Detection
        # ----------------------------------------------------------------------
        changelog = issue.get("changelog", {})
        histories = changelog.get("histories", []) if changelog else []

        if cached_state is None:
            # First time seeing this issue
            # Check if this issue was recently created
            created_dt = parse_iso_datetime(created_str) if created_str else None
            if created_dt and created_dt >= query_start_dt:
                ext_id = f"jira:{task_key}:created"
                event = TaskCreated(
                    source="jira",
                    external_event_id=ext_id,
                    timestamp=created_str or now_str,
                    actor_id=fields.get("creator", {}).get("accountId") or fields.get("reporter", {}).get("accountId"),
                    actor_name=fields.get("creator", {}).get("displayName") or fields.get("reporter", {}).get("displayName"),
                    actor_email=fields.get("creator", {}).get("emailAddress"),
                    project_id=project_obj.get("id"),
                    project_key=project_key,
                    task_id=issue.get("id"),
                    task_key=task_key,
                    title=summary,
                    description=str(fields.get("description", "")),
                    status=status_name,
                    priority=priority_name or "Medium",
                    assignee_id=assignee_obj.get("accountId"),
                    assignee_name=assignee_name,
                    reporter_id=fields.get("reporter", {}).get("accountId"),
                    reporter_name=fields.get("reporter", {}).get("displayName"),
                    due_date=duedate,
                    payload=event_payload
                )
                events_to_emit.append(event)
                new_activity_time = created_str

        # Inspect changelog histories
        for history in histories:
            hist_id = history.get("id")
            hist_created = history.get("created")
            hist_dt = parse_iso_datetime(hist_created) if hist_created else None

            # Only evaluate changelog entries created within/after the query window
            if hist_dt and hist_dt < query_start_dt:
                continue

            author = history.get("author", {})
            actor_id = author.get("accountId") or author.get("name")
            actor_name = author.get("displayName") or author.get("name")
            actor_email = author.get("emailAddress")
            items = history.get("items", [])

            for item in items:
                field_name = (item.get("field") or "").lower()
                from_str = item.get("fromString", "")
                to_str = item.get("toString", "")

                # 1. Status Changed / Reopened / Completed
                if field_name == "status":
                    old_st = from_str
                    new_st = to_str
                    ext_id = f"jira:{task_key}:status:{hist_id}"

                    if old_st.lower() in DONE_STATUSES and new_st.lower() not in DONE_STATUSES:
                        event = TaskReopened(
                            source="jira",
                            external_event_id=ext_id,
                            timestamp=hist_created or now_str,
                            actor_id=actor_id,
                            actor_name=actor_name,
                            actor_email=actor_email,
                            project_id=project_obj.get("id"),
                            project_key=project_key,
                            task_id=issue.get("id"),
                            task_key=task_key,
                            previous_status=old_st,
                            new_status=new_st,
                            reopened_by=actor_name,
                            payload=event_payload
                        )
                    elif new_st.lower() in DONE_STATUSES and old_st.lower() not in DONE_STATUSES:
                        event = TaskCompleted(
                            source="jira",
                            external_event_id=ext_id,
                            timestamp=hist_created or now_str,
                            actor_id=actor_id,
                            actor_name=actor_name,
                            actor_email=actor_email,
                            project_id=project_obj.get("id"),
                            project_key=project_key,
                            task_id=issue.get("id"),
                            task_key=task_key,
                            completion_time=hist_created or now_str,
                            resolved_by=actor_name,
                            payload=event_payload
                        )
                    else:
                        event = TaskStatusChanged(
                            source="jira",
                            external_event_id=ext_id,
                            timestamp=hist_created or now_str,
                            actor_id=actor_id,
                            actor_name=actor_name,
                            actor_email=actor_email,
                            project_id=project_obj.get("id"),
                            project_key=project_key,
                            task_id=issue.get("id"),
                            task_key=task_key,
                            old_status=old_st,
                            new_status=new_st,
                            payload=event_payload
                        )
                    events_to_emit.append(event)
                    new_activity_time = hist_created

                # 2. Assignee Changed
                elif field_name == "assignee":
                    ext_id = f"jira:{task_key}:assignee:{hist_id}"
                    event = TaskAssigned(
                        source="jira",
                        external_event_id=ext_id,
                        timestamp=hist_created or now_str,
                        actor_id=actor_id,
                        actor_name=actor_name,
                        actor_email=actor_email,
                        project_id=project_obj.get("id"),
                        project_key=project_key,
                        task_id=issue.get("id"),
                        task_key=task_key,
                        old_assignee_id=item.get("from"),
                        old_assignee_name=from_str,
                        new_assignee_id=item.get("to"),
                        new_assignee_name=to_str,
                        payload=event_payload
                    )
                    events_to_emit.append(event)
                    new_activity_time = hist_created

                # 3. Priority Changed
                elif field_name == "priority":
                    ext_id = f"jira:{task_key}:priority:{hist_id}"
                    event = TaskPriorityChanged(
                        source="jira",
                        external_event_id=ext_id,
                        timestamp=hist_created or now_str,
                        actor_id=actor_id,
                        actor_name=actor_name,
                        actor_email=actor_email,
                        project_id=project_obj.get("id"),
                        project_key=project_key,
                        task_id=issue.get("id"),
                        task_key=task_key,
                        old_priority=from_str or "Unknown",
                        new_priority=to_str or "Unknown",
                        payload=event_payload
                    )
                    events_to_emit.append(event)
                    new_activity_time = hist_created

                # 4. Meaningful Field Update (summary, description, duedate)
                elif field_name in ("summary", "description", "duedate"):
                    ext_id = f"jira:{task_key}:{field_name}:{hist_id}"
                    event = TaskUpdated(
                        source="jira",
                        external_event_id=ext_id,
                        timestamp=hist_created or now_str,
                        actor_id=actor_id,
                        actor_name=actor_name,
                        actor_email=actor_email,
                        project_id=project_obj.get("id"),
                        project_key=project_key,
                        task_id=issue.get("id"),
                        task_key=task_key,
                        changed_fields=[field_name],
                        changes={field_name: {"from": from_str, "to": to_str}},
                        payload=event_payload
                    )
                    events_to_emit.append(event)
                    new_activity_time = hist_created

        # ----------------------------------------------------------------------
        # Deterministic Comments & Worklogs Ingestion (Amendment 1)
        # ----------------------------------------------------------------------
        # Check comments embedded in issue fields
        comment_section = fields.get("comment", {})
        comments_list = comment_section.get("comments", []) if isinstance(comment_section, dict) else []
        for c in comments_list:
            cid = c.get("id")
            c_created = c.get("created")
            c_dt = parse_iso_datetime(c_created) if c_created else None
            if c_dt and c_dt >= query_start_dt and cid:
                ext_id = f"jira:{task_key}:comment:{cid}"
                author = c.get("author", {})
                body_val = c.get("body", "")
                body_text, mentioned_acc_ids, mentioned_names = JiraEventNormalizer.extract_adf_text_and_mentions(body_val)

                event = TaskCommentAdded(
                    source="jira",
                    external_event_id=ext_id,
                    timestamp=c_created or now_str,
                    actor_id=author.get("accountId"),
                    actor_name=author.get("displayName"),
                    actor_email=author.get("emailAddress"),
                    project_id=project_obj.get("id"),
                    project_key=project_key,
                    task_id=issue.get("id"),
                    task_key=task_key,
                    comment_id=cid,
                    comment_body=body_text,
                    author_id=author.get("accountId"),
                    author_name=author.get("displayName"),
                    mentioned_account_ids=mentioned_acc_ids,
                    mentioned_display_names=mentioned_names,
                    payload=event_payload
                )
                events_to_emit.append(event)
                new_activity_time = c_created

        # Check worklogs embedded in issue fields
        worklog_section = fields.get("worklog", {})
        worklogs_list = worklog_section.get("worklogs", []) if isinstance(worklog_section, dict) else []
        for w in worklogs_list:
            wid = w.get("id")
            if not wid:
                continue
            author = w.get("author", {})
            time_secs = int(w.get("timeSpentSeconds", 0))
            w_started = w.get("started") or w.get("created") or now_str
            w_created = w.get("created")
            w_updated = w.get("updated")
            w_comment_raw = w.get("comment", "")
            w_comment = JiraEventNormalizer._extract_adf_text(w_comment_raw) if isinstance(w_comment_raw, dict) else str(w_comment_raw or "")

            # Persist to local SQLite worklog repository (idempotent, duplicate prevention)
            self.worklog_repo.upsert_worklog(
                worklog_id=str(wid),
                jira_issue_key=task_key,
                jira_issue_id=str(issue.get("id")),
                author_account_id=author.get("accountId"),
                author_display_name=author.get("displayName"),
                time_spent_seconds=time_secs,
                started_at=w_started,
                created_at=w_created,
                updated_at=w_updated,
                comment=w_comment,
                team_group=team_group,
                source="jira"
            )

            # Check if this worklog is within the current polling lookback to emit event
            w_event_time = w_created or w_started
            w_dt = parse_iso_datetime(w_event_time) if w_event_time else None
            if w_dt and w_dt >= query_start_dt:
                ext_id = f"jira:{task_key}:worklog:{wid}"
                event = TaskWorklogged(
                    source="jira",
                    external_event_id=ext_id,
                    timestamp=w_event_time or now_str,
                    actor_id=author.get("accountId"),
                    actor_name=author.get("displayName"),
                    actor_email=author.get("emailAddress"),
                    project_id=project_obj.get("id"),
                    project_key=project_key,
                    task_id=issue.get("id"),
                    task_key=task_key,
                    worklog_id=str(wid),
                    time_spent_seconds=time_secs,
                    time_spent_human=w.get("timeSpent"),
                    comment=w_comment,
                    payload=event_payload
                )
                events_to_emit.append(event)
                new_activity_time = w_event_time

        # Fallback: If issue was updated according to timestamp, but no specific event was produced yet
        if not events_to_emit and cached_state and updated_str != cached_state.get("updated_at"):
            # Check if status or assignee changed relative to cached state without a parsed changelog
            if cached_state.get("status") and status_name != cached_state.get("status"):
                ext_id = f"jira:{task_key}:status:cached_diff:{hashlib.md5(updated_str.encode()).hexdigest()[:8]}"
                event = TaskStatusChanged(
                    source="jira",
                    external_event_id=ext_id,
                    timestamp=updated_str or now_str,
                    actor_id=None,
                    actor_name=None,
                    project_id=project_obj.get("id"),
                    project_key=project_key,
                    task_id=issue.get("id"),
                    task_key=task_key,
                    old_status=cached_state.get("status", "Unknown"),
                    new_status=status_name,
                    payload=event_payload
                )
                events_to_emit.append(event)
                new_activity_time = updated_str
            else:
                # Generic TaskUpdated fallback
                fingerprint = hashlib.md5(f"{task_key}:{updated_str}".encode()).hexdigest()[:10]
                ext_id = f"jira:{task_key}:updated:{fingerprint}"
                event = TaskUpdated(
                    source="jira",
                    external_event_id=ext_id,
                    timestamp=updated_str or now_str,
                    actor_id=None,
                    actor_name=None,
                    project_id=project_obj.get("id"),
                    project_key=project_key,
                    task_id=issue.get("id"),
                    task_key=task_key,
                    changed_fields=["updated"],
                    changes={"updated": {"from": cached_state.get("updated_at"), "to": updated_str}},
                    payload=event_payload
                )
                events_to_emit.append(event)
                # Note: per Amendment 2, generic non-meaningful update does not set new_activity_time

        # ----------------------------------------------------------------------
        # Update Local jira_issue_state Projection (Cache)
        # ----------------------------------------------------------------------
        # Preserves last_activity_at unless a meaningful activity occurred
        effective_activity_time = new_activity_time or (cached_state.get("last_activity_at") if cached_state else (updated_str or now_str))

        self.issue_state_repo.upsert(
            jira_issue_key=task_key,
            summary=summary,
            status=status_name,
            assignee=assignee_name,
            priority=priority_name,
            due_date=duedate,
            updated_at=updated_str,
            last_seen_at=now_str,
            last_activity_at=effective_activity_time,
            project_key=project_key,
            raw_reference=issue,
            team_group=settings.JIRA_TEAM_GROUP.strip() if settings.is_jira_team_group_configured() else None
        )

        # ----------------------------------------------------------------------
        # Emit Events through Orchestrator Ingestion
        # ----------------------------------------------------------------------
        generated_count = 0
        skipped_count = 0

        for ev in events_to_emit:
            if ev.external_event_id and self.event_repo.exists_by_external_id("jira", ev.external_event_id):
                skipped_count += 1
                continue

            await orchestrator.ingest_polled_event(ev)
            generated_count += 1

        return generated_count, skipped_count
