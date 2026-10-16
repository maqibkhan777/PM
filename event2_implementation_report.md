# Event 2 Implementation Report: Jira Comment Direction → Mattermost Notification

**Date:** 2026-10-15  
**Component:** PM Operations Agent (Event 2 Subsystem)  
**Status:** IMPLEMENTED, FULLY VERIFIED, INACTIVE (Baseline Safe State)  

---

## 1. Architecture Implemented

Event 2 monitors incoming Jira comments and dispatches a concise, direct Mattermost notification containing the Jira comment link when a comment begins with a directed user mention.

The implementation strictly follows the established architectural layers of the PM Operations Agent without creating parallel pipelines, duplicate user mapping registries, or hardcoded identifiers:
- **Connector / Poller Layer (`app/connectors/jira/poller.py` & `app/connectors/jira/normalizer.py`):** Normalizes comments and creates `TaskCommentAdded` events preserving issue fields, current assignee, and raw comment structures.
- **Service Layer (`app/services/jira_comment_notification_service.py`):** Encapsulates leading mention parsing, assignee resolution, direct comment URL generation, deterministic idempotency checks, and Action construction.
- **User Mapping Subsystem (`app/services/user_mapping_service.py`):** Authoritatively resolves Jira account IDs to verified Mattermost user IDs using the existing `UserMappingRepository`.
- **Execution & Guard Layer (`app/core/actions/engine.py` & `app/connectors/mattermost/connector.py`):** Uses fine-grained event-scoped authorization (`EventScope.JIRA_COMMENT_NOTIFICATION`) while leaving the global self-only testing guard (`MATTERMOST_SELF_ONLY_USER_ID`) intact for unscoped/generic actions.
- **Persistence & Idempotency Layer (`app/database/schema.py` & `app/database/repositories.py`):** Maintains the `event2_notifications` table to guarantee that each Jira comment produces at most one notification.

---

## 2. Jira Comment Detection Mechanism

Jira comments are ingested via:
1. `JiraPoller.poll_updates()`: Identifies new comments embedded in Jira issue responses since the previous polling checkpoint and emits `TaskCommentAdded`.
2. `JiraWebhookHandler`: Ingests real-time Jira `comment_created` webhooks and normalizes them to `TaskCommentAdded`.
3. Embedded payloads include:
   - `comment_id`: Jira comment ID
   - `task_key`: Jira issue key (e.g., `ZBS-25`)
   - `task_id`: Jira issue ID
   - `author_id`: Jira account ID of the commenter
   - `comment_body`: Rendered text representation
   - `payload`: Contains raw comment structure (including ADF JSON) and issue `fields` containing the current issue `assignee`.

---

## 3. Leading-Tag Extraction Logic

The trigger condition requires that the **first meaningful content** of the comment is a valid structured Jira user mention.

`JiraCommentNotificationService.extract_leading_mention(doc_or_text)` handles both:
1. **Atlassian Document Format (ADF):**
   - Inspects the first block node in `content`.
   - Skips leading empty or whitespace-only text nodes.
   - If the first non-whitespace inline node is of `type: "mention"`, extracts `attrs["id"]` and `attrs["text"]`.
   - If any text node with non-whitespace content appears before the mention node, leading mention detection returns `None`.
2. **Plain Text and Jira Wiki Markup:**
   - Normalizes harmless leading whitespace (`lstrip()`).
   - Evaluates leading patterns:
     - `^\[~accountid:([^\]|]+)(?:\|([^\]]+))?\]` (structured Jira account ID)
     - `^\[~([^\]|]+)(?:\|([^\]]+))?\]` (Jira wiki markup user mention)
     - `^@([a-zA-Z0-9_\-:\.]+)` (text mention tag)
   - If text appears before the mention (e.g., `"Please investigate this. @Mubashir"` or `"FYI @Mubashir"`), the parser returns `None`.

---

## 4. Assignee Resolution Logic

Once a leading user mention is detected:
1. The service extracts the current Jira issue assignee from the issue fields.
2. If the issue is unassigned (`assignee is None`), the service fails closed with `NO_NOTIFICATION`.
3. If the assignee is inactive in Jira (`active == False`), the service fails closed with `NO_NOTIFICATION`.
4. The assignee is resolved via `UserMappingService.resolve_jira_to_mattermost()`:
   - Priority 1: Explicit DB mapping in `user_mappings` (`EXPLICIT_MAPPING`).
   - Priority 2: Verified exact email match in `users` (`EXACT_EMAIL_MATCH`).
   - Priority 3: Unique exact display name match in `users` (`EXACT_NAME_MATCH`).
5. If the assignee cannot be mapped to Mattermost or is ambiguous, the service fails closed with `NO_NOTIFICATION`.
6. Identities are never guessed from display names when account IDs are available.

---

## 5. Final Recipient-Selection Rule

The core invariant is strictly enforced:
> **The first valid Jira user tag at the beginning of the comment is the trigger. The current Jira issue assignee is the sole Mattermost recipient.**

```text
Jira comment
      ↓
Leading structured user mention?
      ↓ YES
Resolve current Jira issue assignee
      ↓ YES
Resolve assignee through UserMappingService
      ↓ YES
Verified Mattermost user ID
      ↓
ONE Mattermost DM to Assignee
```

- The first tagged user does **not** receive a notification unless they are also the current assignee.
- There is **never** more than one Mattermost recipient per comment.
- No broadcast to all tagged users.

---

## 6. CC Handling

Later user mentions in the comment are treated strictly as CC/context:
- CC mentions never trigger notifications independently.
- CC mentions are never used as fallback recipients.
- CC mentions do not receive Mattermost messages.
- CC mentions are omitted from the Mattermost DM body to keep messages concise.

---

## 7. Jira Comment URL Generation

Direct comment links are generated deterministically:
```python
base_url = settings.JIRA_BASE_URL.rstrip('/')
comment_url = f"{base_url}/browse/{task_key}?focusedCommentId={comment_id}#comment-{comment_id}"
```
Example:
`https://objectsws.atlassian.net/browse/ZBS-25?focusedCommentId=653371#comment-653371`

Fails closed if `comment_id` or `task_key` is missing.

---

## 8. Mattermost Mapping Resolution

Mattermost accounts are resolved using `UserMappingService`:
- Queries the existing `user_mappings` SQLite table.
- Validates that the mapping record has `active = 1`.
- Provides the recipient's verified Mattermost User ID (`mattermost_user_id`).

---

## 9. Event-Scoped Authorization

To allow delivery to the verified assignee while maintaining the global self-only restriction for general testing:
1. Added `EventScope.JIRA_COMMENT_NOTIFICATION = "JIRA_COMMENT_NOTIFICATION"` to `app/core/models/enums.py`.
2. Updated `ActionEngine` (`app/core/actions/engine.py`):
   - When `event_scope == "JIRA_COMMENT_NOTIFICATION"`, validates that the target user is an active mapped Mattermost recipient in `user_mappings`.
   - Authorizes execution for the verified assignee.
3. Updated `MattermostConnector` (`app/connectors/mattermost/connector.py`):
   - Permits DM dispatch when `event_scope == "JIRA_COMMENT_NOTIFICATION"`.
4. Global self-only protection (`MATTERMOST_SELF_ONLY_USER_ID`) remains enforced for all unscoped/general actions.
5. Mattermost channel posting remains unconditionally blocked.

---

## 10. Idempotency Implementation

Duplicate prevention is enforced at two distinct boundaries:
1. **Idempotency Key Generation:**
   ```python
   raw_key = f"EVENT2:{jira_comment_id}"
   idempotency_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:16]
   ```
2. **Database Record (`event2_notifications`):**
   - Unique constraints on `comment_id` and `idempotency_key`.
   - Atomic check before processing: if `comment_id` or `idempotency_key` exists, skips execution with `ALREADY_PROCESSED`.
   - Prevents duplicate delivery during polling overlaps, scheduler cycles, or application restarts.

---

## 11. Fail-Closed Conditions

A Mattermost notification is **never** sent under any of the following conditions:
- No leading user mention in the comment.
- Mention appears later in comment (e.g., text precedes mention).
- Comment cannot be parsed or comment ID is missing.
- Issue has no assignee (Unassigned).
- Assignee Jira account ID cannot be resolved.
- Assignee is marked inactive in Jira.
- Assignee has no verified Mattermost mapping.
- Assignee mapping is ambiguous.
- Comment URL cannot be generated reliably.
- Comment was already processed (idempotency match).

---

## 12. Dry-Run Simulation Results

Execution of `simulate_event2.py` against the production schema produced the following results:

```text
=== DRY-RUN EVENT 2 SIMULATIONS ===
--- Scenario: Mandatory Case B (First tag differs from assignee) ---
comment_id: 653371
task_key: ZBS-25
leading_tagged_user: Mubashir
leading_account_id: None
assignee: Ahsan Iftikhar
assignee_account_id: 712020:1b564792-a3ab-447c-951d-17aa5507b946
all_mentions: ['Mubashir', 'Ahsan', 'Talha']
selected_mattermost_recipient: Ahsan Iftikhar
mattermost_user_id: fpf9jhpde3na5b1bj971199p1h
comment_url: https://objectsws.atlassian.net/browse/ZBS-25?focusedCommentId=653371#comment-653371
idempotency_key: 93d8455be4bdd9b8
decision: SEND
prepared_message:
You have been tagged in a Jira comment.

https://objectsws.atlassian.net/browse/ZBS-25?focusedCommentId=653371#comment-653371

--- Scenario: Case A (Normal: First tag is assignee) ---
comment_id: 653372
task_key: ZBS-25
leading_tagged_user: Mubashir
leading_account_id: None
assignee: Mubashir Butt
assignee_account_id: 712020:e268bcd8-d981-4b4d-992d-d5694745df8b
all_mentions: []
selected_mattermost_recipient: Mubashir Butt
mattermost_user_id: e956k4q3tpbfteu4t7sanb914r
comment_url: https://objectsws.atlassian.net/browse/ZBS-25?focusedCommentId=653372#comment-653372
idempotency_key: ab79f3bdc5aad0aa
decision: SEND
prepared_message:
You have been tagged in a Jira comment.

https://objectsws.atlassian.net/browse/ZBS-25?focusedCommentId=653372#comment-653372

--- Scenario: Case E (No leading tag) ---
comment_id: 653373
task_key: ZBS-25
leading_tagged_user: None
leading_account_id: None
assignee: None
assignee_account_id: None
all_mentions: []
selected_mattermost_recipient: None
mattermost_user_id: None
comment_url: https://objectsws.atlassian.net/browse/ZBS-25?focusedCommentId=653373#comment-653373
idempotency_key: 082a61532371a8b2
decision: NO_NOTIFICATION
prepared_message: None

--- Scenario: Case F (Unassigned ticket) ---
comment_id: 653374
task_key: ZBS-25
leading_tagged_user: Mubashir
leading_account_id: None
assignee: None
assignee_account_id: None
all_mentions: []
selected_mattermost_recipient: None
mattermost_user_id: None
comment_url: https://objectsws.atlassian.net/browse/ZBS-25?focusedCommentId=653374#comment-653374
idempotency_key: ceea6d4bfe07cfa2
decision: NO_NOTIFICATION
prepared_message: None

--- Scenario: Case G (Unmapped assignee) ---
comment_id: 653375
task_key: ZBS-25
leading_tagged_user: Mubashir
leading_account_id: None
assignee: None
assignee_account_id: None
all_mentions: []
selected_mattermost_recipient: None
mattermost_user_id: None
comment_url: https://objectsws.atlassian.net/browse/ZBS-25?focusedCommentId=653375#comment-653375
idempotency_key: 55f3bdb42a053a3a
decision: NO_NOTIFICATION
prepared_message: None
```

---

## 13. Mandatory Example Result

```text
Comment:
@Mubashir Please investigate this issue. @Ahsan @Talha

Assignee:
Ahsan
```

**Evaluation Result:**
- **Trigger:** YES
- **Leading Tag:** Mubashir
- **Assignee:** Ahsan Iftikhar
- **Selected Mattermost Recipient:** Ahsan Iftikhar
- **Mattermost Recipient ID:** `fpf9jhpde3na5b1bj971199p1h`
- **Mubashir Notified:** NO (0 messages)
- **Talha Notified:** NO (0 messages)
- **Message Content:**
  ```text
  You have been tagged in a Jira comment.

  https://objectsws.atlassian.net/browse/ZBS-25?focusedCommentId=653371#comment-653371
  ```

---

## 14. Test Results

### Event 2 Dedicated Test Suite (`tests/test_event2_comment_notification.py`):
```text
tests/test_event2_comment_notification.py::test_tc1_valid_leading_user_tag_triggers PASSED
tests/test_event2_comment_notification.py::test_tc2_leading_whitespace_before_tag_triggers PASSED
tests/test_event2_comment_notification.py::test_tc3_no_leading_tag_no_notification PASSED
tests/test_event2_comment_notification.py::test_tc4_tag_appearing_later_in_comment_no_notification PASSED
tests/test_event2_comment_notification.py::test_tc5_structured_jira_adf_mention_recognized PASSED
tests/test_event2_comment_notification.py::test_tc5b_structured_adf_with_leading_whitespace_text_node PASSED
tests/test_event2_comment_notification.py::test_tc5c_structured_adf_with_text_before_mention_rejected PASSED
tests/test_event2_comment_notification.py::test_tc6_invalid_empty_mention_fails_closed PASSED
tests/test_event2_comment_notification.py::test_tc7_first_tag_equals_assignee PASSED
tests/test_event2_comment_notification.py::test_tc8_first_tag_differs_from_assignee_mandatory_example PASSED
tests/test_event2_comment_notification.py::test_tc9_multiple_later_cc_mentions PASSED
tests/test_event2_comment_notification.py::test_tc10_assignee_differs_from_every_tagged_user PASSED
tests/test_event2_comment_notification.py::test_tc11_no_assignee_no_notification PASSED
tests/test_event2_comment_notification.py::test_tc12_assignee_has_no_mattermost_mapping_fails_closed PASSED
tests/test_event2_comment_notification.py::test_tc13_assignee_inactive_fails_closed PASSED
tests/test_event2_comment_notification.py::test_tc14_generic_mattermost_action_remains_protected PASSED
tests/test_event2_comment_notification.py::test_tc15_event2_scoped_action_authorizes_verified_assignee PASSED
tests/test_event2_comment_notification.py::test_tc16_channel_posting_remains_blocked PASSED
tests/test_event2_comment_notification.py::test_tc19_same_comment_processed_twice_produces_one_notification PASSED
tests/test_event2_comment_notification.py::test_tc20_overlapping_polling_windows_no_duplicate PASSED
tests/test_event2_comment_notification.py::test_tc23_correct_direct_jira_comment_url_generated PASSED
tests/test_event2_comment_notification.py::test_tc25_missing_comment_id_fails_closed PASSED
tests/test_event2_comment_notification.py::test_mandatory_example_1 PASSED
tests/test_event2_comment_notification.py::test_mandatory_example_2 PASSED
tests/test_event2_comment_notification.py::test_mandatory_example_3 PASSED

Event 2 tests:
Passed: 25
Failed: 0
Skipped: 0
```

---

## 15. Regression Results

### Event 1 & Core Components Regression Suite:
```text
pytest tests/test_event1_implementation.py \
       tests/test_event1_discord_approval.py \
       tests/test_event1_decision_logic.py \
       tests/test_mattermost_self_only_guard.py \
       tests/test_connectors.py \
       tests/test_user_mapping.py \
       tests/test_scheduler.py \
       tests/test_action_engine.py -q

Event 1 regression:
Passed: 89
Failed: 0
Skipped: 0
```

### Full Repository Regression Suite:
```text
pytest -q

Full repository:
Passed: 635
Failed: 0
Skipped: 1 (pre-existing test marked skip)
```

---

## 16. Safety-State Confirmation

The system remains in its safe, inactive baseline state:
- `DRY_RUN`: `True`
- `SCHEDULER_ENABLED`: `False`
- `JIRA_POLLING_ENABLED`: `False`
- `EVENT1_ENABLED`: `False`
- `EVENT2_ENABLED`: `False`
- `MATTERMOST_SELF_ONLY_USER_ID`: Intact (`yp7quu37tf8p5enxr9rr8gsqay`)
- Live Mattermost message delivery: **0 live messages sent during implementation or testing**.

---

## 17. Remaining Blockers

- **None.** Event 2 implementation is complete, functionally verified, fully tested, and ready for future authorized activation.
