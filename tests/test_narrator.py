from lookat.narrator import Narrator, describe
from lookat.screens import ScreenManager


def opt(label):
    return {"id": label, "label": label}


CHECKIN = {"screen": "checkin", "title": "Would you like help adjusting your pillows?",
           "left": opt("Yes, please."), "middle": opt("Return to main menu"), "right": opt("No, thanks.")}


def test_describe_reads_title_then_left_right_center():
    assert describe(CHECKIN) == ("Would you like help adjusting your pillows? Left: Yes, please. "
                                 "Right: No, thanks. Center: Return to main menu.")


def test_new_screen_spoken_once():
    n = Narrator()
    first = n.update(CHECKIN)
    assert len(first) == 1 and first[0][1] is True
    assert n.update(CHECKIN) == []


def test_locked_option_spoken_as_question_once():
    n = Narrator()
    n.update(CHECKIN)
    s = {**CHECKIN, "focus": {"spot": "left", "state": "dwell"}}
    assert n.update(s) == []
    s = {**CHECKIN, "focus": {"spot": "left", "state": "asking"}}
    assert n.update(s) == [("Yes, please?", True)]
    assert n.update(s) == []


def test_call_nurse_overlay():
    n = Narrator()
    n.update(CHECKIN)
    out = n.update({**CHECKIN, "overlay": {"kind": "call_nurse"}})
    assert out == [("Calling your nurse. Help is on the way.", True)]
    assert n.update({**CHECKIN, "overlay": {"kind": "call_nurse"}}) == []


def test_calibration_is_silent_here():
    n = Narrator()
    assert n.update({**CHECKIN, "overlay": {"kind": "calibrate", "text": "Look left"}}) == []


def test_every_real_screen_has_something_to_say():
    room = {"lamp_on": False, "temp_c": 24.5}
    sm = ScreenManager(room=lambda: room)
    n, t = Narrator(), 0.0
    said = []
    said += n.update(sm.view(t))                        # idle
    sm.select("left", t); said += n.update(sm.view(t))  # room
    sm.select("left", t); said += n.update(sm.view(t))  # lights
    sm.nurse_question("q1", "Did you sleep okay?", ["Yes", "No"], t); said += n.update(sm.view(t))
    texts = [s for s, _ in said]
    assert len(texts) == 4 and all(texts)
    assert texts[0].startswith("You're all set.") and "Left: Room Controls." in texts[0]
    assert texts[3].startswith("Did you sleep okay?") and "Left: Yes." in texts[3]
