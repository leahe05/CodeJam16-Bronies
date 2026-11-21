"""LookAt state machine: pure logic, no camera, no threads, no I/O.

Call step(now, spot, events, face) once per camera frame. It returns a list of
actions for the workers to carry out:
    ("say", text)          speak text to the patient
    ("lamp", "toggle")     toggle the lamp
    ("lamp", "flash")      flash the lamp (emergency)
    ("answer", text)       send the patient's answer to the nurse page
    ("menu", item)         send a menu request (Help / Water / Pain) to the nurse page
    ("help",)              emergency alert to the nurse page
    ("cancel_question",)   the open question was closed without an answer

States: IDLE -> ASKING (after DWELL_S on one spot) -> FIRE -> COOLDOWN -> IDLE.
Modes: ROOM (left=yes, middle=lamp+menu, right=no) and QUESTION (spots = the choices).
"""
from typing import Optional

from lookat import config

EPS = 1e-9  # float tolerance for time comparisons

IDLE = "IDLE"
ASKING = "ASKING"
COOLDOWN = "COOLDOWN"
ROOM = "ROOM"
QUESTION = "QUESTION"

ROOM_LABELS = {"left": "Yes", "middle": "Lamp", "right": "No"}


class Machine:
    def __init__(self) -> None:
        self.state = IDLE
        self.mode = ROOM
        self.question: Optional[dict] = None
        self.question_at = 0.0
        self._candidate: Optional[str] = None   # spot being dwelled on in IDLE
        self._since = 0.0                       # when the candidate started
        self._need_release = False              # after a timeout: look away before asking again
        self._locked: Optional[str] = None      # spot locked while ASKING
        self._ask_start = 0.0                   # when the current item was spoken
        self._menu_index = 0                    # middle in ROOM: 0 = Lamp, 1.. = MENU_ITEMS
        self._cooldown_until = 0.0

    # ----- nurse side -------------------------------------------------------
    def start_question(self, question: dict, now: float) -> list:
        """Open a question. question has keys spoken, left, middle, right."""
        self.mode = QUESTION
        self.question = dict(question)
        self.question_at = now
        self._go_idle(None, now)
        return [("say", question["spoken"])]

    def cancel_question(self, now: float) -> list:
        """The nurse cancelled the question."""
        if self.mode != QUESTION:
            return []
        self._close_question()
        self._go_idle(None, now)
        return [("cancel_question",)]

    # ----- per frame -------------------------------------------------------
    def step(self, now: float, spot: Optional[str], events=(), face: bool = True) -> list:
        events = set(events or ())

        # 1. Emergency beats everything, in any state and mode. The question stays open.
        if "emergency" in events:
            self._go_idle(spot, now)
            self._need_release = True
            return [("say", "Calling the nurse"), ("lamp", "flash"), ("help",)]

        actions = []

        # 2. Question timeout.
        if self.mode == QUESTION and now - self.question_at >= config.QUESTION_TIMEOUT_S - EPS:
            self._close_question()
            self._go_idle(spot, now)
            actions += [("say", "Question closed"), ("cancel_question",)]

        # 3. No face: cancel whatever was happening.
        if not face:
            if self.state == ASKING:
                self._go_idle(None, now)
            elif self.state == IDLE:
                self._candidate = None
            return actions

        # 4. Cooldown.
        if self.state == COOLDOWN:
            if now < self._cooldown_until - EPS:
                return actions
            self._go_idle(spot, now)
            self._need_release = True  # look away before the next selection

        # 5. ASKING: wait for confirm, look-away or timeout.
        if self.state == ASKING:
            if spot != self._locked:
                self._go_idle(spot, now)
                return actions
            if "confirm" in events:
                return actions + self._fire(now)
            if self._is_room_middle():
                if now - self._ask_start >= config.MENU_STEP_S - EPS:
                    self._menu_index += 1
                    if self._menu_index > len(config.MENU_ITEMS):
                        self._timeout(spot, now)
                    else:
                        self._ask_start = now
                        actions.append(("say", self._label(self._locked) + "?"))
            elif now - self._ask_start >= config.ASK_TIMEOUT_S - EPS:
                self._timeout(spot, now)
            return actions

        # 6. IDLE: dwell on a selectable spot.
        if spot != self._candidate:
            self._candidate = spot
            self._since = now
            self._need_release = False
            return actions
        if self._need_release or not self._selectable(spot):
            return actions
        if now - self._since >= config.DWELL_S - EPS:
            self.state = ASKING
            self._locked = spot
            self._ask_start = now
            self._menu_index = 0
            actions.append(("say", self._label(spot) + "?"))
        return actions

    # ----- helpers ---------------------------------------------------------
    def _is_room_middle(self) -> bool:
        return self.mode == ROOM and self._locked == "middle"

    def _selectable(self, spot: Optional[str]) -> bool:
        if spot not in config.SPOTS:
            return False
        if self.mode == QUESTION:
            return bool((self.question or {}).get(spot))
        return True

    def _label(self, spot: str) -> str:
        if self.mode == QUESTION:
            return self.question[spot]
        if spot == "middle" and self._menu_index > 0:
            return config.MENU_ITEMS[self._menu_index - 1]
        return ROOM_LABELS[spot]

    def _fire(self, now: float) -> list:
        spot = self._locked
        if self.mode == QUESTION:
            choice = self.question[spot]
            actions = [("answer", choice), ("say", choice)]
            self._close_question()
        elif spot == "middle" and self._menu_index > 0:
            item = config.MENU_ITEMS[self._menu_index - 1]
            actions = [("menu", item), ("say", item)]
        elif spot == "middle":
            actions = [("lamp", "toggle")]
        else:
            text = ROOM_LABELS[spot]
            actions = [("answer", text), ("say", text)]
        self.state = COOLDOWN
        self._cooldown_until = now + config.COOLDOWN_S
        self._locked = None
        return actions

    def _timeout(self, spot: Optional[str], now: float) -> None:
        self._go_idle(spot, now)
        self._need_release = True  # don't ask again until the patient looks away

    def _go_idle(self, spot: Optional[str], now: float) -> None:
        self.state = IDLE
        self._locked = None
        self._candidate = spot
        self._since = now
        self._need_release = False

    def _close_question(self) -> None:
        self.mode = ROOM
        self.question = None


if __name__ == "__main__":
    # Self-test: look at the middle for 2 s, raise eyebrows, look away.
    m = Machine()
    script = [(0.0, 1.5, "middle", ()), (1.5, 1.6, "middle", ("confirm",)), (1.6, 5.0, None, ())]
    ok = False
    for start, end, spot, ev in script:
        t = start
        while t < end - EPS:
            for a in m.step(round(t, 3), spot, ev):
                print(f"{t:5.1f}s  {a}")
                ok = ok or a == ("lamp", "toggle")
            t += 0.1
    print("PASS" if ok else "FAIL")
