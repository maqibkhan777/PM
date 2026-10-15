import pytest
from unittest.mock import MagicMock
from app.database.connection import db_manager
from app.services.user_mapping_service import UserMappingService
from app.config.settings import settings

# Exact specification reminder message
EVENT1_REMINDER_MESSAGE = "You have not yet logged your work. Kindly log your work for today in the respective tickets."

def evaluate_event1_decision(
    worklog_minutes: int,
    has_activity: bool,
    leave_status: str,
    is_weekend: bool,
    is_holiday: bool,
    is_excluded: bool,
    is_unmapped_mattermost: bool
):
    """Pure decision logic for Event 1 evaluation."""
    if is_excluded:
        return "EXCLUDED", "Excluded member account"
    if is_weekend:
        return "SKIPPED_WEEKEND", "Weekend (Non-working day)"
    if is_holiday:
        return "SKIPPED_HOLIDAY", "Gazetted / Approved Holiday"
    if worklog_minutes >= 60:
        return "COMPLIANT", f"Met threshold ({worklog_minutes}m >= 60m)"
    if 1 <= worklog_minutes < 60:
        if leave_status == "approved":
            return "SKIPPED_APPROVED_LEAVE", "Approved leave"
        if is_unmapped_mattermost:
            return "REMINDER_CANDIDATE_UNMAPPED", "Candidate but unmapped Mattermost account"
        return "REMINDER_CANDIDATE", f"Underlogged ({worklog_minutes}m < 60m)"
    # Zero worklog
    if not has_activity:
        return "SKIPPED_NO_ACTIVITY_LEAVE_UNVERIFIED", "Zero worklog and zero activity (Presumed leave/non-working)"
    if leave_status == "approved":
        return "SKIPPED_APPROVED_LEAVE", "Approved leave"
    if is_unmapped_mattermost:
        return "REMINDER_CANDIDATE_UNMAPPED", "Candidate but unmapped Mattermost account"
    return "REMINDER_CANDIDATE", "Zero worklog but active on Jira"

def test_exact_reminder_message_format():
    """Verify exact wording, absence of greetings, tags, emojis, or formatting."""
    assert EVENT1_REMINDER_MESSAGE == "You have not yet logged your work. Kindly log your work for today in the respective tickets."
    assert "Hi" not in EVENT1_REMINDER_MESSAGE
    assert "Hello" not in EVENT1_REMINDER_MESSAGE
    assert "@" not in EVENT1_REMINDER_MESSAGE
    assert "!" not in EVENT1_REMINDER_MESSAGE

def test_worklog_thresholds():
    # >= 60 min -> COMPLIANT
    res, _ = evaluate_event1_decision(60, True, "unavailable", False, False, False, False)
    assert res == "COMPLIANT"
    res, _ = evaluate_event1_decision(120, False, "unavailable", False, False, False, False)
    assert res == "COMPLIANT"
    
    # 1 - 59 min -> REMINDER_CANDIDATE
    res, _ = evaluate_event1_decision(45, True, "unavailable", False, False, False, False)
    assert res == "REMINDER_CANDIDATE"

def test_zero_worklog_zero_activity():
    # 0 min, no activity -> SKIPPED_NO_ACTIVITY_LEAVE_UNVERIFIED
    res, _ = evaluate_event1_decision(0, False, "unavailable", False, False, False, False)
    assert res == "SKIPPED_NO_ACTIVITY_LEAVE_UNVERIFIED"

def test_zero_worklog_with_activity():
    # 0 min, with activity -> REMINDER_CANDIDATE
    res, _ = evaluate_event1_decision(0, True, "unavailable", False, False, False, False)
    assert res == "REMINDER_CANDIDATE"

def test_approved_leave_exclusion():
    # Underlogged or zero with approved leave -> SKIPPED_APPROVED_LEAVE
    res, _ = evaluate_event1_decision(30, True, "approved", False, False, False, False)
    assert res == "SKIPPED_APPROVED_LEAVE"
    res, _ = evaluate_event1_decision(0, True, "approved", False, False, False, False)
    assert res == "SKIPPED_APPROVED_LEAVE"

def test_weekend_and_holiday_skips():
    # Weekend
    res, _ = evaluate_event1_decision(0, True, "unavailable", True, False, False, False)
    assert res == "SKIPPED_WEEKEND"
    # Holiday
    res, _ = evaluate_event1_decision(0, True, "unavailable", False, True, False, False)
    assert res == "SKIPPED_HOLIDAY"

def test_exclusion_of_mursaleen_aqib_and_usman():
    # Excluded account
    res, _ = evaluate_event1_decision(0, True, "unavailable", False, False, True, False)
    assert res == "EXCLUDED"
    
    canonical_excluded = settings.get_canonical_excluded_account_ids()
    # Confirm Aqib's accounts
    assert "712020:566cad70-4a54-42bc-bf36-0c6132fe3cf0" in canonical_excluded
    assert "712020:1b564792-a3ab-447c-951d-17aa5507b946" in canonical_excluded
    # Confirm Mursaleen
    assert "557058:8b3f9c31-7d88-473a-9351-abacc5b84933" in canonical_excluded
    # Confirm Usman
    assert "5f83e3937d9637006ffd0436" in canonical_excluded

def test_all_18_mursaleen_cluster_mappings_persisted():
    """Verify all 18 eligible accounts resolve via UserMappingService from DB."""
    svc = UserMappingService(db_manager)
    expected_mappings = {
        "712020:1ddac8e3-e006-48e7-b4c9-ee941efc8e6e": "zhth9yx8yprpbds1nyc4h7tich", # Azain Hassan
        "712020:bb2e5830-7156-4852-bba8-75fa773fc55d": "b3zeoe6h538wtrk6qfhiz7szcr", # Talha Bukhari
        "61ee41431c42100069344a09": "ywpkqpdd3jb7bq8t3piz1ur3ic",                     # Syed ali
        "712020:12e1da4b-147f-4f91-9d2d-965b66e19b61": "7ytz6ec89pnc5xti9pnqcstgso", # Muhammad Bilal Khan
        "63da2ba4f1475ad42c584247": "fpf9jhpde3na5b1bj971199p1h",                     # Ahsan Iftikhar
        "5fb3d908facfd6007697c25a": "rx11z31933n59bnakwaadbcf5y",                     # Muhammad Hamza
        "712020:c12d2371-1e5b-4797-a888-369c0c9c5a65": "eb1i9dapp3fkzd6k4qauf89haa", # Muhammad Sufiyan
        "712020:fb8608cb-6393-48a7-a3ab-1ad744a2b7f6": "7j347ruqitnotr8mrgibjkrdbw", # Muhammad Usama Azad
        "638490c75fce844d606a16ef": "a61ssoxtijy7ddtuxmx47csdme",                     # Hamza Hanif
        "606570150a6b3f00698f9430": "pjzc8k68ytf5txhh1jperrqeyy",                     # Muneeb Jalal
        "712020:e268bcd8-d981-4b4d-992d-d5694745df8b": "e956k4q3tpbfteu4t7sanb914r", # Mubashir Butt
        "712020:a6d04898-c6d8-4a39-a521-103e4b8bfe7c": "o7qamzdy138k5qmbuam4tuhhze", # Muhammad Shahmeer Khan
        "712020:0eca0fb9-4f12-4532-a435-4c178f2d90e8": "yq4bnkg5dfbjde7e64a7czox3o", # Nauman Sadiq
        "712020:2783ea21-c611-402d-9adb-0529f5b7066d": "ceujrj84zjyfuce8kx8b7bnn4a", # Muhammad Ali Siddiqui
        "63e362bd790148a180977179": "cekznaowyigp5mtgagbiz9w5xc",                     # Daniyal Raza
        "712020:32e5be05-80c9-4ece-ac19-301da7c9487d": "9grxc1xwoidzdrqnxc99yp96wy", # shoaib hassan askari
        "638855b85fce844d606bb422": "k3n3krao33gy9dwubgwr1udrbe",                     # Tahir Ali
        "712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de": "kf4nkziu7t8w9de1pdog9cemfo", # Ahsan Amin
    }
    for jira_id, expected_mm_id in expected_mappings.items():
        resolved_mm_id, method = svc.resolve_jira_to_mattermost(jira_id)
        assert resolved_mm_id == expected_mm_id
        assert method == "EXPLICIT_MAPPING"
