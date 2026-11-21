"""Tests for lookat/clock.py with a fake real-time source."""
from datetime import datetime

import pytest

from lookat.clock import Clock, in_window


class FakeReal:
    def __init__(self):
        self.t = datetime(2026, 11, 21, 12, 0).timestamp()

    def __call__(self):
        return self.t


def make():
    real = FakeReal()
    return Clock(real=real), real


def test_starts_at_real_time_and_not_demo():
    c, real = make()
    assert c.hhmm() == "12:00" and not c.demo


def test_set_time_and_time_keeps_running():
    c, real = make()
    c.set_time("02:00")
    assert c.hhmm() == "02:00" and c.demo
    real.t += 90
    assert c.now().strftime("%H:%M:%S") == "02:01:30"


def test_skip_to_2159_then_22():
    c, real = make()
    c.set_time("21:59")
    assert not c.is_night()
    real.t += 60
    assert c.hhmm() == "22:00" and c.is_night()


def test_day_offset_and_advance():
    c, real = make()
    start = c.now().date()
    c.set_time("22:00", day_offset=7)
    assert (c.now().date() - start).days == 7
    c.advance(3600)
    assert c.hhmm() == "23:00"


def test_speed():
    c, real = make()
    c.set_speed(60)
    real.t += 10
    assert c.hhmm() == "12:10"


def test_reset_goes_back_to_real_time():
    c, real = make()
    c.set_time("02:00")
    c.reset()
    assert c.hhmm() == "12:00" and not c.demo


def test_night_window_wraps_midnight():
    d = datetime(2026, 11, 21)
    assert in_window(d.replace(hour=23), "22:00", "07:00")
    assert in_window(d.replace(hour=2), "22:00", "07:00")
    assert not in_window(d.replace(hour=7), "22:00", "07:00")
    assert not in_window(d.replace(hour=12), "22:00", "07:00")
    assert in_window(d.replace(hour=12), "09:00", "17:00")


def test_bad_time_rejected():
    c, _ = make()
    with pytest.raises(ValueError):
        c.set_time("25:00")
