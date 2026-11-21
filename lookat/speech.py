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

Voice (Lea's choice, PR #8): a calm, natural female voice, slightly slower than default.
Windows: one persistent PowerShell System.Speech process reading lines from stdin, first
installed female voice (usually Zira), Rate -1; a line starting with "!" cancels first.
macOS: the `say` command with the best installed female voice (gaze.SystemVoice.MAC_VOICES),
one line at a time from a queue; interrupting kills the current `say`. Elsewhere: print only.
"""
import itertools
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from typing import Callable, Optional

from lookat.state import log_event

RAISE = "Raise your eyebrows."
NAME_AFTER_S = 0.3      # look at a card this long before its name is said (skips gaze flicker)
RENAME_S = 6.0        # the same card is not named again within this time (gaze dropouts restart the dwell)
NURSE_CALLED = "Calling your nurse. Help is on the way."
# screens whose title is the news (a question or a suggestion); on the others it's a live
# status like "The room is 23.4 °C" that must not be read out again every time it changes
TITLE_IS_NEWS = ("suggestion", "checkin", "assistant", "nurse", "room")

_SCRIPT = ("[Console]::InputEncoding = [Text.Encoding]::UTF8; Add-Type -AssemblyName System.Speech; "
           "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
           "try { $s.SelectVoiceByHints([System.Speech.Synthesis.VoiceGender]::Female, "
           "[System.Speech.Synthesis.VoiceAge]::Adult) } catch { }; $s.Rate = -1; $s.Volume = 100; "
           "while ($null -ne ($l = [Console]::In.ReadLine())) { "
           "if ($l.StartsWith('!')) { $s.SpeakAsyncCancelAll(); $l = $l.Substring(1) }; "
           "if ($l.Length -gt 0) { [void]$s.SpeakAsync($l) } }")


def sentence(label: str) -> str:
    """'Lamp on' -> 'Lamp on.'; 'No, thanks.' stays as it is."""
    label = str(label).strip()
    return label if label[-1:] in ".!?" else label + "."


def speakable(text: str) -> str:
    """One line, no symbols the voice reads badly."""
    return " ".join(str(text).replace("°C", " degrees").replace("°", " degrees").split())


class Speech:
    def __init__(self, enabled: bool = True):
        self.proc = None                 # Windows: the voice process
        self._mac_q = None               # macOS: lines waiting for `say`
        self._mac_now = None             # macOS: the `say` process speaking now
        if not enabled:
            return
        if sys.platform == "win32":
            try:
                self.proc = subprocess.Popen(["powershell", "-NoProfile", "-Command", _SCRIPT],
                                             stdin=subprocess.PIPE, text=True, encoding="utf-8")
            except OSError as e:
                print(f"[voice off: {e}]")
        elif sys.platform == "darwin" and shutil.which("say"):
            from lookat.gaze import SystemVoice
            self._mac_voice = SystemVoice._pick_mac_voice()
            self._mac_rate = SystemVoice.MAC_RATE
            self._mac_q = queue.Queue()
            threading.Thread(target=self._mac_run, name="voice", daemon=True).start()

    def __call__(self, text: str) -> None:
        """Queued after anything already being said (calibration prompts)."""
        print(f"[say] {text}")
        self._send(speakable(text), interrupt=False)

    def say(self, text: str) -> None:
        """Stops what is being said and says this instead ("" = just stop)."""
        if text:
            print(f"[voice] {text}")
        self._send(speakable(text), interrupt=True)

    def _send(self, line: str, interrupt: bool) -> None:
        if self._mac_q is not None:
            if interrupt:
                self._mac_stop()
            if line:
                self._mac_q.put(line)
            return
        if self.proc is None:
            return
        try:
            self.proc.stdin.write(("!" if interrupt else "") + line + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError) as e:   # the voice process died: keep running silently
            print(f"[voice failed: {e}]")
            self.proc = None

    # ----- macOS -------------------------------------------------------------
    def _mac_stop(self) -> None:
        try:
            while True:
                self._mac_q.get_nowait()
        except queue.Empty:
            pass
        p = self._mac_now
        if p is not None and p.poll() is None:
            p.terminate()

    def _mac_run(self) -> None:
        while True:
            line = self._mac_q.get()
            if line is None:
                return
            voice = ["-v", self._mac_voice] if self._mac_voice else []
            try:
                self._mac_now = subprocess.Popen(["say", *voice, "-r", self._mac_rate, line])
                self._mac_now.wait()
            except OSError as e:
                print(f"[say failed: {e}]")

    def close(self) -> None:
        if self._mac_q is not None:
            self._mac_stop()
            self._mac_q.put(None)
        if self.proc is not None:
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=5)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                self.proc.kill()
            self.proc = None


class WebVoice:
    """The patient page reads the lines aloud (Edge's natural voices, docs/API.md `voice`).
    Same API as Speech: voice(text) queues, voice.say(text) interrupts ("" = stop)."""

    KEEP = 20

    def __init__(self):
        self.lock = threading.Lock()
        self.lines: list = []
        # ids keep growing across restarts, so an open page never mistakes new lines for old ones
        self._ids = itertools.count(int(time.time() * 10))

    def _add(self, text: str, cut: bool) -> None:
        with self.lock:
            self.lines.append({"id": next(self._ids), "text": text, "cut": cut})
            del self.lines[:-self.KEEP]

    def __call__(self, text: str) -> None:
        print(f"[say] {text}")
        self._add(speakable(text), cut=False)

    def say(self, text: str) -> None:
        if text:
            print(f"[voice] {text}")
        self._add(speakable(text), cut=True)

    def recent(self) -> list:
        with self.lock:
            return [dict(x) for x in self.lines]

    def close(self) -> None:
        pass


class EdgeVoice:
    """Microsoft Edge's natural neural voice (edge-tts, needs internet), played on this laptop.
    Same API as Speech: voice(text) queues, voice.say(text) interrupts ("" = stop).

    Same voice, settings and cache (cache/voice/) as Lea's gaze.AvaVoice, which speaks the
    calibration-tool prompts; this one can also stop a line mid-sentence (say), which the
    selection prompts need. Fetching a new line takes ~1 s over the hotspot, so prewarm()
    fetches the menu lines at start: in use they play at once. No internet -> the line is
    said with the laptop voice (Speech) instead, so nothing goes silent."""

    VOICE = "en-US-AvaNeural"           # keep these three equal to gaze.AvaVoice (shared cache)
    RATE, PITCH = "-8%", "-2Hz"
    TIMEOUT_S = 8
    CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache", "voice")

    def __init__(self, voice: str = VOICE):
        self.voice = voice
        self._q: "queue.Queue" = queue.Queue()
        self._gen = 0                    # bumped by say(): anything older is dropped / stopped
        self._fallback = None
        os.makedirs(self.CACHE, exist_ok=True)
        threading.Thread(target=self._run, name="edge-voice", daemon=True).start()

    def __call__(self, text: str) -> None:
        print(f"[say] {text}")
        self._q.put((self._gen, speakable(text)))

    def say(self, text: str) -> None:
        if text:
            print(f"[voice] {text}")
        self._gen += 1
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass
        if text:
            self._q.put((self._gen, speakable(text)))

    def prewarm(self, lines) -> None:
        """Fetch these lines in the background so they play instantly later."""
        def run():
            got = sum(1 for t in dict.fromkeys(lines) if t and self._file(speakable(t)))
            print(f"Voice: {got} lines ready ({self.voice})")
        threading.Thread(target=run, name="edge-prewarm", daemon=True).start()

    def close(self) -> None:
        self.say("")
        self._q.put(None)

    # ----- worker ------------------------------------------------------------
    def _file(self, text: str) -> Optional[str]:
        import hashlib
        key = hashlib.sha1(f"{self.voice}|{self.RATE}|{self.PITCH}|{text}".encode()).hexdigest()[:16]
        path = os.path.join(self.CACHE, key + ".mp3")
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return path
        try:
            import asyncio
            import edge_tts
            tmp = path + ".part"
            comm = edge_tts.Communicate(text, self.voice, rate=self.RATE, pitch=self.PITCH)
            asyncio.run(asyncio.wait_for(comm.save(tmp), self.TIMEOUT_S))
            os.replace(tmp, path)
            return path
        except Exception as e:   # noqa: BLE001 - offline, blocked, or edge-tts missing
            log_event("voice_offline", error=type(e).__name__)
            return None

    def _run(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            gen, text = item
            if gen != self._gen:
                continue
            path = self._file(text)
            if gen != self._gen:
                continue                  # interrupted while fetching
            if path is None:
                if self._fallback is None:
                    self._fallback = Speech()
                self._fallback(text)
                continue
            self._play(path, gen)

    def _play(self, path: str, gen: int) -> None:
        """Windows MCI (winmm): play the mp3, stop at once if say() was called meanwhile."""
        if sys.platform != "win32":
            subprocess.run(["afplay", path]) if shutil.which("afplay") else None
            return
        import ctypes
        mci = ctypes.windll.winmm.mciSendStringW
        buf = ctypes.create_unicode_buffer(32)
        mci(f'open "{path}" type mpegvideo alias lookat_voice', None, 0, None)
        mci("play lookat_voice", None, 0, None)
        try:
            while gen == self._gen:
                mci("status lookat_voice mode", buf, 32, None)
                if buf.value != "playing":
                    break
                time.sleep(0.02)
        finally:
            mci("stop lookat_voice", None, 0, None)
            mci("close lookat_voice", None, 0, None)


def known_lines() -> list:
    """Every fixed line the Announcer can say on the built-in menus (for EdgeVoice.prewarm):
    card names and screen titles; each is said as its own line, so each can be fetched ahead."""
    from lookat.screens import ScreenManager
    lines = [RAISE, NURSE_CALLED]
    for lamp_on in (False, True):
        sm = ScreenManager(room=lambda lamp_on=lamp_on: {"lamp_on": lamp_on, "temp_c": None})
        for kind in ("idle", "room", "lights", "temperature", "assistant"):
            sm._go(kind, 0.0)
            v = sm.view(0.0)
            lines += [sentence(v[spot]["label"]) for spot in ("left", "middle", "right") if v.get(spot)]
            lines.append(screen_line(v))
    return [x for x in dict.fromkeys(lines) if x]


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
    """A new screen, read out the way Lea's narrator does: the title, then each option with its
    direction ("Left: Yes, please. Right: No, thanks. Center: Return to main menu."), so the
    patient knows the choices without turning to read them."""
    from lookat.narrator import describe
    line = describe(screen)
    if screen.get("screen") == "nurse" and screen.get("title"):
        return f"Your nurse asks: {line}"
    return line


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
            parts.append(sentence(chosen))
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
                parts.append(sentence(label))           # name the card while the patient looks at it
                self._named[target] = t
        cue = state == "asking" and target != self._cued
        self._cued = target if state == "asking" else None

        parts = [p for p in parts if p]
        if parts:
            self.say(parts[0])                      # news or a new card's name: replaces the old
            for p in parts[1:]:
                self.queue(p)                       # e.g. the new screen's title, right after
        if cue:
            self.queue(RAISE)                       # right after the name, never over it
        return " ".join(parts + ([RAISE] if cue else [])) or None

    def _named_target(self, t: float) -> Optional[tuple]:
        """The current target if its name was said recently (so it isn't repeated), else None."""
        when = self._named.get(self._target)
        return self._target if when is not None and t - when < RENAME_S else None
