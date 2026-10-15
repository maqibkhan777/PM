import json
import datetime
from collections import defaultdict

def analyze_historical_data():
    with open("historical_worklogs_cache.json", "r", encoding="utf-8") as f:
        wl_cache = json.load(f)
        
    with open("historical_activity_cache.json", "r", encoding="utf-8") as f:
        act_cache = json.load(f)
        
    dates = wl_cache["dates"]
    eligible_members = wl_cache["eligible_members"]
    worklogs = wl_cache["worklogs_by_date_member"]
    
    # Official gazetted public holidays in Pakistan during Sept 9 - Oct 8 2026:
    # 1. Eid Milad-un-Nabi (12 Rabi-ul-Awwal) - In 2026, approx late August or September.
    # Official gazetted holidays in Pakistan Sept 9 - Oct 8: None officially gazetted in late September / early October (Defence Day is Sept 6 which is before Sept 9; Iqbal Day is Nov 9).
    # Let's check weekends (Saturday=5, Sunday=6)
    
    total_dates = len(dates)
    total_members = len(eligible_members)
    total_combinations = total_dates * total_members
    
    weekend_dates = set()
    weekday_dates = []
    
    for d in dates:
        dt = datetime.datetime.strptime(d, "%Y-%m-%d")
        if dt.weekday() in (5, 6): # Saturday or Sunday
            weekend_dates.add(d)
        else:
            weekday_dates.append(d)
            
    print(f"Total dates: {total_dates} (Weekdays: {len(weekday_dates)}, Weekends: {len(weekend_dates)})")
    
    # Data tables
    records = []
    summary_stats = {
        "dates_evaluated": total_dates,
        "eligible_members": total_members,
        "combinations_evaluated": total_combinations,
        "compliant_cases": 0,
        "reminder_candidates": 0,
        "skipped_zero_worklog_zero_activity": 0,
        "skipped_approved_leave": 0,
        "skipped_weekends": 0,
        "skipped_holidays": 0,
        "unmapped_members": total_members, # 18 unmapped to MM in dev db
        "undetermined_cases": 0
    }
    
    candidate_list = []
    
    for d in dates:
        dt = datetime.datetime.strptime(d, "%Y-%m-%d")
        is_weekend = d in weekend_dates
        day_name = dt.strftime("%A")
        
        for aid, name in eligible_members.items():
            user_wls = worklogs.get(d, {}).get(aid, [])
            total_seconds = sum(w["time_spent_seconds"] for w in user_wls)
            minutes = total_seconds // 60
            
            # Activity check
            active_users_today = set(act_cache.get(d, []))
            has_activity = aid in active_users_today or len(user_wls) > 0
            
            # Leave status: No leave management system in repo -> UNAVAILABLE
            leave_status = "unavailable"
            
            # Decision evaluation
            if is_weekend:
                holiday_decision = f"Weekend ({day_name})"
                outcome = "SKIPPED"
                reason = "Non-working day (Weekend)"
                summary_stats["skipped_weekends"] += 1
            else:
                holiday_decision = "Working Day"
                if minutes >= 60:
                    outcome = "COMPLIANT"
                    reason = f"Met threshold ({minutes}m >= 60m)"
                    summary_stats["compliant_cases"] += 1
                elif 1 <= minutes < 60:
                    outcome = "REMINDER_CANDIDATE"
                    reason = f"Underlogged ({minutes}m < 60m) on working day"
                    summary_stats["reminder_candidates"] += 1
                    candidate_list.append({
                        "date": d,
                        "day": day_name,
                        "account_id": aid,
                        "name": name,
                        "minutes": minutes,
                        "activity": "yes" if has_activity else "no",
                        "reason": f"Logged only {minutes}m (Threshold: 60m)"
                    })
                else: # minutes == 0
                    if not has_activity:
                        outcome = "SKIPPED"
                        reason = "Zero worklog and zero activity (Presumed on leave / non-working)"
                        summary_stats["skipped_zero_worklog_zero_activity"] += 1
                    else:
                        outcome = "REMINDER_CANDIDATE"
                        reason = "Zero worklog but meaningful Jira activity detected"
                        summary_stats["reminder_candidates"] += 1
                        candidate_list.append({
                            "date": d,
                            "day": day_name,
                            "account_id": aid,
                            "name": name,
                            "minutes": 0,
                            "activity": "yes",
                            "reason": "Active on Jira today but 0m logged"
                        })
                        
            records.append({
                "date": d,
                "account_id": aid,
                "name": name,
                "minutes": minutes,
                "activity": "yes" if has_activity else "no",
                "leave_status": leave_status,
                "holiday_decision": holiday_decision,
                "outcome": outcome,
                "reason": reason
            })
            
    print("=== SUMMARY STATS ===")
    for k, v in summary_stats.items():
        print(f" - {k}: {v}")
        
    print(f"\nTotal Reminder Candidates: {len(candidate_list)}")
    print(f"Sample candidates (first 10 of {len(candidate_list)}):")
    for c in candidate_list[:10]:
        print(f"  [{c['date']}] {c['name']} - {c['minutes']}m logged | Activity: {c['activity']} | {c['reason']}")
        
    analysis_result = {
        "summary": summary_stats,
        "candidates": candidate_list,
        "records_count": len(records)
    }
    with open("historical_analysis_results.json", "w", encoding="utf-8") as f:
        json.dump(analysis_result, f, indent=2)
    print("Saved historical_analysis_results.json successfully!")

if __name__ == "__main__":
    analyze_historical_data()
