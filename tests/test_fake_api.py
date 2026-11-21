"""The fake API must return the shapes promised in docs/API.md."""
import time

from lookat.fake_api import SCREENS, Fake, _public

SCREEN_KEYS = {"v", "screen", "badge", "title", "subtitle", "icon", "left", "middle", "right", "footer",
               "page", "show_call_nurse", "focus", "overlay", "clock", "room", "face"}
SCREEN_IDS = {"idle", "suggestion", "checkin", "assistant", "room", "lights", "temperature", "nurse"}


def test_screen_shape_and_every_screen_present():
    d = Fake().screen_json()
    assert set(d) == SCREEN_KEYS
    assert {s["screen"] for s in SCREENS} == SCREEN_IDS
    for s in SCREENS:
        for spot in ("left", "middle", "right"):
            assert s[spot] is None or set(s[spot]) == {"id", "label", "hint", "icon"}


def test_nurse_mode_hides_call_nurse_banner():
    assert all(not s["show_call_nurse"] for s in SCREENS if s["screen"] == "nurse")


def test_send_question_shows_on_patient_screen_and_gets_answered():
    f = Fake()
    assert f.nurse_action("send", {"question": "Did you sleep okay?", "options": ["Yes", "No"]})["ok"]
    d = f.screen_json()
    assert d["screen"] == "nurse" and d["title"] == "Did you sleep okay?"
    f.session["thread"][-1]["_sent"] = time.time() - 10
    f.tick()
    q = f.nurse_json()["session"]["thread"][-1]
    assert q["answer"] == "Yes" and f.nurse_json()["session"]["follow_ups"]
    assert "_sent" not in _public(f.nurse_json())["session"]["thread"][-1]


def test_send_needs_two_options():
    assert not Fake().nurse_action("send", {"question": "Hi?", "options": ["Yes"]})["ok"]


def test_cold_setpoint_creates_flag():
    f = Fake()
    n = len(f.alerts)
    f.nurse_action("room", {"setpoint_c": 18})
    assert len(f.alerts) == n + 1 and f.alerts[0]["kind"] == "flag"


def test_unknown_actions_fail_cleanly():
    f = Fake()
    assert not f.nurse_action("nope", {})["ok"]
    assert not f.demo_action({"action": "nope"})["ok"]
