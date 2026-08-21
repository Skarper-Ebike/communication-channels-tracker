"""AI client abstraction.

One provider (Anthropic API), one method that matters (classify). No agent
framework, no tool calling — Claude only classifies and summarises supplied
text, via a schema-validated structured output.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Protocol

from app.ai.schemas import ClassificationResponse
from app.models import Activity

log = logging.getLogger("work-agent")

DEFAULT_MODEL = "claude-opus-5"
MAX_TEXT_CHARS = 1500

_PROMPT_FILE = Path(__file__).resolve().parent / "prompts" / "classify.md"


class AIError(Exception):
    """The AI provider failed or returned an unusable response."""


class AIClient(Protocol):
    def classify(self, activities: list[Activity]) -> ClassificationResponse: ...


def activity_payload(a: Activity) -> dict:
    """Compact, truncated view of one activity — never secrets, never state."""
    return {
        "activity_id": a.id,
        "source": a.source.value,
        "object_type": a.object_type,
        "event_type": a.event_type,
        "actor": a.actor_name,
        "title": a.title,
        "text": (a.text or "")[:MAX_TEXT_CHARS],
        "mentions_user": a.mentions_user,
        "directed_at_user": a.directed_at_user,
        "assigned_to_user": a.assigned_to_user,
        "created_at": a.created_at.isoformat(),
    }


class AnthropicAIClient:
    def __init__(self, model: str = DEFAULT_MODEL):
        import anthropic  # deferred so mock/no-ai runs never need the key

        self._anthropic = anthropic
        self._client = anthropic.Anthropic()
        self.model = model
        self._system = _PROMPT_FILE.read_text(encoding="utf-8")

    def classify(self, activities: list[Activity]) -> ClassificationResponse:
        payload = [activity_payload(a) for a in activities]
        try:
            response = self._client.messages.parse(
                model=self.model,
                max_tokens=8000,
                system=self._system,
                messages=[{
                    "role": "user",
                    "content": "Classify these activities:\n"
                               + json.dumps(payload, ensure_ascii=False),
                }],
                output_format=ClassificationResponse,
            )
        except self._anthropic.APIError as exc:
            raise AIError(f"Anthropic API error: {exc}") from exc

        if response.stop_reason == "refusal":
            raise AIError("classification request was refused")
        parsed = response.parsed_output
        if parsed is None:
            raise AIError("no structured output in response")
        log.info("Claude classify: %d activities, %d/%d tokens",
                 len(activities), response.usage.input_tokens,
                 response.usage.output_tokens)
        return parsed
