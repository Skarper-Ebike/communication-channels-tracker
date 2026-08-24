"""Structured classification schema.

The API response is validated against this — action state is never parsed out
of freeform prose. An unknown category is a validation error, not a guess.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AICategory = Literal[
    "DIRECT_REQUEST",
    "APPROVAL_REQUIRED",
    "BLOCKER",
    "DECISION",
    "IMPORTANT_CHANGE",
    "POSSIBLE_WAITING",
    "POSSIBLE_COMMITMENT",
    "FYI",
    "NOISE",
]


class ClassifiedActivity(BaseModel):
    activity_id: str
    relevant: bool
    category: AICategory
    importance: int = Field(ge=0, le=100)
    confidence: float = Field(ge=0.0, le=1.0)
    requires_action: bool
    reason: str
    summary: str
    waiting_person: str | None = None


class ClassificationResponse(BaseModel):
    items: list[ClassifiedActivity]


class DigestResult(BaseModel):
    """Team-day summary: everything that happened, involved or not."""

    overview: str
    decisions: list[str]
    issues: list[str]
    notes: list[str]
