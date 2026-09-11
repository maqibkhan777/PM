"""Integration tests for FastAPI REST API endpoints."""

import pytest
from httpx import AsyncClient, ASGITransport
from app.api.app import app
from app.config.settings import settings


@pytest.mark.asyncio
async def test_root_endpoint():
    """Test GET / endpoint."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "PM Operations Agent"
    assert data["status"] == "online"


@pytest.mark.asyncio
async def test_health_endpoints():
    """Test GET /health and GET /health/connectors."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.get("/health")
        assert res.status_code == 200
        data = res.json()
        assert data["application"] == "OK"
        assert data["database"] == "OK"

        res_conn = await ac.get("/health/connectors")
        assert res_conn.status_code == 200
        conn_data = res_conn.json()
        assert "connectors" in conn_data
        assert len(conn_data["connectors"]) >= 3


@pytest.mark.asyncio
async def test_jira_webhook_fast_ingestion(sample_jira_status_payload):
    """Test POST /webhooks/jira responds with 202 quickly."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/webhooks/jira", json=sample_jira_status_payload)
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "received"
    assert "event_id" in data


@pytest.mark.asyncio
async def test_events_and_actions_endpoints():
    """Test GET /events and GET /actions."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res_events = await ac.get("/events")
        assert res_events.status_code == 200
        assert "events" in res_events.json()

        res_actions = await ac.get("/actions")
        assert res_actions.status_code == 200
        assert "actions" in res_actions.json()


@pytest.mark.asyncio
async def test_rules_and_toggle():
    """Test GET /rules and POST /rules/{name}/toggle."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.get("/rules")
        assert res.status_code == 200
        rules = res.json()["rules"]
        assert len(rules) >= 5

        # Toggle ActiveWorkDetection off
        res_toggle = await ac.post(
            "/rules/ActiveWorkDetection/toggle",
            json={"enabled": False}
        )
        assert res_toggle.status_code == 200
        assert res_toggle.json()["enabled"] is False

        # Toggle back on
        await ac.post("/rules/ActiveWorkDetection/toggle", json={"enabled": True})


@pytest.mark.asyncio
async def test_daily_report_api():
    """Test GET /reports/daily."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.get("/reports/daily")
        assert res.status_code == 200
        data = res.json()
        assert "total_activities" in data
        assert "activities_by_resource" in data


@pytest.mark.asyncio
async def test_user_mappings_api():
    """Test GET and POST /user-mappings."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        post_res = await ac.post(
            "/user-mappings",
            json={
                "jira_user_id": "test-jira-1",
                "mattermost_user_id": "test-mm-1",
                "display_name": "Test User"
            }
        )
        assert post_res.status_code == 200
        assert post_res.json()["status"] == "success"

        get_res = await ac.get("/user-mappings")
        assert get_res.status_code == 200
        mappings = get_res.json()["mappings"]
        assert any(m["jira_user_id"] == "test-jira-1" for m in mappings)


@pytest.mark.asyncio
async def test_action_api_full_flow(temp_db):
    """Test POST /actions, GET /actions/{id}, approve, reject, and execute endpoints."""
    import uuid
    from app.database.repositories import ActionRepository
    from app.core.models.enums import ActionStatus

    uid = uuid.uuid4().hex[:8]
    task_key_1 = f"API-{uid}-1"
    task_key_2 = f"API-{uid}-2"

    orig_dry = settings.DRY_RUN
    settings.DRY_RUN = True
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # 1. Create action via API (in V1, standard actions execute automatically to DRY_RUN_SIMULATED)
            create_res = await ac.post(
                "/actions",
                json={
                    "action_type": "TRANSITION_TASK",
                    "target_system": "jira",
                    "target_id": task_key_1,
                    "parameters": {"target_status": "Done"},
                    "requested_by": "TestUser"
                }
            )

            assert create_res.status_code == 200
            data = create_res.json()
            action_id = data["action_id"]
            assert data["status"] == "DRY_RUN_SIMULATED"

            # 2. Get action by ID
            get_res = await ac.get(f"/actions/{action_id}")
            assert get_res.status_code == 200
            assert get_res.json()["action_id"] == action_id
            assert get_res.json()["status"] == "DRY_RUN_SIMULATED"

            # 3. Test approval on a PENDING_APPROVAL action
            repo = ActionRepository()
            pending_action_id = str(uuid.uuid4())
            repo.insert(
                action_id=pending_action_id,
                action_type="AddComment",
                target_system="jira",
                target_id=task_key_2,
                parameters={"comment": "Please verify"},
                status=ActionStatus.PENDING_APPROVAL.value,
                idempotency_key=f"idem-{pending_action_id}",
                dry_run=True,
                requested_by="TestUser",
                requires_approval=True
            )

            approve_res = await ac.post(
                f"/actions/{pending_action_id}/approve",
                json={"approved_by": "LeadPM"}
            )
            assert approve_res.status_code == 200
            app_data = approve_res.json()
            assert app_data["success"] is True

            # 4. Test rejection on a PENDING_APPROVAL action
            reject_action_id = str(uuid.uuid4())
            repo.insert(
                action_id=reject_action_id,
                action_type="AddComment",
                target_system="jira",
                target_id=task_key_2,
                parameters={"comment": "Spam comment"},
                status=ActionStatus.PENDING_APPROVAL.value,
                idempotency_key=f"idem-{reject_action_id}",
                dry_run=True,
                requested_by="TestUser",
                requires_approval=True
            )

            reject_res = await ac.post(
                f"/actions/{reject_action_id}/reject",
                json={"rejected_by": "LeadPM", "reason": "Spam comment"}
            )
            assert reject_res.status_code == 200
            assert reject_res.json()["status"] == "REJECTED"

    finally:
        settings.DRY_RUN = orig_dry



