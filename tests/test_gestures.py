"""Tests for lookat/gestures.py with synthetic eyebrow time series (30 fps)."""
from lookat import config
from lookat.gestures import (GestureDetector, compute_threshold, load_settings,
                             make_settings, save_settings)

FPS = 30
DT = 1 / FPS
REST, UP = 0.10, 0.80
BROW = make_settings("brow", compute_threshold(0.20, [0.70, 0.75, 0.80]))   # threshold 0.45


def run(det, series):
    """series: list of (seconds, value or None). Returns all events with their times."""
    events, t = [], 0.0
    for seconds, value in series:
        for _ in range(round(seconds * FPS)):
            f = None if value is None else {"browInnerUp": value, "jawOpen": value}
            events += [(round(t, 2), e) for e in det.update(f, t)]
            t += DT
    return events


def names(events):
    return [e for _, e in events]


# ----- calibration math ----------------------------------------------------
def test_threshold_is_halfway_between_rest_and_weakest_raise():
    c = compute_threshold(0.20, [0.80, 0.60, 0.70])
    assert abs(c["threshold"] - 0.40) < 1e-9
    assert c["ok"]


def test_weak_raises_flag_backup():
    c = compute_threshold(0.30, [0.40, 0.50, 0.42])
    assert not c["ok"]


def test_settings_roundtrip(tmp_path):
    save_settings("test patient", BROW, folder=str(tmp_path))
    assert load_settings("test patient", folder=str(tmp_path))["threshold"] == BROW["threshold"]
    assert load_settings("nobody", folder=str(tmp_path)) is None


# ----- the required cases --------------------------------------------------
def test_half_second_raise_is_one_confirm():
    ev = run(GestureDetector(BROW), [(1, REST), (0.5, UP), (1, REST)])
    assert names(ev) == ["confirm"]


def test_short_raise_does_nothing():
    ev = run(GestureDetector(BROW), [(1, REST), (0.2, UP), (1, REST)])
    assert ev == []


def test_long_hold_is_confirm_then_one_emergency():
    ev = run(GestureDetector(BROW), [(1, REST), (2.5, UP), (1, REST)])
    assert names(ev) == ["confirm", "emergency"]
    confirm_t, emergency_t = ev[0][0], ev[1][0]
    assert abs(emergency_t - confirm_t - (config.EMERGENCY_S - config.CONFIRM_S)) < 0.1


def test_talking_spikes_do_nothing():
    series = [(0.1, UP), (0.2, REST)] * 33   # 0.1 s spike every 0.3 s for ~10 s
    assert run(GestureDetector(BROW), series) == []


# ----- extra rules ----------------------------------------------------------
def test_ten_raises_ten_confirms():
    series = [(0.6, UP), (0.8, REST)] * 10
    assert names(run(GestureDetector(BROW), series)) == ["confirm"] * 10


def test_very_long_hold_only_one_emergency():
    ev = run(GestureDetector(BROW), [(6, UP)])
    assert names(ev) == ["confirm", "emergency"]


def test_losing_face_ends_the_raise():
    ev = run(GestureDetector(BROW), [(0.2, UP), (0.1, None), (0.2, UP)])
    assert ev == []


def test_small_dip_does_not_end_a_hold():
    # dips to 0.40: below threshold 0.45 but above the release line (~0.39)
    series = [(1.0, UP), (0.1, 0.40), (1.5, UP)]
    assert names(run(GestureDetector(BROW), series)) == ["confirm", "emergency"]


# ----- on_release mode (main.py): a long raise must not also choose ----------
def test_on_release_short_raise_confirms_when_it_ends():
    ev = run(GestureDetector(BROW, on_release=True), [(1, REST), (0.5, UP), (1, REST)])
    assert names(ev) == ["confirm"] and 1.49 <= ev[0][0] <= 1.55


def test_on_release_long_raise_is_only_emergency():
    ev = run(GestureDetector(BROW, on_release=True), [(1, REST), (2.5, UP), (1, REST)])
    assert names(ev) == ["emergency"]


def test_on_release_too_short_or_face_lost_does_nothing():
    assert run(GestureDetector(BROW, on_release=True), [(0.2, UP), (1, REST)]) == []
    assert run(GestureDetector(BROW, on_release=True), [(0.5, UP), (0.1, None), (1, REST)]) == []


def test_on_release_ten_raises_ten_confirms():
    series = [(0.6, UP), (0.8, REST)] * 10
    assert names(run(GestureDetector(BROW, on_release=True), series)) == ["confirm"] * 10


def test_mouth_backup_needs_longer_hold():
    mouth = make_settings("mouth", compute_threshold(0.05, [0.6, 0.7, 0.65]))
    assert mouth["key"] == "jawOpen"
    assert run(GestureDetector(mouth), [(0.5, UP), (1, REST)]) == []
    assert names(run(GestureDetector(mouth), [(0.8, UP), (1, REST)])) == ["confirm"]
