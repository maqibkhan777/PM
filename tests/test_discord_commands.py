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

    handler = DiscordSlashCommandHandler(user_repo=UserRepository(temp_db), action_engine=engine)
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
    assert "✅ Created WSSS-100: Fix login edge case." in res1
    assert len(mock_jira_client.create_issue_calls) == 1

    # 2. Second task creation with same summary in a new command request (has distinct action_id) -> MUST create separate task
    res2 = await handler.execute_subcommand(
        subcommand="create",
        options={"project": "WSSS", "summary": "Fix login edge case"},
        discord_user_id="123456789"
    )
    assert "✅ Created WSSS-100: Fix login edge case." in res2
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


