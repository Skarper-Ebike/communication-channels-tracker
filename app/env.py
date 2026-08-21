"""Load .local/.env into the process environment.

Plain KEY=VALUE lines; existing environment variables are never overridden.
Values are read once at CLI start and stay on this machine.
"""

from __future__ import annotations

import os

from app.storage import LocalPaths


def load_env(paths: LocalPaths) -> None:
    try:
        text = paths.env_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value
