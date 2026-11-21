"""Eyebrow raise -> "confirm" / "emergency" events (owner P2).

How it works:
1. calibrate_gestures() measures THIS person: eyebrow height at rest, then 3 deliberate
   raises. The threshold is halfway between the resting max and the weakest raise.
   If raises are too weak (gap < MIN_GAP), it calibrates mouth-open as the backup gesture.
2. GestureDetector.update() is called every frame. It times how long the value stays
   above the threshold:
       held >= CONFIRM_S   -> "confirm"   (once per raise)
       held >= EMERGENCY_S -> "emergency" (once; no second confirm for the same raise)
   Dropping below the release line, or losing the face, ends the raise.

Self-test: py -3.11 -m lookat.gestures --patient NAME [--recalibrate]   (q quits)
"""
import json
import os
import re
import time

from lookat import config

MIN_GAP = 0.15          # raise must beat rest by at least this, else use the mouth backup
RELEASE_FRACTION = 0.25  # release line sits 25% of the way back down toward rest (stops flicker)
REST_S = 5.0
RAISE_S = 3.0
RAISES = 3
SETTLE_S = 1.5

GESTURES = {
    "brow": {"key": "browInnerUp", "prompt": "Raise your eyebrows", "confirm_s": config.CONFIRM_S},
    "mouth": {"key": "jawOpen", "prompt": "Open your mouth", "confirm_s": config.MOUTH_CONFIRM_S},
}

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CALIB_DIR = os.path.join(_REPO, "calib")


# ----- calibration math (pure, tested) -------------------------------------
def compute_threshold(rest_max: float, peaks: list) -> dict:
    """Threshold halfway between resting max and the weakest raise."""
    weakest = min(peaks)
    return {
        "rest": rest_max,
        "peaks": list(peaks),
        "gap": weakest - rest_max,
        "threshold": (rest_max + weakest) / 2,
        "ok": weakest - rest_max >= MIN_GAP,
    }


def make_settings(gesture: str, calib: dict) -> dict:
    """The dict GestureDetector needs (also what gets saved to disk)."""
    g = GESTURES[gesture]
    return {"gesture": gesture, "key": g["key"], "threshold": calib["threshold"],
            "rest": calib["rest"], "confirm_s": g["confirm_s"], "calib": calib}


def _path(patient: str, folder: str = CALIB_DIR) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", patient) or "patient"
    return os.path.join(folder, f"{safe}_gestures.json")


def save_settings(patient: str, settings: dict, folder: str = CALIB_DIR) -> str:
    os.makedirs(folder, exist_ok=True)
    path = _path(patient, folder)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
    return path


def load_settings(patient: str, folder: str = CALIB_DIR):
    path = _path(patient, folder)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ----- live detector ------------------------------------------------------
class GestureDetector:
    def __init__(self, settings: dict):
        self.key = settings["key"]
        self.threshold = settings["threshold"]
        rest = settings.get("rest", 0.0)
        self.release = self.threshold - RELEASE_FRACTION * (self.threshold - rest)
        self.confirm_s = settings.get("confirm_s", config.CONFIRM_S)
        self.value = 0.0
        self._reset()

    def _reset(self) -> None:
        self.above_since = None   # when the current raise started
        self.confirmed = False    # this raise already sent "confirm"
        self.emergency = False    # this raise already sent "emergency"

    def held(self, now: float) -> float:
        return 0.0 if self.above_since is None else now - self.above_since

    def update(self, features, now: float) -> list:
        """Call once per frame. Returns a list of new events: "confirm" and/or "emergency"."""
        if features is None:          # face lost: the raise is over
            self.value = 0.0
            self._reset()
            return []
        self.value = float(features.get(self.key, 0.0))

        if self.above_since is None:
            if self.value < self.threshold:
                return []
            self.above_since = now    # a raise starts
        elif self.value < self.release:
            self._reset()             # the raise ended
            return []

        events = []
        held = now - self.above_since
        if not self.confirmed and held >= self.confirm_s - 1e-9:
            self.confirmed = True
            events.append("confirm")
        if not self.emergency and held >= config.EMERGENCY_S - 1e-9:
            self.emergency = True
            events.append("emergency")
        return events


# ----- calibration with the camera ------------------------------------------
def calibrate_gestures(tracker, say, patient: str, show: bool = True) -> dict:
    """Spoken calibration. Returns settings for GestureDetector and saves them."""
    import cv2

    def pump(label: str):
        frame, f = tracker.read()
        if show and frame is not None:
            cv2.putText(frame, label, (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (0, 200, 255), 3)
            cv2.imshow("LookAt gestures (operator)", frame)
            cv2.waitKey(1)
        return f

    def wait(seconds: float, label: str):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            pump(label)

    def record_max(seconds: float, label: str) -> dict:
        best = {"browInnerUp": 0.0, "jawOpen": 0.0}
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            f = pump(label)
            if f is not None:
                for k in best:
                    best[k] = max(best[k], f[k])
        return best

    def peaks(gesture: str) -> list:
        g = GESTURES[gesture]
        out = []
        for i in range(RAISES):
            say(g["prompt"])
            out.append(record_max(RAISE_S, f"{g['prompt']} {i + 1}/{RAISES}")[g["key"]])
            say("Relax")
            wait(SETTLE_S, "relax")
        return out

    say("Relax and look ahead")
    wait(SETTLE_S, "relax")
    rest = record_max(REST_S, "resting")

    calib = compute_threshold(rest["browInnerUp"], peaks("brow"))
    print(f"eyebrows: rest {calib['rest']:.2f}, raises {[round(p, 2) for p in calib['peaks']]}, "
          f"gap {calib['gap']:.2f}, threshold {calib['threshold']:.2f}")
    gesture = "brow"
    if not calib["ok"]:
        print(f"WARNING: eyebrow gap {calib['gap']:.2f} < {MIN_GAP}. Calibrating mouth open as backup.")
        mouth = compute_threshold(rest["jawOpen"], peaks("mouth"))
        print(f"mouth: rest {mouth['rest']:.2f}, opens {[round(p, 2) for p in mouth['peaks']]}, "
              f"gap {mouth['gap']:.2f}, threshold {mouth['threshold']:.2f}")
        if mouth["gap"] > calib["gap"]:
            gesture, calib = "mouth", mouth
    say("Thank you")

    settings = make_settings(gesture, calib)
    print(f"using {gesture}; saved {save_settings(patient, settings)}")
    return settings


# ----- self-test ------------------------------------------------------------
def _self_test(patient: str, recalibrate: bool, camera: int) -> None:
    import cv2
    from lookat.gaze import LocalSpeaker
    from lookat.vision import FaceTracker

    say = LocalSpeaker()
    tracker = FaceTracker(camera)
    counts = {"confirm": 0, "emergency": 0}
    start = time.monotonic()
    try:
        settings = None if recalibrate else load_settings(patient)
        if settings is None:
            settings = calibrate_gestures(tracker, say, patient)
        else:
            print(f"loaded gesture calibration for {patient}: {settings['gesture']}, "
                  f"threshold {settings['threshold']:.2f}")
        det = GestureDetector(settings)
        print("Live. Raise to confirm, hold 2 s for emergency. q quits.")
        while True:
            frame, f = tracker.read()
            if frame is None:
                print("FAIL: camera returned no frame")
                break
            now = time.monotonic()
            for ev in det.update(f, now):
                counts[ev] += 1
                print(f"{now - start:7.2f}s  {ev.upper()}  (confirms {counts['confirm']}, "
                      f"emergencies {counts['emergency']})")

            # score bar: grey bar = value, red line = threshold, yellow line = release
            h, w = frame.shape[:2]
            x0, x1, y0, y1 = 40, w - 40, h - 80, h - 40
            def xpos(v): return int(x0 + max(0.0, min(1.0, v)) * (x1 - x0))
            cv2.rectangle(frame, (x0, y0), (x1, y1), (60, 60, 60), -1)
            color = (0, 200, 0) if det.above_since is not None else (200, 200, 200)
            cv2.rectangle(frame, (x0, y0), (xpos(det.value), y1), color, -1)
            cv2.line(frame, (xpos(det.threshold), y0 - 10), (xpos(det.threshold), y1 + 10), (0, 0, 255), 3)
            cv2.line(frame, (xpos(det.release), y0 - 5), (xpos(det.release), y1 + 5), (0, 220, 255), 1)
            label = "no face" if f is None else f"{det.key} {det.value:.2f}  held {det.held(now):.1f}s"
            cv2.putText(frame, label, (x0, y0 - 20), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
            cv2.putText(frame, f"confirms {counts['confirm']}  emergencies {counts['emergency']}",
                        (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 3)
            cv2.imshow("LookAt gestures (operator)", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        tracker.close()
        say.close()
        cv2.destroyAllWindows()
        print(f"totals: {counts['confirm']} confirms, {counts['emergency']} emergencies")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Eyebrow confirm / emergency self-test")
    ap.add_argument("--patient", required=True)
    ap.add_argument("--recalibrate", action="store_true")
    ap.add_argument("--camera", type=int, default=0)
    a = ap.parse_args()
    _self_test(a.patient, a.recalibrate, a.camera)
