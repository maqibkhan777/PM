import asyncio
import json
from app.connectors.jira.client import JiraClient

async def fetch_activities():
    with open("historical_worklogs_cache.json", "r", encoding="utf-8") as f:
        cache = json.load(f)
    
    dates = cache["dates"]
    eligible_members = cache["eligible_members"]
    
    client = JiraClient()
    
    # We will search Jira changelogs and comments across the cluster for the 30-day window
    # JQL to find all issues updated in that date window by these authors or assignees
    jql = f'updatedDate >= "{dates[0]}" AND updatedDate <= "{dates[-1]} 23:59"'
    print("Searching updated issues with changelogs...")
    
    # Map: date_str -> set of account_ids with detected activity
    activity_by_date: dict[str, set[str]] = {d: set() for d in dates}
    
    # 1. Any worklog logged counts as activity on that date
    for d, member_wls in cache["worklogs_by_date_member"].items():
        for aid, wls in member_wls.items():
            if len(wls) > 0:
                activity_by_date[d].add(aid)
                
    # 2. Check comment author activity in the 684 issues already fetched or recent issues
    # Let's search issues updated in date range
    next_page_token = None
    page = 1
    total_issues = 0
    while True:
        res = await client.search_issues(
            jql=jql,
            max_results=50,
            next_page_token=next_page_token,
            expand="changelog",
            fields=["comment", "assignee", "updated", "status"]
        )
        issues = res.get("issues", [])
        total_issues += len(issues)
        for issue in issues:
            fields = issue.get("fields", {})
            # Check comments
            comments = (fields.get("comment") or {}).get("comments", [])
            for c in comments:
                author_id = (c.get("author") or {}).get("accountId")
                c_created = c.get("created") or ""
                c_date = c_created[:10]
                if author_id in eligible_members and c_date in activity_by_date:
                    activity_by_date[c_date].add(author_id)
            
            # Check changelog histories
            changelog = issue.get("changelog", {})
            histories = changelog.get("histories", []) if isinstance(changelog, dict) else []
            for h in histories:
                author_id = (h.get("author") or {}).get("accountId")
                h_created = h.get("created") or ""
                h_date = h_created[:10]
                if author_id in eligible_members and h_date in activity_by_date:
                    activity_by_date[h_date].add(author_id)
                    
        print(f"Changelog Page {page}: processed {len(issues)} issues")
        if res.get("isLast", True) or not res.get("nextPageToken") or page >= 15: # safety cap 15 pages
            break
        next_page_token = res.get("nextPageToken")
        page += 1
        
    await client.close()
    
    # Save activity map
    activity_serializable = {d: list(aids) for d, aids in activity_by_date.items()}
    with open("historical_activity_cache.json", "w", encoding="utf-8") as f:
        json.dump(activity_serializable, f, indent=2)
    print("Saved historical_activity_cache.json successfully!")

if __name__ == "__main__":
    asyncio.run(fetch_activities())
