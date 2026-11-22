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
    # one switch per device; the label says what choosing it will do (the room fixture: lamp off, fan off)
    assert labels(m) == ["Turn the lamp on", RETURN, "Turn the fan on"]
    assert m.view(1)["subtitle"] == "Lamp off · Fan off"
    assert m.select("left", 2) == [("device", "lamp", "on")] and m.kind == "room"
    assert m.select("right", 3) == [("device", "fan", "on")] and m.kind == "room"
    m.select("middle", 4)                        # Return to main menu
    assert m.kind == "idle"


def test_room_switch_labels_follow_the_room():
    state = {"lamp_on": False, "fan_on": True}
    m = ScreenManager(room=lambda: state)
    m.select("left", 0)
    assert labels(m) == ["Turn the lamp on", RETURN, "Turn the fan off"]
    assert m.select("right", 1) == [("device", "fan", "off")]
    state["fan_on"] = False
    assert labels(m)[2] == "Turn the fan on"


def test_talk_to_ai_assistant():
    m = sm()
    assert m.select("middle", 1) == [("talk_to_ai",)]
    v = m.view(1)
    assert v["title"] == "How can I help you?" and v["badge"] == "AI ASSISTANT"
    assert labels(m) == ["Get more comfortable", RETURN, "More suggestions"]
    assert m.select("right", 2) == []
    assert labels(m)[0] == "Message my nurse"           # always second
    assert m.select("right", 2) == []
    assert labels(m)[0] == "Quiet for 2 hours"
    assert m.select("left", 3) == [("assistant_choice", "Quiet for 2 hours")]
    assert m.kind == "idle"


def test_assistant_more_suggestions_wraps_and_adjust_room_goes_to_room():
    m = sm()
    m.select("middle", 1)
    for _ in range(5):                                  # 4 options + Message my nurse
        m.select("right", 2)
    assert labels(m)[0] == "Get more comfortable"
    for _ in range(3):
        m.select("right", 3)
    assert labels(m)[0] == "Adjust the room"
    assert m.select("left", 4) == [] and m.kind == "room"


def test_assistant_with_one_option_shows_it_and_message_my_nurse():
    m = sm()
    m.show_assistant(["Water"])
    m.select("middle", 1)
    assert labels(m) == ["Water", RETURN, "Message my nurse"]
    assert m.assistant["options"] == ["Water"]          # the agent's own list is unchanged


def test_message_my_nurse_sends_a_ready_made_message():
    m = sm()
    m.select("middle", 1)
    m.select("right", 2)                                # More suggestions -> Message my nurse
    assert m.select("left", 3) == [] and m.kind == "checkin"
    v = m.view(3)
    assert v["badge"] == "MESSAGE YOUR NURSE" and v["title"] == "What would you like to tell your nurse?"
    assert labels(m) == ["I'm in pain", "More options", "I'm thirsty"]
    assert m.select("right", 4) == [("patient_message", "I'm thirsty")] and m.kind == "idle"


def test_message_my_nurse_return_sends_nothing():
    m = sm()
    m.open_messages(1)
    m.select("middle", 2)
    m.select("middle", 2)
    assert labels(m)[1] == RETURN
    assert m.select("middle", 3) == [] and m.kind == "idle"


def test_plain_nurse_message_has_one_ok_in_the_centre():
    m = sm()
    m.nurse_question("q1", "I'll be there in 5 minutes.", ["OK"], 1, kind="message")
    v = m.view(1)
    assert v["title"] == "I'll be there in 5 minutes." and v["left"] is None and v["right"] is None
    assert v["middle"]["label"] == "OK"
    assert m.select("middle", 2) == [("nurse_answer", "q1", "OK")]


def test_suggestion_shows_its_two_choices_directly():
    m = sm()
    assert m.show_suggestion("s1", "It's getting cold in here.", 0, question="What would help?",
                             options=["Turn the fan off", "Ask nurse for a blanket"])
    v = m.view(0)
    assert v["badge"] == "AI SUGGESTION" and v["title"] == "It's getting cold in here."
    assert v["subtitle"] == "What would help?"
    assert labels(m) == ["Turn the fan off", "Not now", "Ask nurse for a blanket"]
    assert m.select("right", 1) == [("engaged", "s1"), ("answer", "s1", "Ask nurse for a blanket")]
    assert m.kind == "idle"


def test_not_now_dismisses_the_suggestion():
    m = sm()
    m.show_suggestion("s1", "Too warm?", 0)
    assert labels(m) == ["Yes, please.", "Not now", "No, thanks."]
    assert m.select("middle", 1) == [("dismissed", "s1")] and m.kind == "idle"


def test_open_suggestion_engage_opens_paged_checkin():
    m = sm()
    m.show_suggestion("s1", "Anything I can do?", 0, options=["Water", "Pillows", "Position"])
    assert labels(m) == ["Room Controls", "Look center + raise eyebrows to engage", None]
    assert m.select("middle", 1) == [("engaged", "s1")]
    assert m.view(1)["badge"] == "AI CHECK-IN"
    m2 = sm()
    m2.show_suggestion("s2", "Anything I can do?", 0, options=["Water", "Pillows", "Position"])
    assert m2.select("left", 1) == [("dismissed", "s2")] and m2.kind == "room"


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
