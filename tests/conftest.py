"""Test runs must never write into the real logs/events.jsonl (it confuses checking a live run)."""
import pytest

from lookat import state


@pytest.fixture(autouse=True)
def _log_to_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "LOG_PATH", str(tmp_path / "events.jsonl"))
