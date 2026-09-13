"""Active Epic review and status synchronization service for PM Operations Agent."""

from typing import Any, Dict, List, Optional, Tuple
from app.config.settings import settings
from app.connectors.jira.client import JiraClient
from app.core.actions.types import create_transition_task_action, create_send_notification_action
from app.core.actions.engine import action_engine
from app.core.performance.roles import (
    resolve_canonical_account_id,
    get_employee_designation_and_category,
)
from app.database.connection import db_manager, DatabaseManager
from app.database.repositories import EmployeeRoleRepository
from app.services.user_identity_service import user_identity_service
from app.connectors.discord.formatter import DiscordFormatter, COLOR_ATTENTION
from app.utils.logger import logger


# Key canonical identities
AZAIN_HASSAN_ACCOUNT_ID = "712020:1ddac8e3-e006-48e7-b4c9-ee941efc8e6e"
TAHIR_ALI_ACCOUNT_ID = "638855b85fce844d606bb422"

MUBASHIR_BUTT_ACCOUNT_ID = "712020:e268bcd8-d981-4b4d-992d-d5694745df8b"

DONE_STATUS_NAMES = {
    "done",
    "completed",
    "resolved",
    "closed",
    "finished",
    "cancelled",
    "rejected",
}

QA_ROLE_CATEGORIES = {"qa", "quality assurance"}
DEV_ROLE_CATEGORIES = {"wordpress development", "frontend development", "development", "developer"}
BA_ROLE_CATEGORIES = {"business analysis", "ba", "project management"}


class EpicReviewService:
    """Evaluates active Epics and synchronizes their operational status based on child ticket state."""

    def __init__(
        self,
        manager: Optional[DatabaseManager] = None,
        client: Optional[JiraClient] = None,
    ):
        self.mgr = manager or db_manager
        self.role_repo = EmployeeRoleRepository(self.mgr)
        self.client = client or JiraClient()

    def is_ticket_done(self, ticket: Dict[str, Any]) -> bool:
        """Check if a child ticket is completed."""
        fields = ticket.get("fields", {}) if isinstance(ticket, dict) else {}
        status_obj = fields.get("status", {})
        if isinstance(status_obj, dict):
            status_name = str(status_obj.get("name", "")).strip().lower()
            category_name = str(status_obj.get("statusCategory", {}).get("name", "")).strip().lower()
            if category_name in ("done", "complete"):
                return True
        else:
            status_name = str(status_obj).strip().lower()

        return status_name in DONE_STATUS_NAMES

    def classify_child_tickets(
        self,
        child_tickets: List[Dict[str, Any]],
        pm_account_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Inspect and categorize child tickets into active vs completed buckets."""
        active_azain: List[Dict[str, Any]] = []
        active_dev: List[Dict[str, Any]] = []
        active_qa: List[Dict[str, Any]] = []
        active_planning: List[Dict[str, Any]] = []
        active_marketing: List[Dict[str, Any]] = []
        active_pm: List[Dict[str, Any]] = []
        done_tickets: List[Dict[str, Any]] = []
        other_active: List[Dict[str, Any]] = []

        for t in child_tickets:
            key = t.get("key", "UNKNOWN")
            fields = t.get("fields", {})
            summary = fields.get("summary", "Untitled")
            status_name = fields.get("status", {}).get("name", "Unknown") if isinstance(fields.get("status"), dict) else str(fields.get("status", "Unknown"))
            issue_type = fields.get("issuetype", {}).get("name", "Task") if isinstance(fields.get("issuetype"), dict) else str(fields.get("issuetype", "Task"))
            assignee = fields.get("assignee") or {}
            assignee_acc_id = assignee.get("accountId") if isinstance(assignee, dict) else None
            assignee_name = assignee.get("displayName") if isinstance(assignee, dict) else str(assignee or "Unassigned")
            if not assignee_name:
                assignee_name = "Unassigned"
            clean_assignee_name = str(assignee_name).lower()

            # Resolve canonical assignee and role
            can_acc_id = resolve_canonical_account_id(assignee_acc_id, display_name=assignee_name, role_repo=self.role_repo)
            designation, role_cat, is_resolved = get_employee_designation_and_category(can_acc_id, display_name=assignee_name, role_repo=self.role_repo)
            norm_role = role_cat.strip().lower()

            ticket_info = {
                "key": key,
                "summary": summary,
                "status": status_name,
                "issue_type": issue_type,
                "assignee_id": can_acc_id,
                "assignee_name": assignee_name,
                "designation": designation,
                "role_category": role_cat,
            }

            if self.is_ticket_done(t):
                done_tickets.append(ticket_info)
                continue

            # Active (non-done) ticket routing
            # 1. Azain check
            if can_acc_id == AZAIN_HASSAN_ACCOUNT_ID or norm_role == "design" or "azain" in clean_assignee_name:
                active_azain.append(ticket_info)

            # 2. Marketing / Tahir Ali check
            elif can_acc_id == TAHIR_ALI_ACCOUNT_ID or norm_role in ("content", "content / marketing", "seo") or "tahir" in clean_assignee_name:
                active_marketing.append(ticket_info)

            # 3. QA check
            elif norm_role in QA_ROLE_CATEGORIES or "qa" in designation.lower() or "tester" in designation.lower() or "quality" in designation.lower() or issue_type.lower() in ("bug", "qa"):
                active_qa.append(ticket_info)

            # 4. Planning / BA check
            elif norm_role in BA_ROLE_CATEGORIES or "ba" in designation.lower() or "analyst" in designation.lower() or issue_type.lower() in ("planning", "requirement"):
                active_planning.append(ticket_info)

            # 5. Developer check
            elif norm_role in DEV_ROLE_CATEGORIES or "developer" in designation.lower() or "engineer" in designation.lower() or issue_type.lower() in ("story", "development"):
                active_dev.append(ticket_info)

            # 6. PM check
            elif can_acc_id and pm_account_id and can_acc_id == pm_account_id:
                active_pm.append(ticket_info)

            else:
                other_active.append(ticket_info)

        return {
            "active_azain": active_azain,
            "active_dev": active_dev,
            "active_qa": active_qa,
            "active_planning": active_planning,
            "active_marketing": active_marketing,
            "active_pm": active_pm,
            "done_tickets": done_tickets,
            "other_active": other_active,
            "total_children": len(child_tickets),
        }

    def determine_epic_status_recommendation(
        self,
        epic_key: str,
        current_status: str,
        classified: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Evaluate deterministic precedence hierarchy across classified child tickets.

        Precedence:
        1. On Hold -> unchanged
        2. Any active sprint ticket assigned to Azain -> 'Ready for kickoff'
        3. Active development work assigned to Developer -> 'In Development'
        4. Active QA work assigned to QA -> 'In QA'
        5. Active planning work assigned to BA -> 'In Planning'
        6. Marketing condition:
           - Marketing ticket assigned to Tahir Ali
           - AND all other relevant non-Tahir tickets are either Done or assigned to PM
           -> 'In Marketing'
        7. Otherwise -> None (unchanged)
        """
        clean_status = (current_status or "").strip()

        # 1. On Hold Check
        if clean_status.lower() in ("on hold", "on-hold", "hold"):
            return {
                "epic_key": epic_key,
                "current_status": clean_status,
                "recommended_status": None,
                "is_on_hold": True,
                "reason": "Epic is On Hold; leaving status unchanged regardless of child ticket states.",
                "classified": classified,
            }

        # 2. Azain assigned to any active ticket -> Ready for kickoff
        if classified["active_azain"]:
            keys_str = ", ".join([t["key"] for t in classified["active_azain"]])
            return {
                "epic_key": epic_key,
                "current_status": clean_status,
                "recommended_status": "Ready for kickoff",
                "is_on_hold": False,
                "reason": f"Active ticket(s) [{keys_str}] assigned to Azain Hassan (Design/Kickoff phase).",
                "classified": classified,
            }

        # 3. Active development work assigned to Developer -> In Development
        if classified["active_dev"]:
            keys_str = ", ".join([t["key"] for t in classified["active_dev"]])
            return {
                "epic_key": epic_key,
                "current_status": clean_status,
                "recommended_status": "In Development",
                "is_on_hold": False,
                "reason": f"Active development ticket(s) [{keys_str}] assigned to Developer(s).",
                "classified": classified,
            }

        # 4. Active QA work assigned to QA -> In QA
        if classified["active_qa"]:
            keys_str = ", ".join([t["key"] for t in classified["active_qa"]])
            return {
                "epic_key": epic_key,
                "current_status": clean_status,
                "recommended_status": "In QA",
                "is_on_hold": False,
                "reason": f"Active QA ticket(s) [{keys_str}] assigned to QA engineer(s) (no active dev tickets).",
                "classified": classified,
            }

        # 5. Active planning work assigned to BA -> In Planning
        if classified["active_planning"]:
            keys_str = ", ".join([t["key"] for t in classified["active_planning"]])
            return {
                "epic_key": epic_key,
                "current_status": clean_status,
                "recommended_status": "In Planning",
                "is_on_hold": False,
                "reason": f"Active planning ticket(s) [{keys_str}] assigned to Business Analyst(s).",
                "classified": classified,
            }

        # 6. Marketing Condition:
        # Marketing ticket assigned to Tahir Ali AND all other tickets are either Done or assigned to PM
        if classified["active_marketing"]:
            # Check if there are other active non-PM non-marketing tickets
            has_blocking_active = bool(classified["other_active"])
            if not has_blocking_active:
                keys_str = ", ".join([t["key"] for t in classified["active_marketing"]])
                return {
                    "epic_key": epic_key,
                    "current_status": clean_status,
                    "recommended_status": "In Marketing",
                    "is_on_hold": False,
                    "reason": f"Marketing ticket(s) [{keys_str}] assigned to Tahir Ali and all other child tickets are Done or PM assigned.",
                    "classified": classified,
                }
            else:
                other_keys = ", ".join([t["key"] for t in classified["other_active"]])
                return {
                    "epic_key": epic_key,
                    "current_status": clean_status,
                    "recommended_status": None,
                    "is_on_hold": False,
                    "reason": f"Marketing ticket assigned to Tahir Ali, but other active tickets [{other_keys}] remain incomplete.",
                    "classified": classified,
                }

        # 7. No matching conditions met
        return {
            "epic_key": epic_key,
            "current_status": clean_status,
            "recommended_status": None,
            "is_on_hold": False,
            "reason": "No active phase conditions matched; keeping current status.",
            "classified": classified,
        }

    async def fetch_active_epics(self, pm_account_id: str) -> List[Dict[str, Any]]:
        """Query Jira for all active Epics assigned to or reported by the PM."""
        if not settings.is_jira_configured():
            logger.warning("Jira not configured; cannot fetch active Epics.")
            return []

        jql = (
            f'(assignee = "{pm_account_id}" OR reporter = "{pm_account_id}") '
            f'AND issuetype in (Epic) '
            f'AND statusCategory != Done '
            f'ORDER BY updated DESC'
        )
        logger.info(f"Fetching active PM Epics with JQL: {jql}")
        try:
            res = await self.client.search_issues(jql=jql, max_results=50, fields=["summary", "status", "assignee", "reporter", "subtasks", "issuelinks"])
            return res.get("issues", []) if isinstance(res, dict) else []
        except Exception as e:
            logger.error(f"Error fetching active Epics from Jira: {e}", exc_info=True)
            return []

    async def fetch_child_tickets_for_epic(self, epic_key: str) -> List[Dict[str, Any]]:
        """Fetch all sprint / child issues belonging to an Epic."""
        if not settings.is_jira_configured():
            return []

        jql = f'parent = "{epic_key}" OR "Epic Link" = "{epic_key}" ORDER BY key ASC'
        try:
            res = await self.client.search_issues(jql=jql, max_results=100, fields=["summary", "status", "assignee", "issuetype", "customfield_10020", "sprint"])
            issues = res.get("issues", []) if isinstance(res, dict) else []
            if issues:
                return issues
        except Exception as e:
            logger.debug(f"JQL child search for {epic_key} encountered notice: {e}")

        # Fallback to issue details / links
        try:
            epic_issue = await self.client.get_issue(epic_key)
            fields = epic_issue.get("fields", {}) if isinstance(epic_issue, dict) else {}
            subtasks = fields.get("subtasks", []) or []
            return subtasks
        except Exception as e:
            logger.warning(f"Failed to fetch child issues for Epic {epic_key}: {e}")
            return []

    async def run_review(self) -> List[Dict[str, Any]]:
        """Execute scheduled review of all active PM Epics and dispatch transitions through ActionEngine."""
        logger.info("Starting Active Epic review cycle...")
        ident = await user_identity_service.get_my_identity(client=self.client)
        pm_account_id = ident.get("account_id")
        if not pm_account_id:
            logger.warning("Could not resolve PM account ID; skipping Epic review.")
            return []

        epics = await self.fetch_active_epics(pm_account_id)
        logger.info(f"Found {len(epics)} active PM Epics to review.")
        results: List[Dict[str, Any]] = []

        for epic in epics:
            epic_key = epic.get("key")
            if not epic_key:
                continue

            fields = epic.get("fields", {})
            current_status = fields.get("status", {}).get("name", "Unknown") if isinstance(fields.get("status"), dict) else str(fields.get("status", "Unknown"))
            summary = fields.get("summary", "Untitled Epic")

            children = await self.fetch_child_tickets_for_epic(epic_key)
            classified = self.classify_child_tickets(children, pm_account_id=pm_account_id)
            eval_result = self.determine_epic_status_recommendation(epic_key, current_status, classified)

            recommended = eval_result.get("recommended_status")
            action_executed = False
            action_result = None

            # Only transition if recommended is different from current status
            if recommended and recommended.strip().lower() != current_status.strip().lower():
                logger.info(
                    f"Epic {epic_key} ('{summary}') status mismatch: current='{current_status}', recommended='{recommended}'. "
                    f"Dispatching TransitionTask action via ActionEngine..."
                )
                action = create_transition_task_action(
                    target_system="jira",
                    task_key=epic_key,
                    target_status=recommended,
                    task_title=summary,
                    requested_by="EpicReviewService"
                )
                try:
                    res = await action_engine.execute(action)
                    action_executed = True
                    action_result = res.model_dump()
                    logger.info(f"ActionEngine executed Epic transition for {epic_key} to '{recommended}': status={res.status.value}")
                except Exception as e:
                    logger.error(f"Failed to execute Epic transition for {epic_key} to '{recommended}': {e}", exc_info=True)
                    action_result = {"error": str(e)}

            eval_result["summary"] = summary
            eval_result["action_executed"] = action_executed
            eval_result["action_result"] = action_result
            results.append(eval_result)

        logger.info(f"Active Epic review cycle complete. Processed {len(results)} Epics.")
        return results
