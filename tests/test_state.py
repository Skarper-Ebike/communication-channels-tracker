import json
from unittest import mock

import pytest

from app.storage import atomic_write_json, migrate, read_json


def test_atomic_write_and_read(tmp_path):
    target = tmp_path / "state.json"
    atomic_write_json(target, {"version": 1, "cursor": "abc"})
    assert read_json(target, {}) == {"version": 1, "cursor": "abc"}
    assert not list(tmp_path.glob("*.tmp"))


def test_missing_file_returns_default(tmp_path):
    assert read_json(tmp_path / "nope.json", {"version": 1}) == {"version": 1}


def test_corrupt_json_quarantined_and_default_returned(tmp_path):
    target = tmp_path / "state.json"
    target.write_text("{not valid json", encoding="utf-8")
    assert read_json(target, {"version": 1}) == {"version": 1}
    assert not target.exists()
    assert (tmp_path / "state.json.corrupt").read_text() == "{not valid json"


def test_failed_write_keeps_previous_state(tmp_path):
    target = tmp_path / "state.json"
    atomic_write_json(target, {"version": 1, "value": "old"})

    with mock.patch("app.storage.json.dump", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            atomic_write_json(target, {"version": 1, "value": "new"})

    assert json.loads(target.read_text()) == {"version": 1, "value": "old"}
    assert not list(tmp_path.glob("*.tmp"))


def test_migrate_upgrades_stepwise():
    def v1_to_v2(data):
        data["version"] = 2
        data["renamed"] = data.pop("old")
        return data

    out = migrate({"version": 1, "old": 5}, {1: v1_to_v2})
    assert out == {"version": 2, "renamed": 5}
    # Already-current data passes through untouched.
    assert migrate({"version": 2, "renamed": 5}, {1: v1_to_v2})["version"] == 2
