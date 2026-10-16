"""Comprehensive test suite for Event 2: Jira Comment Direction -> Mattermost Notification.

Specification and Acceptance Criteria:
1. Detect whether the Jira comment begins with a valid structured Jira user mention.
2. If no valid leading user mention -> do not trigger Event 2.
3. Identify the current Jira issue assignee.
4. The assignee is the sole Mattermost recipient.
5. Resolve assignee through UserMappingService.
6. Send the notification only to that Mattermost user.
7. Later mentions are treated as CC/context and never trigger or receive notifications.
8. First tagged user is not necessarily the recipient.
9. No broadcast, no guessing identities.
10. Fail-closed on missing, inactive, unmapped, or ambiguous assignee.
11. Deterministic idempotency based on Jira comment ID.
12. Direct Jira comment link in message.
"""

import hashlib
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.config.settings import Settings, settings
from app.connectors.mattermost.client import MattermostClient
from app.connectors.mattermost.connector import MattermostConnector
from app.core.actions.engine import ActionEngine
from app.core.events.types import TaskCommentAdded
from app.core.models.enums import ActionStatus, ActionType, EventScope
from app.database.connection import DatabaseManager
from app.database.repositories import (
    Event2NotificationRepository,
    UserMappingRepository,
    UserRepository,
)
from app.services.jira_comment_notification_service import (
    JiraCommentNotificationService,
)


@pytest.fixture
def event2_service(temp_db):
    """Instantiate JiraCommentNotificationService against isolated test database."""
    return JiraCommentNotificationService(manager=temp_db)


@pytest.fixture
def setup_users(temp_db):
    """Pre-seed test users in UserMappingRepository."""
    mapping_repo = UserMappingRepository(temp_db)
    user_repo = UserRepository(temp_db)

    # Mubashir: jira_user_id='jira-mubashir', mm_id='mm-mubashir'
    mapping_repo.upsert_mapping(
        jira_user_id="jira-mubashir",
        mattermost_user_id="mm-mubashir",
        display_name="Mubashir Butt",
        active=True
    )
    # Ahsan: jira_user_id='jira-ahsan', mm_id='mm-ahsan'
    mapping_repo.upsert_mapping(
        jira_user_id="jira-ahsan",
        mattermost_user_id="mm-ahsan",
        display_name="Ahsan Iftikhar",
        active=True
    )
    # Talha: jira_user_id='jira-talha', mm_id='mm-talha'
    mapping_repo.upsert_mapping(
        jira_user_id="jira-talha",
        mattermost_user_id="mm-talha",
        display_name="Talha Bukhari",
        active=True
    )
    # Muneeb: jira_user_id='jira-muneeb', mm_id='mm-muneeb'
    mapping_repo.upsert_mapping(
        jira_user_id="jira-muneeb",
        mattermost_user_id="mm-muneeb",
        display_name="Muneeb Jalal",
        active=True
    )
    # Daniyal: jira_user_id='jira-daniyal', mm_id='mm-daniyal'
    mapping_repo.upsert_mapping(
        jira_user_id="jira-daniyal",
        mattermost_user_id="mm-daniyal",
        display_name="Daniyal Raza",
        active=True
    )

    return mapping_repo


# ==============================================================================
# SECTION 1: TRIGGER & LEADING MENTION DETECTION TESTS
# ==============================================================================

def test_tc1_valid_leading_user_tag_triggers(event2_service):
    """TC1: Valid leading user tag -> Event 2 triggers."""
    comment = "@Mubashir Please investigate this issue."
    res = event2_service.extract_leading_mention(comment)
    assert res is not None
    assert res["display_name"] == "Mubashir"


def test_tc2_leading_whitespace_before_tag_triggers(event2_service):
    """TC2: Leading whitespace before tag -> Event 2 still triggers."""
    comment = "   \n\t  @Mubashir Please investigate this."
    res = event2_service.extract_leading_mention(comment)
    assert res is not None
    assert res["display_name"] == "Mubashir"


def test_tc3_no_leading_tag_no_notification(event2_service):
    """TC3: No leading tag -> no notification."""
    comment = "Please investigate this issue."
    res = event2_service.extract_leading_mention(comment)
    assert res is None


def test_tc4_tag_appearing_later_in_comment_no_notification(event2_service):
    """TC4: Tag appearing later in comment -> no notification."""
    comment = "Please investigate this issue. @Mubashir"
    res = event2_service.extract_leading_mention(comment)
    assert res is None

    comment_fyi = "FYI @Mubashir please check"
    res_fyi = event2_service.extract_leading_mention(comment_fyi)
    assert res_fyi is None


def test_tc5_structured_jira_adf_mention_recognized(event2_service):
    """TC5: Structured Jira ADF mention is correctly recognized as leading mention."""
    adf_doc = {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {
                        "type": "mention",
                        "attrs": {
                            "id": "712020:mubashir-acc",
                            "text": "@Mubashir Butt",
                            "userType": "DEFAULT"
                        }
                    },
                    {
                        "type": "text",
                        "text": " Please investigate this issue."
                    }
                ]
            }
        ]
    }
    res = event2_service.extract_leading_mention(adf_doc)
    assert res is not None
    assert res["account_id"] == "712020:mubashir-acc"
    assert res["display_name"] == "Mubashir Butt"


def test_tc5b_structured_adf_with_leading_whitespace_text_node(event2_service):
    """ADF with leading empty or whitespace-only text node before mention still extracts mention."""
    adf_doc = {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {
                        "type": "text",
                        "text": "   "
                    },
                    {
                        "type": "mention",
                        "attrs": {
                            "id": "712020:mubashir-acc",
                            "text": "@Mubashir Butt"
                        }
                    },
                    {
                        "type": "text",
                        "text": " please review."
                    }
                ]
            }
        ]
    }
    res = event2_service.extract_leading_mention(adf_doc)
    assert res is not None
    assert res["account_id"] == "712020:mubashir-acc"


def test_tc5c_structured_adf_with_text_before_mention_rejected(event2_service):
    """ADF with non-whitespace text before mention is rejected as leading mention."""
    adf_doc = {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {
                        "type": "text",
                        "text": "Hey "
                    },
                    {
                        "type": "mention",
                        "attrs": {
                            "id": "712020:mubashir-acc",
                            "text": "@Mubashir Butt"
                        }
                    }
                ]
            }
        ]
    }
    res = event2_service.extract_leading_mention(adf_doc)
    assert res is None


def test_section3_case_a_valid_leading_mention(event2_service):
    """Section 3 Case A: @Mubashir Please investigate this issue."""
    comment = "@Mubashir Please investigate this issue."
    res = event2_service.extract_leading_mention(comment)
    assert res is not None
    assert res["display_name"] == "Mubashir"


def test_section3_case_b_multiple_mentions(event2_service):
    """Section 3 Case B: @Mubashir Please investigate this issue. @Ahsan @Talha"""
    comment = "@Mubashir Please investigate this issue. @Ahsan @Talha"
    res = event2_service.extract_leading_mention(comment)
    assert res is not None
    assert res["display_name"] == "Mubashir"


def test_section3_case_c_whitespace_before_mention(event2_service):
    """Section 3 Case C: Multiple newlines/whitespace before mention."""
    comment = "\n\n@Mubashir Please investigate this issue."
    res = event2_service.extract_leading_mention(comment)
    assert res is not None
    assert res["display_name"] == "Mubashir"


def test_section3_case_d_text_before_mention(event2_service):
    """Section 3 Case D: Please investigate this issue @Mubashir"""
    comment = "Please investigate this issue @Mubashir"
    res = event2_service.extract_leading_mention(comment)
    assert res is None


def test_section3_case_e_mention_later_in_comment(event2_service):
    """Section 3 Case E: FYI @Mubashir please investigate this."""
    comment = "FYI @Mubashir please investigate this."
    res = event2_service.extract_leading_mention(comment)
    assert res is None


def test_section3_case_f_multiple_leading_mentions(event2_service):
    """Section 3 Case F: @Mubashir @Ahsan Please investigate this."""
    comment = "@Mubashir @Ahsan Please investigate this."
    res = event2_service.extract_leading_mention(comment)
    assert res is not None
    assert res["display_name"] == "Mubashir"


# ==============================================================================
# SECTION 2: RECIPIENT RESOLUTION & ASSIGNEE ROUTING TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_tc7_first_tag_equals_assignee(event2_service, setup_users):
    """TC7 (Case A): First tag = assignee -> notify assignee."""
    event = TaskCommentAdded(
        source="jira",
        task_key="PROJ-101",
        comment_id="comm-101",
        comment_body="@Mubashir Please investigate this issue.",
        author_id="jira-author",
        author_name="Project Manager"
    )
    assignee = {
        "accountId": "jira-mubashir",
        "displayName": "Mubashir Butt",
        "active": True
    }

    report = await event2_service.evaluate_comment_event(
        event=event,
        current_assignee=assignee,
        dry_run=True
    )

    assert report["trigger"] is True
    assert report["decision"] == "SEND"
    assert report["leading_tagged_user"] == "Mubashir"
    assert report["assignee"] == "Mubashir Butt"
    assert report["selected_mattermost_recipient"] == "Mubashir Butt"
    assert report["mattermost_user_id"] == "mm-mubashir"


@pytest.mark.asyncio
async def test_tc8_first_tag_differs_from_assignee_mandatory_example(event2_service, setup_users):
    """TC8 (Mandatory Case B): First tag differs from assignee.
    
    Comment:
    @Mubashir Please investigate this issue. @Ahsan @Talha

    Assignee:
    Ahsan

    Expected result:
    Trigger: YES
    Leading tag: Mubashir
    Assignee: Ahsan
    Mattermost recipient: Ahsan
    Mubashir notified: NO
    Talha notified: NO
    """
    event = TaskCommentAdded(
        source="jira",
        task_key="PROJ-202",
        comment_id="comm-202",
        comment_body="@Mubashir Please investigate this issue. @Ahsan @Talha",
        author_id="jira-pm",
        author_name="Lead PM",
        mentioned_account_ids=["jira-mubashir", "jira-ahsan", "jira-talha"],
        mentioned_display_names=["Mubashir", "Ahsan", "Talha"]
    )
    assignee = {
        "accountId": "jira-ahsan",
        "displayName": "Ahsan Iftikhar",
        "active": True
    }

    report = await event2_service.evaluate_comment_event(
        event=event,
        current_assignee=assignee,
        dry_run=True
    )

    assert report["trigger"] is True
    assert report["decision"] == "SEND"
    assert report["leading_tagged_user"] == "Mubashir"
    assert report["assignee"] == "Ahsan Iftikhar"
    assert report["selected_mattermost_recipient"] == "Ahsan Iftikhar"
    assert report["mattermost_user_id"] == "mm-ahsan"
    # Verify neither Mubashir nor Talha is selected
    assert report["mattermost_user_id"] != "mm-mubashir"
    assert report["mattermost_user_id"] != "mm-talha"
    assert report["prepared_message"] == "https://objectsws.atlassian.net/browse/PROJ-202?focusedCommentId=comm-202#comment-comm-202"


@pytest.mark.asyncio
async def test_tc9_multiple_later_cc_mentions(event2_service, setup_users):
    """TC9 (Case D): Multiple CC mentions -> notify assignee only."""
    event = TaskCommentAdded(
        source="jira",
        task_key="PROJ-303",
        comment_id="comm-303",
        comment_body="@Mubashir Please investigate this issue. @Ahsan @Talha @Daniyal",
        author_id="jira-pm",
        author_name="Lead PM",
        mentioned_display_names=["Mubashir", "Ahsan", "Talha", "Daniyal"]
    )
    assignee = {
        "accountId": "jira-muneeb",
        "displayName": "Muneeb Jalal",
        "active": True
    }

    report = await event2_service.evaluate_comment_event(
        event=event,
        current_assignee=assignee,
        dry_run=True
    )

    assert report["trigger"] is True
    assert report["decision"] == "SEND"
    assert report["selected_mattermost_recipient"] == "Muneeb Jalal"
    assert report["mattermost_user_id"] == "mm-muneeb"


@pytest.mark.asyncio
async def test_section5_test1_first_tag_differs_from_assignee(event2_service, setup_users):
    """Section 5 Test 1:
    Comment: @Mubashir Please investigate this issue. @Ahsan @Talha
    Assignee: Ahsan
    Expected recipient: Ahsan
    Expected non-recipients: Mubashir, Talha
    """
    event = TaskCommentAdded(
        source="jira",
        task_key="SEC5-01",
        comment_id="comm-sec5-1",
        comment_body="@Mubashir Please investigate this issue. @Ahsan @Talha",
        author_id="jira-author",
        author_name="PM Author"
    )
    assignee = {"accountId": "jira-ahsan", "displayName": "Ahsan Iftikhar", "active": True}
    report = await event2_service.evaluate_comment_event(event=event, current_assignee=assignee, dry_run=True)
    assert report["trigger"] is True
    assert report["selected_mattermost_recipient"] == "Ahsan Iftikhar"
    assert report["mattermost_user_id"] == "mm-ahsan"
    assert report["mattermost_user_id"] != "mm-mubashir"
    assert report["mattermost_user_id"] != "mm-talha"


@pytest.mark.asyncio
async def test_section5_test2_first_tag_ahsan_assignee_mubashir(event2_service, setup_users):
    """Section 5 Test 2:
    Comment: @Ahsan Please investigate this issue.
    Assignee: Mubashir
    Expected recipient: Mubashir (Not Ahsan)
    """
    event = TaskCommentAdded(
        source="jira",
        task_key="SEC5-02",
        comment_id="comm-sec5-2",
        comment_body="@Ahsan Please investigate this issue.",
        author_id="jira-author",
        author_name="PM Author"
    )
    assignee = {"accountId": "jira-mubashir", "displayName": "Mubashir Butt", "active": True}
    report = await event2_service.evaluate_comment_event(event=event, current_assignee=assignee, dry_run=True)
    assert report["trigger"] is True
    assert report["selected_mattermost_recipient"] == "Mubashir Butt"
    assert report["mattermost_user_id"] == "mm-mubashir"
    assert report["mattermost_user_id"] != "mm-ahsan"


@pytest.mark.asyncio
async def test_section5_test3_first_tag_mubashir_assignee_mubashir(event2_service, setup_users):
    """Section 5 Test 3:
    Comment: @Mubashir Please investigate this issue.
    Assignee: Mubashir
    Expected recipient: Mubashir
    """
    event = TaskCommentAdded(
        source="jira",
        task_key="SEC5-03",
        comment_id="comm-sec5-3",
        comment_body="@Mubashir Please investigate this issue.",
        author_id="jira-author",
        author_name="PM Author"
    )
    assignee = {"accountId": "jira-mubashir", "displayName": "Mubashir Butt", "active": True}
    report = await event2_service.evaluate_comment_event(event=event, current_assignee=assignee, dry_run=True)
    assert report["trigger"] is True
    assert report["selected_mattermost_recipient"] == "Mubashir Butt"
    assert report["mattermost_user_id"] == "mm-mubashir"


@pytest.mark.asyncio
async def test_section5_test4_first_tag_mubashir_assignee_talha(event2_service, setup_users):
    """Section 5 Test 4:
    Comment: @Mubashir Please investigate this issue. @Ahsan @Talha
    Assignee: Talha
    Expected recipient: Talha
    """
    event = TaskCommentAdded(
        source="jira",
        task_key="SEC5-04",
        comment_id="comm-sec5-4",
        comment_body="@Mubashir Please investigate this issue. @Ahsan @Talha",
        author_id="jira-author",
        author_name="PM Author"
    )
    assignee = {"accountId": "jira-talha", "displayName": "Talha Bukhari", "active": True}
    report = await event2_service.evaluate_comment_event(event=event, current_assignee=assignee, dry_run=True)
    assert report["trigger"] is True
    assert report["selected_mattermost_recipient"] == "Talha Bukhari"
    assert report["mattermost_user_id"] == "mm-talha"
    assert report["mattermost_user_id"] != "mm-mubashir"
    assert report["mattermost_user_id"] != "mm-ahsan"


@pytest.mark.asyncio
async def test_tc11_no_assignee_no_notification(event2_service, setup_users):
    """TC11 (Case F): No assignee -> no notification."""
    event = TaskCommentAdded(
        source="jira",
        task_key="PROJ-505",
        comment_id="comm-505",
        comment_body="@Mubashir Please investigate this issue.",
        author_id="jira-pm",
        author_name="Lead PM"
    )

    report = await event2_service.evaluate_comment_event(
        event=event,
        current_assignee=None,  # Unassigned
        dry_run=True
    )

    assert report["trigger"] is True
    assert report["decision"] == "NO_NOTIFICATION"
    assert report["reason"] == "ASSIGNEE_UNRESOLVED_OR_UNMAPPED"
    assert report["selected_mattermost_recipient"] is None


@pytest.mark.asyncio
async def test_tc12_assignee_has_no_mattermost_mapping_fails_closed(event2_service, setup_users):
    """TC12 (Case G): Assignee has no Mattermost mapping -> fail closed (no notification)."""
    event = TaskCommentAdded(
        source="jira",
        task_key="PROJ-606",
        comment_id="comm-606",
        comment_body="@Mubashir Please investigate this issue.",
        author_id="jira-pm",
        author_name="Lead PM"
    )
    assignee = {
        "accountId": "jira-unknown-dev",
        "displayName": "Unknown Developer",
        "active": True
    }

    report = await event2_service.evaluate_comment_event(
        event=event,
        current_assignee=assignee,
        dry_run=True
    )

    assert report["trigger"] is True
    assert report["decision"] == "NO_NOTIFICATION"
    assert report["reason"] == "ASSIGNEE_UNRESOLVED_OR_UNMAPPED"
    assert report["selected_mattermost_recipient"] is None


@pytest.mark.asyncio
async def test_tc13_assignee_inactive_fails_closed(event2_service, setup_users):
    """TC13: Assignee is marked inactive in Jira -> fail closed."""
    event = TaskCommentAdded(
        source="jira",
        task_key="PROJ-707",
        comment_id="comm-707",
        comment_body="@Mubashir Please investigate this issue.",
        author_id="jira-pm",
        author_name="Lead PM"
    )
    assignee = {
        "accountId": "jira-ahsan",
        "displayName": "Ahsan Iftikhar",
        "active": False  # Inactive user
    }

    report = await event2_service.evaluate_comment_event(
        event=event,
        current_assignee=assignee,
        dry_run=True
    )

    assert report["trigger"] is True
    assert report["decision"] == "NO_NOTIFICATION"
    assert report["reason"] == "ASSIGNEE_UNRESOLVED_OR_UNMAPPED"


# ==============================================================================
# SECTION 3: SAFETY & MATTERMOST AUTHORIZATION TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_tc14_generic_mattermost_action_remains_protected(temp_db):
    """TC14: Generic unscoped Mattermost action remains protected by self-only guard."""
    mapping_repo = UserMappingRepository(temp_db)
    mapping_repo.upsert_mapping(
        jira_user_id="jira-random",
        mattermost_user_id="mm-random-user",
        display_name="Random User"
    )

    engine = ActionEngine(manager=temp_db)
    mock_client = MagicMock(spec=MattermostClient)
    connector = MattermostConnector(client=mock_client)
    engine.register_connector(connector)

    # Action with no event_scope
    from app.core.actions.types import create_send_message_action
    action = create_send_message_action(
        target_system="mattermost",
        target_id="jira-random",
        text="Generic message",
        recipient_name="Random User"
    )

    res = await engine.execute(action)
    assert res.success is False
    assert res.status == ActionStatus.REJECTED
    assert "blocked" in res.error_message


@pytest.mark.asyncio
async def test_tc15_event2_scoped_action_authorizes_verified_assignee(temp_db):
    """TC15: ActionEngine authorizes JIRA_COMMENT_NOTIFICATION event scope for mapped recipient."""
    mapping_repo = UserMappingRepository(temp_db)
    mapping_repo.upsert_mapping(
        jira_user_id="jira-ahsan",
        mattermost_user_id="mm-ahsan",
        display_name="Ahsan Iftikhar",
        active=True
    )

    engine = ActionEngine(manager=temp_db)
    mock_client = MagicMock(spec=MattermostClient)
    connector = MattermostConnector(client=mock_client)
    engine.register_connector(connector)

    from app.core.actions.types import create_send_message_action
    action = create_send_message_action(
        target_system="mattermost",
        target_id="jira-ahsan",
        text="https://objectsws.atlassian.net/browse/OPS-100?focusedCommentId=comm-123#comment-comm-123",
        recipient_name="Ahsan Iftikhar"
    )
    action.dry_run = True
    action.parameters["event_scope"] = EventScope.JIRA_COMMENT_NOTIFICATION.value
    action.parameters["jira_user_id"] = "jira-ahsan"
    action.parameters["recipient_id"] = "mm-ahsan"

    res = await engine.execute(action)
    assert res.success is True
    assert res.status == ActionStatus.DRY_RUN_SIMULATED


@pytest.mark.asyncio
async def test_tc16_channel_posting_remains_blocked():
    """TC16: Mattermost channel messaging remains strictly blocked under Event 2."""
    mock_client = MagicMock(spec=MattermostClient)
    connector = MattermostConnector(client=mock_client)

    from app.core.actions.base import BaseAction
    action = BaseAction(
        action_type=ActionType.SEND_MESSAGE,
        target_system="mattermost",
        target_id="town-square",
        parameters={
            "channel_id": "town-square",
            "text": "Broadcast message",
            "event_scope": EventScope.JIRA_COMMENT_NOTIFICATION.value
        }
    )

    with pytest.raises(PermissionError) as exc_info:
        await connector.execute_action(action)
    assert "channel messaging is blocked" in str(exc_info.value)


# ==============================================================================
# SECTION 4: IDEMPOTENCY & DUPLICATE PREVENTION TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_tc19_same_comment_processed_twice_produces_one_notification(event2_service, setup_users):
    """TC19: Same comment processed twice -> exactly one notification."""
    event = TaskCommentAdded(
        source="jira",
        task_key="PROJ-808",
        comment_id="comm-808",
        comment_body="@Mubashir Please investigate this issue.",
        author_id="jira-pm",
        author_name="Lead PM"
    )
    assignee = {
        "accountId": "jira-ahsan",
        "displayName": "Ahsan Iftikhar",
        "active": True
    }

    # First run
    res1 = await event2_service.process_comment_notification(
        event=event,
        current_assignee=assignee,
        dry_run=True
    )
    assert res1["decision"] == "SEND"

    # Second run with identical comment
    res2 = await event2_service.process_comment_notification(
        event=event,
        current_assignee=assignee,
        dry_run=True
    )
    assert res2["decision"] == "ALREADY_PROCESSED"
    assert "already processed" in res2["reason"].lower()


@pytest.mark.asyncio
async def test_tc20_overlapping_polling_windows_no_duplicate(event2_service, setup_users):
    """TC20: Same comment returned in overlapping polling windows -> deduplicated."""
    event1 = TaskCommentAdded(
        source="jira",
        task_key="PROJ-909",
        comment_id="comm-909",
        comment_body="@Ahsan Please check this.",
        author_id="jira-pm"
    )
    assignee = {"accountId": "jira-ahsan", "displayName": "Ahsan Iftikhar", "active": True}

    res1 = await event2_service.process_comment_notification(event=event1, current_assignee=assignee, dry_run=True)
    assert res1["decision"] == "SEND"

    # Simulated overlapping poll returning same event with same comment_id
    event2 = TaskCommentAdded(
        source="jira",
        task_key="PROJ-909",
        comment_id="comm-909",
        comment_body="@Ahsan Please check this.",
        author_id="jira-pm"
    )
    res2 = await event2_service.process_comment_notification(event=event2, current_assignee=assignee, dry_run=True)
    assert res2["decision"] == "ALREADY_PROCESSED"


# ==============================================================================
# SECTION 5: URL GENERATION TESTS
# ==============================================================================

def test_tc23_correct_direct_jira_comment_url_generated(event2_service):
    """TC23: Correct direct Jira comment URL generated."""
    url = event2_service.generate_comment_url(task_key="ZBS-25", comment_id="653371")
    assert url == "https://objectsws.atlassian.net/browse/ZBS-25?focusedCommentId=653371#comment-653371"


def test_tc25_missing_comment_id_fails_closed(event2_service):
    """TC25: Missing comment ID fails closed."""
    with pytest.raises(ValueError):
        event2_service.generate_comment_url(task_key="ZBS-25", comment_id="")
    with pytest.raises(ValueError):
        event2_service.generate_comment_url(task_key="", comment_id="12345")


# ==============================================================================
# SECTION 6: MANDATORY EXAMPLES VERIFICATION (SECTION 18)
# ==============================================================================

@pytest.mark.asyncio
async def test_mandatory_example_1(event2_service, setup_users):
    """Verify Section 18 Mandatory Example 1:
    Comment:
    @Mubashir Please investigate this issue. @Ahsan @Talha

    Assignee:
    Ahsan
    """
    event = TaskCommentAdded(
        source="jira",
        task_key="OPS-100",
        comment_id="comm-m1",
        comment_body="@Mubashir Please investigate this issue. @Ahsan @Talha",
        author_id="jira-author",
        author_name="PM Author"
    )
    assignee = {"accountId": "jira-ahsan", "displayName": "Ahsan Iftikhar", "active": True}

    report = await event2_service.evaluate_comment_event(event=event, current_assignee=assignee, dry_run=True)

    assert report["trigger"] is True
    assert report["leading_tagged_user"] == "Mubashir"
    assert report["assignee"] == "Ahsan Iftikhar"
    assert report["selected_mattermost_recipient"] == "Ahsan Iftikhar"
    assert report["mattermost_user_id"] == "mm-ahsan"
    # Ensure others receive nothing
    assert report["mattermost_user_id"] != "mm-mubashir"
    assert report["mattermost_user_id"] != "mm-talha"
    assert report["prepared_message"] == "https://objectsws.atlassian.net/browse/OPS-100?focusedCommentId=comm-m1#comment-comm-m1"


@pytest.mark.asyncio
async def test_mandatory_example_2(event2_service, setup_users):
    """Verify Section 18 Mandatory Example 2:
    Comment:
    @Mubashir Please investigate this issue.

    Assignee:
    Mubashir
    """
    event = TaskCommentAdded(
        source="jira",
        task_key="OPS-200",
        comment_id="comm-m2",
        comment_body="@Mubashir Please investigate this issue.",
        author_id="jira-author"
    )
    assignee = {"accountId": "jira-mubashir", "displayName": "Mubashir Butt", "active": True}

    report = await event2_service.evaluate_comment_event(event=event, current_assignee=assignee, dry_run=True)

    assert report["trigger"] is True
    assert report["selected_mattermost_recipient"] == "Mubashir Butt"
    assert report["mattermost_user_id"] == "mm-mubashir"


@pytest.mark.asyncio
async def test_mandatory_example_3(event2_service, setup_users):
    """Verify Section 18 Mandatory Example 3:
    Comment:
    Please investigate this issue. @Mubashir

    Assignee:
    Mubashir
    """
    event = TaskCommentAdded(
        source="jira",
        task_key="OPS-300",
        comment_id="comm-m3",
        comment_body="Please investigate this issue. @Mubashir",
        author_id="jira-author"
    )
    assignee = {"accountId": "jira-mubashir", "displayName": "Mubashir Butt", "active": True}

    report = await event2_service.evaluate_comment_event(event=event, current_assignee=assignee, dry_run=True)

    assert report["trigger"] is False
    assert report["decision"] == "NO_NOTIFICATION"
    assert report["selected_mattermost_recipient"] is None
