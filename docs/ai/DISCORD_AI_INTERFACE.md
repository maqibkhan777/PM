# Dedicated PM AI Discord Mention Interface — Phase 1 Documentation

## Overview

The **Dedicated PM AI Discord Mention Interface** provides an isolated, read-only conversational assistant within Discord for the PM Operations Agent. Authorized team members can mention (`@PM AI`) or directly reply to the dedicated AI Bot in approved test channels to receive AI-powered project management decision support, attention analysis, and advisory planning proposals.

---

## Safety & Architectural Invariants

1. **Read-Only Advisory Operation:**
   - The AI interface produces read-only insights and planning proposals.
   - It **NEVER** mutates Jira issues (no status transitions, no due date modifications, no assignments, no comments, no worklogs).
   - It **NEVER** invokes the Action Engine or approves/executes planning proposals.
2. **Channel & User Isolation:**
   - The bot responds **only** in explicitly allowlisted test channels (`DISCORD_AI_ALLOWED_CHANNEL_IDS`).
   - The bot accepts queries **only** from explicitly allowlisted Discord users (`DISCORD_AI_ALLOWED_USER_IDS`).
   - All unapproved channels and unapproved users fail closed.
3. **Explicit Mention Requirement:**
   - The bot responds only when explicitly mentioned (`<@BOT_ID>` or `<@!BOT_ID>`) or directly replied to.
   - Normal channel chatter and messages without mentions are completely ignored.
   - Bot messages (including messages from the primary PM operations bot) are ignored to prevent recursive loops.
4. **Idempotency & Deduplication:**
   - Incoming Discord message IDs are tracked in an in-memory TTL cache to prevent duplicate processing.
5. **Provider Agnostic:**
   - Dynamically resolves configured AI providers (`mock`, `null`, `deepseek`) through `resolve_ai_provider` without hardcoded provider bindings.
6. **Zero Impact on Production Commands:**
   - Existing `/pm` slash commands, reports, and production notifications remain completely untouched.

---

## Discord Application Setup & Permissions

To set up the separate PM AI Discord Bot:

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications).
2. Click **New Application** and name it (e.g. `PM AI Assistant`).
3. Navigate to the **Bot** tab:
   - Click **Add Bot** / **Reset Token** to generate a Bot Token.
   - Enable Privileged Gateway Intents:
     - **Message Content Intent** (Required to read mentions and user queries).
4. Navigate to **OAuth2 -> URL Generator**:
   - Scopes: `bot`
   - Bot Permissions:
     - `Read Messages/View Channels`
     - `Send Messages`
     - `Send Messages in Threads`
     - `Embed Links`
     - `Read Message History`
5. Copy the generated OAuth2 URL and authorize the bot into your private Discord test server.

---

## Environment Variables Configuration

Add the following configuration parameters to your `.env` file:

```env
# ------------------------------------------------------------------------------
# Dedicated AI Discord Mention Interface (Separate Bot — Phase 1)
# ------------------------------------------------------------------------------
# Master switch (default: false)
DISCORD_AI_BOT_ENABLED=false

# Dedicated Bot Credentials
DISCORD_AI_BOT_TOKEN=your_dedicated_ai_bot_token_here
DISCORD_AI_APPLICATION_ID=your_dedicated_ai_application_id_here

# Channel Allowlist: Comma-separated Discord channel Snowflake IDs
DISCORD_AI_ALLOWED_CHANNEL_IDS=123456789012345678,987654321098765432

# User Allowlist: Comma-separated Discord user Snowflake IDs
DISCORD_AI_ALLOWED_USER_IDS=112233445566778899,223344556677889900
```

---

## Enabling & Testing Locally

1. Set `DISCORD_AI_BOT_ENABLED=true` in your `.env`.
2. Ensure `DISCORD_AI_ALLOWED_CHANNEL_IDS` matches your test Discord channel ID.
3. Ensure `DISCORD_AI_ALLOWED_USER_IDS` contains your Discord numeric user ID.
4. Start the application:
   ```bash
   uvicorn app.main:app --reload
   ```
5. In your authorized Discord channel, mention the bot:
   - `@PM AI help`
   - `@PM AI what tasks need attention right now?`
   - `@PM AI propose a schedule plan for the team`
   - `@PM AI what is the status of WSSS-326?`

---

## Emergency Kill Switch

To immediately disable the AI Discord Mention Interface:

1. Set `DISCORD_AI_BOT_ENABLED=false` in `.env` or clear `DISCORD_AI_BOT_TOKEN`.
2. Restart the PM Operations Agent service or container.
