"""The demo wall clock. Every module uses lookat.clock.clock for time of day,
never datetime.now(), so the demo can jump to 2 AM or 9:59 PM.

    from lookat.clock import clock
    clock.now()            -> datetime (demo time)
    clock.set_time("02:00")
    clock.is_night()

Timers (dwell, cooldown, fades) keep using time.monotonic(); only "what time
of day is it for the patient" comes from here.
"""
import time
from datetime import datetime, timedelta
from typing import Callable

from lookat import config


def parse_hhmm(text: str) -> tuple:
    h, m = text.strip().split(":")
    h, m = int(h), int(m)
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"bad time {text!r}, expected HH:MM")
    return h, m


def in_window(when: datetime, start: str, end: str) -> bool:
    """True if when's time of day is in [start, end). The window may wrap midnight."""
    t = (when.hour, when.minute)
    s, e = parse_hhmm(start), parse_hhmm(end)
    return s <= t < e if s <= e else (t >= s or t < e)


class Clock:
    def __init__(self, real: Callable[[], float] = time.time, speed: float = 1.0):
        self._real = real                      # real seconds (injectable for tests)
        self.speed = speed
        self.reset(speed)

    def now(self) -> datetime:
        return self._anchor + timedelta(seconds=(self._real() - self._anchor_real) * self.speed)

    def set_time(self, hhmm: str, day_offset: int = 0) -> datetime:
        """Jump to HH:MM today (+ day_offset days), seconds at 0."""
        h, m = parse_hhmm(hhmm)
        target = self.now().replace(hour=h, minute=m, second=0, microsecond=0) + timedelta(days=day_offset)
        self._reanchor(target)
        self.demo = True
        return target

    def advance(self, seconds: float) -> datetime:
        self._reanchor(self.now() + timedelta(seconds=seconds))
        self.demo = True
        return self.now()

    def set_speed(self, speed: float) -> None:
        self._reanchor(self.now())
        self.speed = speed

    def reset(self, speed: float = 1.0) -> None:
        """Back to the real time."""
        self._anchor_real = self._real()
        self._anchor = datetime.fromtimestamp(self._anchor_real)
        self.speed = speed
        self.demo = False                      # True once the demo changed the time

    def is_night(self) -> bool:
        return in_window(self.now(), config.NIGHT_START, config.NIGHT_END)

    def hhmm(self) -> str:
        return self.now().strftime("%H:%M")

    def _reanchor(self, target: datetime) -> None:
        self._anchor_real = self._real()
        self._anchor = target


clock = Clock()


if __name__ == "__main__":
    c = Clock()
    print("real now:", c.hhmm(), "night:", c.is_night())
    c.set_time("02:00")
    print("set 02:00 ->", c.hhmm(), "night:", c.is_night(), "demo:", c.demo)
    c.advance(30 * 60)
    print("advance 30 min ->", c.hhmm())
    c.set_time("21:59")
    c.advance(60)
    ok = c.hhmm() == "22:00" and c.is_night()
    c.set_time("07:30")
    ok = ok and not c.is_night()
    print("PASS" if ok else "FAIL")
