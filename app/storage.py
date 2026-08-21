"""Local file storage: paths under .local/ and safe atomic JSON read/write.

Plain JSON files replace a database, so writes must never be able to destroy
the previous valid state: write to a temp file in the same directory, flush,
fsync, then os.replace() over the target. A corrupt file on read is moved
aside (.corrupt) and the caller gets its default back.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

log = logging.getLogger("work-agent")


class LocalPaths:
    """Layout of the per-user .local/ directory.

    Root resolution: WORK_AGENT_HOME env var if set (used by tests), else
    .local/ at the repository root.
    """

    def __init__(self, root: Path | None = None):
        if root is None:
            env = os.environ.get("WORK_AGENT_HOME")
            if env:
                root = Path(env)
            else:
                root = Path(__file__).resolve().parents[1] / ".local"
        self.root = root
        self.config_file = root / "config.yaml"
        self.env_file = root / ".env"
        self.secrets = root / "secrets"
        self.state = root / "state"
        self.attention = root / "attention"
        self.cache = root / "cache"
        self.audit = root / "audit"
        self.logs = root / "logs"
        self.lock_file = root / "work-agent.lock"

    def ensure(self) -> None:
        for d in (self.root, self.secrets, self.state, self.attention,
                  self.cache, self.audit, self.logs):
            d.mkdir(parents=True, exist_ok=True)
        os.chmod(self.secrets, 0o700)


def read_json(path: Path, default: Any) -> Any:
    """Read a JSON file; on missing or corrupt content return `default`.

    A corrupt file is renamed to <name>.corrupt so the evidence survives but
    the agent keeps working from a clean default.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        quarantine = path.with_suffix(path.suffix + ".corrupt")
        try:
            os.replace(path, quarantine)
            log.warning("Corrupt state file %s moved to %s", path, quarantine)
        except OSError:
            log.warning("Corrupt state file %s could not be quarantined", path)
        return default


def atomic_write_json(path: Path, data: Any) -> None:
    """Atomically replace `path` with `data` serialised as JSON.

    If this process dies at any point, the previous file remains intact.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def migrate(data: dict, migrations: dict[int, Any]) -> dict:
    """Upgrade a versioned state dict in place using `{from_version: fn}`.

    Every persisted file carries {"version": N}; users must never hand-edit
    state after pulling a code update.
    """
    version = data.get("version", 1)
    while version in migrations:
        data = migrations[version](data)
        version = data["version"]
    return data
