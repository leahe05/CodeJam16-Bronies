"""The selection engine: turns gaze + eyebrow events into choices.
Pure logic: no camera, no threads, no I/O. It knows nothing about menus; the
screens (lookat/screens.py) say which spots can be chosen and what they mean.

Call step(now, spot, events, face, active) once per camera frame:
    now     seconds (time.monotonic())
    spot    "left" / "middle" / "right" / None, from the gaze model
    events  from gestures.py: "confirm" (short raise), "emergency" (long raise)
    face    False when no face is in view
    active  the spots that have an option on the current screen
It returns a list of actions:
    ("select", spot)   the patient chose the option at spot
    ("call_nurse",)    long eyebrow raise, on any screen

States: IDLE -> (look at an active spot for DWELL_S) -> ASKING (spot locked,
highlighted) -> short raise within ASK_TIMEOUT_S -> select -> COOLDOWN.
Rules (CLAUDE.md): the long raise is checked first; looking away, a timeout or
losing the face cancels; after a choice or a timeout the patient must look away
before the same spot can be chosen again.
"""
from typing import Iterable, Optional

from lookat import config

EPS = 1e-9  # float tolerance for time comparisons

IDLE = "IDLE"
ASKING = "ASKING"
COOLDOWN = "COOLDOWN"


class Machine:
    def __init__(self) -> None:
        self.state = IDLE
        self._candidate: Optional[str] = None   # spot being looked at in IDLE
        self._since = 0.0                       # when the candidate started
        self._need_release = False              # look away before choosing again
        self._locked: Optional[str] = None      # spot locked while ASKING
        self._ask_start = 0.0
        self._cooldown_until = 0.0
        self._chosen: Optional[str] = None      # last chosen spot (for the flash)
        self._chosen_at = -1e9
        self._now = 0.0
        self._active: set = set(config.SPOTS)

    def step(self, now: float, spot: Optional[str], events: Iterable[str] = (), face: bool = True,
             active: Iterable[str] = tuple(config.SPOTS)) -> list:
        self._now = now
        events = set(events or ())
        active = set(active or ())
        self._active = active

        # 1. The long raise beats everything, on every screen.
        if "emergency" in events:
            self._go_idle(spot, now)
            self._need_release = True
            return [("call_nurse",)]

        # 2. No face: cancel whatever was happening.
        if not face:
            if self.state == ASKING:
                self._go_idle(None, now)
            elif self.state == IDLE:
                self._candidate = None
            return []

        # 3. Cooldown after a choice.
        if self.state == COOLDOWN:
            if now < self._cooldown_until - EPS:
                return []
            self._go_idle(spot, now)
            self._need_release = True

        # 4. ASKING: wait for the raise, a look-away or the timeout.
        if self.state == ASKING:
            if spot != self._locked or self._locked not in active:
                self._go_idle(spot, now)
                return []
            if "confirm" in events:
                chosen = self._locked
                self.state = COOLDOWN
                self._cooldown_until = now + config.COOLDOWN_S
                self._locked = None
                self._chosen, self._chosen_at = chosen, now
                return [("select", chosen)]
            if now - self._ask_start >= config.ASK_TIMEOUT_S - EPS:
                self._go_idle(spot, now)
                self._need_release = True
            return []

        # 5. IDLE: dwell on an active spot.
        if spot != self._candidate:
            self._candidate = spot
            self._since = now
            self._need_release = False
            return []
        if self._need_release or spot not in active:
            return []
        if now - self._since >= config.DWELL_S - EPS:
            self.state = ASKING
            self._locked = spot
            self._ask_start = now
        return []

    def reset(self, now: float) -> None:
        """The screen changed under the patient (e.g. the nurse sent a question):
        drop any lock, and make them look away before choosing."""
        self._go_idle(self._candidate, now)
        self._need_release = self._candidate is not None

    @property
    def busy(self) -> bool:
        """True while the patient is in the middle of choosing (don't interrupt):
        locked, in cooldown, or looking at a choosable spot for 0.3 s or more."""
        if self.state != IDLE:
            return True
        return (self._candidate in self._active and not self._need_release
                and self._now - self._since >= 0.3)

    def view(self) -> Optional[dict]:
        """The highlight for the patient screen: {"spot", "state"} or None."""
        if self._chosen and self._now - self._chosen_at < config.CHOSEN_FLASH_S - EPS:
            return {"spot": self._chosen, "state": "chosen"}
        if self.state == ASKING:
            return {"spot": self._locked, "state": "asking"}
        if self.state == IDLE and self._candidate in self._active and not self._need_release:
            return {"spot": self._candidate, "state": "dwell"}
        return None

    def _go_idle(self, spot: Optional[str], now: float) -> None:
        self.state = IDLE
        self._locked = None
        self._candidate = spot
        self._since = now
        self._need_release = False


if __name__ == "__main__":
    # Self-test: look left 1.5 s, raise eyebrows, look away; then a long raise.
    m = Machine()
    script = [(0.0, 1.5, "left", ()), (1.5, 1.6, "left", ("confirm",)), (1.6, 3.0, None, ()),
              (3.0, 3.1, "middle", ("emergency",))]
    got = []
    for start, end, spot, ev in script:
        t = start
        while t < end - EPS:
            for a in m.step(round(t, 3), spot, ev):
                print(f"{t:5.1f}s  {a}")
                got.append(a)
            t += 0.1
    print("PASS" if got == [("select", "left"), ("call_nurse",)] else "FAIL")
