import asyncio
import json
import zoneinfo
from datetime import datetime, timedelta
from typing import Dict, Any, List, Set
from app.config.settings import settings
from app.connectors.jira.client import JiraClient
from app.connectors.jira.normalizer import JiraEventNormalizer

async def run_audit():
    client = JiraClient()
    tz = zoneinfo.ZoneInfo("Asia/Karachi")
    now_pkt = datetime.now(tz)
    
    # 30 calendar days ending yesterday
    yesterday = (now_pkt - timedelta(days=1)).date()
    start_date = (now_pkt - timedelta(days=30)).date()
    
    date_list = [(start_date + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(30)]
    print(f"Date range: {date_list[0]} to {date_list[-1]} ({len(date_list)} dates)")
    
    # Group members
    team_group = settings.JIRA_TEAM_GROUP
    raw_members = await client.get_group_members(team_group)
    excluded_ids = settings.get_canonical_excluded_account_ids()
    
    eligible_members = {}
    for m in raw_members:
        aid = m.get("accountId")
        if not aid or not m.get("active", True):
            continue
        if aid in excluded_ids:
            continue
        eligible_members[aid] = m.get("displayName") or "Unknown"
        
    print(f"Total group members: {len(raw_members)}")
    print(f"Eligible members (excluding Mursaleen & Aqib): {len(eligible_members)}")
    for aid, name in eligible_members.items():
        print(f" - {name} ({aid})")
        
    # Search JQL
    jql = f'worklogAuthor in membersOf("{team_group}") AND worklogDate >= "{date_list[0]}" AND worklogDate <= "{date_list[-1]}"'
    print(f"Searching JQL: {jql}")
    
    all_issues = []
    next_page_token = None
    page = 1
    while True:
        res = await client.search_issues(
            jql=jql,
            max_results=50,
            next_page_token=next_page_token,
            fields=["summary", "worklog", "assignee"]
        )
        issues = res.get("issues", [])
        all_issues.extend(issues)
        print(f"Page {page}: fetched {len(issues)} issues")
        if res.get("isLast", True) or not res.get("nextPageToken"):
            break
        next_page_token = res.get("nextPageToken")
        page += 1
        
    print(f"Total issues fetched: {len(all_issues)}")
    
    # Extract all worklogs
    # map: (date_str, account_id) -> list of worklog dicts
    worklogs_by_date_member: Dict[str, Dict[str, List[Dict[str, Any]]]] = {
        d: {aid: [] for aid in eligible_members} for d in date_list
    }
    
    for issue in all_issues:
        task_key = issue.get("key")
        fields = issue.get("fields", {})
        wl_section = fields.get("worklog", {})
        worklogs_list = wl_section.get("worklogs", []) if isinstance(wl_section, dict) else []
        total_wls = wl_section.get("total", len(worklogs_list)) if isinstance(wl_section, dict) else len(worklogs_list)
        
        if total_wls > len(worklogs_list):
            try:
                worklogs_list = await client.get_issue_worklogs(task_key)
            except Exception as e:
                print(f"Warning: could not fetch full worklogs for {task_key}: {e}")
                
        for w in worklogs_list:
            author = w.get("author", {})
            author_id = author.get("accountId")
            if not author_id or author_id not in eligible_members:
                continue
                
            # started date in PKT
            started_raw = w.get("started") or w.get("created")
            if not started_raw:
                continue
            # started format e.g. "2026-09-10T14:30:00.000+0500"
            work_date_str = started_raw[:10]
            if work_date_str in worklogs_by_date_member and author_id in worklogs_by_date_member[work_date_str]:
                time_secs = int(w.get("timeSpentSeconds", 0))
                worklogs_by_date_member[work_date_str][author_id].append({
                    "issue_key": task_key,
                    "time_spent_seconds": time_secs,
                    "worklog_id": str(w.get("id")),
                    "comment": w.get("comment", "")
                })

    # Save intermediate results to json for analysis
    summary_data = {
        "dates": date_list,
        "eligible_members": eligible_members,
        "total_issues": len(all_issues),
        "worklogs_by_date_member": worklogs_by_date_member
    }
    with open("historical_worklogs_cache.json", "w", encoding="utf-8") as f:
        json.dump(summary_data, f, indent=2)
    print("Saved cache to historical_worklogs_cache.json successfully!")
    await client.close()

if __name__ == "__main__":
    asyncio.run(run_audit())
