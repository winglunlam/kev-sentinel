"""Overlap protection for cron runs."""

from __future__ import annotations

import os

import sentinel.cli as cli
from sentinel.lock import FileLock

from .conftest import copy_fixture


def test_lock_acquire_and_release(tmp_path):
    lock = FileLock(tmp_path / "x.lock")
    assert lock.acquire() is True
    assert (tmp_path / "x.lock").read_text().strip() == str(os.getpid())
    assert FileLock(tmp_path / "x.lock").acquire() is False
    lock.release()
    assert not (tmp_path / "x.lock").exists()


def test_stale_lock_is_reclaimed(tmp_path):
    path = tmp_path / "x.lock"
    path.write_text("999999")
    lock = FileLock(path)
    assert lock.acquire() is True
    lock.release()


def test_check_skips_when_locked(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    fixture = copy_fixture(tmp_path)
    assert cli.main(["init", "jobs-api", "--fixture", str(fixture)]) == 0
    lock_path = tmp_path / ".sentinel" / "check.lock"
    lock_path.write_text(str(os.getpid()))
    capsys.readouterr()
    assert cli.main(["check", "--config", str(tmp_path / "sentinel.yaml"), "--no-sem", "--lock"]) == 0
    assert "already running" in capsys.readouterr().out
    assert cli.main(["check", "--config", str(tmp_path / "sentinel.yaml"), "--no-sem", "--lock"]) == 0
