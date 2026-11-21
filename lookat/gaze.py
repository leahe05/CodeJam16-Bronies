"""Calibration + which spot the patient is looking at (owner P1).

GazeModel: standardise gaze_vector -> k-nearest-neighbours (k=7, plain NumPy) ->
majority vote over the last 10 frames. predict() returns (spot, confidence), or
(None, confidence) when confidence < MIN_CONF.

Self-test: py -3.11 -m lookat.gaze --patient NAME [--recalibrate]   (q quits)
"""
import hashlib
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import Counter, deque
from itertools import combinations

import numpy as np

from lookat import config

K = 7
VOTE_FRAMES = 10
MIN_CONF = 0.7
CALIB_FRAMES = 90          # per spot, split over ROUNDS
ROUNDS = 2                 # left, middle, right, then again: captures natural variation
SETTLE_S = 2.0
# Calibrate on the CARDS (where the patient will really look), not the screen edges.
SPOKEN_NAMES = {"left": "the LEFT card", "middle": "the MIDDLE card", "right": "the RIGHT card"}

# Smallest spread per feature (order = vision.GAZE_FEATURES) used when standardising.
# A patient holds very still during calibration, so e.g. head yaw varies by only ~2°;
# without a floor, a 3° head turn later counts as a huge change and wins over the eyes.
MIN_STD = np.array([6.0, 6.0, 6.0]                  # pitch, yaw, roll (degrees)
                   + [0.05] * 4 + [0.10] * 4        # eyeLookIn/Out, eyeLookUp/Down
                   + [0.03, 0.04, 0.03, 0.04])      # iris x, y (both eyes)


def _spread(X) -> np.ndarray:
    std = np.asarray(X, dtype=float).std(axis=0)
    floor = MIN_STD if std.shape == MIN_STD.shape else 1e-6
    return np.maximum(std, floor)

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CALIB_DIR = os.path.join(_REPO, "calib")


def _calib_path(patient: str, folder: str = CALIB_DIR) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", patient) or "patient"
    return os.path.join(folder, f"{safe}.npz")


class GazeModel:
    def __init__(self, X=None, y=None):
        self.history = deque(maxlen=VOTE_FRAMES)
        self.X = self.y = self.mean = self.std = self.Xs = None
        if X is not None:
            self.fit(X, y)

    def fit(self, X, y) -> "GazeModel":
        self.X = np.asarray(X, dtype=float)
        self.y = np.asarray(y, dtype=str)
        self.mean = self.X.mean(axis=0)
        self.std = _spread(self.X)
        self.Xs = (self.X - self.mean) / self.std
        self.reset()
        return self

    def reset(self) -> None:
        """Forget the vote history (call when the face is lost)."""
        self.history.clear()

    def classify(self, vec) -> tuple:
        """One frame, no voting: (spot, share of the K neighbours that agree)."""
        v = (np.asarray(vec, dtype=float) - self.mean) / self.std
        d = ((self.Xs - v) ** 2).sum(axis=1)
        k = min(K, len(d))
        nearest = self.y[np.argpartition(d, k - 1)[:k]]
        spot, n = Counter(nearest).most_common(1)[0]
        return str(spot), n / k

    def predict(self, features) -> tuple:
        """features = FaceTracker features dict (or None). Returns (spot|None, confidence)."""
        if features is None:
            self.reset()
            return None, 0.0
        self.history.append(self.classify(features["gaze_vector"]))
        winner = Counter(s for s, _ in self.history).most_common(1)[0][0]
        conf = sum(c for s, c in self.history if s == winner) / len(self.history)
        return (winner if conf >= MIN_CONF else None), conf

    def save(self, patient: str, folder: str = CALIB_DIR) -> str:
        os.makedirs(folder, exist_ok=True)
        path = _calib_path(patient, folder)
        np.savez(path, X=self.X, y=self.y)
        return path

    @classmethod
    def load(cls, patient: str, folder: str = CALIB_DIR) -> "GazeModel":
        with np.load(_calib_path(patient, folder)) as d:
            return cls(d["X"], d["y"])

    @staticmethod
    def exists(patient: str, folder: str = CALIB_DIR) -> bool:
        return os.path.exists(_calib_path(patient, folder))


def separation_scores(X, y) -> dict:
    """Per spot pair: distance between spot means / average within-spot spread (> 3 good)."""
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=str)
    Xs = (X - X.mean(axis=0)) / _spread(X)
    means, spreads = {}, {}
    for s in dict.fromkeys(y):
        pts = Xs[y == s]
        means[s] = pts.mean(axis=0)
        spreads[s] = np.linalg.norm(pts - means[s], axis=1).mean()
    out = {}
    for a, b in combinations(means, 2):
        spread = max((spreads[a] + spreads[b]) / 2, 1e-6)
        out[f"{a}-{b}"] = float(np.linalg.norm(means[a] - means[b]) / spread)
    return out


def self_check(X, y) -> float:
    """Train on the first half of each spot's frames, test on the second half (per frame)."""
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=str)
    train, test = [], []
    for s in dict.fromkeys(y):
        idx = np.flatnonzero(y == s)
        half = len(idx) // 2
        train += list(idx[:half])
        test += list(idx[half:])
    model = GazeModel(X[train], y[train])
    hits = sum(model.classify(X[i])[0] == y[i] for i in test)
    return hits / max(len(test), 1)


def round_check(X, y, rounds) -> float:
    """Honest accuracy: learn from the first round only, test on the later rounds
    (different moments, like real use). The per-frame self_check is optimistic."""
    X, y, rounds = np.asarray(X, dtype=float), np.asarray(y, dtype=str), np.asarray(rounds)
    train, test = rounds == rounds.min(), rounds != rounds.min()
    if not test.any() or len(set(y[train])) < 2:
        return self_check(X, y)
    model = GazeModel(X[train], y[train])
    return float(np.mean([model.classify(v)[0] == s for v, s in zip(X[test], y[test])]))


def calibrate(tracker, say, patient: str, frames: int = CALIB_FRAMES, show: bool = True) -> GazeModel:
    """Speak each spot, wait SETTLE_S, record `frames` frames with a face, save calib/<patient>.npz."""
    import cv2

    def pump(label: str):
        frame, f = tracker.read()
        if show and frame is not None:
            cv2.putText(frame, label, (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 200, 255), 3)
            cv2.imshow("LookAt gaze (operator)", frame)
            cv2.waitKey(1)
        return f

    X, y, rounds = [], [], []
    per_round = max(1, frames // ROUNDS)
    for r in range(ROUNDS):
        for spot in config.SPOTS:
            say(f"Look at {SPOKEN_NAMES[spot]}")
            end = time.monotonic() + SETTLE_S
            while time.monotonic() < end:
                pump(f"get ready: {spot} (round {r + 1}/{ROUNDS})")
            got = 0
            while got < per_round:
                f = pump(f"recording {spot} {got}/{per_round} (round {r + 1}/{ROUNDS})")
                if f is not None:
                    X.append(f["gaze_vector"])
                    y.append(spot)
                    rounds.append(r)
                    got += 1
    say("Thank you")

    model = GazeModel(X, y)
    path = model.save(patient)
    print(f"saved {path}")
    for pair, score in separation_scores(X, y).items():
        print(f"  separation {pair:13s} {score:5.2f}  {'good' if score > 3 else 'LOW'}")
    if ROUNDS > 1:
        acc = round_check(X, y, rounds)
        print(f"  round check (learn round 1, test round 2): {acc:.0%}  "
              f"{'good' if acc >= 0.9 else 'LOW: recalibrate (light, camera position, keep the head still)'}")
    print(f"  self-check accuracy {self_check(X, y):.0%}")
    return model


class SystemVoice:
    """The computer's own voice (works offline). Blocks until spoken (call it from a worker).

    Windows: one persistent PowerShell System.Speech process, female voice (usually "Microsoft Zira").
    macOS: the built-in `say` command with the best installed female voice (see MAC_VOICES).
    """

    # best first; "(Premium)"/"(Enhanced)" voices sound the most natural once downloaded in
    # System Settings > Accessibility > Spoken Content > System Voice > Manage Voices
    MAC_VOICES = ["Ava (Premium)", "Zoe (Premium)", "Ava (Enhanced)", "Zoe (Enhanced)",
                  "Allison (Enhanced)", "Susan (Enhanced)", "Samantha (Enhanced)", "Samantha"]
    MAC_RATE = "175"   # words per minute; default is ~185
    FAST_RATE = "205"  # calibration: short, quick prompts

    def __init__(self, fast: bool = False):
        self.proc = None
        self.mac_voice = None
        self.rate = self.FAST_RATE if fast else self.MAC_RATE
        if sys.platform == "win32":
            script = ("Add-Type -AssemblyName System.Speech; "
                      "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                      "try { $s.SelectVoiceByHints([System.Speech.Synthesis.VoiceGender]::Female, "
                      "[System.Speech.Synthesis.VoiceAge]::Adult) } catch {}; "
                      f"$s.Rate = {1 if fast else -1}; "
                      "while ($null -ne ($l = [Console]::In.ReadLine())) { $s.Speak($l); [Console]::Out.WriteLine('done') }")
            self.proc = subprocess.Popen(["powershell", "-NoProfile", "-Command", script],
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        elif shutil.which("say"):
            self.mac_voice = self._pick_mac_voice()

    @classmethod
    def _pick_mac_voice(cls):
        try:
            listing = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        installed = {line.split("  ")[0].strip() for line in listing.splitlines()}
        return next((v for v in cls.MAC_VOICES if v in installed), None)

    def speak(self, text: str) -> None:
        if self.proc is not None:
            self.proc.stdin.write(text.replace("\n", " ") + "\n")
            self.proc.stdin.flush()
            self.proc.stdout.readline()
        elif shutil.which("say"):
            voice = ["-v", self.mac_voice] if self.mac_voice else []
            subprocess.run(["say", *voice, "-r", self.rate, text])

    def close(self) -> None:
        if self.proc is not None:
            self.proc.stdin.close()
            self.proc.wait(timeout=5)


class AvaVoice:
    """LookAt's own voice: Microsoft "Ava" (calm, caring neural voice) through edge-tts.

    Needs internet the FIRST time a sentence is spoken; every sentence is then cached in
    cache/voice/, so repeated prompts work offline. Any failure (edge-tts missing or blocked,
    no internet, timeout) raises, and LocalSpeaker falls back to SystemVoice.
    """
    VOICE = "en-US-AvaNeural"
    RATE, PITCH = "-8%", "-2Hz"   # a little slower and lower = calmer
    TIMEOUT_S = 8
    CACHE = os.path.join(_REPO, "cache", "voice")

    def __init__(self):
        import edge_tts   # optional: not in requirements.txt (Smart App Control); ImportError = no Ava
        self.edge_tts = edge_tts
        self.player = None
        if sys.platform == "win32":   # one persistent Windows Media Player, plays one file per line
            script = ("$w = New-Object -ComObject WMPlayer.OCX; "
                      "while ($null -ne ($f = [Console]::In.ReadLine())) { $w.URL = $f; $w.controls.play(); "
                      "$t = 0; while ($w.playState -ne 3 -and $t -lt 40) { Start-Sleep -Milliseconds 50; $t++ }; "
                      "while ($w.playState -eq 3) { Start-Sleep -Milliseconds 50 }; [Console]::Out.WriteLine('done') }")
            self.player = subprocess.Popen(["powershell", "-NoProfile", "-Command", script],
                                           stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        elif not shutil.which("afplay"):
            raise RuntimeError("no audio player")

    def path(self, text: str) -> str:
        key = hashlib.sha1(f"{self.VOICE}|{self.RATE}|{self.PITCH}|{text}".encode()).hexdigest()[:16]
        return os.path.join(self.CACHE, key + ".mp3")

    def fetch(self, text: str) -> str:
        """The cached mp3 for text, made with edge-tts if missing."""
        path = self.path(text)
        if not os.path.exists(path):
            import asyncio
            os.makedirs(self.CACHE, exist_ok=True)
            tmp = path + ".part"
            comm = self.edge_tts.Communicate(text, self.VOICE, rate=self.RATE, pitch=self.PITCH)
            try:
                asyncio.run(asyncio.wait_for(comm.save(tmp), self.TIMEOUT_S))
                os.replace(tmp, path)
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
        return path

    def speak(self, text: str) -> None:
        path = self.fetch(text)
        if self.player is not None:
            self.player.stdin.write(path + "\n")
            self.player.stdin.flush()
            self.player.stdout.readline()
        else:
            subprocess.run(["afplay", path])

    def close(self) -> None:
        if self.player is not None:
            self.player.stdin.close()
            self.player.wait(timeout=5)


class LocalSpeaker:
    """LookAt's voice for every spoken prompt. Never blocks the caller: sentences are queued and
    spoken one after another by a worker thread. Uses Ava (AvaVoice) when it works, otherwise the
    computer's own female voice (SystemVoice), so the prompts are never silent."""

    def __init__(self, ava: bool = True, fast: bool = False):
        """ava=False, fast=True: the instant, offline computer voice, a bit quicker (calibration)."""
        self.system = SystemVoice(fast=fast)
        self.ava = None
        if ava:
            try:
                self.ava = AvaVoice()
            except Exception as e:   # edge-tts missing / blocked, or no audio player
                print(f"[voice] Ava unavailable ({type(e).__name__}); using the system voice")
        self.q: "queue.Queue" = queue.Queue()
        self.worker = threading.Thread(target=self._run, daemon=True, name="voice")
        self.worker.start()

    def __call__(self, text: str) -> None:
        self.say(text)

    def say(self, text: str, interrupt: bool = False) -> None:
        """Queue text. interrupt=True drops sentences still waiting (the screen moved on);
        the one already playing finishes."""
        print(f"[say] {text}")
        if interrupt:
            while True:
                try:
                    stale = self.q.get_nowait()
                except queue.Empty:
                    break
                self.q.task_done()
                if stale is None:          # never drop a close request
                    self.q.put(None)
                    break
        self.q.put(text)

    def prefetch(self, text: str) -> None:
        """Make Ava's audio for text now (cached), without playing it."""
        if self.ava is not None:
            try:
                self.ava.fetch(text)
            except Exception:
                pass

    def _run(self) -> None:
        while True:
            text = self.q.get()
            try:
                if text is None:
                    return
                self._speak(text)
            finally:
                self.q.task_done()

    def _speak(self, text: str) -> None:
        try:
            if self.ava is not None:
                try:
                    self.ava.speak(text)
                    return
                except Exception as e:   # offline, timeout, service error: fall back for this sentence
                    print(f"[voice] Ava failed ({type(e).__name__}); system voice")
            self.system.speak(text)
        except Exception as e:
            print(f"[say failed: {e}]")

    def wait(self, timeout: float = 30) -> None:
        """Block until everything queued has been spoken (tests, the end of a script)."""
        end = time.monotonic() + timeout
        while self.q.unfinished_tasks and time.monotonic() < end:
            time.sleep(0.05)

    def close(self) -> None:
        self.q.put(None)
        self.worker.join(timeout=10)
        for v in (self.ava, self.system):
            if v is not None:
                try:
                    v.close()
                except Exception:
                    pass


def _self_test(patient: str, recalibrate: bool, camera: int) -> None:
    import cv2
    from lookat.vision import FaceTracker

    say = LocalSpeaker()
    tracker = FaceTracker(camera)
    try:
        if recalibrate or not GazeModel.exists(patient):
            model = calibrate(tracker, say, patient)
        else:
            model = GazeModel.load(patient)
            print(f"loaded calibration for {patient}")
        last = "?"
        while True:
            frame, f = tracker.read()
            if frame is None:
                print("FAIL: camera returned no frame")
                break
            spot, conf = model.predict(f)
            label = "no face" if f is None else (spot or "unsure")
            if label != last:
                print(f"looking at: {label}")
                last = label
            color = (0, 200, 0) if spot else (0, 165, 255)
            cv2.putText(frame, label.upper(), (40, 160), cv2.FONT_HERSHEY_SIMPLEX, 4, color, 8)
            cv2.putText(frame, f"confidence {conf:.2f}", (40, 230), cv2.FONT_HERSHEY_SIMPLEX, 1.2, color, 3)
            cv2.imshow("LookAt gaze (operator)", frame)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
    finally:
        tracker.close()
        say.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Gaze calibration + live spot self-test")
    ap.add_argument("--patient", required=True)
    ap.add_argument("--recalibrate", action="store_true")
    ap.add_argument("--camera", type=int, default=0)
    a = ap.parse_args()
    _self_test(a.patient, a.recalibrate, a.camera)
