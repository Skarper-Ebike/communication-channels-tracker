"""Connector contract.

Every connector receives only its own local state dict and returns an updated
one; the state layer persists it atomically, and only after the whole run has
processed successfully (the cursor rule).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from app.models import Activity


class ValidationResult(BaseModel):
    ok: bool
    detail: str = ""


class SyncResult(BaseModel):
    activities: list[Activity] = Field(default_factory=list)
    state: dict[str, Any] = Field(default_factory=dict)


class Action(BaseModel):
    name: str
    description: str = ""


class ActionRequest(BaseModel):
    action: str
    object_id: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ActionResult(BaseModel):
    ok: bool
    detail: str = ""


@runtime_checkable
class Connector(Protocol):
    @property
    def name(self) -> str: ...

    def authenticate(self) -> None: ...

    def validate(self) -> ValidationResult: ...

    def initial_sync(self) -> SyncResult: ...

    def fetch_changes(self, state: dict) -> SyncResult: ...

    def normalize(self, raw_item: Any) -> list[Activity]: ...

    def available_actions(self, activity: Activity) -> list[Action]: ...

    def execute_action(self, request: ActionRequest) -> ActionResult: ...
