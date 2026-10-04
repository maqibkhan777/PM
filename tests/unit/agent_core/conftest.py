import pytest

from app.services.ai import pm_tools


@pytest.fixture(autouse=True)
def disable_live_jira_for_agent_core(monkeypatch):
    monkeypatch.setattr(type(pm_tools.settings), "is_jira_configured", lambda self: False, raising=False)
