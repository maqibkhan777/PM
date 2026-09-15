"""Comprehensive test suite for Discord Slash Commands (/pm).

Tests:
1. Discord user authorization (authorized vs unauthorized user)
2. Command arguments and interaction options parsing
3. /pm help
4. Read-only /pm status (directly calls Jira read, no Action creation)
5. /pm transition <ticket> <status>
6. /pm assign <ticket> <user> (exact account ID, 'me', exact display name, no fuzzy guessing)
7. /pm comment <ticket> <comment>
8. /pm create <project> <summary> (request-level idempotency)
9. /pm update <ticket> <field> <value> (field allowlist enforcement)
10. /pm notify <user> <message>
11. /pm message <user> <message>
12. Dry run responses (DRY_RUN=true)
13. ActionEngine invocation and safety checks
14. Safe error responses (no stack traces or leaked secrets)
15. Interaction payload handling (PING -> PONG, command payloads)
"""

from typing import Any, Dict, List, Optional
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.connectors.discord.slash_commands import (
    DiscordSlashCommandHandler,
    PM_HELP_TEXT,
    ALLOWED_UPDATE_FIELDS,
    INTERACTION_RESPONSE_TYPE_PONG,
    INTERACTION_RESPONSE_TYPE_CHANNEL_MESSAGE,
)
from app.connectors.discord.bot_connector import DiscordBotConnector
from app.core.actions.engine import ActionEngine
from app.core.actions.base import ActionResult
from app.core.models.enums import ActionStatus, ActionType
from app.config.settings import settings
from app.database.repositories import UserRepository, ActionRepository, AuditRepository


class MockJiraClientForDiscord:
    """Mock Jira client for testing Discord slash commands."""

    def __init__(self):
        self.transitions = [
            {"id": "11", "name": "In Progress", "to": {"name": "In Progress"}},
            {"id": "21", "name": "Done", "to": {"name": "Done"}},
        ]
        self.issues: Dict[str, Dict[str, Any]] = {
            "WSSS-326": {
                "key": "WSSS-326",
                "fields": {
                    "summary": "Implement checkout optimization",
                    "status": {"name": "In Progress"},
                    "assignee": {"displayName": "Aqib Khan", "accountId": "557058:ba931089-a292-4f11"},
                    "priority": {"name": "High"},
                    "duedate": "2026-09-15",
                },
            }
        }
        self.users = [
            {"accountId": "557058:ba931089-a292-4f11", "displayName": "Aqib Khan", "emailAddress": "aqib@example.com", "active": True},
            {"accountId": "712020:c12d2371-c035-423a-bc44-30799981aa7a", "displayName": "Abdul Subhan", "emailAddress": "abdul@example.com", "active": True},
            {"accountId": "712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de", "displayName": "Ahsan Amin", "emailAddress": "ahsan@example.com", "active": True},
            {"accountId": "557058:charlie-1234", "displayName": "Charlie Brown", "emailAddress": "charlie@example.com", "active": True},
        ]
        self.transition_calls = []
        self.assign_calls = []
        self.comment_calls = []
        self.create_issue_calls = []
        self.update_field_calls = []

    async def get_issue(self, issue_key: str) -> Dict[str, Any]:
        if issue_key in self.issues:
            return self.issues[issue_key]
        raise Exception(f"Issue {issue_key} not found (404)")

    async def get_transitions(self, issue_key: str) -> List[Dict[str, Any]]:
        return list(self.transitions)

    async def transition_issue(self, issue_key: str, transition_id: str) -> Dict[str, Any]:
        self.transition_calls.append((issue_key, transition_id))
        return {"status": "success", "transition_id": transition_id, "transitioned": True}

    async def assign_issue(self, issue_key: str, account_id: str) -> Dict[str, Any]:
        self.assign_calls.append((issue_key, account_id))
        return {"assigned": True, "account_id": account_id}

    async def add_comment(self, issue_key: str, body: str) -> Dict[str, Any]:
        self.comment_calls.append((issue_key, body))
        return {"id": "comment-101", "body": body}

    async def create_issue(
        self,
        project_key: str,
        summary: str,
        issue_type: str = "Task",
        description: Optional[str] = None,
        assignee: Optional[str] = None,
        priority: Optional[str] = None,
        labels: Optional[List[str]] = None,
        **kwargs: Any
    ) -> Dict[str, Any]:
        self.create_issue_calls.append((project_key, summary, issue_type, description, assignee, priority, labels))
        return {"id": "10100", "key": f"{project_key}-100", "issue_key": f"{project_key}-100"}

    async def update_fields(self, issue_key: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        self.update_field_calls.append((issue_key, fields))
        return {"updated": True, "fields": fields}

    async def get_users(self, query: str) -> List[Dict[str, Any]]:
        q = query.lower()
        return [u for u in self.users if q in u["displayName"].lower() or q in u["emailAddress"].lower() or q == u["accountId"]]


class MockDiscordWebhookConnector:
    def __init__(self):
        self.dispatched = []

    async def execute_action(self, action: Any) -> Dict[str, Any]:
        self.dispatched.append(action)
        return {"status": "success"}


from app.connectors.jira import JiraConnector
from app.connectors.discord import DiscordWebhookConnector


@pytest.fixture
def slash_setup(temp_db):
    """Setup ActionEngine and DiscordSlashCommandHandler for isolated testing."""
    engine = ActionEngine(manager=temp_db)
    mock_jira_client = MockJiraClientForDiscord()
    jira_conn = JiraConnector(client=mock_jira_client)

    engine.register_connector(jira_conn)

    class WrapperDiscordConn(DiscordWebhookConnector):
        async def execute_action(self, action: Any) -> Dict[str, Any]:
            return {"status": "success", "http_code": 204}

    engine.register_connector(WrapperDiscordConn(webhook_url="https://discord.com/mock"))

    user_repo = UserRepository(temp_db)
    for u in mock_jira_client.users:
        user_repo.upsert(
            external_system="jira",
            external_user_id=u["accountId"],
            display_name=u["displayName"],
            email=u["emailAddress"]
        )

    handler = DiscordSlashCommandHandler(user_repo=user_repo, action_engine=engine)
    bot = DiscordBotConnector(bot_token="test_token_123", slash_handler=handler)

    # Setup allowed user
    settings.DISCORD_PM_ALLOWED_USERS = "123456789,987654321"
    settings.DRY_RUN = False

    return handler, bot, mock_jira_client, engine, temp_db


# ==============================================================================
# 1. AUTHORIZATION TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_discord_authorization_allowed_and_unauthorized_users(slash_setup):
    """Test that unauthorized Discord users are blocked while authorized users are permitted."""
    handler, _, _, _, _ = slash_setup

    # 1. Unauthorized user
    res_unauth = await handler.execute_subcommand(
        subcommand="help",
        options={},
        discord_user_id="999999999"  # Not in allowlist
    )
    assert res_unauth == "❌ You are not authorized to use PM commands."

    # 2. Authorized user
    res_auth = await handler.execute_subcommand(
        subcommand="help",
        options={},
        discord_user_id="123456789"  # In allowlist
    )
    assert "PM Commands" in res_auth


# ==============================================================================
# 2. /PM HELP TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_pm_help_command(slash_setup):
    """Test /pm help returns concise beginner-friendly command list."""
    handler, _, _, _, _ = slash_setup

    res = await handler.execute_subcommand(
        subcommand="help",
        options={},
        discord_user_id="123456789"
    )
    assert "/pm status <ticket>" in res
    assert "/pm transition <ticket> <status>" in res
    assert "/pm assign <ticket> <user>" in res
    assert "/pm comment <ticket> <comment>" in res
    assert "/pm create <project> <summary>" in res
    assert "/pm update <ticket> <field> <value>" in res
    assert "/pm notify <user> <message>" in res
    assert "/pm message <user> <message>" in res


# ==============================================================================
# 3. /PM STATUS TESTS (READ-ONLY)
# ==============================================================================

@pytest.mark.asyncio
async def test_pm_status_read_only_success_and_not_found(slash_setup):
    """Test /pm status: READ-ONLY, queries Jira issue, creates NO Action record."""
    handler, _, mock_jira_client, _, temp_db = slash_setup

    # 1. Successful status query
    res = await handler.execute_subcommand(
        subcommand="status",
        options={"ticket": "WSSS-326"},
        discord_user_id="123456789"
    )
    assert "📋 **WSSS-326**: Implement checkout optimization" in res
    assert "**Status:** In Progress" in res
    assert "**Assignee:** Aqib Khan" in res
    assert "**Priority:** High" in res
    assert "**Due:** 2026-09-15" in res

    # Verify NO Action was inserted in SQLite actions table
    action_repo = ActionRepository(temp_db)
    actions = action_repo.list_actions()
    assert len(actions) == 0, "Read-only /pm status must NEVER create Action objects"

    # 2. Missing ticket
    res_missing = await handler.execute_subcommand(
        subcommand="status",
        options={"ticket": ""},
        discord_user_id="123456789"
    )
    assert "❌ Ticket key is required" in res_missing

    # 3. Non-existent ticket (404)
    res_404 = await handler.execute_subcommand(
        subcommand="status",
        options={"ticket": "WSSS-999"},
        discord_user_id="123456789"
    )
    assert "❌ Ticket WSSS-999 not found." in res_404


# ==============================================================================
# 4. /PM TRANSITION TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_pm_transition_command_and_dry_run(slash_setup):
    """Test /pm transition execution and dry run."""
    handler, _, mock_jira_client, _, temp_db = slash_setup

    # 1. Real execution
    res = await handler.execute_subcommand(
        subcommand="transition",
        options={"ticket": "WSSS-326", "status": "Done"},
        discord_user_id="123456789"
    )
    assert res == "✅ WSSS-326 transitioned to Done."
    assert len(mock_jira_client.transition_calls) == 1
    assert mock_jira_client.transition_calls[0] == ("WSSS-326", "21")

    # 2. Dry run mode
    settings.DRY_RUN = True
    res_dry = await handler.execute_subcommand(
        subcommand="transition",
        options={"ticket": "WSSS-326", "status": "In Progress"},
        discord_user_id="123456789"
    )
    assert "🧪 DRY RUN" in res_dry
    assert "Would transition WSSS-326 to In Progress." in res_dry
    assert len(mock_jira_client.transition_calls) == 1  # No new call

    # 3. Missing arguments
    settings.DRY_RUN = False
    res_missing = await handler.execute_subcommand(
        subcommand="transition",
        options={"ticket": "WSSS-326", "status": ""},
        discord_user_id="123456789"
    )
    assert "❌ Both `ticket` and `status` are required." in res_missing


# ==============================================================================
# 5. /PM ASSIGN TESTS & SAFE USER RESOLUTION
# ==============================================================================

@pytest.mark.asyncio
async def test_pm_assign_exact_account_id_and_unresolved_user(slash_setup):
    """Test /pm assign with exact account ID, 'me', display name, and unknown user rejection."""
    handler, _, mock_jira_client, _, _ = slash_setup

    # 1. Direct Atlassian Account ID
    res_id = await handler.execute_subcommand(
        subcommand="assign",
        options={"ticket": "WSSS-326", "user": "557058:ba931089-a292-4f11"},
        discord_user_id="123456789"
    )
    assert "✅ Assigned WSSS-326" in res_id
    assert len(mock_jira_client.assign_calls) == 1

    # 2. Unknown user that cannot be safely resolved -> no fuzzy guessing
    res_unknown = await handler.execute_subcommand(
        subcommand="assign",
        options={"ticket": "WSSS-326", "user": "non_existent_person_xyz"},
        discord_user_id="123456789"
    )
    assert res_unknown == "❌ I couldn't safely identify that Jira user."

    # 3. Dry run
    settings.DRY_RUN = True
    res_dry = await handler.execute_subcommand(
        subcommand="assign",
        options={"ticket": "WSSS-327", "user": "557058:ba931089-a292-4f11"},
        discord_user_id="123456789"
    )
    assert "🧪 DRY RUN" in res_dry
    assert "Would assign WSSS-327 to 557058:ba931089-a292-4f11." in res_dry


# ==============================================================================
# 6. /PM COMMENT TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_pm_comment_command(slash_setup):
    """Test /pm comment executes comment addition and checks dry run."""
    handler, _, mock_jira_client, _, _ = slash_setup

    res = await handler.execute_subcommand(
        subcommand="comment",
        options={"ticket": "WSSS-326", "comment": "Please verify this change."},
        discord_user_id="123456789"
    )
    assert res == "✅ Comment added to WSSS-326."
    assert len(mock_jira_client.comment_calls) == 1
    assert mock_jira_client.comment_calls[0] == ("WSSS-326", "Please verify this change.")

    # Dry run
    settings.DRY_RUN = True
    res_dry = await handler.execute_subcommand(
        subcommand="comment",
        options={"ticket": "WSSS-326", "comment": "Another comment"},
        discord_user_id="123456789"
    )
    assert "🧪 DRY RUN" in res_dry
    assert "Would add comment to WSSS-326:" in res_dry


# ==============================================================================
# 7. /PM CREATE TESTS (REQUEST-LEVEL IDEMPOTENCY)
# ==============================================================================

@pytest.mark.asyncio
async def test_pm_create_task_and_idempotency(slash_setup):
    """Test /pm create creates task and uses request-level idempotency so distinct requests can create tasks with same summary."""
    handler, _, mock_jira_client, _, _ = slash_setup
    settings.DRY_RUN = False

    # 1. First task creation
    res1 = await handler.execute_subcommand(
        subcommand="create",
        options={"project": "WSSS", "summary": "Fix login edge case"},
        discord_user_id="123456789"
    )
    assert "Created WSSS-100 successfully." in res1
    assert settings.get_jira_browse_url("WSSS-100") in res1
    assert len(mock_jira_client.create_issue_calls) == 1

    # 2. Second task creation with same summary in a new command request (has distinct action_id) -> MUST create separate task
    res2 = await handler.execute_subcommand(
        subcommand="create",
        options={"project": "WSSS", "summary": "Fix login edge case"},
        discord_user_id="123456789"
    )
    assert "Created WSSS-100 successfully." in res2
    assert len(mock_jira_client.create_issue_calls) == 2


# ==============================================================================
# 8. /PM UPDATE TESTS & FIELD ALLOWLIST
# ==============================================================================

@pytest.mark.asyncio
async def test_pm_update_field_allowlist(slash_setup):
    """Test /pm update enforces allowed fields allowlist."""
    handler, _, mock_jira_client, _, _ = slash_setup
    settings.DRY_RUN = False

    # 1. Disallowed field
    res_bad = await handler.execute_subcommand(
        subcommand="update",
        options={"ticket": "WSSS-326", "field": "security_level", "value": "top_secret"},
        discord_user_id="123456789"
    )
    assert "❌ Field 'security_level' is not permitted for update." in res_bad

    # 2. Allowed field (priority)
    res_valid = await handler.execute_subcommand(
        subcommand="update",
        options={"ticket": "WSSS-326", "field": "priority", "value": "Highest"},
        discord_user_id="123456789"
    )
    assert res_valid == "✅ Updated WSSS-326."
    assert len(mock_jira_client.update_field_calls) == 1


# ==============================================================================
# 9. /PM NOTIFY & /PM MESSAGE TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_pm_notify_and_message_commands(slash_setup):
    """Test /pm notify and /pm message dispatches."""
    handler, _, _, _, _ = slash_setup
    settings.DRY_RUN = False

    # 1. /pm notify
    res_notify = await handler.execute_subcommand(
        subcommand="notify",
        options={"user": "pm-channel", "message": "Deployment completed successfully."},
        discord_user_id="123456789"
    )
    assert "✅ Notification sent to pm-channel." in res_notify

    # 2. /pm message
    res_msg = await handler.execute_subcommand(
        subcommand="message",
        options={"user": "123456789", "message": "Please review ticket WSSS-326."},
        discord_user_id="123456789"
    )
    assert "✅ Message sent to 123456789." in res_msg


# ==============================================================================
# 10. DISCORD INTERACTION PAYLOAD HANDLING (PING, SLASH COMMANDS)
# ==============================================================================

@pytest.mark.asyncio
async def test_discord_interaction_payload_handling(slash_setup):
    """Test Discord interaction webhook/REST payload processing for PING and slash command."""
    handler, bot, _, _, _ = slash_setup

    # 1. Type 1: PING -> PONG
    ping_payload = {"type": 1}
    res_pong = await handler.handle_interaction(ping_payload)
    assert res_pong == {"type": INTERACTION_RESPONSE_TYPE_PONG}

    # 2. Type 2: APPLICATION_COMMAND (/pm status WSSS-326)
    cmd_payload = {
        "type": 2,
        "data": {
            "name": "pm",
            "options": [
                {
                    "name": "status",
                    "type": 1,
                    "options": [
                        {"name": "ticket", "value": "WSSS-326"}
                    ]
                }
            ]
        },
        "member": {
            "user": {"id": "123456789", "username": "aqib"}
        }
    }

    res_cmd = await bot.handle_interaction(cmd_payload)
    assert res_cmd["type"] == INTERACTION_RESPONSE_TYPE_CHANNEL_MESSAGE
    assert "📋 **WSSS-326**: Implement checkout optimization" in res_cmd["data"]["content"]


# ==============================================================================
# 11. SLASH COMMAND SCHEMA & REGISTRATION TESTS
# ==============================================================================

from app.connectors.discord.gateway_client import (
    DiscordGatewayClient,
    build_pm_slash_command_schema,
    OP_HELLO,
    OP_HEARTBEAT,
    OP_HEARTBEAT_ACK,
    OP_DISPATCH,
)


def test_pm_slash_command_schema_structure():
    """Verify that the registered /pm command schema includes all 9 required subcommands."""
    schema = build_pm_slash_command_schema()
    assert schema["name"] == "pm"
    options = schema["options"]
    subcommand_names = [opt["name"] for opt in options]

    required_subcommands = [
        "help",
        "status",
        "transition",
        "assign",
        "comment",
        "create",
        "update",
        "notify",
        "message",
    ]
    for sub in required_subcommands:
        assert sub in subcommand_names, f"Subcommand '{sub}' missing from schema"

    # Verify status option
    status_opt = next(opt for opt in options if opt["name"] == "status")
    assert status_opt["options"][0]["name"] == "ticket"
    assert status_opt["options"][0]["required"] is True

    # Verify transition options
    transition_opt = next(opt for opt in options if opt["name"] == "transition")
    trans_opt_names = [o["name"] for o in transition_opt["options"]]
    assert "ticket" in trans_opt_names
    assert "status" in trans_opt_names

    # Verify assign options
    assign_opt = next(opt for opt in options if opt["name"] == "assign")
    assign_opt_names = [o["name"] for o in assign_opt["options"]]
    assert "ticket" in assign_opt_names
    assert "user" in assign_opt_names


@pytest.mark.asyncio
async def test_slash_command_registration_guild_and_global():
    """Test slash command registration HTTP PUT calls for both guild-specific and global scopes."""
    gw_guild = DiscordGatewayClient(
        bot_token="test_token",
        application_id="11223344",
        guild_id="99887766",
    )

    with patch("httpx.AsyncClient.put") as mock_put:
        mock_put.return_value = AsyncMock(status_code=200, text="[]")

        # 1. Guild registration
        success = await gw_guild.register_slash_commands()
        assert success is True
        call_url = mock_put.call_args[0][0]
        assert call_url == "https://discord.com/api/v10/applications/11223344/guilds/99887766/commands"

    gw_global = DiscordGatewayClient(
        bot_token="test_token",
        application_id="11223344",
        guild_id="",
    )

    with patch("httpx.AsyncClient.put") as mock_put:
        mock_put.return_value = AsyncMock(status_code=200, text="[]")

        # 2. Global registration
        success = await gw_global.register_slash_commands()
        assert success is True
        call_url = mock_put.call_args[0][0]
        assert call_url == "https://discord.com/api/v10/applications/11223344/commands"


@pytest.mark.asyncio
async def test_slash_command_registration_missing_app_id():
    """Registration must safely return False if application ID is not configured."""
    gw = DiscordGatewayClient(bot_token="test_token", application_id="")
    with patch.object(settings, "DISCORD_APPLICATION_ID", ""):
        success = await gw.register_slash_commands()
        assert success is False


# ==============================================================================
# 12. DEFERRED INTERACTION ACKNOWLEDGMENT & RESPONSE TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_deferred_acknowledgement_and_response_patch():
    """Test deferred interaction acknowledgment (Type 5) and response patching via Discord REST API."""
    gw = DiscordGatewayClient(
        bot_token="test_token",
        application_id="11223344",
        guild_id="99887766",
    )

    # 1. Defer callback POST
    with patch("httpx.AsyncClient.post") as mock_post:
        mock_post.return_value = AsyncMock(status_code=204)
        ok = await gw.send_deferred_acknowledgement("int_id_123", "int_token_abc")
        assert ok is True
        call_url = mock_post.call_args[0][0]
        assert call_url == "https://discord.com/api/v10/interactions/int_id_123/int_token_abc/callback"
        call_json = mock_post.call_args[1]["json"]
        assert call_json == {"type": 5}

    # 2. Update deferred message PATCH
    with patch("httpx.AsyncClient.patch") as mock_patch:
        mock_patch.return_value = AsyncMock(status_code=200)
        ok = await gw.update_deferred_response("int_token_abc", "✅ Task created successfully.")
        assert ok is True
        call_url = mock_patch.call_args[0][0]
        assert call_url == "https://discord.com/api/v10/webhooks/11223344/int_token_abc/messages/@original"
        call_json = mock_patch.call_args[1]["json"]
        assert call_json == {"content": "✅ Task created successfully."}


@pytest.mark.asyncio
async def test_gateway_process_interaction_create_flow(slash_setup):
    """Test end-to-end Gateway interaction flow: defer -> execute -> patch response."""
    handler, _, mock_jira_client, _, _ = slash_setup

    gw = DiscordGatewayClient(
        bot_token="test_token",
        application_id="11223344",
        slash_handler=handler,
    )

    interaction_payload = {
        "id": "int_999",
        "token": "tok_888",
        "type": 2,
        "data": {
            "name": "pm",
            "options": [
                {
                    "name": "status",
                    "type": 1,
                    "options": [
                        {"name": "ticket", "value": "WSSS-326"}
                    ]
                }
            ]
        },
        "member": {
            "user": {"id": "123456789", "username": "aqib"}
        }
    }

    with patch.object(gw, "send_deferred_acknowledgement", new_callable=AsyncMock) as mock_defer:
        with patch.object(gw, "update_deferred_response", new_callable=AsyncMock) as mock_update:
            mock_defer.return_value = True
            mock_update.return_value = True

            await gw._process_interaction_create(interaction_payload)

            mock_defer.assert_awaited_once_with("int_999", "tok_888")
            mock_update.assert_awaited_once()
            updated_content = mock_update.call_args[0][1]
            assert "📋 **WSSS-326**: Implement checkout optimization" in updated_content


# ==============================================================================
# 13. BOT CONFIGURATION & HEALTH CHECK TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_bot_connector_health_check_states():
    """Verify health check states: DISABLED, NOT_CONFIGURED (token/app_id), DISCONNECTED, OK."""
    # 1. Disabled
    with patch.object(settings, "DISCORD_PM_COMMAND_ENABLED", False):
        bot = DiscordBotConnector(bot_token="token", application_id="app_id")
        h = await bot.health_check()
        assert h.status == "DISABLED"
        assert h.is_connected is False

    # 2. Missing Token
    with patch.object(settings, "DISCORD_PM_COMMAND_ENABLED", True):
        bot_no_token = DiscordBotConnector(bot_token="", application_id="app_id")
        with patch.object(settings, "DISCORD_BOT_TOKEN", ""):
            h = await bot_no_token.health_check()
            assert h.status == "NOT_CONFIGURED"
            assert "DISCORD_BOT_TOKEN" in h.details.get("missing", [])

    # 3. Missing Application ID
    with patch.object(settings, "DISCORD_PM_COMMAND_ENABLED", True):
        bot_no_app = DiscordBotConnector(bot_token="valid_token", application_id="")
        with patch.object(settings, "DISCORD_APPLICATION_ID", ""):
            h = await bot_no_app.health_check()
            assert h.status == "NOT_CONFIGURED"
            assert "DISCORD_APPLICATION_ID" in h.details.get("missing", [])

    # 4. Configured but disconnected
    with patch.object(settings, "DISCORD_PM_COMMAND_ENABLED", True):
        bot_disc = DiscordBotConnector(
            bot_token="valid_token",
            application_id="valid_app_id",
            gateway_client=DiscordGatewayClient(bot_token="valid_token", application_id="valid_app_id")
        )
        h = await bot_disc.health_check()
        assert h.status == "DISCONNECTED"
        assert h.is_connected is False

    # 5. Connected OK
    with patch.object(settings, "DISCORD_PM_COMMAND_ENABLED", True):
        mock_gw = DiscordGatewayClient(bot_token="valid_token", application_id="valid_app_id")
        mock_gw._is_connected = True
        mock_gw._bot_user = {"username": "PMAgentBot", "id": "11223344"}
        bot_ok = DiscordBotConnector(
            bot_token="valid_token",
            application_id="valid_app_id",
            gateway_client=mock_gw
        )
        h = await bot_ok.health_check()
        assert h.status == "OK"
        assert h.is_connected is True
        assert h.details["bot_username"] == "PMAgentBot"


def test_is_discord_bot_configured_settings_helper():
    """Verify settings.is_discord_bot_configured() requires both token and application ID."""
    with patch.object(settings, "DISCORD_BOT_TOKEN", "my_token"):
        with patch.object(settings, "DISCORD_APPLICATION_ID", "my_app_id"):
            assert settings.is_discord_bot_configured() is True

    with patch.object(settings, "DISCORD_BOT_TOKEN", ""):
        with patch.object(settings, "DISCORD_APPLICATION_ID", "my_app_id"):
            assert settings.is_discord_bot_configured() is False

    with patch.object(settings, "DISCORD_BOT_TOKEN", "my_token"):
        with patch.object(settings, "DISCORD_APPLICATION_ID", ""):
            assert settings.is_discord_bot_configured() is False


# ==============================================================================
# 14. RATE-LIMIT EFFICIENCY & RECONNECT STABILITY TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_slash_command_registration_429_retry_after():
    """Test that Discord slash command registration handles HTTP 429 by parsing retry_after and retrying."""
    gw = DiscordGatewayClient(
        bot_token="test_token",
        application_id="11223344",
        guild_id="99887766",
    )

    resp_429 = MagicMock()
    resp_429.status_code = 429
    resp_429.json.return_value = {
        "message": "You are being rate limited.",
        "retry_after": 5.8,
        "global": False
    }
    resp_429.headers = {"Retry-After": "5.8"}

    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.json.return_value = []

    with patch("httpx.AsyncClient.put", new_callable=AsyncMock) as mock_put:
        mock_put.side_effect = [resp_429, resp_200]
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            success = await gw.register_slash_commands()

            assert success is True
            assert mock_put.call_count == 2
            mock_sleep.assert_awaited_once()
            slept_duration = mock_sleep.call_args[0][0]
            # Must respect retry_after (5.8) + jitter (0.1 to 0.5)
            assert 5.8 <= slept_duration <= 6.4


@pytest.mark.asyncio
async def test_gateway_reconnect_does_not_re_register_commands():
    """Test that subsequent READY events upon reconnect do not re-register slash commands if already registered."""
    gw = DiscordGatewayClient(
        bot_token="test_token",
        application_id="11223344",
        guild_id="99887766",
    )

    with patch.object(gw, "register_slash_commands", new_callable=AsyncMock) as mock_reg:
        # First READY event: registers commands
        await gw._handle_dispatch_event("READY", {"session_id": "sess_1", "user": {"username": "Bot"}})
        assert mock_reg.call_count == 1

        # Simulate registration completion
        gw._commands_registered = True

        # Second READY event (e.g. after network blip and reconnect): MUST NOT register commands again
        await gw._handle_dispatch_event("READY", {"session_id": "sess_2", "user": {"username": "Bot"}})
        assert mock_reg.call_count == 1


@pytest.mark.asyncio
async def test_gateway_start_idempotency_no_duplicate_workers():
    """Test that calling start() multiple times does not create duplicate gateway tasks."""
    gw = DiscordGatewayClient(
        bot_token="test_token",
        application_id="11223344",
    )

    with patch.object(settings, "DISCORD_BOT_TOKEN", "test_token"):
        with patch.object(settings, "DISCORD_APPLICATION_ID", "11223344"):
            with patch.object(settings, "DISCORD_PM_COMMAND_ENABLED", True):
                with patch.object(gw, "_gateway_loop", new_callable=AsyncMock) as mock_loop:
                    await gw.start()
                    first_task = gw._gateway_task
                    assert first_task is not None

                    # Calling start() again should be a no-op
                    await gw.start()
                    assert gw._gateway_task is first_task

                    await gw.stop()
                    assert gw._running is False


# ==============================================================================
# 15. ON-DEMAND REPORT SLASH COMMANDS TESTS (v1.2)
# ==============================================================================

@pytest.mark.asyncio
async def test_slash_command_report_overdue(slash_setup):
    """Test /pm report name:overdue and /pm overdue generate overdue tasks report."""
    handler, bot, jira_client, engine, temp_db = slash_setup
    from app.database.repositories import JiraIssueStateRepository
    state_repo = JiraIssueStateRepository(temp_db)
    state_repo.upsert(
        jira_issue_key="WSSS-326",
        summary="Checkout optimization",
        status="In Progress",
        assignee="Abdul Subhan",
        due_date="2026-09-10",
        updated_at="2026-09-11 18:25",
        team_group=settings.JIRA_TEAM_GROUP
    )

    # 1. Via /pm report name:overdue
    res1 = await handler.execute_subcommand(
        subcommand="report",
        options={"name": "overdue", "date": "2026-09-12"},
        discord_user_id="123456789"
    )
    assert "📋" in res1
    assert "Overdue Tasks" in res1
    assert "WSSS-326" in res1
    assert "Abdul Subhan" in res1
    assert "2026-09-10" in res1
    assert "Generated by PM Operations Agent" in res1

    # 2. Via direct alias /pm overdue
    res2 = await handler.execute_subcommand(
        subcommand="overdue",
        options={"date": "2026-09-12"},
        discord_user_id="123456789"
    )
    assert "📋" in res2
    assert "WSSS-326" in res2


@pytest.mark.asyncio
async def test_slash_command_report_overdue_empty(slash_setup):
    """Test /pm report overdue returns clean empty state when no overdue tasks exist."""
    handler, bot, jira_client, engine, temp_db = slash_setup

    res = await handler.execute_subcommand(
        subcommand="report",
        options={"name": "overdue", "date": "2026-09-12"},
        discord_user_id="123456789"
    )
    assert "📋" in res
    assert "No overdue tasks found." in res
    assert "Generated by PM Operations Agent" in res


@pytest.mark.asyncio
async def test_slash_command_report_worklog(slash_setup):
    """Test /pm report name:worklog and /pm worklog generate daily worklog report."""
    handler, bot, jira_client, engine, temp_db = slash_setup
    from app.database.repositories import JiraWorklogRepository
    wl_repo = JiraWorklogRepository(temp_db)
    wl_repo.upsert_worklog(
        worklog_id="wl-101",
        jira_issue_key="WSSS-326",
        jira_issue_id="1001",
        author_account_id="557058:ba931089-a292-4f11",
        author_display_name="Aqib Khan",
        time_spent_seconds=7200,
        started_at="2026-09-12T10:00:00Z",
        created_at="2026-09-12T10:00:00Z",
        updated_at="2026-09-12T10:00:00Z",
        comment="Refactored reporting",
        team_group=settings.JIRA_TEAM_GROUP
    )

    res = await handler.execute_subcommand(
        subcommand="report",
        options={"name": "worklog", "date": "2026-09-12"},
        discord_user_id="123456789"
    )
    assert isinstance(res, dict)
    assert "embeds" in res
    assert len(res["embeds"]) == 1
    embed = res["embeds"][0]
    assert "📊" in embed["title"]
    assert "Daily Worklog" in embed["title"]
    assert "Aqib Khan" in embed["description"]
    assert "2h" in embed["description"]
    assert embed["footer"]["text"] == "Generated by PM Operations Agent"

    # Via direct alias /pm worklog
    res_alias = await handler.execute_subcommand(
        subcommand="worklog",
        options={"date": "2026-09-12"},
        discord_user_id="123456789"
    )
    assert isinstance(res_alias, dict)
    assert "embeds" in res_alias
    embed_alias = res_alias["embeds"][0]
    assert "📊" in embed_alias["title"]
    assert "Aqib Khan" in embed_alias["description"]


@pytest.mark.asyncio
async def test_slash_command_report_attention(slash_setup):
    """Test /pm report name:attention and /pm attention generate PM Attention Digest."""
    handler, bot, jira_client, engine, temp_db = slash_setup
    from app.database.repositories import JiraIssueStateRepository
    state_repo = JiraIssueStateRepository(temp_db)
    state_repo.upsert(
        jira_issue_key="WSSS-500",
        summary="Unassigned Task",
        status="To Do",
        assignee=None,
        due_date="2026-09-20",
        updated_at="2026-09-10 10:00",
        team_group=settings.JIRA_TEAM_GROUP
    )

    res = await handler.execute_subcommand(
        subcommand="report",
        options={"name": "attention", "date": "2026-09-12"},
        discord_user_id="123456789"
    )
    assert "PM Attention Digest" in res
    assert "WSSS-500" in res
    assert "Generated by PM Operations Agent" in res

    # Direct alias /pm attention
    res_alias = await handler.execute_subcommand(
        subcommand="attention",
        options={"date": "2026-09-12"},
        discord_user_id="123456789"
    )
    assert "PM Attention Digest" in res_alias
    assert "WSSS-500" in res_alias


@pytest.mark.asyncio
async def test_slash_command_report_activity(slash_setup):
    """Test /pm report name:activity and /pm activity generate Daily PM Activity Report."""
    handler, bot, jira_client, engine, temp_db = slash_setup
    from app.database.repositories import EventRepository
    event_repo = EventRepository(temp_db)
    event_repo.insert(
        event_id="evt-1",
        event_type="TaskCreated",
        source="jira",
        external_event_id="ext-1",
        timestamp="2026-09-12T12:00:00Z",
        actor_name="Aqib Khan",
        task_id="WSSS-326",
        payload={}
    )

    res = await handler.execute_subcommand(
        subcommand="report",
        options={"name": "activity", "date": "2026-09-12"},
        discord_user_id="123456789"
    )
    assert "Daily PM Activity Report" in res
    assert "Total Activities" in res
    assert "Generated by PM Operations Agent" in res

    # Direct alias /pm activity
    res_alias = await handler.execute_subcommand(
        subcommand="activity",
        options={"date": "2026-09-12"},
        discord_user_id="123456789"
    )
    assert "Daily PM Activity Report" in res_alias


@pytest.mark.asyncio
async def test_slash_command_report_invalid_choice(slash_setup):
    """Test /pm report with unknown report name returns helpful error."""
    handler, bot, jira_client, engine, temp_db = slash_setup

    res = await handler.execute_subcommand(
        subcommand="report",
        options={"name": "invalid_report"},
        discord_user_id="123456789"
    )
    assert "❌ Unknown report 'invalid_report'" in res
    assert "Available reports: `overdue`, `worklog`, `attention`, `activity`" in res


@pytest.mark.asyncio
async def test_slash_command_report_unauthorized(slash_setup):
    """Test unauthorized users cannot request reports via slash commands."""
    handler, bot, jira_client, engine, temp_db = slash_setup

    res = await handler.execute_subcommand(
        subcommand="report",
        options={"name": "overdue"},
        discord_user_id="999999999"  # Unauthorized ID
    )
    assert "❌ You are not authorized to use PM commands." in res


# ==============================================================================
# 16. RESOURCE-SPECIFIC REPORTS & ACTIVE QUEUE TESTS (v1.3)
# ==============================================================================

@pytest.mark.asyncio
async def test_slash_command_user_worklog_success_and_empty(slash_setup):
    """Test /pm worklog user:<resource> and /pm report name:worklog user:<resource>."""
    handler, bot, jira_client, engine, temp_db = slash_setup
    from app.database.repositories import JiraWorklogRepository
    wl_repo = JiraWorklogRepository(temp_db)
    wl_repo.upsert_worklog(
        worklog_id="wl-201",
        jira_issue_key="WSSS-326",
        jira_issue_id="1001",
        author_account_id="557058:ba931089-a292-4f11",
        author_display_name="Aqib Khan",
        time_spent_seconds=14400,
        started_at="2026-09-12T09:00:00Z",
        created_at="2026-09-12T09:00:00Z",
        updated_at="2026-09-12T09:00:00Z",
        comment="Implemented reporting v1.3",
        team_group=settings.JIRA_TEAM_GROUP
    )

    # 1. Direct alias /pm worklog user:Aqib Khan
    res = await handler.execute_subcommand(
        subcommand="worklog",
        options={"user": "Aqib Khan", "date": "2026-09-12"},
        discord_user_id="123456789"
    )
    assert "📊" in res
    assert "Daily Worklog — Aqib Khan" in res
    assert "4h" in res
    assert "WSSS-326" in res
    assert "Generated by PM Operations Agent" in res

    # 2. Via /pm report name:worklog user:Aqib Khan
    res_report = await handler.execute_subcommand(
        subcommand="report",
        options={"name": "worklog", "user": "Aqib Khan", "date": "2026-09-12"},
        discord_user_id="123456789"
    )
    assert "📊" in res_report
    assert "Daily Worklog — Aqib Khan" in res_report
    assert "4h" in res_report

    # 3. User with no worklogs on date -> empty clean format
    res_empty = await handler.execute_subcommand(
        subcommand="worklog",
        options={"user": "Aqib Khan", "date": "2026-09-01"},
        discord_user_id="123456789"
    )
    assert "📊" in res_empty
    assert "No worklogs logged" in res_empty


@pytest.mark.asyncio
async def test_slash_command_user_overdue_success_and_empty(slash_setup):
    """Test /pm overdue user:<resource> and /pm report name:overdue user:<resource>."""
    handler, bot, jira_client, engine, temp_db = slash_setup
    from app.database.repositories import JiraIssueStateRepository
    state_repo = JiraIssueStateRepository(temp_db)
    state_repo.upsert(
        jira_issue_key="WSSS-401",
        summary="Complete load test",
        status="In Progress",
        assignee="Abdul Subhan",
        due_date="2026-09-10",
        updated_at="2026-09-11 18:25",
        team_group=settings.JIRA_TEAM_GROUP,
        raw_reference={"assignee": {"accountId": "712020:c12d2371-c035-423a-bc44-30799981aa7a", "displayName": "Abdul Subhan"}}
    )

    # 1. Direct alias /pm overdue user:Abdul Subhan
    res = await handler.execute_subcommand(
        subcommand="overdue",
        options={"user": "Abdul Subhan", "date": "2026-09-12"},
        discord_user_id="123456789"
    )
    assert "📋" in res
    assert "Overdue Tasks — Abdul Subhan" in res
    assert "WSSS-401" in res
    assert "Abdul Subhan" in res
    assert "2026-09-10" in res
    assert "Total Overdue:** 1" in res
    assert "Generated by PM Operations Agent" in res

    # 2. Via /pm report name:overdue user:Abdul Subhan
    res_report = await handler.execute_subcommand(
        subcommand="report",
        options={"name": "overdue", "user": "Abdul Subhan", "date": "2026-09-12"},
        discord_user_id="123456789"
    )
    assert "📋" in res_report
    assert "WSSS-401" in res_report
    assert "Abdul Subhan" in res_report

    # 3. User with no overdue tasks -> empty state
    res_empty = await handler.execute_subcommand(
        subcommand="overdue",
        options={"user": "Ahsan Amin", "date": "2026-09-12"},
        discord_user_id="123456789"
    )
    assert "📋" in res_empty
    assert "No overdue tasks found" in res_empty


@pytest.mark.asyncio
async def test_slash_command_user_queue_canonical(slash_setup):
    """Test /pm queue user:<resource> and /pm report name:queue user:<resource> reusing canonical queue definition."""
    handler, bot, jira_client, engine, temp_db = slash_setup
    from app.database.repositories import JiraIssueStateRepository
    state_repo = JiraIssueStateRepository(temp_db)
    state_repo.upsert(
        jira_issue_key="WSSS-501",
        summary="Fix payment gateway timeout",
        status="In Progress",
        assignee="Abdul Subhan",
        priority="High",
        due_date="2026-09-15",
        updated_at="2026-09-12 11:00",
        team_group=settings.JIRA_TEAM_GROUP,
        raw_reference={"assignee": {"accountId": "712020:c12d2371-c035-423a-bc44-30799981aa7a", "displayName": "Abdul Subhan"}}
    )
    state_repo.upsert(
        jira_issue_key="WSSS-502",
        summary="Completed refactoring",
        status="Done",
        assignee="Abdul Subhan",
        priority="Medium",
        due_date="2026-09-10",
        updated_at="2026-09-12 12:00",
        team_group=settings.JIRA_TEAM_GROUP,
        raw_reference={"assignee": {"accountId": "712020:c12d2371-c035-423a-bc44-30799981aa7a", "displayName": "Abdul Subhan"}}
    )

    # 1. Direct alias /pm queue user:Abdul Subhan
    res = await handler.execute_subcommand(
        subcommand="queue",
        options={"user": "Abdul Subhan"},
        discord_user_id="123456789"
    )
    assert "Active Queue — Abdul Subhan" in res
    assert "WSSS-501" in res
    assert "Fix payment gateway timeout" in res
    assert "WSSS-502" not in res, "Done status must NOT appear in Active Queue"
    assert "Total Active:** 1" in res
    assert "Generated by PM Operations Agent" in res

    # 2. Via /pm report name:queue user:Abdul Subhan
    res_report = await handler.execute_subcommand(
        subcommand="report",
        options={"name": "queue", "user": "Abdul Subhan"},
        discord_user_id="123456789"
    )
    assert "Active Queue — Abdul Subhan" in res_report
    assert "WSSS-501" in res_report


@pytest.mark.asyncio
async def test_slash_command_unresolvable_and_excluded_user(slash_setup):
    """Test /pm report / queue / worklog with unresolvable and excluded user handles cleanly without crash."""
    handler, bot, jira_client, engine, temp_db = slash_setup

    # 1. Unresolvable user
    res_unresolved = await handler.execute_subcommand(
        subcommand="queue",
        options={"user": "unknown_ghost_user_999"},
        discord_user_id="123456789"
    )
    assert "Resource not found." in res_unresolved or "Could not safely identify" in res_unresolved or "unknown_ghost_user_999" in res_unresolved

    # 2. Missing user option on /pm queue
    res_missing = await handler.execute_subcommand(
        subcommand="queue",
        options={},
        discord_user_id="123456789"
    )
    assert "Please specify a user" in res_missing or "required" in res_missing


# ==============================================================================
# 14. DISCORD INTERACTION RESPONSE LIFECYCLE & ERROR HANDLING TESTS (REPORTS v1.3)
# ==============================================================================

@pytest.mark.asyncio
async def test_slash_command_report_worklog_deferral_lifecycle(slash_setup):
    """Test /pm report name:worklog user:<user> date:<date> immediately defers and returns report."""
    handler, _, mock_jira_client, _, temp_db = slash_setup

    from app.database.repositories import JiraWorklogRepository
    worklog_repo = JiraWorklogRepository(temp_db)
    worklog_repo.upsert_worklog(
        worklog_id="wl-101",
        jira_issue_key="WSSS-326",
        jira_issue_id="1001",
        author_account_id="712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de",
        author_display_name="Ahsan Amin",
        time_spent_seconds=14400,
        started_at="2026-09-12T09:00:00Z",
        created_at="2026-09-12T09:00:00Z",
        updated_at="2026-09-12T09:00:00Z",
        comment="Implemented features",
        team_group=settings.JIRA_TEAM_GROUP
    )

    gw = DiscordGatewayClient(
        bot_token="test_token",
        application_id="11223344",
        slash_handler=handler,
    )

    payload = {
        "id": "int_worklog_1",
        "token": "tok_worklog_1",
        "type": 2,
        "data": {
            "name": "pm",
            "options": [
                {
                    "name": "report",
                    "type": 1,
                    "options": [
                        {"name": "name", "value": "worklog"},
                        {"name": "user", "value": "Ahsan Amin"},
                        {"name": "date", "value": "2026-09-12"}
                    ]
                }
            ]
        },
        "member": {
            "user": {"id": "123456789", "username": "aqib"}
        }
    }

    with patch.object(gw, "send_deferred_acknowledgement", new_callable=AsyncMock) as mock_defer:
        with patch.object(gw, "update_deferred_response", new_callable=AsyncMock) as mock_update:
            mock_defer.return_value = True
            mock_update.return_value = True

            await gw._process_interaction_create(payload)

            mock_defer.assert_awaited_once_with("int_worklog_1", "tok_worklog_1")
            mock_update.assert_awaited_once()
            res_text = mock_update.call_args[0][1]
            assert "Daily Worklog — Ahsan Amin" in res_text
            assert "WSSS-326" in res_text
            assert "4h" in res_text


@pytest.mark.asyncio
async def test_slash_command_worklog_direct_alias_deferral_lifecycle(slash_setup):
    """Test /pm worklog user:<user> date:<date> uses same reliable deferred lifecycle."""
    handler, _, mock_jira_client, _, temp_db = slash_setup

    from app.database.repositories import JiraWorklogRepository
    worklog_repo = JiraWorklogRepository(temp_db)
    worklog_repo.upsert_worklog(
        worklog_id="wl-102",
        jira_issue_key="WSSS-326",
        jira_issue_id="1001",
        author_account_id="712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de",
        author_display_name="Ahsan Amin",
        time_spent_seconds=7200,
        started_at="2026-09-12T09:00:00Z",
        created_at="2026-09-12T09:00:00Z",
        updated_at="2026-09-12T09:00:00Z",
        comment="Implemented features",
        team_group=settings.JIRA_TEAM_GROUP
    )

    gw = DiscordGatewayClient(
        bot_token="test_token",
        application_id="11223344",
        slash_handler=handler,
    )

    payload = {
        "id": "int_worklog_2",
        "token": "tok_worklog_2",
        "type": 2,
        "data": {
            "name": "pm",
            "options": [
                {
                    "name": "worklog",
                    "type": 1,
                    "options": [
                        {"name": "user", "value": "Ahsan Amin"},
                        {"name": "date", "value": "2026-09-12"}
                    ]
                }
            ]
        },
        "member": {
            "user": {"id": "123456789", "username": "aqib"}
        }
    }

    with patch.object(gw, "send_deferred_acknowledgement", new_callable=AsyncMock) as mock_defer:
        with patch.object(gw, "update_deferred_response", new_callable=AsyncMock) as mock_update:
            mock_defer.return_value = True
            mock_update.return_value = True

            await gw._process_interaction_create(payload)

            mock_defer.assert_awaited_once_with("int_worklog_2", "tok_worklog_2")
            mock_update.assert_awaited_once()
            res_text = mock_update.call_args[0][1]
            assert "Daily Worklog — Ahsan Amin" in res_text
            assert "2h" in res_text


@pytest.mark.asyncio
async def test_slash_command_report_exception_sanitized_response(slash_setup):
    """Test that an exception during report generation returns a clean sanitized response."""
    handler, _, _, _, _ = slash_setup

    with patch("app.core.reports.worklog_report.DailyWorklogReportGenerator.generate_user_worklog_report", side_effect=RuntimeError("Database query timed out")):
        res = await handler.execute_subcommand(
            subcommand="report",
            options={"name": "worklog", "user": "Ahsan Amin", "date": "2026-09-12"},
            discord_user_id="123456789"
        )
        assert res == "❌ Unable to generate the worklog report right now."
        assert "Database query timed out" not in res
        assert "Traceback" not in res


@pytest.mark.asyncio
async def test_slash_command_all_report_types_exception_resilience(slash_setup):
    """Verify all report types (overdue, worklog, queue, attention, activity) return sanitized error on exception."""
    handler, _, _, _, _ = slash_setup

    # 1. Overdue error
    with patch("app.core.reports.overdue_report.DailyOverdueReportGenerator.generate_digest", side_effect=Exception("Overdue failure")):
        res = await handler.execute_subcommand(subcommand="report", options={"name": "overdue"}, discord_user_id="123456789")
        assert res == "❌ Unable to generate the overdue report right now."

    # 2. Queue error
    with patch("app.core.reports.queue_report.ResourceQueueReportGenerator.generate_user_queue_report", side_effect=Exception("Queue failure")):
        res = await handler.execute_subcommand(subcommand="queue", options={"user": "Ahsan Amin"}, discord_user_id="123456789")
        assert res == "❌ Unable to generate the active queue report right now."

    # 3. Attention error
    with patch("app.core.reports.attention_report.DailyPMAttentionReportGenerator.generate_digest", side_effect=Exception("Attention failure")):
        res = await handler.execute_subcommand(subcommand="attention", options={}, discord_user_id="123456789")
        assert res == "❌ Unable to generate the attention report right now."

    # 4. Activity error
    with patch("app.core.reports.daily_report.DailyActivityReportGenerator.generate_report", side_effect=Exception("Activity failure")):
        res = await handler.execute_subcommand(subcommand="activity", options={}, discord_user_id="123456789")
        assert res == "❌ Unable to generate the daily activity report right now."


@pytest.mark.asyncio
async def test_gateway_interaction_unhandled_exception_fallback(slash_setup):
    """Verify gateway client catches any unhandled exception in execution and still patches response."""
    handler, _, _, _, _ = slash_setup
    gw = DiscordGatewayClient(bot_token="test_token", application_id="11223344", slash_handler=handler)

    payload = {
        "id": "int_err",
        "token": "tok_err",
        "type": 2,
        "data": {"name": "pm", "options": [{"name": "status", "type": 1, "options": [{"name": "ticket", "value": "WSSS-1"}]}]},
        "member": {"user": {"id": "123456789"}}
    }

    with patch.object(gw, "send_deferred_acknowledgement", new_callable=AsyncMock) as mock_defer:
        with patch.object(gw, "update_deferred_response", new_callable=AsyncMock) as mock_update:
            with patch.object(handler, "execute_subcommand", side_effect=Exception("Critical system crash")):
                mock_defer.return_value = True
                mock_update.return_value = True

                await gw._process_interaction_create(payload)

                mock_defer.assert_awaited_once()
                mock_update.assert_awaited_once()
                res_content = mock_update.call_args[0][1]
                assert "❌ An unexpected error occurred" in res_content
                assert "Critical system crash" not in res_content


# ==============================================================================
# 15. EXPANDED /PM CREATE COMMAND TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_create_project_and_summary_only(slash_setup):
    """Test /pm create project:WSSS summary:'Fix login issue' (minimal required options)."""
    handler, _, mock_jira_client, engine, _ = slash_setup

    res = await handler.execute_subcommand(
        subcommand="create",
        options={"project": "WSSS", "summary": "Fix login issue"},
        discord_user_id="123456789"
    )

    assert "Created WSSS-100 successfully." in res
    assert settings.get_jira_browse_url("WSSS-100") in res
    assert len(mock_jira_client.create_issue_calls) == 1
    proj, summ, itype, desc, assignee, priority, labels = mock_jira_client.create_issue_calls[0]
    assert proj == "WSSS"
    assert summ == "Fix login issue"
    assert desc is None
    assert assignee is None


@pytest.mark.asyncio
async def test_create_with_description(slash_setup):
    """Test /pm create with optional description."""
    handler, _, mock_jira_client, _, _ = slash_setup

    res = await handler.execute_subcommand(
        subcommand="create",
        options={
            "project": "WSSS",
            "summary": "Fix login issue",
            "description": "Users cannot log in after password reset\nSteps to reproduce: 1. Reset password"
        },
        discord_user_id="123456789"
    )

    assert "Created WSSS-100 successfully." in res
    assert len(mock_jira_client.create_issue_calls) == 1
    proj, summ, itype, desc, assignee, priority, labels = mock_jira_client.create_issue_calls[0]
    assert desc == "Users cannot log in after password reset\nSteps to reproduce: 1. Reset password"


@pytest.mark.asyncio
async def test_create_with_assignee_me(slash_setup):
    """Test /pm create with assignee:'me' safely resolved to active user."""
    handler, _, mock_jira_client, _, _ = slash_setup

    with patch.object(settings, "MY_JIRA_ACCOUNT_ID", "557058:ba931089-a292-4f11"):
        with patch.object(settings, "MY_JIRA_DISPLAY_NAME", "Aqib Khan"):
            res = await handler.execute_subcommand(
                subcommand="create",
                options={"project": "WSSS", "summary": "Fix login issue", "assignee": "me"},
                discord_user_id="123456789"
            )

            assert "Created WSSS-100 successfully." in res
            assert len(mock_jira_client.create_issue_calls) == 1
            proj, summ, itype, desc, assignee, priority, labels = mock_jira_client.create_issue_calls[0]
            assert assignee == "557058:ba931089-a292-4f11"


@pytest.mark.asyncio
async def test_create_with_assignee_exact_display_name(slash_setup):
    """Test /pm create with assignee:'Ahsan Amin' resolving by exact display name."""
    handler, _, mock_jira_client, _, _ = slash_setup

    res = await handler.execute_subcommand(
        subcommand="create",
        options={"project": "WSSS", "summary": "Fix login issue", "assignee": "Ahsan Amin"},
        discord_user_id="123456789"
    )

    assert "Created WSSS-100 successfully." in res
    assert len(mock_jira_client.create_issue_calls) == 1
    proj, summ, itype, desc, assignee, priority, labels = mock_jira_client.create_issue_calls[0]
    assert assignee == "712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de"


@pytest.mark.asyncio
async def test_create_with_assignee_canonical_account_id(slash_setup):
    """Test /pm create with assignee as direct canonical account ID."""
    handler, _, mock_jira_client, _, _ = slash_setup

    res = await handler.execute_subcommand(
        subcommand="create",
        options={"project": "WSSS", "summary": "Fix login issue", "assignee": "712020:c12d2371-c035-423a-bc44-30799981aa7a"},
        discord_user_id="123456789"
    )

    assert "Created WSSS-100 successfully." in res
    assert len(mock_jira_client.create_issue_calls) == 1
    proj, summ, itype, desc, assignee, priority, labels = mock_jira_client.create_issue_calls[0]
    assert assignee == "712020:c12d2371-c035-423a-bc44-30799981aa7a"


@pytest.mark.asyncio
async def test_create_with_unknown_assignee_rejected_safely(slash_setup):
    """Test /pm create with unknown assignee is safely rejected before issue creation."""
    handler, _, mock_jira_client, _, _ = slash_setup

    res = await handler.execute_subcommand(
        subcommand="create",
        options={"project": "WSSS", "summary": "Fix login issue", "assignee": "NonExistentUser123"},
        discord_user_id="123456789"
    )

    assert res == "Could not resolve that Jira user. Please use an exact Jira display name or `me`."
    assert len(mock_jira_client.create_issue_calls) == 0, "No Jira ticket must be created if assignee fails resolution"


@pytest.mark.asyncio
async def test_create_with_comment_success(slash_setup):
    """Test /pm create with comment adds the comment to the newly-created ticket via ActionEngine."""
    handler, _, mock_jira_client, engine, _ = slash_setup

    res = await handler.execute_subcommand(
        subcommand="create",
        options={
            "project": "WSSS",
            "summary": "Fix login issue",
            "comment": "Please verify this after deployment"
        },
        discord_user_id="123456789"
    )

    assert "Created WSSS-100 successfully and added the comment." in res
    assert settings.get_jira_browse_url("WSSS-100") in res
    assert len(mock_jira_client.create_issue_calls) == 1
    assert len(mock_jira_client.comment_calls) == 1
    target_key, comment_body = mock_jira_client.comment_calls[0]
    assert target_key == "WSSS-100"
    assert comment_body == "Please verify this after deployment"


@pytest.mark.asyncio
async def test_create_with_all_options(slash_setup):
    """Test /pm create project:WSSS summary:'...' description:'...' assignee:'...' comment:'...'."""
    handler, _, mock_jira_client, _, _ = slash_setup

    res = await handler.execute_subcommand(
        subcommand="create",
        options={
            "project": "WSSS",
            "summary": "Fix login issue",
            "description": "Users cannot log in after password reset",
            "assignee": "Ahsan Amin",
            "comment": "Please verify this after deployment"
        },
        discord_user_id="123456789"
    )

    assert "Created WSSS-100 successfully and added the comment." in res
    assert len(mock_jira_client.create_issue_calls) == 1
    assert len(mock_jira_client.comment_calls) == 1
    proj, summ, itype, desc, assignee, priority, labels = mock_jira_client.create_issue_calls[0]
    assert proj == "WSSS"
    assert summ == "Fix login issue"
    assert desc == "Users cannot log in after password reset"
    assert assignee == "712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de"
    assert mock_jira_client.comment_calls[0] == ("WSSS-100", "Please verify this after deployment")


@pytest.mark.asyncio
async def test_create_comment_not_attempted_on_creation_failure(slash_setup):
    """If issue creation fails, comment must NOT be attempted."""
    handler, _, mock_jira_client, _, _ = slash_setup

    with patch.object(mock_jira_client, "create_issue", side_effect=RuntimeError("Jira project not found")):
        res = await handler.execute_subcommand(
            subcommand="create",
            options={
                "project": "INVALID",
                "summary": "Fix login issue",
                "comment": "Should not be called"
            },
            discord_user_id="123456789"
        )

        assert "❌ Could not create task in INVALID" in res
        assert len(mock_jira_client.comment_calls) == 0


@pytest.mark.asyncio
async def test_create_partial_failure_comment_failed(slash_setup):
    """If issue creation succeeds but comment fails, report partial failure truthfully."""
    handler, _, mock_jira_client, _, _ = slash_setup

    with patch.object(mock_jira_client, "add_comment", side_effect=RuntimeError("Comment permission denied")):
        res = await handler.execute_subcommand(
            subcommand="create",
            options={
                "project": "WSSS",
                "summary": "Fix login issue",
                "comment": "This will fail"
            },
            discord_user_id="123456789"
        )

        assert "Created WSSS-100 successfully, but the requested comment could not be added." in res
        assert "added the comment" not in res.replace("could not be added", "")
        assert settings.get_jira_browse_url("WSSS-100") in res


@pytest.mark.asyncio
async def test_create_dry_run_flow(slash_setup):
    """Test /pm create in DRY_RUN=true generates simulated CREATE_TASK and COMMENT actions through ActionEngine."""
    handler, _, mock_jira_client, _, _ = slash_setup

    with patch.object(settings, "DRY_RUN", True):
        res = await handler.execute_subcommand(
            subcommand="create",
            options={
                "project": "WSSS",
                "summary": "Fix login issue",
                "description": "Users cannot log in after password reset",
                "assignee": "Ahsan Amin",
                "comment": "Please verify this after deployment"
            },
            discord_user_id="123456789"
        )

        assert "🧪 DRY RUN" in res
        assert "Created WSSS-SIMULATED successfully and added the comment." in res
        assert len(mock_jira_client.create_issue_calls) == 0, "No real API call during DRY RUN"
        assert len(mock_jira_client.comment_calls) == 0, "No real API call during DRY RUN"


# ==============================================================================
# 20. DISCORD EMBED REDESIGN FOR /pm worklog TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_pm_worklog_embed_redesign_representative_2026_09_14(slash_setup):
    """Test /pm worklog produces a valid mobile-friendly Discord embed with all criteria."""
    import re
    from unittest.mock import AsyncMock
    handler, bot, jira_client, engine, temp_db = slash_setup
    from app.database.repositories import JiraWorklogRepository
    wl_repo = JiraWorklogRepository(temp_db)

    # 12 active members with representative 2026-09-14 worklogs
    active_member_specs = [
        ("Hamza Hanif", "638490c75fce844d606a16ef", 28500, [f"WPEP-{i}" for i in range(16)]),
        ("Mubashir Butt", "712020:e268bcd8-d981-4b4d-992d-d5694745df8b", 25560, [f"PP-{i}" for i in range(26)]),
        ("Daniyal Raza", "63e362bd790148a180977179", 24900, [f"TREN-{i}" for i in range(5)]),
        ("shoaib hassan askari", "712020:32e5be05-80c9-4ece-ac19-301da7c9487d", 23400, ["AIOL-101"]),
        ("Muneeb Jalal", "606570150a6b3f00698f9430", 21900, [f"HFCF-{i}" for i in range(3)]),
        ("Ahsan Amin", "712020:8bc58bcd-fe17-4f1b-9825-c5251cb6b1de", 21600, ["WSSS-101", "WSSS-102"]),
        ("Muhammad Ali Siddiqui", "712020:2783ea21-c611-402d-9adb-0529f5b7066d", 18300, [f"CF7-{i}" for i in range(6)]),
        ("Azain Hassan", "712020:1ddac8e3-e006-48e7-b4c9-ee941efc8e6e", 18300, [f"POST-{i}" for i in range(8)]),
        ("Muhammad Usama Azad", "712020:fb8608cb-6393-48a7-a3ab-1ad744a2b7f6", 15300, [f"INV-{i}" for i in range(3)]),
        ("Tahir Ali", "638855b85fce844d606bb422", 14400, [f"PAY-{i}" for i in range(4)]),
        ("Muhammad Shahmeer Khan", "712020:a6d04898-c6d8-4a39-a521-103e4b8bfe7c", 13800, [f"AUTH-{i}" for i in range(3)]),
        ("Nauman Sadiq", "712020:0eca0fb9-4f12-4532-a435-4c178f2d90e8", 7200, ["OPS-101"]),
    ]

    for name, acc_id, time_spent, tickets in active_member_specs:
        for idx, tkey in enumerate(tickets):
            wl_repo.upsert_worklog(
                worklog_id=f"wl-{acc_id}-{idx}",
                jira_issue_key=tkey,
                jira_issue_id=f"issue-{idx}",
                author_account_id=acc_id,
                author_display_name=name,
                time_spent_seconds=time_spent // len(tickets),
                started_at="2026-09-14T09:00:00Z",
                created_at="2026-09-14T09:00:00Z",
                updated_at="2026-09-14T09:00:00Z",
                comment="Worklog entry",
                team_group=settings.JIRA_TEAM_GROUP,
            )

    # 6 zero-work members in group
    zero_members_specs = [
        ("Ahsan Iftikhar", "63da2ba4f1475ad42c584247"),
        ("Muhammad Bilal Khan", "712020:12e1da4b-147f-4f91-9d2d-965b66e19b61"),
        ("Muhammad Hamza", "5fb3d908facfd6007697c25a"),
        ("Muhammad Sufiyan", "712020:c12d2371-1e5b-4797-a888-369c0c9c5a65"),
        ("Syed ali", "61ee41431c42100069344a09"),
        ("Talha Bukhari", "712020:bb2e5830-7156-4852-bba8-75fa773fc55d"),
    ]
    zero_work_names = [name for name, _ in zero_members_specs]

    mock_group_members = [
        {"accountId": acc_id, "displayName": name, "active": True}
        for name, acc_id, _, _ in active_member_specs
    ] + [
        {"accountId": acc_id, "displayName": name, "active": True}
        for name, acc_id in zero_members_specs
    ] + [
        # Excluded users that should be safely ignored
        {"accountId": "712020:566cad70-4a54-42bc-bf36-0c6132fe3cf0", "displayName": "Aqib Khan", "active": True},
        {"accountId": "557058:8b3f9c31-7d88-473a-9351-abacc5b84933", "displayName": "Mohammad Mursaleen", "active": True},
    ]

    with patch("app.connectors.jira.client.JiraClient.get_group_members", new_callable=AsyncMock) as mock_get_members:
        mock_get_members.return_value = mock_group_members

        # 1. Test /pm worklog
        payload = await handler.execute_subcommand(
            subcommand="worklog",
            options={"date": "2026-09-14"},
            discord_user_id="123456789"
        )

        # - Valid embed payload structure
        assert isinstance(payload, dict), "Payload must be a dictionary"
        assert "embeds" in payload, "Payload must contain 'embeds' key"

        # - Exactly one embed is produced
        assert len(payload["embeds"]) == 1, "Must produce exactly one embed"
        embed = payload["embeds"][0]

        # - Embed title is correct
        assert embed["title"] == "📊 Mursaleen Cluster — Daily Worklog"

        # - Embed color is correct (0x9B59B6)
        assert embed["color"] == 0x9B59B6

        # - Footer text is correct
        assert embed["footer"]["text"] == "Generated by PM Operations Agent"

        # - Embed description is below Discord's 4096-character limit
        desc = embed["description"]
        assert len(desc) < 4096, f"Description exceeds 4096 limit: {len(desc)} characters"

        # - Header contains Date, Tickets Worked, Total Worklogs, Active Members
        assert "**Date:** 2026-09-14" in desc
        assert "**Tickets Worked:**" in desc
        assert "**Total Worklogs:**" in desc
        assert "**Active Members:** 12 / 18" in desc
        assert "👥 **Team Worklog**" in desc

        # - All 12 active members are present
        for name, _, _, _ in active_member_specs:
            assert name in desc, f"Active member {name} missing from description"

        # - Each active member has a clickable [X ticket(s)](URL) link
        assert "[16 tickets](https://" in desc
        assert "[26 tickets](https://" in desc
        assert "[5 tickets](https://" in desc
        assert "[1 ticket](https://" in desc  # shoaib hassan askari (singular)
        assert "[2 tickets](https://" in desc

        # - Long Jira URLs are NOT displayed as raw visible URLs
        # Every URL must be inside markdown link parentheses: ](https://...
        url_matches = list(re.finditer(r"https?://\S+", desc))
        assert len(url_matches) > 0, "Expected Jira links in markdown format"
        for match in url_matches:
            start_pos = match.start()
            end_pos = match.end()
            # Ensure URL is directly preceded by ]( and directly followed by )
            assert desc[max(0, start_pos - 2):start_pos] == "](", f"URL at {start_pos} is not inside markdown link: {match.group()}"
            assert desc[end_pos - 1] == ")" or desc[end_pos:end_pos + 1] == ")", f"URL at {start_pos} is missing closing paren: {match.group()}"

        # - The 6 zero-work members remain visible
        assert "⏸️ **No Logged Work**" in desc
        for zero_name in zero_work_names:
            assert zero_name in desc, f"Zero-work member {zero_name} missing from description"

        # - Also test aliases: worklogs, daily_worklog, report name:worklog
        payload_alias1 = await handler.execute_subcommand(
            subcommand="worklogs",
            options={"date": "2026-09-14"},
            discord_user_id="123456789"
        )
        assert isinstance(payload_alias1, dict) and "embeds" in payload_alias1

        payload_alias2 = await handler.execute_subcommand(
            subcommand="daily_worklog",
            options={"date": "2026-09-14"},
            discord_user_id="123456789"
        )
        assert isinstance(payload_alias2, dict) and "embeds" in payload_alias2

        payload_report = await handler.execute_subcommand(
            subcommand="report",
            options={"name": "worklog", "date": "2026-09-14"},
            discord_user_id="123456789"
        )
        assert isinstance(payload_report, dict) and "embeds" in payload_report


@pytest.mark.asyncio
async def test_gateway_update_deferred_response_embed_and_string_chunking():
    """Test gateway client update_deferred_response correctly handles embed payloads and string chunking."""
    gw = DiscordGatewayClient(
        bot_token="test_token",
        application_id="11223344"
    )

    # 1. Embed payload handling: PATCH with {"embeds": [...]}
    embed_payload = {
        "embeds": [{
            "title": "📊 Mursaleen Cluster — Daily Worklog",
            "description": "Sample description",
            "color": 0x9B59B6,
            "footer": {"text": "Generated by PM Operations Agent"}
        }]
    }

    with patch("httpx.AsyncClient.patch") as mock_patch:
        mock_patch.return_value = AsyncMock(status_code=200)
        ok = await gw.update_deferred_response("token_embed", embed_payload)
        assert ok is True
        call_url = mock_patch.call_args[0][0]
        assert call_url == "https://discord.com/api/v10/webhooks/11223344/token_embed/messages/@original"
        call_json = mock_patch.call_args[1]["json"]
        assert "embeds" in call_json
        assert len(call_json["embeds"]) == 1
        assert call_json["embeds"][0]["title"] == "📊 Mursaleen Cluster — Daily Worklog"
        assert "content" not in call_json

    # 2. Standard string response handling: PATCH with {"content": "..."}
    with patch("httpx.AsyncClient.patch") as mock_patch:
        mock_patch.return_value = AsyncMock(status_code=200)
        ok = await gw.update_deferred_response("token_str", "Standard string output")
        assert ok is True
        call_json = mock_patch.call_args[1]["json"]
        assert call_json == {"content": "Standard string output"}

    # 3. Long string response with safe line-boundary chunking fallback
    lines = [f"Line {i}: Details about operation {i} with some extra padding text" for i in range(100)]
    long_content = "\n".join(lines)
    assert len(long_content) > 2000

    with patch("httpx.AsyncClient.patch") as mock_patch:
        mock_patch.return_value = AsyncMock(status_code=200)
        ok = await gw.update_deferred_response("token_long", long_content)
        assert ok is True
        call_json = mock_patch.call_args[1]["json"]
        content_sent = call_json["content"]
        assert len(content_sent) <= 2000
        assert content_sent.endswith("... *(output truncated)*")
        # Line boundary check: before suffix, it should break cleanly after a newline
        prefix = content_sent.replace("\n\n... *(output truncated)*", "")
        assert not prefix.endswith("Line")  # Shouldn't cut mid-word or mid-line arbitrarily
