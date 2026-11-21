"""Lea's main.py checks (PR #6), run against the live backend main.py uses (lookat.live.Live):
fake gaze + eyebrow events drive the real screens, with simulated plugs."""
from lookat import config
from lookat.devices import Room, SimPlug
from lookat.live import Live

SAMPLE_NURSE_Q = ("What would help you feel more comfortable?",
                  ["Adjust pillows", "Change position", "Water", "Something else"])


def make_live(now=0.0):
    return Live(Room(lamp_ip="", fan_ip="", plug_factory=SimPlug), now=lambda: now)


def choose(b, spot, t):
    """Look at spot through the dwell, then a short raise. Returns (events, time after)."""
    out = []
    for _ in range(3):
        out += b.frame(t, spot, [], True)
        t += config.DWELL_S / 2
    out += b.frame(t, spot, ["confirm"], True)
    t += config.COOLDOWN_S + 0.1
    out += b.frame(t, None, [], True)      # look away so the next choice can start
    return out, t + 0.1


def test_idle_screen_json():
    s = make_live().screen_json()
    assert s["screen"] == "idle" and s["left"]["label"] == "Room Controls" and "time" in s["clock"]


def test_dwell_shows_asking_highlight():
    b, t = make_live(), 0.0
    for _ in range(4):
        b.frame(t, "left", [], True)
        t += 0.4
    assert b.screen_json()["focus"] == {"spot": "left", "state": "asking"}


def test_lamp_on_by_gaze_and_eyebrows():
    b = make_live()
    _, t = choose(b, "left", 0.0)           # Room Controls
    assert b.sm.kind == "room"
    _, t = choose(b, "left", t)             # Adjust the lights
    ev, t = choose(b, "left", t)            # Lamp on
    assert ("device", "lamp", "on") in ev and b.room.state()["lamp_on"]


def test_long_raise_shows_call_nurse_overlay():
    b = make_live()
    ev = b.frame(0.0, "middle", ["confirm", "emergency"], True)
    assert ("call_nurse",) in ev and b.screen_json()["overlay"]["kind"] == "call_nurse"


def test_nurse_question_answer():
    b = make_live()
    qid = b.send_question(*SAMPLE_NURSE_Q, 0.0)
    assert b.screen_json()["screen"] == "nurse"
    ev, _ = choose(b, "right", 1.0)
    assert ("nurse_answer", qid, "Change position") in ev


def test_no_face_cancels():
    b, t = make_live(), 0.0
    for _ in range(4):
        b.frame(t, "left", [], True)
        t += 0.4
    b.frame(t, None, [], False)
    s = b.screen_json()
    assert s["focus"] is None and s["face"] is False
