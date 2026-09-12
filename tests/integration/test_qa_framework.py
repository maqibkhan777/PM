"""Integration tests for the Live QA Framework, Tier Classification, and Integrity Gates."""

import json
import os
import pytest
from app.config.settings import settings
from app.core.qa.models import QAStatus, TestTier
from app.core.qa.runner import QARunner


def test_qa_runner_full_dry_run_pipeline():
    """Verify QARunner executes all QA scenarios cleanly in DRY_RUN mode with honest classification."""
    runner = QARunner(tier=TestTier.DRY_RUN_E2E, target_issue="TREN-378")
    summary = runner.run_all_scenarios()

    assert summary.total_tests >= 13
    assert summary.failed == 0
    assert summary.invalid_live_tests == 0
    assert summary.not_executed == 0
    assert summary.pass_rate_percent == 100.0
    assert summary.tier == TestTier.DRY_RUN_E2E
    assert summary.target_issue == "TREN-378"
    assert summary.genuinely_live_count == 0  # In DRY_RUN mode, zero tests are genuinely live
    assert summary.simulated_count == summary.total_tests

    # Verify structured evidence files were written and redacted
    for res in summary.results:
        assert res.status == QAStatus.PASS
        assert res.test_id.startswith("RT-")
        if res.evidence_file:
            abs_path = os.path.abspath(res.evidence_file)
            assert os.path.exists(abs_path)
            with open(abs_path, "r", encoding="utf-8") as f:
                evidence_data = json.load(f)
            assert evidence_data["test_id"] == res.test_id
            # Verify secrets are scrubbed
            dumped_str = json.dumps(evidence_data)
            assert "ATATT3x" not in dumped_str
            assert "placeholder_token" not in dumped_str
            assert "secret_jira_token" not in dumped_str


def test_qa_runner_dry_run_safety_scenario():
    """Verify DRY_RUN scenario produces simulation preview without remote mutation."""
    runner = QARunner(tier=TestTier.DRY_RUN_E2E, target_issue="TREN-378")
    dry_results = runner.run_all_scenarios()
    dry_e2e = [r for r in dry_results.results if r.test_tier == TestTier.DRY_RUN_E2E]

    assert len(dry_e2e) >= 1
    for r in dry_e2e:
        assert r.status == QAStatus.PASS
        assert r.test_id == "RT-DRY-001"
        assert r.execution_mode.get("database") == "REAL"
        assert r.execution_mode.get("jira") == "NOT_USED"


def test_qa_runner_integration_scenarios():
    """Verify integration tier scenarios test rules, deduplication, and identity safety."""
    runner = QARunner(tier=TestTier.INTEGRATION, target_issue="TREN-378")
    results = runner.run_integration_scenarios()

    assert len(results) >= 9
    test_ids = [r.test_id for r in results]
    assert "RT-INT-VIOL-001" in test_ids
    assert "RT-INT-DEDUP-001" in test_ids
    assert "RT-INT-STALE-001" in test_ids
    assert "RT-INT-MM-001" in test_ids
    assert "RT-INT-BLCK-001" in test_ids
    assert "RT-INT-REOP-001" in test_ids
    assert "RT-INT-FAIL-001" in test_ids
    assert "RT-INT-SCHED-001" in test_ids
    assert "RT-INT-PERF-001" in test_ids

    for r in results:
        assert r.status == QAStatus.PASS
        assert r.test_tier == TestTier.INTEGRATION


@pytest.mark.asyncio
async def test_qa_runner_live_e2e_integrity_gate_when_disabled():
    """Verify that LIVE_E2E tests report NOT_EXECUTED when LIVE_QA_ENABLED is False (Never a false PASS)."""
    orig_enabled = settings.LIVE_QA_ENABLED
    settings.LIVE_QA_ENABLED = False
    try:
        runner = QARunner(tier=TestTier.LIVE_E2E, target_issue="TREN-378")
        res = await runner.run_live_assignment_scenario("target_123", "Test User", "RT-LIVE-ASSIGN-TEST")

        assert res.status == QAStatus.NOT_EXECUTED
        assert res.test_tier == TestTier.LIVE_E2E
        assert "LIVE_QA_ENABLED" in (res.failure_reason or "")
        assert not res.is_genuinely_live()
    finally:
        settings.LIVE_QA_ENABLED = orig_enabled


@pytest.mark.asyncio
async def test_qa_runner_live_e2e_safeguard_blocks_invalid_tickets():
    """Verify that LIVE_E2E tests report INVALID_LIVE_TEST on unapproved tickets."""
    orig_enabled = settings.LIVE_QA_ENABLED
    settings.LIVE_QA_ENABLED = True
    try:
        runner = QARunner(tier=TestTier.LIVE_E2E, target_issue="UNAPPROVED-999")
        res = await runner.run_live_assignment_scenario("target_123", "Test User", "RT-LIVE-ASSIGN-TEST")

        assert res.status == QAStatus.INVALID_LIVE_TEST
        assert res.test_tier == TestTier.LIVE_E2E
        assert "Safeguard violation" in res.actual
        assert not res.is_genuinely_live()
    finally:
        settings.LIVE_QA_ENABLED = orig_enabled

