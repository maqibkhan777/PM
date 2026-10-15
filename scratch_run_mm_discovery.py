import asyncio
import json
from app.connectors.jira.client import JiraClient
from app.connectors.mattermost.client import MattermostClient
from app.config.settings import settings

async def run_discovery():
    jira = JiraClient()
    mm = MattermostClient()
    
    raw_members = await jira.get_group_members("Mursaleen Cluster")
    excluded = settings.get_canonical_excluded_account_ids()
    eligible = [m for m in raw_members if m.get("accountId") and m.get("active", True) and m.get("accountId") not in excluded]
    
    print(f"Total eligible members: {len(eligible)}")
    
    results = []
    
    for m in eligible:
        aid = m.get("accountId")
        dname = m.get("displayName")
        email = m.get("emailAddress")
        
        mm_user = None
        method_desc = ""
        
        # 1. Primary check: Exact corporate email lookup via GET /users/email/{email}
        if email and email.strip():
            try:
                res = await mm.get_user_by_email(email.strip())
                if res and res.get("id"):
                    mm_user = res
                    method_desc = f"GET /users/email/{email.strip()}"
            except Exception as e:
                print(f"Email lookup error for {email}: {e}")
                
        # 2. Secondary check: If email missing/hidden, attempt username search
        if not mm_user:
            candidate_unames = []
            if email and "@" in email:
                candidate_unames.append(email.split("@")[0].strip().lower())
            parts = [p.strip().lower() for p in dname.split() if p.strip()]
            if len(parts) >= 2:
                candidate_unames.extend([f"{parts[0]}.{parts[-1]}", f"{parts[0]}{parts[-1]}", parts[0]])
            elif len(parts) == 1:
                candidate_unames.append(parts[0])
                
            for uname in candidate_unames:
                try:
                    res = await mm.get_user_by_username(uname)
                    if res and res.get("id"):
                        mm_user = res
                        method_desc = f"GET /users/username/{uname}"
                        break
                except Exception:
                    pass
                    
        if mm_user and mm_user.get("id"):
            mm_id = mm_user.get("id")
            mm_uname = mm_user.get("username", "")
            mm_email = mm_user.get("email", "")
            mm_first = mm_user.get("first_name", "")
            mm_last = mm_user.get("last_name", "")
            mm_full = f"{mm_first} {mm_last}".strip()
            
            # Policy classification: Matches found via corporate email or username are CANDIDATE_REQUIRES_CONFIRMATION
            status = "CANDIDATE_REQUIRES_CONFIRMATION"
            evidence = f"Mattermost account matched via {method_desc} -> Username: @{mm_uname}, Email: {mm_email}, Name: '{mm_full}'"
            follow_up = "Operator confirmation required before seeding into user_mappings"
        else:
            mm_id = ""
            mm_uname = ""
            status = "NOT_FOUND"
            evidence = f"No active Mattermost account found for email: '{email or 'Hidden in Jira'}' or name variations"
            follow_up = "Manual Mattermost username/ID provisioning needed"
            
        results.append({
            "jira_display_name": dname,
            "jira_account_id": aid,
            "jira_email": email or "",
            "mm_username": mm_uname,
            "mm_user_id": mm_id,
            "evidence": evidence,
            "status": status,
            "follow_up": follow_up
        })
        
    await jira.close()
    await mm.close()
    
    with open("discovery_output.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
        
    print("Discovery complete! Results saved to discovery_output.json")
    for r in results:
        print(f"[{r['status']}] {r['jira_display_name']} -> MM @{r['mm_username']} ({r['mm_user_id']})")

if __name__ == "__main__":
    asyncio.run(run_discovery())
