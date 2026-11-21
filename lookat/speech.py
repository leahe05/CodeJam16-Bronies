"""Spoken feedback, for when the patient can't see the screen well (looking far left/right).

    speech = Speech()                      # one Windows voice for the whole program
    speech("Look at the LEFT card")        # queued: calibration prompts, in order
    speech.say("Lamp on.")                 # interrupts: only the latest news matters
    speech.say("")                         # just stop talking
    live.announcer = Announcer(speech.say, queue=speech)   # Live.frame() calls update(...)

What the Announcer says, following the gaze:
  - the patient looks at a card (dwell glow)  -> "<label>."  (said in full, never cut off by a
                                                 gaze flicker or by the cue)
  - the card locks (asking)                   -> "Raise your eyebrows."  QUEUED: plays the moment
                                                 the name has finished (or at once if it has)
  - the patient looks at a DIFFERENT card     -> its name replaces whatever was being said
  - a choice is made        -> "<label>." plus the new screen's title if the screen changed
  - a new screen appears on its own (suggestion, check-in, nurse question) -> its title
  - a long raise            -> "Calling your nurse."
Not spoken: timeouts back to the home screen, live numbers in titles (the temperature
title changes every second), the calibration overlay (calibration speaks itself).

Windows: one persistent PowerShell System.Speech process reading lines from stdin.
A line starting with "!" cancels whatever is being said first. Elsewhere: print only.
"""
import subprocess
import sys
import time
from typing import Callable, Optional

RAISE = "Raise your eyebrows."
NAME_AFTER_S = 0.3      # look at a card this long before its name is said (skips gaze flicker)
RENAME_S = 2.0          # the same card's name is not repeated within this time
VOICE = "Microsoft Zira Desktop"   # calmer than the default David; falls back if missing
NURSE_CALLED = "Calling your nurse. Help is on the way."
# screens whose title is the news (a question or a suggestion); on the others it's a live
# status like "The room is 23.4 °C" that must not be read out again every time it changes
TITLE_IS_NEWS = ("suggestion", "checkin", "assistant", "nurse", "room")

_SCRIPT = ("[Console]::InputEncoding = [Text.Encoding]::UTF8; Add-Type -AssemblyName System.Speech; "
           "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
           f"try {{ $s.SelectVoice('{VOICE}') }} catch {{ }}; $s.Rate = 0; $s.Volume = 100; "
           "while ($null -ne ($l = [Console]::In.ReadLine())) { "
           "if ($l.StartsWith('!')) { $s.SpeakAsyncCancelAll(); $l = $l.Substring(1) }; "
           "if ($l.Length -gt 0) { [void]$s.SpeakAsync($l) } }")


def speakable(text: str) -> str:
    """One line, no symbols the voice reads badly."""
    return " ".join(str(text).replace("°C", " degrees").replace("°", " degrees").split())


class Speech:
    def __init__(self, enabled: bool = True):
        self.proc = None
        if enabled and sys.platform == "win32":
            try:
                self.proc = subprocess.Popen(["powershell", "-NoProfile", "-Command", _SCRIPT],
                                             stdin=subprocess.PIPE, text=True, encoding="utf-8")
            except OSError as e:
                print(f"[voice off: {e}]")

    def __call__(self, text: str) -> None:
        """Queued after anything already being said (calibration prompts)."""
        print(f"[say] {text}")
        self._send(speakable(text))

    def say(self, text: str) -> None:
        """Stops what is being said and says this instead ("" = just stop)."""
        if text:
            print(f"[voice] {text}")
        self._send("!" + speakable(text))

    def _send(self, line: str) -> None:
        if self.proc is None:
            return
        try:
            self.proc.stdin.write(line + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError) as e:   # the voice process died: keep running silently
            print(f"[voice failed: {e}]")
            self.proc = None

    def close(self) -> None:
        if self.proc is not None:
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=5)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                self.proc.kill()
            self.proc = None


def _label(screen: dict, spot: Optional[str]) -> Optional[str]:
    opt = screen.get(spot) if spot else None
    return opt.get("label") if isinstance(opt, dict) else None


def screen_key(screen: dict) -> tuple:
    """What makes a screen 'new' for the voice: its kind, options and page (+ title when it's news)."""
    kind = screen.get("screen")
    return (kind, screen.get("badge"), screen.get("title") if kind in TITLE_IS_NEWS else None,
            _label(screen, "left"), _label(screen, "middle"), _label(screen, "right"),
            (screen.get("page") or {}).get("index"))


def screen_line(screen: dict) -> str:
    title = screen.get("title") or ""
    if screen.get("screen") == "nurse" and title:
        return f"Your nurse asks: {title}"
    return title


class Announcer:
    def __init__(self, say: Callable[[str], None], clock: Callable[[], float] = time.monotonic,
                 queue: Optional[Callable[[str], None]] = None):
        self.say = say                           # interrupts what is being said
        self.queue = queue or say                # plays after what is being said
        self.clock = clock
        self._screen: Optional[tuple] = None
        self._target: Optional[tuple] = None     # (screen key, spot) being looked at (dwell/asking)
        self._target_since = 0.0
        self._named: dict = {}                   # (screen key, spot) -> when its name was said
        self._cued: Optional[tuple] = None       # the lock "Raise your eyebrows" was said for

    def update(self, screen: dict, focus: Optional[dict], chosen: Optional[str], events=(),
               now: Optional[float] = None) -> Optional[str]:
        """Call once per frame with the patient screen, the focus highlight, the label chosen this
        frame (or None) and the frame's patient events. Speaks at most one line; returns it."""
        t = self.clock() if now is None else now
        key = screen_key(screen)
        changed = self._screen is not None and key != self._screen
        first = self._screen is None
        self._screen = key
        parts = []
        if any(ev and ev[0] == "call_nurse" for ev in events):
            parts.append(NURSE_CALLED)
        elif chosen:
            parts.append(f"{chosen}.")
            if changed and screen.get("screen") != "idle":
                parts.append(screen_line(screen))
        elif changed and not first and screen.get("screen") != "idle":
            parts.append(screen_line(screen))      # a pop-up or a nurse question arrived
        news = bool(parts)

        state = focus.get("state") if focus else None
        target = (key, focus["spot"]) if state in ("dwell", "asking") else None
        if target != self._target:
            self._target, self._target_since = target, t
        label = _label(screen, target[1]) if target else None
        if not news and target and label and target != self._named_target(t):
            if state == "asking" or t - self._target_since >= NAME_AFTER_S - 1e-9:
                parts.append(f"{label}.")           # name the card while the patient looks at it
                self._named[target] = t
        cue = state == "asking" and target != self._cued
        self._cued = target if state == "asking" else None

        text = " ".join(p for p in parts if p)
        if text:
            self.say(text)                          # news or a new card's name: replaces the old
        if cue:
            self.queue(RAISE)                       # right after the name, never over it
        return " ".join(p for p in (text, RAISE if cue else "") if p) or None

    def _named_target(self, t: float) -> Optional[tuple]:
        """The current target if its name was said recently (so it isn't repeated), else None."""
        when = self._named.get(self._target)
        return self._target if when is not None and t - when < RENAME_S else None
