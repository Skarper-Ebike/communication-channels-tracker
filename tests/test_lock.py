import json
import time

import pytest

from app.lock import AlreadyRunning, RunLock


def test_lock_acquire_and_release(tmp_path):
    lock_file = tmp_path / "work-agent.lock"
    with RunLock(lock_file):
        assert lock_file.exists()
    assert not lock_file.exists()


def test_second_concurrent_run_exits(tmp_path):
    lock_file = tmp_path / "work-agent.lock"
    with RunLock(lock_file):
        with pytest.raises(AlreadyRunning):
            with RunLock(lock_file):
                pass
    assert not lock_file.exists()


def test_stale_lock_is_broken(tmp_path):
    lock_file = tmp_path / "work-agent.lock"
    lock_file.write_text(json.dumps(
        {"pid": 99999999, "started_at": time.time() - 7200}
    ))
    with RunLock(lock_file, stale_after=60):
        assert lock_file.exists()
    assert not lock_file.exists()


def test_dead_pid_lock_is_broken(tmp_path):
    lock_file = tmp_path / "work-agent.lock"
    # PID 99999999 will not exist; started_at is recent so only liveness applies.
    lock_file.write_text(json.dumps(
        {"pid": 99999999, "started_at": time.time()}
    ))
    with RunLock(lock_file):
        pass


def test_unreadable_fresh_lock_blocks(tmp_path):
    lock_file = tmp_path / "work-agent.lock"
    lock_file.write_text("garbage")
    with pytest.raises(AlreadyRunning):
        with RunLock(lock_file):
            pass
