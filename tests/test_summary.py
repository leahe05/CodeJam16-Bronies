"""Shift summary (lookat/summary.py): only logged events, AI with a guard, template fallback."""
import threading
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from lookat import summary
from lookat.devices import Room, SimPlug
from lookat.live import Live
from lookat.memory import Memory

NOW = datetime(2026, 11, 21, 6, 0)


@pytest.fixture(autouse=True)
def _tmp_log(tmp_path, monkeypatch):
    monkeypatch.setattr("lookat.state.LOG_PATH", str(tmp_path / "events.jsonl"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def night(tmp_path):
    m = Memory("s", folder=str(tmp_path))
    at = lambda h, mi: datetime(2026, 11, 21, h, mi).isoformat(timespec="seconds")
    m.data["suggestions"].append({"time": at(2, 14), "text": "You seem to be awake. Too warm?", "outcome": "Yes, please."})
    m.data["actions"].append({"time": at(2, 15), "device": "fan", "cmd": "on", "by": "agent"})
    m.data["questions"].append({"time": at(2, 23), "question": "Better now?", "answer": "Yes", "kind": "comfort"})
    m.data["deferred"].append({"time": at(5, 0), "note": "Wants repositioning"})
    m.data["actions"].append({"time": at(1, 0), "device": "lamp", "cmd": "off", "by": "routine", "demo": True})
    m.data["actions"].append({"time": "2026-11-20T12:00:00", "device": "lamp", "cmd": "on", "by": "patient"})
    return m


def test_events_are_only_this_shift_in_time_order(tmp_path):
    ev = summary.shift_events(night(tmp_path), [{"time": "02:22", "stage": "checked", "text": "Room 22.8 °C"}], now=NOW)
    texts = [t for _, t in ev]
    assert [x.strftime("%H:%M") for x, _ in ev] == ["02:14", "02:15", "02:22", "02:23", "05:00"]
    assert texts[1] == "LookAt turned the fan on" and texts[2] == "Room 22.8 °C"
    assert not any("lamp" in t for t in texts)     # yesterday noon and the labelled demo data are left out


def test_template_never_adds_anything(tmp_path):
    text = summary.shift_summary(night(tmp_path), now=NOW)["text"]
    assert text.startswith("2:14 AM: Suggested") and text.endswith("For the next visit: Wants repositioning.")
    assert ".." not in text
    assert summary.template([]) == "Quiet shift: nothing was logged."


class FakeClient:
    def __init__(self, text=None, error=None, delay=0.0):
        self.text, self.error, self.delay = text, error, delay
        self.messages = self

    def create(self, **kw):
        time.sleep(self.delay)
        if self.error:
            raise self.error
        self.kw = kw
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.text)])


def test_ai_summary_is_used_when_it_sticks_to_the_log(tmp_path):
    c = FakeClient("At 2:14 AM the patient was warm; the fan went on at 2:15 AM and helped. Repositioning waits.")
    s = summary.shift_summary(night(tmp_path), now=NOW, client=c)
    assert s["source"] == "ai" and s["text"].startswith("At 2:14 AM")
    assert "2:15 AM LookAt turned the fan on" in c.kw["messages"][0]["content"]


def test_ai_summary_with_an_invented_time_is_rejected(tmp_path):
    c = FakeClient("At 3:40 AM the patient asked for water.")           # nothing happened at 3:40
    s = summary.shift_summary(night(tmp_path), now=NOW, client=c)
    assert s["source"] == "template" and "3:40" not in s["text"]


def test_ai_failure_or_empty_falls_back(tmp_path):
    for c in (FakeClient(error=TimeoutError("8 s")), FakeClient(text="  ")):
        assert summary.shift_summary(night(tmp_path), now=NOW, client=c)["source"] == "template"


def test_nurse_summary_button_does_not_hold_the_patient_screen(tmp_path, monkeypatch):
    live = Live(Room(lamp_ip="", fan_ip="", plug_factory=SimPlug), now=lambda: 0.0)
    live.memory = night(tmp_path)
    monkeypatch.setattr(summary.clock, "now", lambda: NOW)            # 6 AM, the end of the night shift
    monkeypatch.setattr(summary, "_client", lambda: FakeClient("At 2:14 AM the patient was warm.", delay=0.5))
    got = {}
    t = threading.Thread(target=lambda: got.update(live.nurse_action("summary", {})))
    t.start()
    time.sleep(0.1)
    start = time.monotonic()
    with live.lock:                                    # the camera loop can still get the lock
        pass
    assert time.monotonic() - start < 0.1
    t.join(5)
    assert got["ok"] and got["source"] == "ai" and live.nurse_json()["summary"] == got["text"]


def test_nurse_summary_without_memory_still_answers():
    live = Live(Room(lamp_ip="", fan_ip="", plug_factory=SimPlug), now=lambda: 0.0)
    r = live.nurse_action("summary", {})
    assert r["ok"] and r["text"] == "Nothing to report yet." and r["source"] == "template"
