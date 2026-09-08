"""Discord rich embed message formatter."""

from typing import Any, Dict, List, Optional
from app.utils.time import utc_now_iso


# Discord Color Constants (Decimal)
COLOR_RED = 15158332       # #E74C3C - Violations, Blocked, Errors
COLOR_AMBER = 15965458     # #F39C12 - Stale, Overdue, Warnings, User Mapping Required
COLOR_BLUE = 3447003       # #3498DB - Transitions, In Progress, Info
COLOR_GREEN = 3066993      # #2ECC71 - Completed, Success
COLOR_PURPLE = 10181046    # #9B59B6 - Reports, Summary


class DiscordFormatter:
    """Formats notifications and events into Discord Webhook embed payloads."""

    @staticmethod
    def format_embed(
        title: str,
        description: str,
        color: int = COLOR_BLUE,
        fields: Optional[List[Dict[str, Any]]] = None,
        footer_text: str = "PM Operations Agent v0.1",
        timestamp: Optional[str] = None
    ) -> Dict[str, Any]:
        """Construct a Discord Webhook payload with a rich embed."""
        embed: Dict[str, Any] = {
            "title": title,
            "description": description,
            "color": color,
            "footer": {"text": footer_text},
            "timestamp": timestamp or utc_now_iso()
        }
        if fields:
            embed["fields"] = fields

        return {"embeds": [embed]}

    @classmethod
    def format_workflow_violation(
        cls,
        task_key: str,
        task_title: str,
        resource_name: str,
        project_name: str,
        details: str,
        timestamp: Optional[str] = None
    ) -> Dict[str, Any]:
        """Format a PM workflow violation alert."""
        fields = [
            {"name": "Issue", "value": task_key, "inline": True},
            {"name": "Project", "value": project_name or "N/A", "inline": True},
            {"name": "Resource", "value": resource_name or "Unassigned", "inline": True},
            {"name": "Title", "value": task_title or "Untitled", "inline": False},
            {"name": "Issue Details", "value": details, "inline": False},
        ]
        return cls.format_embed(
            title="🚨 Jira Workflow Alert",
            description=f"Activity detected on **{task_key}** while remaining in **To Do**.",
            color=COLOR_RED,
            fields=fields,
            timestamp=timestamp
        )

    @classmethod
    def format_stale_task(
        cls,
        task_key: str,
        task_title: str,
        assignee_name: str,
        hours_inactive: float,
        timestamp: Optional[str] = None
    ) -> Dict[str, Any]:
        """Format a stale task warning."""
        fields = [
            {"name": "Issue", "value": task_key, "inline": True},
            {"name": "Assigned to", "value": assignee_name or "Unassigned", "inline": True},
            {"name": "Inactivity", "value": f"{hours_inactive:.1f} hours", "inline": True},
            {"name": "Title", "value": task_title or "Untitled", "inline": False},
        ]
        return cls.format_embed(
            title="⚠️ Stale Task Alert",
            description=f"Task **{task_key}** has been In Progress for >{int(hours_inactive)}h without activity.",
            color=COLOR_AMBER,
            fields=fields,
            timestamp=timestamp
        )

    @classmethod
    def format_overdue_task(
        cls,
        task_key: str,
        task_title: str,
        assignee_name: str,
        due_date: str,
        current_status: str,
        timestamp: Optional[str] = None
    ) -> Dict[str, Any]:
        """Format an overdue task alert."""
        fields = [
            {"name": "Issue", "value": task_key, "inline": True},
            {"name": "Assignee", "value": assignee_name or "Unassigned", "inline": True},
            {"name": "Status", "value": current_status, "inline": True},
            {"name": "Due Date", "value": due_date, "inline": True},
            {"name": "Title", "value": task_title or "Untitled", "inline": False},
        ]
        return cls.format_embed(
            title="⏰ Overdue Task Alert",
            description=f"Task **{task_key}** passed its due date ({due_date}) and is not Done.",
            color=COLOR_AMBER,
            fields=fields,
            timestamp=timestamp
        )

    @classmethod
    def format_user_mapping_required(
        cls,
        jira_user_name: str,
        jira_user_id: str,
        intended_action: str,
        timestamp: Optional[str] = None
    ) -> Dict[str, Any]:
        """Format an alert when user mapping is missing for a direct message."""
        fields = [
            {"name": "Jira User", "value": f"{jira_user_name} (`{jira_user_id}`)", "inline": False},
            {"name": "Intended Action", "value": intended_action, "inline": False},
            {"name": "Status", "value": "❌ Direct Message was NOT sent.", "inline": False},
        ]
        return cls.format_embed(
            title="⚠️ Mattermost Mapping Required",
            description="No verified Mattermost account mapping exists for this resource. Please create a mapping in the database.",
            color=COLOR_AMBER,
            fields=fields,
            timestamp=timestamp
        )

    @classmethod
    def format_daily_report(cls, report_data: Dict[str, Any]) -> Dict[str, Any]:
        """Format daily activity report into a rich Discord embed."""
        date_str = report_data.get("date", "Today")
        total_activities = report_data.get("total_activities", 0)
        tasks_created = report_data.get("tasks_created", 0)
        tasks_completed = report_data.get("tasks_completed", 0)
        transitions = report_data.get("status_transitions", 0)
        comments = report_data.get("comments_added", 0)
        worklogs = report_data.get("worklogs_logged", 0)
        violations = report_data.get("workflow_violations", 0)
        stale_count = report_data.get("stale_tasks", 0)

        fields = [
            {"name": "📊 Total Activities", "value": str(total_activities), "inline": True},
            {"name": "✨ Tasks Created", "value": str(tasks_created), "inline": True},
            {"name": "✅ Tasks Completed", "value": str(tasks_completed), "inline": True},
            {"name": "🔄 Status Transitions", "value": str(transitions), "inline": True},
            {"name": "💬 Comments Added", "value": str(comments), "inline": True},
            {"name": "⏱️ Worklogs Logged", "value": str(worklogs), "inline": True},
            {"name": "🚨 Workflow Violations", "value": str(violations), "inline": True},
            {"name": "⚠️ Stale Tasks", "value": str(stale_count), "inline": True},
        ]

        # Resources breakdown
        by_resource = report_data.get("activities_by_resource", {})
        if by_resource:
            res_summary = "\n".join([f"• **{name}**: {count} activities" for name, count in by_resource.items()])
            fields.append({"name": "👥 Activities by Resource", "value": res_summary[:1000], "inline": False})

        return cls.format_embed(
            title=f"📋 Daily PM Activity Report — {date_str}",
            description=f"Summary of all project activities and automated workflow rules for {date_str}.",
            color=COLOR_PURPLE,
            fields=fields
        )
