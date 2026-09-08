"""CLI simulator for testing end-to-end Jira webhook scenarios locally."""

import argparse
import asyncio
import json
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database.schema import init_db
from app.services.orchestrator import orchestrator
from app.services.scheduler import periodic_scheduler
from app.utils.time import utc_now_iso, format_iso
from datetime import datetime, timezone, timedelta
from app.utils.logger import logger


def generate_scenario_a_payload() -> dict:
    """Scenario A: Developer moves CF7-421 from 'To Do' to 'In Progress'."""
    return {
        "webhookEvent": "jira:issue_updated",
        "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
        "user": {
            "accountId": "jira-user-ahsan",
            "displayName": "Ahsan Amin",
            "emailAddress": "ahsan.amin@example.com"
        },
        "issue": {
            "id": "10042",
            "key": "CF7-421",
            "fields": {
                "summary": "Payment Gateway Testing",
                "status": {"name": "In Progress"},
                "project": {"id": "1001", "key": "CF7", "name": "CF7 Apps"},
                "assignee": {"accountId": "jira-user-ahsan", "displayName": "Ahsan Amin"}
            }
        },
        "changelog": {
            "id": f"chg-{int(datetime.now(timezone.utc).timestamp())}",
            "items": [
                {
                    "field": "status",
                    "fromString": "To Do",
                    "toString": "In Progress"
                }
            ]
        }
    }


def generate_scenario_b_payload() -> dict:
    """Scenario B: Developer comments on task CF7-422 while remaining in 'To Do'."""
    return {
        "webhookEvent": "jira:comment_created",
        "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
        "user": {
            "accountId": "jira-user-ahsan",
            "displayName": "Ahsan Amin",
            "emailAddress": "ahsan.amin@example.com"
        },
        "issue": {
            "id": "10043",
            "key": "CF7-422",
            "fields": {
                "summary": "Stripe Webhook Verification",
                "status": {"name": "To Do"},
                "project": {"id": "1001", "key": "CF7", "name": "CF7 Apps"},
                "assignee": {"accountId": "jira-user-ahsan", "displayName": "Ahsan Amin"}
            }
        },
        "comment": {
            "id": f"comment-{int(datetime.now(timezone.utc).timestamp())}",
            "author": {
                "accountId": "jira-user-ahsan",
                "displayName": "Ahsan Amin"
            },
            "body": "Started looking into the Stripe signature verification bug now."
        }
    }


def generate_scenario_d_payload() -> dict:
    """Scenario D: Activity occurs by an UNMAPPED user (triggers USER_MAPPING_REQUIRED alert)."""
    return {
        "webhookEvent": "jira:issue_updated",
        "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
        "user": {
            "accountId": "jira-user-unmapped-999",
            "displayName": "New Contractor",
            "emailAddress": "contractor@external-agency.com"
        },
        "issue": {
            "id": "10099",
            "key": "CF7-499",
            "fields": {
                "summary": "Legacy PHP Script Refactor",
                "status": {"name": "In Progress"},
                "project": {"id": "1001", "key": "CF7", "name": "CF7 Apps"},
                "assignee": {"accountId": "jira-user-unmapped-999", "displayName": "New Contractor"}
            }
        },
        "changelog": {
            "id": f"chg-{int(datetime.now(timezone.utc).timestamp()) + 100}",
            "items": [
                {
                    "field": "status",
                    "fromString": "To Do",
                    "toString": "In Progress"
                }
            ]
        }
    }


async def run_simulation(scenario: str):
    init_db()
    await orchestrator.initialize()

    print("\n" + "=" * 70)
    print(f"  RUNNING WEBHOOK SIMULATION: SCENARIO {scenario.upper()}")
    print("=" * 70 + "\n")

    if scenario in ("a", "all"):
        print("-> Executing Scenario A: Status transition 'To Do' -> 'In Progress' on CF7-421...")
        payload_a = generate_scenario_a_payload()
        event_id = await orchestrator.process_raw_webhook("jira", payload_a)
        print(f"  [OK] Webhook accepted (Event ID: {event_id})")
        await asyncio.sleep(1.0)

    if scenario in ("b", "all"):
        print("\n-> Executing Scenario B: Active work comment on 'To Do' task CF7-422 (WorkflowViolation)...")
        payload_b = generate_scenario_b_payload()
        event_id = await orchestrator.process_raw_webhook("jira", payload_b)
        print(f"  [OK] Webhook accepted (Event ID: {event_id})")
        await asyncio.sleep(1.0)

    if scenario in ("c", "all"):
        print("\n-> Executing Scenario C: Inactive 'In Progress' task evaluated by Scheduler (StaleTask)...")
        # Ingest a task event with timestamp 26 hours ago
        twenty_six_hours_ago = format_iso(datetime.now(timezone.utc) - timedelta(hours=26))
        stale_payload = {
            "webhookEvent": "jira:issue_updated",
            "timestamp": twenty_six_hours_ago,
            "user": {"accountId": "jira-user-ahsan", "displayName": "Ahsan Amin"},
            "issue": {
                "id": "10050",
                "key": "CF7-450",
                "fields": {
                    "summary": "Database Optimization",
                    "status": {"name": "In Progress"},
                    "project": {"key": "CF7", "name": "CF7 Apps"},
                    "assignee": {"accountId": "jira-user-ahsan", "displayName": "Ahsan Amin"}
                }
            }
        }
        eid = orchestrator.event_repo.insert(
            event_type="TaskStatusChanged",
            source="jira",
            external_event_id=f"jira:sim:stale:{int(datetime.now(timezone.utc).timestamp())}",
            timestamp=twenty_six_hours_ago,
            actor_id="jira-user-ahsan",
            actor_name="Ahsan Amin",
            task_id="CF7-450",
            payload=stale_payload,
            processing_status="PROCESSED"
        )
        print(f"  [OK] Injected stale task event (ID: {eid})")
        print("  [OK] Triggering periodic scheduler evaluation cycle...")
        cycle_res = await periodic_scheduler.run_cycle()
        print(f"  [OK] Scheduler cycle result: {cycle_res}")
        await asyncio.sleep(1.0)

    if scenario in ("d", "all"):
        print("\n-> Executing Scenario D: Unmapped user activity (Missing Mattermost mapping)...")
        payload_d = generate_scenario_d_payload()
        event_id = await orchestrator.process_raw_webhook("jira", payload_d)
        print(f"  [OK] Webhook accepted (Event ID: {event_id})")
        await asyncio.sleep(1.0)

    print("\n" + "=" * 70)
    print("  SIMULATION COMPLETE")
    print("=" * 70 + "\n")

    await orchestrator.shutdown()


def main():
    parser = argparse.ArgumentParser(description="Simulate Jira webhook scenarios locally.")
    parser.add_argument(
        "--scenario",
        choices=["a", "b", "c", "d", "all"],
        default="all",
        help="Scenario to simulate (a: Normal status change, b: Active work on To Do, c: Stale task >24h, d: Unmapped user, all: Run all)"
    )
    args = parser.parse_args()
    asyncio.run(run_simulation(args.scenario))


if __name__ == "__main__":
    main()
