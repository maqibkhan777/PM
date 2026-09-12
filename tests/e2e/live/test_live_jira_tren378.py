"""Live E2E Pytest Suite against Primary Live Jira Fixture TREN-378.

This test suite executes against real Jira Cloud, Discord, and Mattermost only when
LIVE_QA_ENABLED=true is explicitly configured in the environment.
"""

import pytest
from app.config.settings import settings
from app.core.qa.models import TestTier, QAStatus
from app.core.qa.runner import QARunner


@pytest.mark.skipif(
    not settings.LIVE_QA_ENABLED,
    reason="Live QA is disabled. Set LIVE_QA_ENABLED=true to run live E2E mutation tests.",
)
def test_live_jira_tren378_full_e2e_suite():
    """Execute the authoritative live QA suite against TREN-378."""
    # Ensure safe fixture
    settings.assert_live_qa_safe(target_issue="TREN-378")

    runner = QARunner(tier=TestTier.LIVE_E2E, target_issue="TREN-378")
    summary = runner.run_all_scenarios()

    assert summary.total_tests >= 18
    assert summary.failed == 0
    assert summary.pass_rate_percent == 100.0


def test_live_qa_safeguard_blocks_arbitrary_production_ticket():
    """Verify that even when LIVE_QA_ENABLED is true, mutations on non-QA tickets are blocked."""
    enabled_settings = settings.model_copy(update={"LIVE_QA_ENABLED": True, "LIVE_QA_JIRA_ISSUE": "TREN-378"})
    with pytest.raises(ValueError, match="does not match configured safe fixture"):
        enabled_settings.assert_live_qa_safe(target_issue="PROD-999")
