"""Jira Comment Notification Service (Event 2: Jira Comment Direction -> Mattermost Notification).

Routing Rule:
  The first valid Jira user tag at the beginning of the comment is the trigger.
  The current Jira issue assignee is the sole Mattermost recipient.

Safety & Constraints:
- Detect whether comment begins with a valid structured Jira user mention.
- If no leading user mention -> NO_NOTIFICATION.
- Later mentions in the comment are treated as CC/context and never trigger notifications or receive messages.
- The first tagged user is not necessarily the recipient. The current Jira issue assignee is the recipient.
- Assignee is resolved to Mattermost via UserMappingService.
- If assignee is unassigned, inactive, unmapped, or ambiguous -> fail closed (NO_NOTIFICATION).
- Direct link format: {JIRA_BASE_URL}/browse/{task_key}?focusedCommentId={comment_id}#comment-{comment_id}
- Message text:
    <Jira comment link only>
- Deterministic idempotency key: SHA256("EVENT2:{jira_comment_id}")[:16]
- No duplicate notification on re-processing.
- No approval gate required (direct DM delivery once conditions satisfied).
"""

import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Tuple, Union

from app.config.settings import settings
from app.core.actions.engine import action_engine
from app.core.actions.types import create_send_message_action
from app.core.events.types import TaskCommentAdded
from app.core.models.enums import ActionStatus, EventScope
from app.database.connection import DatabaseManager, db_manager
from app.database.repositories import Event2NotificationRepository, UserMappingRepository
from app.services.user_mapping_service import UserMappingService
from app.utils.logger import logger


class JiraCommentNotificationService:
    """Orchestrates Event 2 evaluation, leading mention parsing, assignee resolution, and Mattermost notification."""

    def __init__(self, manager: Optional[DatabaseManager] = None):
        self.mgr = manager or db_manager
        self.notification_repo = Event2NotificationRepository(self.mgr)
        self.mapping_service = UserMappingService(self.mgr)
        self.mapping_repo = UserMappingRepository(self.mgr)

    @staticmethod
    def extract_leading_mention(doc_or_text: Any) -> Optional[Dict[str, Any]]:
        """Extract the leading Jira user mention if and only if it is the first meaningful content.

        Returns:
            Dict with keys:
                - "account_id": Optional[str]
                - "display_name": Optional[str]
            or None if there is no valid leading mention.
        """
        if not doc_or_text:
            return None

        # 1. Handle structured Atlassian Document Format (ADF)
        if isinstance(doc_or_text, dict):
            # doc_or_text could be full ADF root {"type": "doc", "content": [...]} or raw node
            content_nodes = doc_or_text.get("content", []) if doc_or_text.get("type") == "doc" else [doc_or_text]
            
            # Find the first meaningful block node (skip completely empty paragraphs/blocks)
            for block in content_nodes:
                if not isinstance(block, dict):
                    continue
                
                inline_content = block.get("content", [])
                if not inline_content and block.get("type") == "mention":
                    # Direct mention block
                    attrs = block.get("attrs", {})
                    acc_id = attrs.get("id")
                    text_val = attrs.get("text", "")
                    disp_name = text_val.lstrip("@") if text_val else None
                    if acc_id or disp_name:
                        return {"account_id": acc_id, "display_name": disp_name}
                    return None

                for inline_node in inline_content:
                    if not isinstance(inline_node, dict):
                        continue
                    ntype = inline_node.get("type")
                    if ntype == "text":
                        raw_text = inline_node.get("text", "")
                        # If this text node has only whitespace, skip it to check next inline node
                        if not raw_text.strip():
                            continue
                        # Text node contains non-whitespace characters before any mention node!
                        # Check if text itself starts with a text/wiki mention e.g. [~accountid:...] or @User
                        text_leading = JiraCommentNotificationService._extract_leading_mention_from_text(raw_text)
                        if text_leading:
                            return text_leading
                        # Text appears before mention -> NOT a leading mention!
                        return None
                    elif ntype == "mention":
                        attrs = inline_node.get("attrs", {})
                        acc_id = attrs.get("id")
                        text_val = attrs.get("text", "")
                        disp_name = text_val.lstrip("@") if text_val else None
                        if acc_id or disp_name:
                            return {"account_id": acc_id, "display_name": disp_name}
                        return None
                    else:
                        # Non-text, non-mention node (e.g. image, rule, code block) appearing first
                        return None

            return None

        # 2. Handle plain text or Wiki markup string
        if isinstance(doc_or_text, str):
            return JiraCommentNotificationService._extract_leading_mention_from_text(doc_or_text)

        return None

    @staticmethod
    def _extract_leading_mention_from_text(raw_text: str) -> Optional[Dict[str, Any]]:
        """Detect leading mention from plain text or Jira wiki text after stripping harmless leading whitespace."""
        cleaned = raw_text.lstrip()
        if not cleaned:
            return None

        # Pattern 1: Jira Wiki structured account ID: [~accountid:712020:...] or [~accountid:5f83e3937d9637006ffd0436]
        m_wiki_acc = re.match(r"^\[~accountid:([^\]|]+)(?:\|([^\]]+))?\]", cleaned, re.IGNORECASE)
        if m_wiki_acc:
            acc_id = m_wiki_acc.group(1).strip()
            disp_name = m_wiki_acc.group(2).strip() if m_wiki_acc.group(2) else None
            return {"account_id": acc_id, "display_name": disp_name}

        # Pattern 2: Jira Wiki simple mention: [~712020:...] or [~username]
        m_wiki_simple = re.match(r"^\[~([^\]|]+)(?:\|([^\]]+))?\]", cleaned)
        if m_wiki_simple:
            raw_target = m_wiki_simple.group(1).strip()
            disp_name = m_wiki_simple.group(2).strip() if m_wiki_simple.group(2) else None
            if raw_target.lower().startswith("accountid:"):
                raw_target = raw_target[10:].strip()
            return {"account_id": raw_target, "display_name": disp_name}

        # Pattern 3: Standard user mention tag: @[~accountid:...] or @username / @DisplayName
        # E.g. "@Mubashir Please investigate this issue."
        # Stop at whitespace or punctuation like colon, comma
        m_tag = re.match(r"^@([a-zA-Z0-9_\-:\.]+)", cleaned)
        if m_tag:
            tag_name = m_tag.group(1).strip()
            # If tag_name contains account ID or display name
            return {"account_id": None, "display_name": tag_name}

        return None

    @staticmethod
    def generate_comment_url(task_key: str, comment_id: str) -> str:
        """Generate direct comment-specific Jira URL."""
        if not task_key or not comment_id:
            raise ValueError("Both task_key and comment_id are required to generate a direct comment URL.")
        base_url = settings.JIRA_BASE_URL.rstrip("/")
        return f"{base_url}/browse/{task_key}?focusedCommentId={comment_id}#comment-{comment_id}"

    @staticmethod
    def generate_idempotency_key(comment_id: str) -> str:
        """Generate deterministic idempotency key for Event 2 from comment ID."""
        if not comment_id:
            raise ValueError("comment_id is required for Event 2 idempotency key.")
        raw_key = f"EVENT2:{comment_id}"
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def format_notification_message(comment_url: str) -> str:
        """Format the Mattermost message to contain only the direct Jira comment URL."""
        return comment_url.strip()

    def extract_all_mentions(self, event: TaskCommentAdded) -> Tuple[List[str], List[str]]:
        """Return all mentioned account IDs and display names from the event."""
        mentioned_ids = list(getattr(event, "mentioned_account_ids", []) or [])
        mentioned_names = list(getattr(event, "mentioned_display_names", []) or [])
        return mentioned_ids, mentioned_names

    def resolve_assignee(self, current_assignee: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Resolve and validate current Jira issue assignee.
        
        Must return dict with:
            - "account_id": str
            - "display_name": str
            - "active": bool
            - "mattermost_user_id": str
        Or None if unassigned, inactive, or unmapped.
        """
        if not current_assignee or not isinstance(current_assignee, dict):
            logger.info("Event 2: Issue has no assignee (Unassigned). Failing closed.")
            return None

        account_id = current_assignee.get("accountId") or current_assignee.get("name") or current_assignee.get("id")
        display_name = current_assignee.get("displayName") or current_assignee.get("name") or ""
        email = current_assignee.get("emailAddress")
        active = current_assignee.get("active", True)

        if not account_id:
            logger.warning("Event 2: Assignee object has no accountId/name. Failing closed.")
            return None

        if not active:
            logger.warning(f"Event 2: Assignee '{display_name}' ({account_id}) is marked inactive in Jira. Failing closed.")
            return None

        # Resolve through UserMappingService
        mm_id, method = self.mapping_service.resolve_jira_to_mattermost(
            jira_user_id=account_id,
            display_name=display_name,
            email=email
        )

        if not mm_id:
            logger.warning(
                f"Event 2: Assignee '{display_name}' ({account_id}) has no verified Mattermost mapping. Failing closed."
            )
            return None

        return {
            "account_id": account_id,
            "display_name": display_name,
            "email": email,
            "active": active,
            "mattermost_user_id": mm_id,
            "resolution_method": method
        }

    async def evaluate_comment_event(
        self,
        event: TaskCommentAdded,
        current_assignee: Optional[Dict[str, Any]] = None,
        raw_comment_body: Optional[Any] = None,
        dry_run: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Evaluate a TaskCommentAdded event according to Event 2 specification.

        Returns simulation/evaluation dictionary.
        """
        task_key = event.task_key
        comment_id = event.comment_id
        is_dry_run = settings.DRY_RUN if dry_run is None else dry_run

        report: Dict[str, Any] = {
            "comment_id": comment_id,
            "task_key": task_key,
            "trigger": False,
            "leading_tagged_user": None,
            "leading_account_id": None,
            "assignee": None,
            "assignee_account_id": None,
            "all_mentions": [],
            "selected_mattermost_recipient": None,
            "mattermost_user_id": None,
            "comment_url": None,
            "idempotency_key": None,
            "decision": "NO_NOTIFICATION",
            "reason": None,
            "prepared_message": None,
        }

        if not comment_id:
            report["reason"] = "MISSING_COMMENT_ID"
            logger.warning("Event 2 evaluation rejected: missing comment_id.")
            return report

        if not task_key:
            report["reason"] = "MISSING_TASK_KEY"
            logger.warning("Event 2 evaluation rejected: missing task_key.")
            return report

        # 1. Comment URL & Idempotency Key
        try:
            comment_url = self.generate_comment_url(task_key, comment_id)
            idem_key = self.generate_idempotency_key(comment_id)
            report["comment_url"] = comment_url
            report["idempotency_key"] = idem_key
        except Exception as e:
            report["reason"] = f"URL_GENERATION_FAILED: {e}"
            logger.error(f"Event 2 failed to generate URL or idempotency key: {e}")
            return report

        # 2. Check Duplicate / Idempotency
        existing_rec = self.notification_repo.get_by_comment_id(comment_id)
        if not existing_rec:
            existing_rec = self.notification_repo.get_by_idempotency_key(idem_key)
        if existing_rec:
            report["decision"] = "ALREADY_PROCESSED"
            report["reason"] = f"Comment {comment_id} was already processed (status: {existing_rec.get('status')})."
            logger.info(f"Event 2: Comment {comment_id} already processed. Suppressing duplicate.")
            return report

        # Extract all mentions for reporting
        all_ids, all_names = self.extract_all_mentions(event)
        report["all_mentions"] = all_names or all_ids

        # 3. Detect Leading Structured Mention
        # Use raw comment body if provided (e.g. from ADF payload), else event.comment_body
        body_to_parse = raw_comment_body
        if not body_to_parse and isinstance(event.payload, dict):
            body_to_parse = event.payload.get("comment", {}).get("body") or event.payload.get("body")
        if not body_to_parse:
            body_to_parse = event.comment_body

        leading_mention = self.extract_leading_mention(body_to_parse)
        if not leading_mention:
            report["decision"] = "NO_NOTIFICATION"
            report["reason"] = "NO_LEADING_MENTION"
            logger.debug(f"Event 2: Comment {comment_id} on {task_key} does not have a leading user mention.")
            return report

        report["trigger"] = True
        report["leading_tagged_user"] = leading_mention.get("display_name")
        report["leading_account_id"] = leading_mention.get("account_id")

        # 4. Extract & Resolve Assignee
        # If current_assignee was not passed directly, look inside event.payload
        assignee_data = current_assignee
        if not assignee_data and isinstance(event.payload, dict):
            issue = event.payload.get("issue", {})
            fields = issue.get("fields", {}) if isinstance(issue, dict) else {}
            assignee_data = fields.get("assignee")

        resolved_assignee = self.resolve_assignee(assignee_data)
        if not resolved_assignee:
            report["decision"] = "NO_NOTIFICATION"
            report["reason"] = "ASSIGNEE_UNRESOLVED_OR_UNMAPPED"
            logger.info(f"Event 2: Comment {comment_id} triggered, but assignee could not be resolved. Failing closed.")
            return report

        assignee_acc_id = resolved_assignee["account_id"]
        assignee_disp_name = resolved_assignee["display_name"]
        mm_recipient_id = resolved_assignee["mattermost_user_id"]

        report["assignee"] = assignee_disp_name
        report["assignee_account_id"] = assignee_acc_id
        report["selected_mattermost_recipient"] = assignee_disp_name
        report["mattermost_user_id"] = mm_recipient_id

        # 5. Prepare Message
        msg_text = self.format_notification_message(comment_url)
        report["prepared_message"] = msg_text
        report["decision"] = "SEND"

        return report

    async def process_comment_notification(
        self,
        event: TaskCommentAdded,
        current_assignee: Optional[Dict[str, Any]] = None,
        raw_comment_body: Optional[Any] = None,
        dry_run: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Process an Event 2 comment event end-to-end: evaluate, build action, and dispatch."""
        eval_result = await self.evaluate_comment_event(
            event=event,
            current_assignee=current_assignee,
            raw_comment_body=raw_comment_body,
            dry_run=dry_run
        )

        if eval_result.get("decision") != "SEND":
            return eval_result

        comment_id = eval_result["comment_id"]
        task_key = eval_result["task_key"]
        idem_key = eval_result["idempotency_key"]
        assignee_acc_id = eval_result["assignee_account_id"]
        assignee_disp_name = eval_result["assignee"]
        mm_recipient_id = eval_result["mattermost_user_id"]
        comment_url = eval_result["comment_url"]
        prepared_msg = eval_result["prepared_message"]
        is_dry_run = settings.DRY_RUN if dry_run is None else dry_run

        # Record notification in repository for duplicate prevention
        status_to_record = "DRY_RUN_SIMULATED" if is_dry_run else "PROCESSED"
        rec_id, was_created = self.notification_repo.record_notification(
            comment_id=comment_id,
            task_key=task_key,
            leading_tagged_user=eval_result.get("leading_tagged_user"),
            leading_account_id=eval_result.get("leading_account_id"),
            assignee_account_id=assignee_acc_id,
            mattermost_user_id=mm_recipient_id,
            idempotency_key=idem_key,
            comment_url=comment_url,
            status=status_to_record
        )

        if not was_created:
            eval_result["decision"] = "ALREADY_PROCESSED"
            eval_result["reason"] = f"Concurrent attempt or already recorded notification for comment {comment_id}."
            return eval_result

        # Build Action with EventScope.JIRA_COMMENT_NOTIFICATION
        if "mattermost" not in action_engine._connectors:
            from app.connectors.mattermost.connector import MattermostConnector
            action_engine.register_connector(MattermostConnector())

        action = create_send_message_action(
            target_system="mattermost",
            target_id=assignee_acc_id,
            text=prepared_msg,
            recipient_name=assignee_disp_name
        )
        action.dry_run = is_dry_run
        action.idempotency_key = idem_key
        action.parameters["event_scope"] = EventScope.JIRA_COMMENT_NOTIFICATION.value
        action.parameters["jira_user_id"] = assignee_acc_id
        action.parameters["recipient_id"] = mm_recipient_id
        action.parameters["display_name"] = assignee_disp_name

        try:
            exec_result = await action_engine.execute(action, force_live=not is_dry_run)
            eval_result["action_result"] = {
                "success": exec_result.success,
                "status": exec_result.status.value if exec_result.status else None,
                "action_id": exec_result.action_id,
                "error_message": exec_result.error_message
            }
            if not exec_result.success:
                eval_result["decision"] = "DISPATCH_FAILED"
                eval_result["reason"] = exec_result.error_message
                self.notification_repo.update_status(comment_id, "FAILED", error_details=exec_result.error_message)
            else:
                final_status = "DRY_RUN_SIMULATED" if is_dry_run else "PROCESSED"
                self.notification_repo.update_status(comment_id, final_status)
        except Exception as e:
            logger.error(f"Event 2 dispatch exception for comment {comment_id}: {e}", exc_info=True)
            eval_result["decision"] = "DISPATCH_FAILED"
            eval_result["reason"] = str(e)
            self.notification_repo.update_status(comment_id, "FAILED", error_details=str(e))

        return eval_result


# Global service instance
jira_comment_notification_service = JiraCommentNotificationService()
