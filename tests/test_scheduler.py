from pathlib import Path

import pytest

from app import scheduler
from app.config import AppConfig


def test_build_jobs_covers_enabled_briefings():
    jobs = scheduler.build_jobs(AppConfig(), "/opt/uv", Path("/logs"))
    assert set(jobs) == {"briefing", "urgent", "weekly"}

    morning = jobs["briefing"]
    assert morning["ProgramArguments"][0] == "/opt/uv"
    assert morning["ProgramArguments"][-1] == "briefing"
    assert morning["RunAtLoad"] is False
    # Weekdays only, at the configured 08:30 default.
    assert [e["Weekday"] for e in morning["StartCalendarInterval"]] == [1, 2, 3, 4, 5]
    assert morning["StartCalendarInterval"][0]["Hour"] == 8
    assert morning["StartCalendarInterval"][0]["Minute"] == 30

    assert jobs["urgent"]["StartInterval"] == 3600
    assert jobs["weekly"]["StartCalendarInterval"]["Weekday"] == 5  # Friday


def test_disabled_briefings_produce_no_jobs():
    config = AppConfig()
    config.briefing.morning.enabled = False
    config.briefing.urgent_scan.enabled = False
    config.briefing.weekly.enabled = False
    assert scheduler.build_jobs(config, "/opt/uv", Path("/logs")) == {}


def test_urgent_interval_floor_is_five_minutes():
    config = AppConfig()
    config.briefing.urgent_scan.interval_minutes = 1
    jobs = scheduler.build_jobs(config, "/opt/uv", Path("/logs"))
    assert jobs["urgent"]["StartInterval"] == 300


def test_invalid_time_rejected():
    config = AppConfig()
    config.briefing.morning.time = "25:99"
    with pytest.raises(scheduler.SchedulerError, match="invalid time"):
        scheduler.build_jobs(config, "/opt/uv", Path("/logs"))
