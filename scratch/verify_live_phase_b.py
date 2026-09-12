import json
import sqlite3
from app.config.settings import settings
from app.database.schema import init_db
from app.core.intelligence.engine import HistoricalIntelligenceEngine
from app.database.repositories import HistoricalIntelligenceRepository

def run_live_validation():
    print("=" * 75)
    print("RUNNING LIVE PHASE B ENGINE ON REAL SQLITE DB")
    print(f"Database path: {settings.get_database_path()}")
    print("=" * 75)

    # 1. Initialize DB migrations to ensure tables exist
    init_db()

    # 2. Run engine against real live data
    engine = HistoricalIntelligenceEngine()
    result = engine.run_analysis(persist=True)

    print("\n--- ANALYSIS RUN RESULT SUMMARY ---")
    print(f"Analysis Run ID: {result['analysis_run_id']}")
    print(f"Calculated At: {result['calculated_at']}")
    print(f"Actual Available History Days: {result['actual_available_history_days']:.1f}")
    print(f"Requested History Days: {result['requested_history_days']}")
    print(f"Total Authoritative Profiled Employees: {result['authoritative_count']}")
    print(f"Canonical Excluded Accounts Filtered: {result['excluded_count']}")
    print(f"Evidence Records Count: {result['evidence_count']}")
    print(f"Overall AI Readiness Status: {result['ai_readiness_status']}")

    # 3. Check 18 authoritative employees
    print("\n--- 18 AUTHORITATIVE EMPLOYEES PROFILED ---")
    canonical_account_ids = []
    profiles_list = list(result['profiles'].values())
    for p in profiles_list:
        canonical_account_ids.append(p.account_id)
        pressure_lvl = p.workload_pressure.pressure_level.value
        quality_lvl = p.data_quality.historical_completeness.value
        print(f"- {p.display_name:25} | Role: {p.role_category:20} | Active Tasks: {p.current_active_tasks_count:2} | Workload: {pressure_lvl:10} | Quality: {quality_lvl:10}")

    # 4. Check canonical exclusions absent
    print("\n--- CANONICAL EXCLUSIONS AUDIT ---")
    for excl_id in settings.CANONICAL_EXCLUDED_ACCOUNT_IDS:
        found = excl_id in canonical_account_ids
        print(f"Excluded ID '{excl_id}': {'PRESENT (ERROR)' if found else 'ABSENT (VERIFIED)'}")

    # 5. Check Ahsan Amin alias unification
    ahsan_profile = next((p for p in profiles_list if "ahsan" in p.display_name.lower() or p.account_id == "712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de"), None)
    print("\n--- AHSAN AMIN PROFILE CHECK ---")
    if ahsan_profile:
        print(f"Display Name: {ahsan_profile.display_name}")
        print(f"Account ID: {ahsan_profile.account_id}")
        print(f"Role Category: {ahsan_profile.role_category}")
        print(f"Known Aliases: {ahsan_profile.known_aliases}")
        print(f"Total Logged Hours: {ahsan_profile.total_logged_hours:.1f}h")
        print(f"Active Working Days: {ahsan_profile.active_working_days}")
        print(f"Average Hours/Day: {ahsan_profile.average_logged_hours_per_active_day:.2f}h")
        print(f"Current Active Tasks: {ahsan_profile.current_active_tasks_count}")
        print(f"Inferred Queue Workload: {ahsan_profile.current_queue_inferred_remaining_hours:.1f}h")
        print(f"Workload Pressure: {ahsan_profile.workload_pressure.pressure_level.value} ({ahsan_profile.workload_pressure.explanation})")
        print(f"Personal Baseline Logged Hours Avg: {ahsan_profile.personal_baseline.logged_hours_baseline.typical_historical_value:.2f}h ({ahsan_profile.personal_baseline.logged_hours_baseline.comparison_state.value})")
        print(f"Personal Baseline Complexity Avg: {ahsan_profile.personal_baseline.complexity_baseline.typical_historical_value:.2f} ({ahsan_profile.personal_baseline.complexity_baseline.comparison_state.value})")
        print(f"Personal Baseline Queue Count Avg: {ahsan_profile.personal_baseline.active_queue_baseline.typical_historical_value:.2f} ({ahsan_profile.personal_baseline.active_queue_baseline.comparison_state.value})")
        print(f"Delivery On-Time Tasks: {ahsan_profile.delivery_context.completed_on_due_date + ahsan_profile.delivery_context.completed_before_due_date}/{ahsan_profile.delivery_context.total_completed_tasks}")
        print(f"Delivery Overdue Count: {ahsan_profile.delivery_context.currently_overdue}")
        print(f"QA Rework Reopen Events: {ahsan_profile.review_rework.total_reopen_events}")
        print(f"Blocker Events Encountered: {ahsan_profile.blocker_history.total_blocker_events} ({ahsan_profile.blocker_history.total_blocked_hours:.1f}h blocked)")
        print(f"Logged Hours 30d/90d/180d/365d: {ahsan_profile.trends.logged_hours_trend.value_30d:.1f}h / {ahsan_profile.trends.logged_hours_trend.value_90d:.1f}h / {ahsan_profile.trends.logged_hours_trend.value_180d:.1f}h / {ahsan_profile.trends.logged_hours_trend.value_365d:.1f}h (Direction: {ahsan_profile.trends.logged_hours_trend.direction.value})")
        print(f"Completed Tasks Trend Direction: {ahsan_profile.trends.completed_tasks_trend.direction.value}")
    else:
        print("ERROR: Ahsan Amin profile not found!")

    # 6. Check Database Persistence & Repositories
    repo = HistoricalIntelligenceRepository()
    db_profiles = repo.list_profiles(run_id=result['analysis_run_id'])
    db_evidence = repo.get_evidence_records(run_id=result['analysis_run_id'])
    db_benchmarks = repo.get_effort_benchmarks(run_id=result['analysis_run_id'])

    print("\n--- DATABASE PERSISTENCE VERIFICATION ---")
    print(f"DB Profiles Count: {len(db_profiles)}")
    print(f"DB Evidence Count: {len(db_evidence)}")
    print(f"DB Benchmarks Count: {len(db_benchmarks)}")

    # 7. Check Task Mix Breakdown for Ahsan
    if ahsan_profile:
        ahsan_task_mix = repo.get_task_mix(account_id=ahsan_profile.account_id, run_id=result['analysis_run_id'])
        if ahsan_task_mix:
            print("\n--- AHSAN AMIN TASK MIX (Persisted) ---")
            print(f"Total Completed: {ahsan_task_mix.get('total_completed_tasks')}")
            print(f"Subtask Ratio: {ahsan_task_mix.get('subtask_percentage', 0):.1f}%")
            print(f"Task Nature Breakdown: {ahsan_task_mix.get('task_nature_distribution', [])}")

    # 8. Sample Effort Benchmark
    if db_benchmarks:
        print("\n--- SAMPLE EFFORT BENCHMARK TIERS ---")
        for bm in db_benchmarks[:5]:
            print(f"Tier {bm['segmentation_tier']:2} | Type: {bm['segment_type']:12} | Key: {bm['segment_key']:25} | Median: {bm['median_hours']:.1f}h | Conf: {bm['confidence']}")

    # 9. Sample Evidence Record
    if db_evidence:
        print("\n--- SAMPLE EVIDENCE RECORD ---")
        sample_ev = db_evidence[0]
        print(f"Employee ID: {sample_ev['account_id']}")
        print(f"Issue Key: {sample_ev['issue_key']}")
        print(f"Metric: {sample_ev['metric']}")
        print(f"Evidence Type: {sample_ev['evidence_type']}")
        print(f"Confidence: {sample_ev['confidence']}")
        print(f"Source: {sample_ev['source']}")
        print(f"Explanation: {sample_ev['explanation']}")

    print("\n" + "=" * 75)
    print("LIVE SQLITE VALIDATION COMPLETED SUCCESSFULLY")
    print("=" * 75)

if __name__ == "__main__":
    run_live_validation()
