"""The room model and the live backend, with simulated plugs (no camera, no network)."""
import pytest

from lookat import config
from lookat.clock import clock
from lookat.devices import FLOOR_C, Room, SimPlug
from lookat.live import Live, fallback_options

DT = 0.1


def make_room():
    return Room(lamp_ip="", fan_ip="", plug_factory=SimPlug)


def choose(live, t, spot):
    """Look at spot until it locks, short raise, look away. Returns (events, new time)."""
    out = []
    for _ in range(int((config.DWELL_S + 0.2) / DT)):
        out += live.frame(round(t, 3), spot)
        t += DT
    out += live.frame(round(t, 3), spot, ["confirm"])
    t += DT
    for _ in range(int((config.COOLDOWN_S + 0.3) / DT)):
        out += live.frame(round(t, 3), None)
        t += DT
    return out, t


# ----- room ------------------------------------------------------------------
def test_fan_cools_and_room_warms_back():
    room = make_room()
    room.set_temp(24.5)
    room.execute("fan", "on", by="patient")
    for i in range(101):
        room.tick(i * 0.1)
    assert room.state()["temp_c"] < 23.5
    room.execute("fan", "off", by="patient")
    for i in range(101, 2000):
        room.tick(i * 0.1)
    assert room.state()["temp_c"] == pytest.approx(24.5, abs=0.05)


def test_cooling_stops_at_floor():
    room = make_room()
    room.set_temp(21.0)
    room.execute("fan", "on", by="patient")
    for i in range(1000):
        room.tick(i * 0.1)
    assert room.state()["temp_c"] == FLOOR_C


def test_thermostat_runs_fan_to_setpoint_and_person_overrides():
    room = make_room()
    room.set_temp(22.0)
    room.set_setpoint(20.0, by="nurse", apply=True)
    for i in range(300):
        room.tick(i * 0.1)
    assert 19.9 <= room.state()["temp_c"] <= 20.4      # held near the setpoint (0.3 °C band)
    assert room.fan.sent.count("on") <= 3                # no plug chatter
    room.execute("fan", "on", by="patient")
    assert room.thermostat is False


def test_execute_logs_hooks_and_rejects_unknown():
    room, seen = make_room(), []
    room.on_action.append(lambda *a: seen.append(a))
    assert room.execute("lamp", "on", by="patient") == "Lamp ON (simulated)"
    assert seen == [("lamp", "on", "patient", "Lamp ON (simulated)")]
    with pytest.raises(ValueError):
        room.execute("tv", "on", by="patient")


# ----- live backend: patient --------------------------------------------------
def test_gaze_turns_the_lamp_on():
    live = Live(make_room(), now=lambda: 0.0)
    t = 0.0
    for spot in ("left", "left"):              # Room Controls -> Turn the lamp on
        ev, t = choose(live, t, spot)
    assert ("device", "lamp", "on") in ev and live.room.lamp.is_on
    assert live.recent[0]["text"].startswith("Patient: Lamp ON")


def test_screen_json_matches_contract():
    live = Live(make_room(), now=lambda: 0.0)
    live.frame(0.0, "left")
    d = live.screen_json()
    assert {"v", "screen", "badge", "title", "left", "middle", "right", "focus", "overlay", "clock",
            "room", "face", "show_call_nurse", "page", "footer", "subtitle", "icon"} <= set(d)
    assert d["screen"] == "idle" and d["focus"] == {"spot": "left", "state": "dwell"}


def test_long_raise_alerts_nurse_and_shows_overlay():
    live = Live(make_room(), now=lambda: 1.0)
    live.frame(1.0, "middle", ["emergency"])
    assert live.alerts[0]["kind"] == "call" and live.alerts[0]["urgency"] == "urgent"
    assert live.screen_json()["overlay"]["kind"] == "call_nurse"


def test_listeners_get_patient_events_and_errors_are_contained():
    live, got = Live(make_room(), now=lambda: 0.0), []
    live.listeners += [lambda ev: 1 / 0, got.append]
    choose(live, 0.0, "middle")                  # Talk to AI
    assert ("talk_to_ai",) in got


# ----- live backend: nurse ------------------------------------------------------
def test_nurse_question_answered_by_gaze():
    live = Live(make_room(), now=lambda: 0.0)
    r = live.nurse_action("send", {"question": "Is 18 °C too cold for you?", "options": ["Too cold", "It's fine"]})
    assert r["ok"] and live.screen_json()["screen"] == "nurse"
    ev, t = choose(live, 0.0, "left")
    assert ("nurse_answer", r["qid"], "Too cold") in ev
    q = live.nurse_json()["session"]["thread"][-1]
    assert q["answer"] == "Too cold" and q["seconds"] is not None
    live.nurse_action("session/end", {})
    assert live.screen_json()["screen"] == "idle" and not live.session["active"]


def test_nurse_send_validation_and_suggest_fallback():
    live = Live(make_room())
    assert not live.nurse_action("send", {"question": "Hi?", "options": ["Yes"]})["ok"]
    r = live.nurse_action("suggest", {"question": "Did you sleep okay?"})
    assert r["ok"] and r["options"][:2] == ["Yes", "No"]
    assert fallback_options("What would help?")[-1] == "Something else"


def test_nurse_room_controls_and_setpoint_event():
    live, got = Live(make_room()), []
    live.listeners.append(got.append)
    assert live.nurse_action("room", {"device": "fan", "cmd": "on"})["ok"] and live.room.fan.is_on
    assert live.nurse_action("room", {"setpoint_c": 18})["ok"] and live.room.setpoint_c == 18
    assert not live.nurse_action("room", {"device": "tv", "cmd": "on"})["ok"]


def test_alert_ask_sends_its_question_and_set_anyway_applies():
    live = Live(make_room(), now=lambda: 0.0)
    a = live.add_alert("flag", "Patient feels cold below 20 °C. Ask them first?",
                       ask={"question": "Is 18 °C too cold for you?", "options": ["Too cold", "It's fine"],
                            "setpoint_c": 18})
    live.nurse_action("alert", {"id": a["id"], "action": "ask"})
    assert live.screen_json()["title"] == "Is 18 °C too cold for you?"
    b = live.add_alert("flag", "x", ask={"question": "q", "options": ["a", "b"], "setpoint_c": 18})
    live.nurse_action("alert", {"id": b["id"], "action": "set_anyway"})
    assert live.room.thermostat and live.room.setpoint_c == 18
    assert "_ask" not in str(__import__("lookat.server", fromlist=["public"]).public(live.nurse_json()))


# ----- demo controls ------------------------------------------------------------
def test_demo_controls():
    live = Live(make_room())
    try:
        assert live.demo_action({"action": "set_time", "value": "02:00"})["ok"] and clock.hhmm() == "02:00"
        assert live.demo_action({"action": "set_temp", "value": 24.5})["ok"] and live.room.temp_c == 24.5
        assert not live.demo_action({"action": "eyes", "value": "sideways"})["ok"]
        assert live.demo_action({"action": "reset"})["ok"] and not clock.demo
    finally:
        clock.reset()


def test_calibration_pauses_choices_and_shows_overlay():
    live = Live(make_room(), now=lambda: 0.0)
    live.set_calibration("Look at the LEFT edge of the screen", "left")
    assert live.frame(0.0, "left", ["emergency"]) == []
    assert live.screen_json()["overlay"] == {"kind": "calibrate", "text": "Look at the LEFT edge of the screen",
                                             "spot": "left"}
