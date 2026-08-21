"""Local scheduling — launchd on macOS.

launchd (not cron) because laptops sleep: a StartCalendarInterval job whose
time passed while the machine slept fires once on wake, which is exactly the
catch-up behaviour the product wants (§44). Linux (systemd timers) is not
implemented yet; the CLI says so instead of pretending.
"""

from __future__ import annotations

import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from app.config import AppConfig

LABEL_PREFIX = "com.personal-work-agent"
REPO_ROOT = Path(__file__).resolve().parents[1]

WEEKDAY_NUMBERS = {  # launchd: 0=Sunday .. 6=Saturday
    "sunday": 0, "monday": 1, "tuesday": 2, "wednesday": 3,
    "thursday": 4, "friday": 5, "saturday": 6,
}


class SchedulerError(Exception):
    pass


def agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def build_jobs(config: AppConfig, uv_path: str, log_dir: Path) -> dict[str, dict]:
    """Job name -> launchd plist dict. Pure — no filesystem access."""

    def plist(command: str, schedule_key: str, schedule_value) -> dict:
        return {
            "Label": f"{LABEL_PREFIX}.{command}",
            "ProgramArguments": [
                uv_path, "--directory", str(REPO_ROOT),
                "run", "python", "-m", "app.cli", command,
            ],
            "WorkingDirectory": str(REPO_ROOT),
            "RunAtLoad": False,
            "StandardOutPath": str(log_dir / f"launchd-{command}.log"),
            "StandardErrorPath": str(log_dir / f"launchd-{command}.log"),
            schedule_key: schedule_value,
        }

    jobs: dict[str, dict] = {}
    briefing = config.briefing

    if briefing.morning.enabled:
        hour, minute = _parse_time(briefing.morning.time)
        jobs["briefing"] = plist("briefing", "StartCalendarInterval", [
            {"Weekday": weekday, "Hour": hour, "Minute": minute}
            for weekday in range(1, 6)  # weekdays only
        ])

    if briefing.urgent_scan.enabled:
        jobs["urgent"] = plist(
            "urgent", "StartInterval",
            max(300, briefing.urgent_scan.interval_minutes * 60))

    if briefing.weekly.enabled:
        weekday = WEEKDAY_NUMBERS.get(briefing.weekly.weekday.lower(), 5)
        hour, minute = _parse_time(briefing.weekly.time)
        jobs["weekly"] = plist("weekly", "StartCalendarInterval",
                               {"Weekday": weekday, "Hour": hour, "Minute": minute})
    return jobs


def install(config: AppConfig, log_dir: Path) -> list[str]:
    _require_macos()
    uv_path = shutil.which("uv")
    if not uv_path:
        raise SchedulerError("uv not found on PATH")

    agents_dir().mkdir(parents=True, exist_ok=True)
    installed = []
    for name, job in build_jobs(config, uv_path, log_dir).items():
        path = agents_dir() / f"{LABEL_PREFIX}.{name}.plist"
        subprocess.run(["launchctl", "unload", str(path)],
                       capture_output=True)  # ok to fail: not loaded yet
        path.write_bytes(plistlib.dumps(job))
        result = subprocess.run(["launchctl", "load", "-w", str(path)],
                                capture_output=True, text=True)
        if result.returncode != 0:
            raise SchedulerError(
                f"launchctl load failed for {name}: {result.stderr.strip()}")
        installed.append(name)
    return installed


def uninstall() -> list[str]:
    _require_macos()
    removed = []
    for path in agents_dir().glob(f"{LABEL_PREFIX}.*.plist"):
        subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
        path.unlink()
        removed.append(path.stem.removeprefix(f"{LABEL_PREFIX}."))
    return removed


def installed_jobs() -> list[str]:
    if sys.platform != "darwin":
        return []
    return sorted(path.stem.removeprefix(f"{LABEL_PREFIX}.")
                  for path in agents_dir().glob(f"{LABEL_PREFIX}.*.plist"))


def _require_macos() -> None:
    if sys.platform != "darwin":
        raise SchedulerError(
            "automatic scheduling is only implemented for macOS (launchd); "
            "on Linux add a systemd timer or cron entry running: "
            "uv run python -m app.cli briefing")


def _parse_time(value: str) -> tuple[int, int]:
    try:
        hour_s, minute_s = value.split(":")
        hour, minute = int(hour_s), int(minute_s)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except ValueError:
        raise SchedulerError(f"invalid time {value!r}; expected HH:MM")
    return hour, minute
