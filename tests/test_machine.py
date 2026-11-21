"""Tests for lookat/machine.py: every rule in CLAUDE.md, with synthetic time."""
from lookat import config
from lookat.machine import ASKING, COOLDOWN, IDLE, QUESTION, ROOM, Machine

DT = 0.1


def run(m, t0, t1, spot, events=(), face=True):
    """Step the machine every DT from t0 (inclusive) to t1 (exclusive). Return all actions."""
    out = []
    n = round((t1 - t0) / DT)
    for i in range(n):
        out += m.step(round(t0 + i * DT, 3), spot, events, face)
    return out


def says(actions):
    return [a[1] for a in actions if a[0] == "say"]


def ask(m, spot, t0=0.0):
    """Look at spot until it is spoken (DWELL_S). Returns the time after."""
    t1 = t0 + config.DWELL_S + DT
    run(m, t0, t1, spot)
    assert m.state == ASKING
    return t1


Q = {"spoken": "Do you want water or juice?", "left": "Water", "middle": "", "right": "Juice"}


# ----- dwell -------------------------------------------------------------
def test_dwell_speaks_spot_name():
    m = Machine()
    acts = run(m, 0.0, 1.1, "left")
    assert acts == [("say", "Yes?")]
    assert m.state == ASKING


def test_short_glance_does_nothing():
    m = Machine()
    acts = run(m, 0.0, 0.8, "left") + run(m, 0.8, 3.0, None)
    assert acts == []
    assert m.state == IDLE


def test_no_spot_never_asks():
    m = Machine()
    assert run(m, 0.0, 5.0, None) == []


def test_switching_spots_restarts_dwell():
    m = Machine()
    acts = run(m, 0.0, 0.6, "left") + run(m, 0.6, 1.2, "right")
    assert acts == []


# ----- confirm -> fire ---------------------------------------------------
def test_confirm_left_answers_yes():
    m = Machine()
    t = ask(m, "left")
    acts = m.step(t, "left", ["confirm"])
    assert acts == [("answer", "Yes"), ("say", "Yes")]
    assert m.state == COOLDOWN


def test_confirm_right_answers_no():
    m = Machine()
    t = ask(m, "right")
    assert m.step(t, "right", ["confirm"]) == [("answer", "No"), ("say", "No")]


def test_confirm_middle_toggles_lamp():
    m = Machine()
    t = ask(m, "middle")
    assert m.step(t, "middle", ["confirm"]) == [("lamp", "toggle")]


def test_nothing_happens_without_confirm():
    m = Machine()
    acts = run(m, 0.0, 10.0, "left")
    assert all(a[0] == "say" for a in acts)


def test_confirm_while_idle_is_ignored():
    m = Machine()
    assert m.step(0.0, "left", ["confirm"]) == []
    assert m.state == IDLE


# ----- cancel ------------------------------------------------------------
def test_look_away_cancels_ask():
    m = Machine()
    t = ask(m, "left")
    m.step(t, "right")
    assert m.state == IDLE
    assert m.step(t + DT, "right", ["confirm"]) == []


def test_spot_is_locked_while_asking():
    m = Machine()
    t = ask(m, "left")
    # A confirm arriving on another spot does not answer for that spot.
    assert m.step(t, "right", ["confirm"]) == []


def test_ask_timeout_cancels_and_needs_look_away():
    m = Machine()
    t = ask(m, "left")
    run(m, t, t + config.ASK_TIMEOUT_S + DT, "left")
    assert m.state == IDLE
    # Still staring: no re-ask, confirm does nothing.
    assert run(m, t + 3.2, t + 6.0, "left") == []
    assert m.step(t + 6.0, "left", ["confirm"]) == []
    # Look away and back: asks again.
    run(m, t + 6.1, t + 6.5, None)
    assert says(run(m, t + 6.5, t + 7.6, "left")) == ["Yes?"]


def test_losing_face_cancels_ask():
    m = Machine()
    t = ask(m, "left")
    m.step(t, None, face=False)
    assert m.state == IDLE
    assert m.step(t + DT, "left", ["confirm"]) == []


# ----- cooldown ----------------------------------------------------------
def test_cooldown_blocks_then_returns_to_idle():
    m = Machine()
    t = ask(m, "left")
    m.step(t, "left", ["confirm"])
    assert run(m, t + DT, t + config.COOLDOWN_S - DT, "left", ["confirm"]) == []
    assert m.state == COOLDOWN
    run(m, t + config.COOLDOWN_S, t + config.COOLDOWN_S + 0.2, None)
    assert m.state == IDLE


def test_after_cooldown_new_selection_works():
    m = Machine()
    t = ask(m, "middle")
    m.step(t, "middle", ["confirm"])
    run(m, t, t + 2.5, None)
    t2 = ask(m, "middle", t + 2.5)
    assert m.step(t2, "middle", ["confirm"]) == [("lamp", "toggle")]


# ----- question mode -----------------------------------------------------
def test_start_question_speaks_it():
    m = Machine()
    assert m.start_question(Q, 0.0) == [("say", Q["spoken"])]
    assert m.mode == QUESTION


def test_question_spots_use_choices():
    m = Machine()
    m.start_question(Q, 0.0)
    assert says(run(m, 0.0, 1.1, "left")) == ["Water?"]


def test_answering_question_returns_to_room():
    m = Machine()
    m.start_question(Q, 0.0)
    t = ask(m, "right")
    assert m.step(t, "right", ["confirm"]) == [("answer", "Juice"), ("say", "Juice")]
    assert m.mode == ROOM and m.question is None


def test_empty_middle_is_not_selectable():
    m = Machine()
    m.start_question(Q, 0.0)
    assert run(m, 0.0, 5.0, "middle") == []


def test_question_times_out():
    m = Machine()
    m.start_question(Q, 0.0)
    acts = run(m, 0.0, config.QUESTION_TIMEOUT_S + 0.2, None)
    assert ("say", "Question closed") in acts and ("cancel_question",) in acts
    assert m.mode == ROOM


def test_nurse_cancel_returns_to_room():
    m = Machine()
    m.start_question(Q, 0.0)
    assert m.cancel_question(1.0) == [("cancel_question",)]
    assert m.mode == ROOM
    assert m.cancel_question(2.0) == []


def test_room_meaning_returns_after_question():
    m = Machine()
    m.start_question(Q, 0.0)
    m.cancel_question(1.0)
    assert says(run(m, 1.0, 2.1, "left")) == ["Yes?"]


# ----- middle menu -------------------------------------------------------
def test_middle_menu_steps_through_items():
    m = Machine()
    acts = run(m, 0.0, 8.0, "middle")
    assert says(acts) == ["Lamp?", "Help?", "Water?", "Pain?"]
    assert m.state == IDLE


def test_confirm_menu_item_sends_it():
    m = Machine()
    t = ask(m, "middle")                       # "Lamp?" at 1.0
    t = 1.0 + 2 * config.MENU_STEP_S + DT      # "Water?" spoken at 4.0
    run(m, 1.1, t, "middle")
    assert m.step(t, "middle", ["confirm"]) == [("menu", "Water"), ("say", "Water")]


def test_menu_only_in_room_mode():
    m = Machine()
    m.start_question({"spoken": "Pick", "left": "A", "middle": "B", "right": "C"}, 0.0)
    assert says(run(m, 0.0, 8.0, "middle")) == ["B?"]


# ----- emergency ---------------------------------------------------------
EMERGENCY = [("say", "Calling the nurse"), ("lamp", "flash"), ("help",)]


def test_emergency_in_idle():
    m = Machine()
    assert m.step(0.0, None, ["emergency"]) == EMERGENCY
    assert m.state == IDLE


def test_emergency_during_ask_cancels_it():
    m = Machine()
    t = ask(m, "left")
    assert m.step(t, "left", ["emergency"]) == EMERGENCY
    assert m.state == IDLE
    assert m.step(t + DT, "left", ["confirm"]) == []


def test_emergency_during_cooldown():
    m = Machine()
    t = ask(m, "left")
    m.step(t, "left", ["confirm"])
    assert m.step(t + DT, "left", ["emergency"]) == EMERGENCY


def test_emergency_keeps_question_open():
    m = Machine()
    m.start_question(Q, 0.0)
    assert m.step(1.0, None, ["emergency"]) == EMERGENCY
    assert m.mode == QUESTION and m.question is not None


def test_emergency_beats_confirm_in_same_frame():
    m = Machine()
    t = ask(m, "left")
    assert m.step(t, "left", ["confirm", "emergency"]) == EMERGENCY
