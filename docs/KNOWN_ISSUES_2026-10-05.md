# Known Issues — to fix on 2026-10-05

## Status summary

- **Live Jira comment test passed** on 2026-10-04 (21:46–21:47 PKT) at commit `cce1784`
  (branch `cursor/agent-core-phase1-db2e`).
  - Flow: Discord mention in #PMalerts → `PROPOSE_ACTION` → `AISafetyGate` → pending approval →
    `approve <id-prefix>` by a listed approver → `ActionEngine` execute → live re-read + verify.
  - Result: exactly one comment on **TREN-378**, Jira comment id **651445**, text
    `[PM Agent test] Automated comment posted via Discord approval flow. Safe to ignore.`
    Action row `COMPLETED`, `verified_comment_id=651445`, `params.source=ai`; no duplicate.
- **AI writes are off by default.** `AI_WRITE_ACTIONS_ENABLED=false` and `AI_APPROVER_DISCORD_IDS=""`
  (empty list refuses all AI approvals). The `.env` used for the live test has been restored;
  neither key is set and `SCHEDULER_ENABLED=true` is back.
- Full suite at `cce1784`: 1016 passed, 2 skipped, 0 failed.
- Development is paused; the items below are the backlog for the next session.

## Bugs / issues to fix

### 1. Test suite writes into the live database
- **Symptom:** `data/pm_operations.db` contains `DRY_RUN_SIMULATED` action rows on TREN-378
  (32 found on 2026-10-04) whose timestamps match full `pytest` runs.
- **Impact:** Pollutes production action/audit history; an identical action later could hit the
  idempotency path (a previously `DRY_RUN_SIMULATED` key is "upgraded to live execution").
- **Likely files:** `tests/conftest.py`, tests that use the global `action_engine` / `db_manager`
  (e.g. `app/core/qa/runner.py` paths, `tests/test_action_engine.py`, report tests),
  `app/database/connection.py`.
- **Suggested fix:** Autouse session fixture that points `db_manager` (and every module-level
  `ActionEngine`/repository) at a temp SQLite file; fail the run if the live DB path is opened
  during tests. Then delete the polluted rows (`status='DRY_RUN_SIMULATED' AND target_id='TREN-378'`
  created by test runs).

### 2. Duplicate-comment risk when verification fails
- **Symptom:** If Jira accepts the comment but the post-write re-read/verify fails (timeout,
  ADF mismatch), the AI action is marked `FAILED` even though the comment exists.
- **Impact:** A user re-requesting / retrying posts a second identical comment.
- **Likely files:** `app/core/actions/engine.py` (ADD_COMMENT verification block in `execute`),
  `app/connectors/discord/ai_discord_router.py` (failure reply).
- **Suggested fix:** Use a distinct status (e.g. `EXECUTED_UNVERIFIED`) when the POST succeeded
  but verification failed, and store the returned comment id. Before any retry/re-proposal for
  the same issue + body, check existing comments for a match (by id, then by ADF text) and
  short-circuit instead of posting again.

### 3. Router keeps/clears pending entries by matching error wording
- **Symptom:** `ai_discord_router.py` decides whether to keep a pending approval by substring
  matching `error_message` ("not authorized", "disabled by configuration", ...).
- **Impact:** Rewording an engine message silently changes behaviour (e.g. an auth refusal could
  start clearing the pending entry again).
- **Likely files:** `app/core/actions/engine.py` (`_check_ai_approval_policy`, `approve_action`),
  `app/core/actions/base.py` (`ActionResult`), `app/connectors/discord/ai_discord_router.py`.
- **Suggested fix:** Add a structured `error_code` to `ActionResult` (e.g. `NOT_AUTHORIZED`,
  `KILL_SWITCH`, `NO_APPROVERS`, `EXPIRED`, `EXECUTION_FAILED`) and branch on it; add tests per code.

### 4. Pending approvals are in memory only
- **Symptom:** `AI_PENDING_WRITE_ACTIONS` lives in process memory. After a restart the Discord
  side forgets them, while DB rows stay `PENDING_APPROVAL`; expiry is only applied lazily when
  someone tries to approve.
- **Impact:** Orphaned `PENDING_APPROVAL` rows; users cannot approve/reject after a restart.
- **Likely files:** `app/connectors/discord/ai_discord_router.py`, `app/core/actions/engine.py`,
  `app/database/repositories.py` (`ActionRepository`), `app/services/scheduler.py`.
- **Suggested fix:** Rebuild the pending store from the DB (`status='PENDING_APPROVAL' AND
  parameters.source='ai'`, plus channel/requester ids persisted with the action), and add a
  periodic sweep that marks rows older than `AI_APPROVAL_TTL_MINUTES` as `EXPIRED` (audited).

### 5. REST reject open for AI actions; REST routes unauthenticated
- **Symptom:** `POST /actions/{id}/reject` accepts AI-originated actions and trusts the
  `rejected_by` in the body; none of the `/actions` routes require authentication
  (approve/execute already return 403 for AI actions).
- **Impact:** Anyone who can reach the API can reject AI proposals or act on non-AI actions,
  with a spoofed actor in the audit log. Currently mitigated only by `HOST=127.0.0.1`.
- **Likely files:** `app/api/routes/actions.py`, `app/api/app.py`.
- **Suggested fix:** Return 403 (audited) for AI actions on `/reject` too, and add an auth
  dependency (API key or local token) to the actions router; derive the actor from auth, not the body.

### 6. Self-approval is allowed
- **Symptom:** A listed approver can approve a proposal they requested themselves.
- **Impact:** No separation of duties for AI writes.
- **Likely files:** `app/core/actions/engine.py` (`_check_ai_approval_policy`),
  `app/connectors/discord/ai_discord_router.py`.
- **Suggested fix:** Add a setting such as `AI_ALLOW_SELF_APPROVAL` (default `false` once more
  than one approver is configured) and refuse when `approved_by` equals the requester id, with an audit row.

### 7. Duplicate audit entries on the approval path
- **Symptom:** The live run logged `Approved` → `Validated` → `Approved` → `Executing` → `Success`
  (Validated and Approved appear twice across proposal + approval).
- **Impact:** Noisy/misleading audit trail.
- **Likely files:** `app/core/actions/engine.py` (`approve_action` audits "Approved", then
  `execute(approved=True)` re-validates and audits "Approved" again).
- **Suggested fix:** Skip the second "Approved" audit when the row is already `APPROVED`
  (check the persisted status, not only `action.status`), and don't re-audit "Validated" on the
  approved re-entry.

### 8. `DEBUG=true` auto-reload wipes pending approvals
- **Symptom:** `run.py` passes `reload=settings.DEBUG`; with `DEBUG=true` any file change under
  `D:\PM` restarts the server.
- **Impact:** In-memory pending approvals are lost mid-flow (compounds issue 4).
- **Likely files:** `run.py`, `app/config/settings.py`, `.env`.
- **Suggested fix:** Decouple reload from DEBUG (e.g. `RELOAD=false` by default), or exclude
  data/log paths from the watcher; fixing issue 4 removes the data-loss part.
