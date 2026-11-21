"""Tests for lookat/machine.py (the selection engine), with synthetic time."""
from lookat import config
from lookat.machine import ASKING, COOLDOWN, IDLE, Machine

DT = 0.1
ALL = ("left", "middle", "right")


def run(m, start, end, spot, events=(), face=True, active=ALL):
    """Step every DT from start to end (exclusive); return (actions, next time)."""
    out, t = [], start
    while t < end - 1e-9:
        out += m.step(round(t, 3), spot, events, face, active)
        t = round(t + DT, 3)
    return out, t


def test_dwell_locks_the_spot():
    m = Machine()
    run(m, 0, config.DWELL_S + DT, "left")
    assert m.state == ASKING and m.view() == {"spot": "left", "state": "asking"}


def test_no_lock_before_dwell():
    m = Machine()
    run(m, 0, config.DWELL_S - 2 * DT, "left")
    assert m.state == IDLE and m.view() == {"spot": "left", "state": "dwell"}


def test_confirm_selects_and_flashes():
    m = Machine()
    _, t = run(m, 0, 1.5, "right")
    assert m.step(t, "right", ("confirm",)) == [("select", "right")]
    assert m.state == COOLDOWN and m.view() == {"spot": "right", "state": "chosen"}


def test_nothing_without_confirm():
    m = Machine()
    acts, _ = run(m, 0, 10, "left")
    assert acts == []


def test_confirm_in_idle_does_nothing():
    m = Machine()
    assert m.step(0, "left", ("confirm",)) == []


def test_look_away_cancels():
    m = Machine()
    _, t = run(m, 0, 1.5, "left")
    m.step(t, "right")
    assert m.state == IDLE
    assert m.step(t + DT, "right", ("confirm",)) == []


def test_ask_timeout_then_must_look_away():
    m = Machine()
    _, t = run(m, 0, 1.0 + config.ASK_TIMEOUT_S + 0.2, "left")
    assert m.state == IDLE
    acts, t = run(m, t, t + 3, "left")
    assert m.state == IDLE and acts == []
    run(m, t, t + 0.3, None)
    run(m, t + 0.3, t + 0.3 + config.DWELL_S + DT, "left")
    assert m.state == ASKING


def test_inactive_spot_never_locks():
    m = Machine()
    run(m, 0, 3, "right", active=("left", "middle"))
    assert m.state == IDLE


def test_lock_dropped_when_spot_becomes_inactive():
    m = Machine()
    _, t = run(m, 0, 1.5, "right")
    m.step(t, "right", active=("left",))
    assert m.state == IDLE


def test_cooldown_then_look_away_before_choosing_again():
    m = Machine()
    _, t = run(m, 0, 1.5, "left")
    m.step(t, "left", ("confirm",))
    acts, t = run(m, t + DT, t + 5, "left", ("confirm",))
    assert acts == []
    run(m, t, t + 0.3, "middle")
    acts, _ = run(m, t + 0.3, t + 2, "middle")
    assert m.state == ASKING


def test_long_raise_first_in_every_state():
    for setup in (0.0, 1.5):          # IDLE, ASKING
        m = Machine()
        _, t = run(m, 0, setup, "left")
        assert m.step(t, "left", ("emergency", "confirm")) == [("call_nurse",)]
    m = Machine()
    _, t = run(m, 0, 1.5, "left")
    m.step(t, "left", ("confirm",))
    assert m.step(t + DT, "left", ("emergency",)) == [("call_nurse",)]   # COOLDOWN


def test_long_raise_works_with_no_active_spots_and_no_face():
    m = Machine()
    assert m.step(0, None, ("emergency",), face=False, active=()) == [("call_nurse",)]


def test_no_face_cancels_asking():
    m = Machine()
    _, t = run(m, 0, 1.5, "left")
    m.step(t, None, face=False)
    assert m.state == IDLE and m.view() is None


def test_reset_drops_lock_and_needs_look_away():
    m = Machine()
    _, t = run(m, 0, 1.5, "left")
    m.reset(t)
    assert m.state == IDLE
    run(m, t, t + 3, "left")
    assert m.state == IDLE


def test_busy_while_choosing():
    m = Machine()
    assert not m.busy
    run(m, 0, 0.5, "left")
    assert m.busy
    run(m, 0.5, 0.6, None)
    assert not m.busy
    run(m, 0.6, 2.0, "right", active=("left", "middle"))   # staring at an empty spot
    assert not m.busy and m.view() is None
