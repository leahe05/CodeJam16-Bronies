"""What MinimEyes says out loud, decided from what the patient screen shows (pure logic, no audio).

    narrator = Narrator()
    for text in narrator.update(live.screen_json()):   # call every frame
        speaker.say(text, interrupt=...)

It speaks:
  - a new screen: the title, then each option with its direction
        "Would you like help adjusting your pillows? Left: Yes, please. Right: No, thanks. Center: Return to main menu."
  - the option the patient locked on (focus "asking"), as a question: "Yes, please?"
  - the call-nurse alert: "Calling your nurse. Help is on the way."
Returns a list of (text, interrupt): interrupt=True means drop anything still waiting to be said
(the screen moved on, old sentences are stale).
"""
from typing import Optional

DIRECTION = {"left": "Left", "middle": "Center", "right": "Right"}
ORDER = ("left", "right", "middle")


def _label(opt) -> Optional[str]:
    return (opt or {}).get("label") if isinstance(opt, dict) else None


def _sentence(text: str) -> str:
    text = (text or "").strip()
    return text if not text or text[-1] in ".?!" else text + "."


def describe(screen: dict) -> str:
    """The whole screen in one short paragraph."""
    parts = [_sentence(screen.get("title"))]
    if screen.get("subtitle") and screen.get("screen") in ("idle", "nurse"):
        parts.append(_sentence(screen["subtitle"]))
    for spot in ORDER:
        label = _label(screen.get(spot))
        if label:
            parts.append(f"{DIRECTION[spot]}: {_sentence(label)}")
    return " ".join(p for p in parts if p)


class Narrator:
    def __init__(self):
        self._screen_key = None
        self._asking = None
        self._overlay = False

    def update(self, s: dict) -> list:
        out = []
        overlay = s.get("overlay") or {}
        if overlay.get("kind") == "calibrate":       # calibration speaks its own prompts
            self._screen_key = None
            return out

        nurse_called = overlay.get("kind") == "call_nurse"
        if nurse_called and not self._overlay:
            out.append(("Calling your nurse. Help is on the way.", True))
        self._overlay = nurse_called

        key = (s.get("screen"), s.get("title"), s.get("subtitle"),
               tuple(_label(s.get(spot)) for spot in ORDER))
        if key != self._screen_key:
            self._screen_key = key
            self._asking = None
            if not nurse_called:
                out.append((describe(s), True))
            return out

        focus = s.get("focus") or {}
        asking = focus.get("spot") if focus.get("state") == "asking" else None
        if asking and asking != self._asking:
            label = _label(s.get(asking))
            if label:
                out.append((label.rstrip(".?!") + "?", True))
        self._asking = asking
        return out
