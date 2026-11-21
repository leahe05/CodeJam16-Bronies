"""Routines (lookat/routines.py), the demo week (lookat/demo_week.py) and the One week later button."""
from datetime import datetime

import pytest

import main
from lookat import demo_week, routines
from lookat.devices import Room, SimPlug
from lookat.live import Live
from lookat.memory import Memory


@pytest.fixture(autouse=True)
def _tmp_log(tmp_path, monkeypatch):
    monkeypatch.setattr("lookat.state.LOG_PATH", str(tmp_path / "events.jsonl"))


TODAY = datetime(2026, 11, 21, 12, 0)


def setup(tmp_path, at="22:01"):
    mem = Memory("t", folder=str(tmp_path))
    demo_week.load(mem, today=TODAY)
    live = Live(Room(lamp_ip="", fan_ip="", plug_factory=SimPlug), now=lambda: 0.0)
    live.room.execute("lamp", "on", by="patient")
    t = {"now": TODAY.replace(hour=int(at[:2]), minute=int(at[3:]))}
    sched = routines.RoutineScheduler(live, mem, now=lambda: t["now"])
    return mem, live, sched, t


def test_candidates_from_three_nights_at_about_the_same_time():
    acts = [{"time": f"2026-11-{d}T{hm}:00", "device": "lamp", "cmd": "off", "by": "patient"}
            for d, hm in ((14, "21:55"), (15, "22:05"), (16, "22:00"))]
    c = routines.find_candidates(acts)
    assert c == [{"device": "lamp", "cmd": "off", "at": "22:00", "nights": ["2026-11-14", "2026-11-15", "2026-11-16"]}]


def test_no_candidate_from_two_nights_or_scattered_times():
    two = [{"time": f"2026-11-{d}T22:00:00", "device": "lamp", "cmd": "off", "by": "patient"} for d in (14, 15)]
    scattered = [{"time": f"2026-11-{d}T{h}:00:00", "device": "lamp", "cmd": "off", "by": "patient"}
                 for d, h in ((14, 18), (15, 20), (16, 23))]
    routine_made = [{"time": f"2026-11-{d}T22:00:00", "device": "lamp", "cmd": "off", "by": "routine"}
                    for d in (14, 15, 16)]
    assert routines.find_candidates(two + scattered + routine_made) == []


def test_demo_week_evidence_and_learning(tmp_path):
    mem, _, sched, _ = setup(tmp_path)
    assert routines.evidence(mem, "lamp", "off", "22:00", TODAY.date()) == "done 6 of the last 7 nights"
    v = sched.view()
    assert v[0]["status"] == "approved" and v[0]["text"] == "Lamp off at 22:00"
    assert "done 6 of the last 7 nights" in v[0]["evidence"]
    assert mem.learn()["hidden_options"]["comfort"] == ["TV on"]
    assert all(a.get("demo") for a in mem.data["actions"])


def test_runs_at_its_time_once_per_night(tmp_path):
    _, live, sched, t = setup(tmp_path, at="21:59")
    assert sched.tick() == [] and live.room.state()["lamp_on"] is True       # not yet
    t["now"] = t["now"].replace(hour=22, minute=0)
    assert len(sched.tick()) == 1
    assert live.room.state()["lamp_on"] is False
    assert live.sm.kind == "suggestion" and live.sm.suggestion["text"] == "It's 10 PM. Turning off your lamp, as usual."
    live.room.execute("lamp", "on", by="patient")
    t["now"] = t["now"].replace(minute=20)
    assert sched.tick() == [] and live.room.state()["lamp_on"] is True       # once per night


def test_not_after_the_window(tmp_path):
    _, live, sched, _ = setup(tmp_path, at="23:30")
    assert sched.tick() == [] and live.room.state()["lamp_on"] is True


def test_waits_during_a_nurse_session_or_a_checkin(tmp_path):
    _, live, sched, t = setup(tmp_path, at="22:00")
    live.session["active"] = True
    assert sched.tick() == []
    live.session["active"] = False
    live.sm.show_checkin("q1", "Too warm?", None, 0.0)
    assert sched.tick() == [] and live.room.state()["lamp_on"] is True
    live.sm._go("idle", 0.0)
    t["now"] = t["now"].replace(minute=10)                                    # still in the window
    assert len(sched.tick()) == 1 and live.room.state()["lamp_on"] is False


def test_already_off_means_no_banner(tmp_path):
    _, live, sched, _ = setup(tmp_path)
    live.room.execute("lamp", "off", by="patient")
    assert len(sched.tick()) == 1
    assert live.sm.kind == "idle"


def test_keep_it_on_switches_straight_back(tmp_path):
    _, live, sched, _ = setup(tmp_path)
    sched.tick()
    sid = live.sm.suggestion["sid"]
    sched._on_event(("answer", sid, "Keep it on"))
    assert live.room.state()["lamp_on"] is True


def test_one_week_later_button_and_panel(tmp_path):
    mem = Memory("t", folder=str(tmp_path))
    live = Live(Room(lamp_ip="", fan_ip="", plug_factory=SimPlug), now=lambda: 0.0)
    assert live.demo_action({"action": "one_week_later"})["ok"] is False      # without main.py: a clear no
    sched = main.wire_routines(live, mem)
    try:
        mem.add_fact("real patient fact")
        real = mem.path
        r = live.demo_action({"action": "one_week_later"})
        assert r["ok"] is True and r["nights"] == 7
        assert mem.path.endswith("t-demo-week.json")                       # sample data goes elsewhere
        assert "real patient fact" in open(real, encoding="utf-8").read()  # the real file is untouched
        p = live.panel_json()
        assert p["memory"]["routines"][0]["text"] == "Lamp off at 22:00"
        assert any("One week later" in f["text"] for f in p["feed"])
    finally:
        sched.close()


def test_announcement_wording():
    assert routines.announcement("lamp", "off", "22:00") == "It's 10 PM. Turning off your lamp, as usual."
    assert routines.announcement("fan", "on", "07:30") == "It's 7:30 AM. Turning on your fan, as usual."
