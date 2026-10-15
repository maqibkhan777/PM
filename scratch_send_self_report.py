import asyncio
import json
from app.config.settings import settings
from app.connectors.mattermost.client import MattermostClient

async def send_consolidated_report():
    with open("historical_analysis_results.json", "r", encoding="utf-8") as f:
        data = json.load(f)
        
    summary = data["summary"]
    candidates = data["candidates"]
    
    # Verify safety: Self-only account
    client = MattermostClient()
    me = await client.get_me()
    me_id = me.get("id")
    expected_self = settings.MATTERMOST_SELF_ONLY_USER_ID
    
    if me_id != expected_self:
        raise ValueError(f"Safety check failed: Authenticated MM user {me_id} != configured self {expected_self}")
        
    # Format message
    lines = [
        "### 📊 Historical Simulation Report: Event 1 (Mursaleen Cluster Worklog Reminder)",
        "",
        "**Evaluation Scope & Date Window:**",
        f"- **Historical Dates Evaluated:** 30 calendar days (`2026-09-09` to `2026-10-08`, Asia/Karachi)",
        f"- **Eligible Cluster Members:** {summary['eligible_members']} (Excluding Mohammad Mursaleen & Aqib Khan)",
        f"- **Total Member-Date Evaluations:** {summary['combinations_evaluated']}",
        "",
        "**Summary Outcome Breakdown:**",
        f"- 🟢 **Compliant Cases (>= 60m):** {summary['compliant_cases']}",
        f"- 🟡 **Reminder Candidates (< 60m or 0m with Activity):** {summary['reminder_candidates']}",
        f"- ⚪ **Skipped (0m + No Activity / Presumed Leave):** {summary['skipped_zero_worklog_zero_activity']}",
        f"- 📅 **Skipped Non-Working Days (Weekends):** {summary['skipped_weekends']} ({summary['skipped_weekends']//summary['eligible_members']} weekend days x {summary['eligible_members']} members)",
        f"- 🌴 **Skipped Official / Local Holidays:** {summary['skipped_holidays']}",
        f"- ❓ **Undetermined Cases:** {summary['undetermined_cases']}",
        "",
        "**Data Quality & Leave Detection Limitations:**",
        "- **Leave Tracking:** No external leave system exists in the repo; evaluated leave status defaults to `unavailable`. Zero-worklog cases without activity were safely skipped.",
        "- **User Mappings:** 18 members lack mapped Mattermost IDs in local DB; in production, notifications will fail closed until explicitly mapped.",
        "",
        "**Candidate Reminders Breakdown (Simulated):**"
    ]
    
    for c in candidates:
        lines.append(f"- **{c['date']}** ({c['day']}): **{c['name']}** — `{c['minutes']}m logged` (Activity: {c['activity']}) → *{c['reason']}*")
        
    lines.append("")
    lines.append("*Note: This is a single historical test simulation sent exclusively to your verified account. No messages were sent to team members or channels.*")
    
    message_text = "\n".join(lines)
    
    print("Dispatching single consolidated direct message to self...")
    post = await client.send_direct_message(target_user_id=expected_self, message=message_text)
    print("Direct message delivered successfully! Post ID:", post.get("id"))
    await client.close()

if __name__ == "__main__":
    asyncio.run(send_consolidated_report())
