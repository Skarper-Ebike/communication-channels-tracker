"""MockConnector: drives the whole pipeline from local fixtures.

Fixture entries are near-Activity dicts. Timestamps may be given either as
absolute ISO strings (used by tests) or as `*_offset_hours` relative to the
current run time, so the demo briefing stays "fresh" whenever it is run.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.connectors.base import (
    Action,
    ActionRequest,
    ActionResult,
    SyncResult,
    ValidationResult,
)
from app.models import Activity

DEFAULT_FIXTURE = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "mock_activities.json"
)

_OFFSET_FIELDS = ("created_at", "updated_at", "due_at")


class MockConnector:
    name = "mock"

    def __init__(self, fixture_path: Path = DEFAULT_FIXTURE,
                 now: datetime | None = None):
        self.fixture_path = fixture_path
        self.now = now or datetime.now().astimezone()

    def authenticate(self) -> None:
        pass

    def validate(self) -> ValidationResult:
        if not self.fixture_path.exists():
            return ValidationResult(ok=False, detail=f"missing fixture: {self.fixture_path}")
        return ValidationResult(ok=True, detail="fixture data")

    def initial_sync(self) -> SyncResult:
        return self.fetch_changes({})

    def fetch_changes(self, state: dict) -> SyncResult:
        raw_items = json.loads(self.fixture_path.read_text(encoding="utf-8"))
        activities: list[Activity] = []
        for raw in raw_items:
            activities.extend(self.normalize(raw))
        new_state = dict(state)
        new_state["version"] = 1
        new_state["last_sync"] = self.now.isoformat()
        return SyncResult(activities=activities, state=new_state)

    def normalize(self, raw_item: Any) -> list[Activity]:
        raw = dict(raw_item)
        for field in _OFFSET_FIELDS:
            offset = raw.pop(f"{field}_offset_hours", None)
            if offset is not None and field not in raw:
                raw[field] = (self.now + timedelta(hours=offset)).isoformat()
        return [Activity.model_validate(raw)]

    def available_actions(self, activity: Activity) -> list[Action]:
        return []

    def execute_action(self, request: ActionRequest) -> ActionResult:
        return ActionResult(ok=False, detail="mock connector performs no actions")
