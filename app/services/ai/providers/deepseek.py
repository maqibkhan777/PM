"""DeepSeek AI provider adapter implementing the AIProvider protocol.

Supports structured OpenAI-compatible chat completions API (https://api.deepseek.com),
enforcing bounded context limits, strict JSON schema validation, fail-closed error handling,
bounded retries on transient errors, and strict credential redaction.
"""

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional
import httpx
from pydantic import ValidationError

from app.services.ai.models import (
    AIContext,
    AIDecision,
    AIDecisionType,
    AIRecommendationType,
    AttentionItemAnalysis,
    PMAttentionAnalysis,
    ProposedAction,
)
from app.utils.logger import redact_text, sanitize_dict

logger = logging.getLogger(__name__)


class DeepSeekProviderError(Exception):
    """Raised when DeepSeek API interaction fails, with guarantees that secrets are redacted."""
    pass


AI_PROMPT_VERSION = "attention-v1"
AI_DECISION_PROMPT_VERSION = "decision-v1"

SYSTEM_PROMPT_DECISION = """You are an expert Project Management Decision Support Assistant.
Analyze the supplied bounded context regarding Jira tasks, resources, and project metrics.
You MUST output a valid JSON object matching this schema:
{
  "decision_type": "PM_ATTENTION" | "TASK_ASSESSMENT" | "WORKLOG_ANOMALY" | "SCHEDULE_FORECAST" | "PERFORMANCE_INSIGHT" | "GENERAL_ANALYSIS",
  "recommendation": "NO_ACTION" | "REVIEW_TASK" | "NOTIFY_PM" | "FOLLOW_UP" | "PROPOSE_COMMENT" | "PROPOSE_TRANSITION",
  "confidence": <float between 0.0 and 1.0>,
  "evidence": [<string>, ...],
  "explanation": "<string explaining reasoning and tradeoffs>",
  "proposed_action": null or {
    "action_type": "<allowed action type, e.g. SEND_MESSAGE or ADD_COMMENT>",
    "target_system": "<e.g. jira or discord>",
    "target_id": "<target id or key>",
    "parameters": {},
    "rationale": "<string>"
  },
  "requires_approval": true
}
IMPORTANT:
- Output ONLY valid JSON, no markdown code block fences, no extraneous conversational text.
- All proposed actions must have requires_approval: true (AI is strictly advisory).
- Base your decision strictly on the facts and metrics provided in the context. Do not invent facts or external entities.
"""

SYSTEM_PROMPT_ATTENTION = """You are an expert Project Management AI specialized in evaluating operational attention signals.
Analyze the supplied bounded context of stale tasks, overdue items, reopened work, and unassigned tickets.
You MUST output a valid JSON object matching this schema:
{
  "analysis_id": "<string unique identifier, e.g. analysis-deepseek-...>",
  "generated_at": "<ISO8601 timestamp string>",
  "scope_team": "<team group or cluster name>",
  "summary": "<clear executive summary of flagged items and operational risk>",
  "attention_items": [
    {
      "issue_key": "<Jira issue key>",
      "title": "<Task title/summary>",
      "current_status": "<Jira status>",
      "assignee": "<Assignee name or null>",
      "priority": "<Priority or null>",
      "due_date": "<Due date or null>",
      "updated_at": "<Last updated timestamp or null>",
      "inactivity_duration": "<Duration string or null>",
      "attention_reason": "<Why this item requires attention>",
      "supporting_evidence": ["<evidence bullet 1>", ...],
      "recommendation": "<Actionable recommendation for PM or assignee>",
      "confidence": <float between 0.0 and 1.0>,
      "uncertainty_or_missing_info": "<string or null>",
      "proposed_action": null or {
        "action_type": "<allowed action type>",
        "target_system": "<e.g. jira or discord>",
        "target_id": "<target id>",
        "parameters": {},
        "rationale": "<string>"
      }
    }
  ],
  "evidence": ["<supporting context evidence 1>", ...],
  "recommendation": "<Consolidated high-level recommendation>",
  "confidence": <float between 0.0 and 1.0>,
  "uncertainty_or_missing_info": "<string or null>",
  "proposed_action": null or {
    "action_type": "<allowed action type>",
    "target_system": "<target system>",
    "target_id": "<target id>",
    "parameters": {},
    "rationale": "<string>"
  },
  "requires_human_review": true
}
IMPORTANT:
- Output ONLY valid JSON, no markdown code block fences, no extraneous text.
- requires_human_review MUST be true. AI is advisory only.
- Never invent issue keys or assignments not present in the context.
- Keep confidence strictly between 0.0 and 1.0.
"""


class DeepSeekAIProvider:
    """DeepSeek adapter implementing the AIProvider protocol using OpenAI-compatible API."""

    DEFAULT_BASE_URL = "https://api.deepseek.com"
    DEFAULT_MODEL = "deepseek-chat"

    def __init__(
        self,
        config: Optional[Any] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: float = 30.0,
        max_input_tokens: int = 4000,
        max_output_tokens: int = 2000,
        client: Optional[httpx.AsyncClient] = None,
    ):
        if config is not None:
            self.base_url = (getattr(config, "base_url", None) or self.DEFAULT_BASE_URL).rstrip("/")
            self.api_key = getattr(config, "api_key", None)
            self.model = getattr(config, "model", None) or self.DEFAULT_MODEL
            self.timeout_seconds = float(getattr(config, "timeout_seconds", 30.0) or 30.0)
            self.max_input_tokens = int(getattr(config, "max_input_tokens", 4000) or 4000)
            self.max_output_tokens = int(getattr(config, "max_output_tokens", 2000) or 2000)
        else:
            self.base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
            self.api_key = api_key
            self.model = model or self.DEFAULT_MODEL
            self.timeout_seconds = float(timeout_seconds)
            self.max_input_tokens = int(max_input_tokens)
            self.max_output_tokens = int(max_output_tokens)

        self._custom_client = client

    def _sanitize_error_message(self, message: str) -> str:
        """Ensure no API keys, tokens, or bearer headers leak in exception strings."""
        if not message:
            return ""
        sanitized = redact_text(str(message))
        if self.api_key and self.api_key in sanitized:
            sanitized = sanitized.replace(self.api_key, "******")
        return sanitized

    def _build_client(self) -> httpx.AsyncClient:
        """Create an httpx.AsyncClient with configured timeout."""
        if self._custom_client is not None:
            return self._custom_client
        return httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds))

    async def _post_chat_completion(
        self,
        messages: List[Dict[str, str]],
    ) -> Dict[str, Any]:
        """Post chat completion request with bounded retries on 5xx/connection errors."""
        if not self.api_key or not self.api_key.strip():
            raise DeepSeekProviderError("DeepSeek API key is missing or unconfigured.")

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key.strip()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "max_tokens": self.max_output_tokens,
            "temperature": 0.2,
        }

        max_attempts = 3
        backoff_seconds = 1.0

        for attempt in range(1, max_attempts + 1):
            try:
                # If custom client provided, use it without context manager closing it
                if self._custom_client is not None:
                    response = await self._custom_client.post(url, headers=headers, json=payload)
                else:
                    async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds)) as client:
                        response = await client.post(url, headers=headers, json=payload)

                status_code = response.status_code

                if status_code == 200:
                    try:
                        return response.json()
                    except Exception as json_err:
                        raise DeepSeekProviderError(
                            f"DeepSeek response was not valid JSON: {self._sanitize_error_message(str(json_err))}"
                        )

                # 4xx client errors (e.g. 401 Unauthorized, 403 Forbidden, 429 Rate Limited, 400 Bad Request)
                # Do NOT retry client errors
                if 400 <= status_code < 500:
                    body_snippet = self._sanitize_error_message(response.text[:300])
                    raise DeepSeekProviderError(
                        f"DeepSeek API client error HTTP {status_code}: {body_snippet}"
                    )

                # 5xx server errors - eligible for bounded retry
                if status_code >= 500:
                    if attempt < max_attempts:
                        logger.warning(
                            f"DeepSeek API server error HTTP {status_code} on attempt {attempt}/{max_attempts}. Retrying in {backoff_seconds}s..."
                        )
                        await asyncio.sleep(backoff_seconds)
                        backoff_seconds *= 2.0
                        continue
                    else:
                        body_snippet = self._sanitize_error_message(response.text[:300])
                        raise DeepSeekProviderError(
                            f"DeepSeek API server error HTTP {status_code} after {max_attempts} attempts: {body_snippet}"
                        )

            except httpx.TimeoutException as te:
                if attempt < max_attempts:
                    logger.warning(
                        f"DeepSeek API timeout on attempt {attempt}/{max_attempts}. Retrying in {backoff_seconds}s..."
                    )
                    await asyncio.sleep(backoff_seconds)
                    backoff_seconds *= 2.0
                    continue
                raise DeepSeekProviderError(
                    f"DeepSeek API request timed out after {self.timeout_seconds}s across {max_attempts} attempts."
                )
            except httpx.RequestError as re:
                if attempt < max_attempts:
                    logger.warning(
                        f"DeepSeek network connection error on attempt {attempt}/{max_attempts}: {self._sanitize_error_message(str(re))}. Retrying..."
                    )
                    await asyncio.sleep(backoff_seconds)
                    backoff_seconds *= 2.0
                    continue
                raise DeepSeekProviderError(
                    f"DeepSeek API network connection failed after {max_attempts} attempts: {self._sanitize_error_message(str(re))}"
                )

        raise DeepSeekProviderError("DeepSeek API request failed: exhausted retry attempts.")

    def _extract_content_json(self, response_data: Dict[str, Any]) -> Dict[str, Any]:
        """Extract and parse JSON object from chat completion choices content."""
        choices = response_data.get("choices")
        if not choices or not isinstance(choices, list):
            raise DeepSeekProviderError("DeepSeek response contains no choices.")

        first_choice = choices[0]
        message = first_choice.get("message") or {}
        content = message.get("content")
        if not content or not str(content).strip():
            raise DeepSeekProviderError("DeepSeek response content is empty.")

        clean_content = str(content).strip()
        # Strip potential markdown code fences if model enclosed JSON in ```json ... ```
        if clean_content.startswith("```"):
            lines = clean_content.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            clean_content = "\n".join(lines).strip()

        try:
            return json.loads(clean_content)
        except Exception as e:
            snippet = self._sanitize_error_message(clean_content[:200])
            raise DeepSeekProviderError(
                f"Failed to parse DeepSeek response content as JSON: {snippet}"
            )

    async def analyze(self, context: AIContext) -> AIDecision:
        """Analyze context using DeepSeek and return a typed AIDecision."""
        context_payload = context.model_dump(mode="json")
        sanitized_context = sanitize_dict(context_payload)

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT_DECISION},
            {
                "role": "user",
                "content": f"Context for PM Decision Support:\n{json.dumps(sanitized_context, indent=2)}",
            },
        ]

        t0 = time.monotonic()
        raw_response = await self._post_chat_completion(messages)
        latency = round(time.monotonic() - t0, 3)

        usage = raw_response.get("usage", {})
        logger.info(
            f"DeepSeek analyze completed in {latency}s (prompt_tokens={usage.get('prompt_tokens')}, completion_tokens={usage.get('completion_tokens')})"
        )

        content_dict = self._extract_content_json(raw_response)

        try:
            decision = AIDecision.model_validate(content_dict)
            return decision
        except ValidationError as ve:
            sanitized_err = self._sanitize_error_message(str(ve))
            raise DeepSeekProviderError(
                f"DeepSeek response failed AIDecision schema validation: {sanitized_err}"
            )

    async def analyze_attention(self, context: AIContext) -> PMAttentionAnalysis:
        """Analyze attention context using DeepSeek and return a typed PMAttentionAnalysis."""
        context_payload = context.model_dump(mode="json")
        sanitized_context = sanitize_dict(context_payload)

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT_ATTENTION},
            {
                "role": "user",
                "content": f"Attention Evaluation Context:\n{json.dumps(sanitized_context, indent=2)}",
            },
        ]

        t0 = time.monotonic()
        raw_response = await self._post_chat_completion(messages)
        latency = round(time.monotonic() - t0, 3)

        usage = raw_response.get("usage", {})
        logger.info(
            f"DeepSeek analyze_attention completed in {latency}s (prompt_tokens={usage.get('prompt_tokens')}, completion_tokens={usage.get('completion_tokens')})"
        )

        content_dict = self._extract_content_json(raw_response)

        try:
            analysis = PMAttentionAnalysis.model_validate(content_dict)
            return analysis
        except ValidationError as ve:
            sanitized_err = self._sanitize_error_message(str(ve))
            raise DeepSeekProviderError(
                f"DeepSeek response failed PMAttentionAnalysis schema validation: {sanitized_err}"
            )
