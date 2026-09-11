"""Discord rich embed message formatter."""

from typing import Any, Dict, List, Optional
from app.utils.time import utc_now_iso
from app.config.settings import settings


# Discord Color Constants (Decimal)
COLOR_RED = 15158332       # #E74C3C - Violations, Blocked, Errors
COLOR_AMBER = 15965458     # #F39C12 - Stale, Overdue, Warnings, User Mapping Required
COLOR_BLUE = 3447003       # #3498DB - Transitions, In Progress, Info
COLOR_GREEN = 3066993      # #2ECC71 - Completed, Success
COLOR_PURPLE = 10181046    # #9B59B6 - Reports, Summary
COLOR_MAGENTA = 15844367   # #F1C40F / Mention, Direct Attention


class DiscordFormatter:
    """Formats notifications and events into Discord Webhook embed payloads."""

    @staticmethod
    def format_jira_link(task_key: str, label: Optional[str] = None) -> str:
        """Construct a clickable markdown link for Jira issues."""
        url = settings.get_jira_browse_url(task_key)
        display_label = label or task_key
        return f"[{display_label}]({url})"

    @staticmethod
    def format_embed(
        title: str,
        description: str,
        color: int = COLOR_BLUE,
        fields: Optional[List[Dict[str, Any]]] = None,
        footer_text: str = "PM Operations Agent v0.1",
        timestamp: Optional[str] = None,
        url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Construct a Discord Webhook payload with a rich embed."""
        embed: Dict[str, Any] = {
            "title": title,
            "description": description,
            "color": color,
            "footer": {"text": footer_text},
            "timestamp": timestamp or utc_now_iso()
        }
        if url:
            embed["url"] = url
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
        issue_link = cls.format_jira_link(task_key)
        jira_url = settings.get_jira_browse_url(task_key)
        fields = [
            {"name": "Issue", "value": issue_link, "inline": True},
            {"name": "Project", "value": project_name or "N/A", "inline": True},
            {"name": "Resource", "value": resource_name or "Unassigned", "inline": True},
            {"name": "Title", "value": task_title or "Untitled", "inline": False},
            {"name": "Issue Details", "value": details, "inline": False},
        ]
        return cls.format_embed(
            title=f"🚨 Jira Workflow Alert — {task_key}",
            description=f"Activity detected on {issue_link} while remaining in **To Do**.",
            color=COLOR_RED,
            fields=fields,
            timestamp=timestamp,
            url=jira_url
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
        issue_link = cls.format_jira_link(task_key)
        jira_url = settings.get_jira_browse_url(task_key)
        fields = [
            {"name": "Issue", "value": issue_link, "inline": True},
            {"name": "Assigned to", "value": assignee_name or "Unassigned", "inline": True},
            {"name": "Inactivity", "value": f"{hours_inactive:.1f} hours", "inline": True},
            {"name": "Title", "value": task_title or "Untitled", "inline": False},
        ]
        return cls.format_embed(
            title=f"⚠️ Stale Task Alert — {task_key}",
            description=f"Task {issue_link} has been In Progress for >{int(hours_inactive)}h without activity.",
            color=COLOR_AMBER,
            fields=fields,
            timestamp=timestamp,
            url=jira_url
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
        issue_link = cls.format_jira_link(task_key)
        jira_url = settings.get_jira_browse_url(task_key)
        fields = [
            {"name": "Issue", "value": issue_link, "inline": True},
            {"name": "Assignee", "value": assignee_name or "Unassigned", "inline": True},
            {"name": "Status", "value": current_status, "inline": True},
            {"name": "Due Date", "value": due_date, "inline": True},
            {"name": "Title", "value": task_title or "Untitled", "inline": False},
        ]
        return cls.format_embed(
            title=f"⏰ Overdue Task Alert — {task_key}",
            description=f"Task {issue_link} passed its due date ({due_date}) and is not Done.",
            color=COLOR_AMBER,
            fields=fields,
            timestamp=timestamp,
            url=jira_url
        )

    @classmethod
    def format_reopened_task(
        cls,
        task_key: str,
        task_title: str,
        reopened_by: str,
        prev_status: str,
        new_status: str,
        timestamp: Optional[str] = None
    ) -> Dict[str, Any]:
        """Format a reopened task alert."""
        issue_link = cls.format_jira_link(task_key)
        jira_url = settings.get_jira_browse_url(task_key)
        fields = [
            {"name": "Issue", "value": issue_link, "inline": True},
            {"name": "Reopened by", "value": reopened_by or "Unknown", "inline": True},
            {"name": "Transition", "value": f"`{prev_status}` ➔ `{new_status}`", "inline": True},
            {"name": "Title", "value": task_title or "Untitled", "inline": False},
        ]
        return cls.format_embed(
            title=f"🔄 Task Reopened Alert — {task_key}",
            description=f"Task {issue_link} was reopened from **{prev_status}** to **{new_status}**.",
            color=COLOR_BLUE,
            fields=fields,
            timestamp=timestamp,
            url=jira_url
        )

    @classmethod
    def format_task_comment(
        cls,
        task_key: str,
        task_title: str,
        author_name: str,
        comment_body: str,
        task_status: str,
        timestamp: Optional[str] = None
    ) -> Dict[str, Any]:
        """Format a general comment notification alert."""
        issue_link = cls.format_jira_link(task_key)
        jira_url = settings.get_jira_browse_url(task_key)
        preview = comment_body if len(comment_body) <= 400 else comment_body[:397] + "..."
        fields = [
            {"name": "Issue", "value": issue_link, "inline": True},
            {"name": "Status", "value": task_status or "Unknown", "inline": True},
            {"name": "Author", "value": author_name or "Unknown", "inline": True},
            {"name": "Title", "value": task_title or "Untitled", "inline": False},
            {"name": "Comment", "value": preview or "(empty)", "inline": False},
        ]
        return cls.format_embed(
            title=f"💬 Jira Comment Added — {task_key}",
            description=f"New comment posted on {issue_link} by **{author_name or 'Unknown'}**.",
            color=COLOR_BLUE,
            fields=fields,
            timestamp=timestamp,
            url=jira_url
        )

    @classmethod
    def format_task_mention(
        cls,
        task_key: str,
        task_title: str,
        author_name: str,
        comment_body: str,
        task_status: str,
        timestamp: Optional[str] = None
    ) -> Dict[str, Any]:
        """Format a high-priority personal mention notification alert."""
        issue_link = cls.format_jira_link(task_key)
        jira_url = settings.get_jira_browse_url(task_key)
        preview = comment_body if len(comment_body) <= 500 else comment_body[:497] + "..."
        fields = [
            {"name": "Issue", "value": issue_link, "inline": True},
            {"name": "Status", "value": task_status or "Unknown", "inline": True},
            {"name": "Mentioned by", "value": author_name or "Unknown", "inline": True},
            {"name": "Title", "value": task_title or "Untitled", "inline": False},
            {"name": "Comment", "value": preview or "(empty)", "inline": False},
        ]
        return cls.format_embed(
            title=f"🔔 You were mentioned on {task_key}",
            description=f"**{author_name or 'Someone'}** mentioned you in a comment on {issue_link}.",
            color=COLOR_AMBER,
            fields=fields,
            timestamp=timestamp,
            url=jira_url
        )

    @classmethod
    def format_task_assigned(
        cls,
        task_key: str,
        task_title: str,
        new_assignee_name: str,
        old_assignee_name: Optional[str] = None,
        assigned_by: Optional[str] = None,
        is_assigned_to_me: bool = False,
        timestamp: Optional[str] = None
    ) -> Dict[str, Any]:
        """Format a ticket assignment notification."""
        issue_link = cls.format_jira_link(task_key)
        jira_url = settings.get_jira_browse_url(task_key)
        
        if is_assigned_to_me:
            title = f"🎯 Task Assigned to You — {task_key}"
            desc = f"You have been assigned to task {issue_link}."
            color = COLOR_GREEN
        else:
            title = f"👤 Task Assigned — {task_key}"
            desc = f"Task {issue_link} was assigned to **{new_assignee_name or 'Unassigned'}**."
            color = COLOR_BLUE

        fields = [
            {"name": "Issue", "value": issue_link, "inline": True},
            {"name": "New Assignee", "value": new_assignee_name or "Unassigned", "inline": True},
            {"name": "Previous Assignee", "value": old_assignee_name or "None", "inline": True},
            {"name": "Title", "value": task_title or "Untitled", "inline": False},
        ]
        if assigned_by:
            fields.append({"name": "Assigned By", "value": assigned_by, "inline": True})

        return cls.format_embed(
            title=title,
            description=desc,
            color=color,
            fields=fields,
            timestamp=timestamp,
            url=jira_url
        )

    @classmethod
    def format_jira_navigator_link(cls, ticket_keys: List[str], label: Optional[str] = None) -> str:
        """Construct a clickable markdown link to Jira Issue Navigator for a list of tickets."""
        if not ticket_keys:
            return label or "0"
        url = settings.get_jira_issue_navigator_url(ticket_keys)
        unique_keys = sorted(list({k.strip() for k in ticket_keys if k and k.strip()}))
        display_label = label if label is not None else str(len(unique_keys))
        return f"[{display_label}]({url})"

    @staticmethod
    def _get_visual_length(text: str) -> int:
        """Calculate visual character width in monospace font (emojis count as 2)."""
        length = 0
        for char in text:
            if ord(char) > 0x2000:
                length += 2
            else:
                length += 1
        return length

    @classmethod
    def _pad_visual(cls, text: str, width: int) -> str:
        """Pad string to visual monospace width with trailing spaces."""
        vlen = cls._get_visual_length(text)
        pad = max(0, width - vlen)
        return text + (" " * pad)

    @classmethod
    def format_daily_worklog_report(cls, report_data: Dict[str, Any]) -> Dict[str, Any]:
        """Format daily team worklog report into a rich Discord embed with clickable Jira links and single unified table."""
        team_name = report_data.get("team_name", "Team")
        report_date = report_data.get("report_date", "Today")
        formatted_date = report_data.get("formatted_date") or report_date
        tickets_worked_count = report_data.get("tickets_worked_count", 0)

        # Build clean monospace table for all eligible members in a single table
        member_entries = report_data.get("members", [])
        table_text = ""
        if member_entries:
            # Determine maximum visual column width for member names, preserving full names without truncation
            max_name_len = 0
            for m in member_entries:
                name = m.get("display_name") or "Unknown"
                time_secs = m.get("time_logged_seconds", 0)
                ticket_keys = m.get("tickets", [])
                has_worked = (time_secs > 0) or (len(ticket_keys) > 0)
                disp_name = name if has_worked else f"🔴 {name}"
                vlen = cls._get_visual_length(disp_name)
                if vlen > max_name_len:
                    max_name_len = vlen

            col_member_w = max(24, max_name_len)
            col_time_w = 11
            col_tickets_w = 7

            header_line = f"| {'Team Member':<{col_member_w}} | {'Time Logged':<{col_time_w}} | {'Tickets':<{col_tickets_w}} |"
            sep_line = f"| {'-' * col_member_w} | {'-' * col_time_w} | {'-' * col_tickets_w} |"

            table_lines = [f"`{header_line}`", f"`{sep_line}`"]
            for m in member_entries:
                name = m.get("display_name") or "Unknown"
                time_secs = m.get("time_logged_seconds", 0)
                ticket_keys = m.get("tickets", [])
                has_worked = (time_secs > 0) or (len(ticket_keys) > 0)

                disp_name = name if has_worked else f"🔴 {name}"
                np = cls._pad_visual(disp_name, col_member_w)
                time_str = m.get("time_logged_human") or "0m" if has_worked else "0m"
                tp = cls._pad_visual(time_str, col_time_w)

                if has_worked:
                    unique_tickets = sorted(list({k.strip() for k in ticket_keys if k and k.strip()}))
                    t_count = m.get("tickets_count") or len(unique_tickets)
                    pad_t = " " * max(0, col_tickets_w - len(str(t_count)))
                    if unique_tickets:
                        nav_url = settings.get_jira_issue_navigator_url(unique_tickets)
                        tickets_cell = f"[{t_count}]({nav_url})"
                    else:
                        tickets_cell = str(t_count)
                    # Inline code wrapping ensures Discord maintains fixed-width monospace alignment
                    # while preserving clickable Jira link outside the backtick markers
                    row = f"`| {np} | {tp} | `{tickets_cell}`{pad_t} |`"
                else:
                    pad_t = " " * max(0, col_tickets_w - 1)
                    row = f"`| {np} | {tp} | 0{pad_t} |`"

                table_lines.append(row)

            table_text = "\n".join(table_lines)

        description = (
            f"**Date:** {formatted_date}\n"
            f"**Tickets Worked:** {tickets_worked_count}\n\n"
            f"👥 Team Worklog\n\n"
            f"{table_text}"
        )

        return cls.format_embed(
            title=f"📊 {team_name} — Daily Worklog",
            description=description,
            color=COLOR_PURPLE,
            fields=[]
        )

    @classmethod
    def format_overdue_digest(cls, report_data: Dict[str, Any]) -> Dict[str, Any]:
        """Format daily consolidated overdue task digest for Discord."""
        team_name = report_data.get("team_name") or "Team"
        formatted_date = report_data.get("formatted_date") or report_data.get("date") or "Today"
        tickets = report_data.get("tickets", [])

        if not tickets:
            title = f"✅ {team_name} — Overdue Tasks"
            description = f"**Date:** {formatted_date}\n\nNo overdue tasks."
            return cls.format_embed(
                title=title,
                description=description,
                color=COLOR_GREEN,
                fields=[]
            )

        title = f"🚨 {team_name} — Overdue Tasks"

        def _format_assignee(name: Any, max_len: int = 16) -> str:
            """Format assignee name cleanly, using middle/last name for long names."""
            if not name or not str(name).strip():
                return "Unassigned"
            s = str(name).strip()
            if len(s) <= max_len:
                return s
            parts = s.split()
            if len(parts) >= 3:
                # e.g., "Muhammad Ali Siddiqui" -> "Ali Siddiqui"
                middle_last = " ".join(parts[1:])
                if len(middle_last) <= max_len:
                    return middle_last.title()
                initialed = f"{parts[0][0]}. {middle_last}"
                if len(initialed) <= max_len:
                    return initialed.title()
                return f"{parts[0][0]}. {parts[-1]}".title()[:max_len]
            elif len(parts) == 2:
                # e.g., "Muhammad Shahmeer" -> "M. Shahmeer"
                initialed = f"{parts[0][0]}. {parts[1]}"
                if len(initialed) <= max_len:
                    return initialed.title()
                return initialed.title()[:max_len]
            return s[:max_len]

        def _format_summary(summary: Any, max_len: int = 30) -> str:
            """Clean and truncate summary string to fixed width."""
            if not summary or not str(summary).strip():
                return "No summary"
            s = " ".join(str(summary).strip().split()).replace("|", "-")
            if len(s) <= max_len:
                return s
            return s[:max_len - 3].rstrip() + "..."

        # Calculate column widths
        col_ticket_w = max(10, max((len(t.get("key", "")) for t in tickets), default=10))
        col_sum_w = 30
        col_ass_w = 16
        col_due_w = 12

        header_line = f"| {'Ticket':<{col_ticket_w}} | {'Summary':<{col_sum_w}} | {'Assignee':<{col_ass_w}} | {'Due Date':<{col_due_w}} |"
        sep_line = f"| {'-' * col_ticket_w} | {'-' * col_sum_w} | {'-' * col_ass_w} | {'-' * col_due_w} |"

        table_lines = [header_line, sep_line]
        ticket_keys = []
        for t in tickets:
            tkey = t.get("key", "Unknown")
            if tkey and tkey != "Unknown":
                ticket_keys.append(tkey)
            sum_str = _format_summary(t.get("summary"), col_sum_w)
            ass_str = _format_assignee(t.get("assignee"), col_ass_w)
            due_str = t.get("due_date") or "N/A"

            row = f"| {tkey:<{col_ticket_w}} | {sum_str:<{col_sum_w}} | {ass_str:<{col_ass_w}} | {due_str:<{col_due_w}} |"
            table_lines.append(row)

        table_block = "```\n" + "\n".join(table_lines) + "\n```"

        nav_url = settings.get_jira_issue_navigator_url(ticket_keys) if ticket_keys else settings.JIRA_BASE_URL
        jira_link_text = f"[🔗 Open {len(tickets)} Overdue Tasks in Jira]({nav_url})"

        description = f"**Date:** {formatted_date}\n\n{table_block}\n{jira_link_text}"

        return cls.format_embed(
            title=title,
            description=description,
            color=COLOR_RED,
            fields=[]
        )



    @classmethod
    def format_pm_attention_digest(cls, report_data: Dict[str, Any]) -> Dict[str, Any]:
        """Format daily consolidated PM Attention Digest for Discord."""
        team_name = report_data.get("team_name") or "Team"
        formatted_date = report_data.get("formatted_date") or report_data.get("date") or "Today"
        total_count = report_data.get("total_count", 0)
        categories = report_data.get("categories", {})

        if total_count == 0:
            title = f"✅ {team_name} — PM Attention Digest"
            description = f"**Date:** {formatted_date}\n\nNo items requiring attention."
            return cls.format_embed(
                title=title,
                description=description,
                color=COLOR_GREEN,
                fields=[]
            )

        title = f"⚠️ {team_name} — PM Attention Digest"
        sections = []

        # 1. Inactive / Stalled
        stale_info = categories.get("inactive_stalled", {})
        stale_tickets = stale_info.get("tickets", [])
        if stale_tickets:
            stale_title = stale_info.get("title", "🟠 Inactive / Stalled")
            col_ticket_w = max(11, max((len(t.get("key", "")) for t in stale_tickets), default=11))
            col_status_w = max(11, max((len(t.get("status", "")) for t in stale_tickets), default=11))
            col_updated_w = 12
            col_inactive_w = 12

            header_line = f"| {'Ticket':<{col_ticket_w}} | {'Status':<{col_status_w}} | {'Last Updated':<{col_updated_w}} | {'Inactive For':<{col_inactive_w}} |"
            sep_line = f"| {'-' * col_ticket_w} | {'-' * col_status_w} | {'-' * col_updated_w} | {'-' * col_inactive_w} |"

            table_lines = [f"`{header_line}`", f"`{sep_line}`"]
            for t in stale_tickets:
                tkey = t.get("key", "Unknown")
                jira_url = t.get("url") or settings.get_jira_browse_url(tkey)
                status_str = t.get("status", "Unknown")
                upd_str = t.get("updated_at", "N/A")
                inactive_str = t.get("inactive_for", "N/A")
                pad_ticket = " " * max(0, col_ticket_w - len(tkey))
                row = f"`| `[{tkey}]({jira_url})`{pad_ticket} | {status_str:<{col_status_w}} | {upd_str:<{col_updated_w}} | {inactive_str:<{col_inactive_w}} |`"
                table_lines.append(row)

            sections.append(f"{stale_title}\n\n" + "\n".join(table_lines))

        # 2. Reopened
        reopened_info = categories.get("reopened", {})
        reopened_tickets = reopened_info.get("tickets", [])
        if reopened_tickets:
            reopened_title = reopened_info.get("title", "🔁 Reopened")
            col_ticket_w = max(11, max((len(t.get("key", "")) for t in reopened_tickets), default=11))
            col_status_w = max(11, max((len(t.get("status", "")) for t in reopened_tickets), default=11))
            col_updated_w = 12

            header_line = f"| {'Ticket':<{col_ticket_w}} | {'Status':<{col_status_w}} | {'Last Updated':<{col_updated_w}} |"
            sep_line = f"| {'-' * col_ticket_w} | {'-' * col_status_w} | {'-' * col_updated_w} |"

            table_lines = [f"`{header_line}`", f"`{sep_line}`"]
            for t in reopened_tickets:
                tkey = t.get("key", "Unknown")
                jira_url = t.get("url") or settings.get_jira_browse_url(tkey)
                status_str = t.get("status", "Unknown")
                upd_str = t.get("updated_at", "N/A")
                pad_ticket = " " * max(0, col_ticket_w - len(tkey))
                row = f"`| `[{tkey}]({jira_url})`{pad_ticket} | {status_str:<{col_status_w}} | {upd_str:<{col_updated_w}} |`"
                table_lines.append(row)

            sections.append(f"{reopened_title}\n\n" + "\n".join(table_lines))

        # 3. Unassigned
        unassigned_info = categories.get("unassigned", {})
        unassigned_tickets = unassigned_info.get("tickets", [])
        if unassigned_tickets:
            unassigned_title = unassigned_info.get("title", "📌 Unassigned")
            col_ticket_w = max(11, max((len(t.get("key", "")) for t in unassigned_tickets), default=11))
            col_status_w = max(11, max((len(t.get("status", "")) for t in unassigned_tickets), default=11))
            col_updated_w = 12

            header_line = f"| {'Ticket':<{col_ticket_w}} | {'Status':<{col_status_w}} | {'Last Updated':<{col_updated_w}} |"
            sep_line = f"| {'-' * col_ticket_w} | {'-' * col_status_w} | {'-' * col_updated_w} |"

            table_lines = [f"`{header_line}`", f"`{sep_line}`"]
            for t in unassigned_tickets:
                tkey = t.get("key", "Unknown")
                jira_url = t.get("url") or settings.get_jira_browse_url(tkey)
                status_str = t.get("status", "Unknown")
                upd_str = t.get("updated_at", "N/A")
                pad_ticket = " " * max(0, col_ticket_w - len(tkey))
                row = f"`| `[{tkey}]({jira_url})`{pad_ticket} | {status_str:<{col_status_w}} | {upd_str:<{col_updated_w}} |`"
                table_lines.append(row)

            sections.append(f"{unassigned_title}\n\n" + "\n".join(table_lines))

        body = "\n\n".join(sections)
        description = f"**Date:** {formatted_date}\n\n{body}"

        return cls.format_embed(
            title=title,
            description=description,
            color=COLOR_AMBER,
            fields=[]
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
