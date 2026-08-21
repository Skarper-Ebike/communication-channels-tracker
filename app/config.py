"""Per-user configuration: .local/config.yaml.

The clone represents exactly one person, so there is no user argument
anywhere — this file *is* the user. Missing file falls back to defaults so
mock mode works before setup has ever run.
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, Field

from app.storage import LocalPaths, atomic_write_json  # noqa: F401 (paths reused by callers)

log = logging.getLogger("work-agent")

CONFIG_VERSION = 1


class Identity(BaseModel):
    name: str = "You"
    timezone: str = "local"


class MorningBriefing(BaseModel):
    enabled: bool = True
    time: str = "08:30"


class UrgentScan(BaseModel):
    enabled: bool = True
    interval_minutes: int = 60


class WeeklyBriefing(BaseModel):
    enabled: bool = True
    weekday: str = "friday"
    time: str = "15:00"


class BriefingConfig(BaseModel):
    morning: MorningBriefing = Field(default_factory=MorningBriefing)
    urgent_scan: UrgentScan = Field(default_factory=UrgentScan)
    weekly: WeeklyBriefing = Field(default_factory=WeeklyBriefing)


class AttentionConfig(BaseModel):
    urgent_threshold: int = 90
    morning_threshold: int = 40


class SlackOutput(BaseModel):
    channel_id: str | None = None
    webhook_env: str = "WORK_AGENT_SLACK_WEBHOOK"


class AIConfig(BaseModel):
    enabled: bool = True
    model: str = "claude-opus-5"


class JiraConnectorConfig(BaseModel):
    enabled: bool = False
    base_url: str = ""
    projects: list[str] = Field(default_factory=list)


class ConnectorsConfig(BaseModel):
    # Future connectors (slack input, google_drive, basecamp, confluence)
    # get typed blocks as they land; unknown keys pass through untouched.
    model_config = {"extra": "allow"}

    jira: JiraConnectorConfig = Field(default_factory=JiraConnectorConfig)


class AppConfig(BaseModel):
    version: int = CONFIG_VERSION
    identity: Identity = Field(default_factory=Identity)
    slack_output: SlackOutput = Field(default_factory=SlackOutput)
    briefing: BriefingConfig = Field(default_factory=BriefingConfig)
    attention: AttentionConfig = Field(default_factory=AttentionConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    connectors: ConnectorsConfig = Field(default_factory=ConnectorsConfig)

    def now(self) -> datetime:
        tz_name = self.identity.timezone
        if tz_name and tz_name != "local":
            try:
                return datetime.now(ZoneInfo(tz_name))
            except ZoneInfoNotFoundError:
                log.warning("Unknown timezone %r; using system local time", tz_name)
        return datetime.now().astimezone()


def load_config(paths: LocalPaths) -> AppConfig:
    if not paths.config_file.exists():
        return AppConfig()
    raw = yaml.safe_load(paths.config_file.read_text(encoding="utf-8")) or {}
    return AppConfig.model_validate(raw)


def save_config(paths: LocalPaths, config: AppConfig) -> None:
    paths.ensure()
    text = yaml.safe_dump(
        config.model_dump(mode="json"), sort_keys=False, allow_unicode=True
    )
    paths.config_file.write_text(text, encoding="utf-8")
