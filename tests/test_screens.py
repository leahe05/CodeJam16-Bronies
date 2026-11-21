"""Tests for lookat/screens.py: labels, navigation, paging, fades, nurse mode."""
from lookat import config
from lookat.screens import RETURN, SOMETHING_ELSE, ScreenManager, icon_for, paged

ROOM = {"lamp_on": False, "temp_c": 24.5}


def sm():
    return ScreenManager(room=lambda: ROOM)


def labels(m, now=0.0):
    v = m.view(now)
    return [v[s]["label"] if v[s] else None for s in config.SPOTS]


def test_idle_matches_mockup():
    m = sm()
    v = m.view(0)
    assert v["screen"] == "idle" and v["title"] == "You're all set."
    assert v["subtitle"] == "I'm here if you need anything."
    assert labels(m) == ["Room Controls", "Talk to AI", None]
    assert v["show_call_nurse"] and v["overlay"] is None
    assert m.active_spots() == ["left", "middle"]


def test_room_controls_navigation_and_devices():
    m = sm()
    assert m.select("left", 1) == []
    assert m.view(1)["badge"] == "ROOM CONTROLS"
    assert labels(m) == ["Adjust the lights", RETURN, "Adjust the temperature"]
    m.select("right", 2)
    assert m.view(2)["title"] == "The room is 24.5 °C"
    assert m.select("left", 3) == [("device", "fan", "on")]
    assert m.select("right", 4) == [("device", "fan", "off")]
    m.select("middle", 5)                        # Back
    m.select("left", 6)                          # Adjust the lights
    assert labels(m) == ["Lamp on", "Back", "Lamp off"] and m.view(6)["title"] == "The lamp is OFF"
    assert m.select("left", 7) == [("device", "lamp", "on")]
    m.select("middle", 8)
    m.select("middle", 9)
    assert m.kind == "idle"


def test_talk_to_ai_assistant():
    m = sm()
    assert m.select("middle", 1) == [("talk_to_ai",)]
    v = m.view(1)
    assert v["title"] == "How can I help you?" and v["badge"] == "AI ASSISTANT"
    assert labels(m) == ["Get more comfortable", RETURN, "More suggestions"]
    assert m.select("right", 2) == []
    assert labels(m)[0] == "Quiet for 2 hours"
    assert m.select("left", 3) == [("assistant_choice", "Quiet for 2 hours")]
    assert m.kind == "idle"


def test_assistant_more_suggestions_wraps_and_adjust_room_goes_to_room():
    m = sm()
    m.select("middle", 1)
    for _ in range(4):
        m.select("right", 2)
    assert labels(m)[0] == "Get more comfortable"
    m.select("right", 3)
    m.select("right", 3)
    assert labels(m)[0] == "Adjust the room"
    assert m.select("left", 4) == [] and m.kind == "room"


def test_assistant_with_two_options_shows_both():
    m = sm()
    m.show_assistant(["Water", "Adjust pillows"])
    m.select("middle", 1)
    assert labels(m) == ["Water", RETURN, "Adjust pillows"]


def test_suggestion_engage_opens_checkin():
    m = sm()
    assert m.show_suggestion("s1", "You seem to be awake. Too warm?", 0)
    v = m.view(0)
    assert v["badge"] == "AI SUGGESTION" and v["title"] == "You seem to be awake. Too warm?"
    assert labels(m) == ["Room Controls", "Look center + raise eyebrows to engage", None]
    assert m.select("middle", 1) == [("engaged", "s1")]
    v = m.view(1)
    assert v["badge"] == "AI CHECK-IN" and labels(m) == ["Yes, please.", RETURN, "No, thanks."]
    assert m.select("left", 2) == [("answer", "s1", "Yes, please.")]
    assert m.kind == "idle"


def test_room_controls_from_suggestion_dismisses_it():
    m = sm()
    m.show_suggestion("s1", "Too warm?", 0)
    assert m.select("left", 1) == [("dismissed", "s1")] and m.kind == "room"


def test_suggestion_fades():
    m = sm()
    m.show_suggestion("s1", "Too warm?", 0)
    assert m.tick(config.SUGGESTION_FADE_S - 1) == []
    assert m.tick(config.SUGGESTION_FADE_S + 1, busy=True) == []
    assert m.tick(config.SUGGESTION_FADE_S + 1) == [("ignored", "s1")]
    assert m.kind == "idle"


def test_checkin_no_room_controls_and_return_dismisses():
    m = sm()
    m.show_checkin("q1", "Would you like help adjusting your pillows?", None, 0, icon="pillow")
    assert "Room Controls" not in labels(m)
    assert m.select("middle", 1) == [("dismissed", "q1")] and m.kind == "idle"


def test_checkin_times_out():
    m = sm()
    m.show_checkin("q1", "Better now?", ["Yes", "No"], 0)
    assert m.tick(config.CHECKIN_TIMEOUT_S + 1) == [("ignored", "q1")]


def test_checkin_paging_with_something_else_last():
    m = sm()
    m.show_checkin("q2", "What would help most?", ["Adjust pillows", "Something else", "Change position", "Water"], 0)
    assert labels(m) == ["Adjust pillows", "More options", "Change position"]
    assert m.view(0)["page"] == {"index": 0, "count": 2}
    m.select("middle", 1)
    assert labels(m) == ["Water", RETURN, SOMETHING_ELSE]
    assert m.select("left", 2) == [("answer", "q2", "Water")]


def test_checkin_open_question_gets_something_else():
    m = sm()
    m.show_checkin("q3", "What would help?", ["A", "B", "C"], 0)
    assert m.checkin["options"][-1] == SOMETHING_ELSE


def test_agent_cannot_interrupt_outside_idle():
    m = sm()
    assert m.can_interrupt()
    m.select("left", 1)
    assert not m.can_interrupt()
    assert not m.show_suggestion("s1", "Hi?", 2)


def test_room_screens_time_out_to_idle():
    m = sm()
    m.select("left", 0)
    m.tick(config.ROOM_TIMEOUT_S + 1)
    assert m.kind == "idle"


def test_nurse_mode_hides_room_return_and_banner():
    m = sm()
    m.nurse_start(0)
    v = m.view(0)
    assert v["badge"] == "From your nurse" and not v["show_call_nurse"] and m.active_spots() == []
    m.nurse_question("q1", "What would help you feel more comfortable?",
                     ["Adjust pillows", "Change position", "Water", "Something else"], 1)
    assert labels(m) == ["Adjust pillows", "More options", "Change position"]
    assert RETURN not in labels(m) and "Room Controls" not in labels(m)
    m.select("middle", 2)
    m.select("middle", 3)                        # wraps back to page 1
    assert labels(m)[0] == "Adjust pillows"
    assert m.select("right", 4) == [("nurse_answer", "q1", "Change position")]
    assert m.kind == "nurse" and "Thank you" in m.view(4)["title"]


def test_nurse_two_options_no_more():
    m = sm()
    m.nurse_question("q1", "Is 18 °C too cold for you?", ["Too cold", "It's fine"], 0)
    assert m.kind == "nurse" and labels(m) == ["Too cold", None, "It's fine"]


def test_nurse_session_blocks_agent_and_end_returns_idle():
    m = sm()
    m.nurse_start(0)
    assert not m.show_checkin("q1", "Too warm?", None, 1)
    assert not m.show_suggestion("s1", "Too warm?", 1)
    assert m.tick(1000) == [] and m.kind == "nurse"
    m.nurse_end(2)
    assert m.kind == "idle"


def test_call_nurse_overlay_on_any_screen():
    m = sm()
    m.nurse_start(0)
    assert m.call_nurse(1) == [("call_nurse",)]
    assert m.view(2)["overlay"]["text"] == "Your nurse has been called"
    assert m.view(1 + config.OVERLAY_S + 0.1)["overlay"] is None


def test_select_empty_spot_does_nothing():
    m = sm()
    assert m.select("right", 0) == [] and m.kind == "idle"


def test_icons_and_paging_helpers():
    assert icon_for("Adjust pillows") == "pillow"
    assert icon_for("Something else") == "question"
    assert icon_for("Not now") is None
    assert icon_for("Yes, please.") == "check"
    assert paged(["a", "b", "c"], 1) == ("c", None, 2)
