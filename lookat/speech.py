"""Spoken feedback, for when the patient can't see the screen well (looking far left/right).

    speech = Speech()                      # one Windows voice for the whole program
    speech("Look at the LEFT card")        # queued: calibration prompts, in order
    speech.say("Lamp on. Raise your eyebrows.")   # interrupts: only the latest news matters
    live.announcer = Announcer(speech.say)        # Live.frame() calls announcer.update(...)

What the Announcer says (one short sentence per moment, never every frame):
  - a card locks (asking)   -> "<label>. Raise your eyebrows."
  - a choice is made        -> "<label>." plus the new screen's title if the screen changed
  - a new screen appears on its own (suggestion, check-in, nurse question) -> its title
  - a long raise            -> "Calling your nurse."
Not spoken: the soft dwell glow, timeouts back to the home screen, live numbers in titles
(the temperature title changes every second), the calibration overlay (calibration speaks itself).

Windows: one persistent PowerShell System.Speech process reading lines from stdin.
A line starting with "!" cancels whatever is being said first. Elsewhere: print only.
"""
import subprocess
import sys
import time
from typing import Callable, Optional

RAISE = "Raise your eyebrows."
REPEAT_S = 8.0          # the same card re-locking within this time is not announced again
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
        """Stops what is being said and says this instead."""
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
    def __init__(self, say: Callable[[str], None], clock: Callable[[], float] = time.monotonic):
        self.say = say
        self.clock = clock
        self._asking: Optional[tuple] = None   # (screen key, spot) of the current lock
        self._said_lock: dict = {}             # (screen key, spot) -> when it was last announced
        self._screen: Optional[tuple] = None

    def update(self, screen: dict, focus: Optional[dict], chosen: Optional[str], events=()) -> Optional[str]:
        """Call once per frame with the patient screen, the focus highlight, the label chosen this
        frame (or None) and the frame's patient events. Speaks at most one line; returns it."""
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

        asking = (key, focus["spot"]) if focus and focus.get("state") == "asking" else None
        if asking and asking != self._asking:
            label = _label(screen, focus["spot"])
            t = self.clock()
            # gaze often drops and re-locks the same card: say it once, not every re-lock
            if label and (chosen or t - self._said_lock.get(asking, -1e9) >= REPEAT_S):
                parts.append(f"{label}. {RAISE}")
                self._said_lock[asking] = t
        self._asking = asking

        text = " ".join(p for p in parts if p)
        if text:
            self.say(text)
        return text or None
