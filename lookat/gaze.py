"""Calibration + which spot the patient is looking at (owner P1).

GazeModel: standardise gaze_vector -> k-nearest-neighbours (k=7, plain NumPy) ->
majority vote over the last 10 frames. predict() returns (spot, confidence), or
(None, confidence) when confidence < MIN_CONF.

Self-test: py -3.11 -m lookat.gaze --patient NAME [--recalibrate]   (q quits)
"""
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter, deque
from itertools import combinations

import numpy as np

from lookat import config

K = 7
VOTE_FRAMES = 10
MIN_CONF = 0.7
CALIB_FRAMES = 90
SETTLE_S = 2.0
SPOKEN_NAMES = {"left": "the YES card", "middle": "the lamp", "right": "the NO card"}

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
        self.std = np.maximum(self.X.std(axis=0), 1e-6)
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
    Xs = (X - X.mean(axis=0)) / np.maximum(X.std(axis=0), 1e-6)
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

    X, y = [], []
    for spot in config.SPOTS:
        say(f"Look at {SPOKEN_NAMES[spot]}")
        end = time.monotonic() + SETTLE_S
        while time.monotonic() < end:
            pump(f"get ready: {spot}")
        got = 0
        while got < frames:
            f = pump(f"recording {spot} {got}/{frames}")
            if f is not None:
                X.append(f["gaze_vector"])
                y.append(spot)
                got += 1
    say("Thank you")

    model = GazeModel(X, y)
    path = model.save(patient)
    print(f"saved {path}")
    for pair, score in separation_scores(X, y).items():
        print(f"  separation {pair:13s} {score:5.2f}  {'good' if score > 3 else 'LOW'}")
    print(f"  self-check accuracy {self_check(X, y):.0%}")
    return model


class LocalSpeaker:
    """Fallback voice for the self-test (before voice.py exists). Never blocks.

    Windows: one persistent PowerShell System.Speech process reading lines from stdin.
    macOS: the built-in `say` command. Otherwise: print.
    """

    def __init__(self):
        self.proc = None
        if sys.platform == "win32":
            script = ("Add-Type -AssemblyName System.Speech; "
                      "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                      "while ($null -ne ($l = [Console]::In.ReadLine())) { $s.Speak($l) }")
            self.proc = subprocess.Popen(["powershell", "-NoProfile", "-Command", script],
                                         stdin=subprocess.PIPE, text=True)

    def __call__(self, text: str) -> None:
        print(f"[say] {text}")
        try:
            if self.proc is not None:
                self.proc.stdin.write(text.replace("\n", " ") + "\n")
                self.proc.stdin.flush()
            elif shutil.which("say"):
                subprocess.Popen(["say", text])
        except OSError as e:
            print(f"[say failed: {e}]")

    def close(self) -> None:
        if self.proc is not None:
            self.proc.stdin.close()
            self.proc.wait(timeout=5)


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
