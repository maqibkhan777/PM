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
