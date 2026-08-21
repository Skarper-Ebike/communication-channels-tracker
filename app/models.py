"""Shared data models.

Activities are the normalised in-memory shape every connector produces; they
live only for the duration of a run. AttentionItems are the small persistent
records in .local/attention/ that let the agent remember what it already told
the user without needing a database.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Source(str, Enum):
    SLACK = "slack"
    JIRA = "jira"
    GOOGLE_DRIVE = "google_drive"
    BASECAMP = "basecamp"
    CONFLUENCE = "confluence"


class AttentionCategory(str, Enum):
    # Deterministic — derived from source API facts, never from inference.
    OVERDUE = "OVERDUE"
    DUE_SOON = "DUE_SOON"
    DIRECT_MENTION = "DIRECT_MENTION"
    NEW_ASSIGNMENT = "NEW_ASSIGNMENT"
    NEW_REPLY = "NEW_REPLY"
    # AI-classified (Milestone 3+). Present now so state files stay stable.
    DIRECT_REQUEST = "DIRECT_REQUEST"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    BLOCKER = "BLOCKER"
    DECISION = "DECISION"
    IMPORTANT_CHANGE = "IMPORTANT_CHANGE"
    POSSIBLE_WAITING = "POSSIBLE_WAITING"
    POSSIBLE_COMMITMENT = "POSSIBLE_COMMITMENT"
    FYI = "FYI"
    NOISE = "NOISE"


class AttentionStatus(str, Enum):
    OPEN = "OPEN"
    SNOOZED = "SNOOZED"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


class Activity(BaseModel):
    id: str

    source: Source

    object_type: str
    object_id: str
    thread_id: str | None = None

    event_type: str

    actor_id: str | None = None
    actor_name: str | None = None

    title: str | None = None
    text: str | None = None
    excerpt: str | None = None

    url: str

    created_at: datetime
    updated_at: datetime | None = None

    assigned_to_user: bool = False
    mentions_user: bool = False
    directed_at_user: bool = False

    due_at: datetime | None = None
    completed: bool | None = None

    priority: str | None = None

    metadata: dict[str, Any] = Field(default_factory=dict)


class AttentionItem(BaseModel):
    key: str

    source: Source
    object_id: str

    category: AttentionCategory
    status: AttentionStatus = AttentionStatus.OPEN

    score: int = 0
    confidence: float = 1.0

    deterministic: bool = True

    title: str
    reason: str
    summary: str = ""

    url: str

    first_seen_at: datetime
    last_seen_at: datetime

    last_reported_at: datetime | None = None
    report_count: int = 0

    snoozed_until: datetime | None = None
    resolved_at: datetime | None = None

    # Carried so ranking can re-score without refetching the source.
    due_at: datetime | None = None
    priority: str | None = None


def attention_key(source: Source, object_id: str, category: AttentionCategory) -> str:
    return f"{source.value}:{object_id}:{category.value.lower()}"
