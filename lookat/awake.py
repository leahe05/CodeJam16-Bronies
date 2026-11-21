"""Eyes open / awake, from the camera's blink scores (pure logic; task A3).

    det = AwakeDetector()
    det.update(features, now)   # every frame; features from vision.FaceTracker (or None = no face)
    -> {"eyes_open": bool|None, "open_ratio": float|None, "awake": bool|None}

eyes_open = mean(eyeBlinkLeft, eyeBlinkRight) < threshold (default 0.45).
awake     = eyes open in >= AWAKE_RATIO of the face frames of the last WINDOW_S seconds.
No face, or under MIN_SAMPLES_S of data -> None ("unknown"), never "asleep".
A demo override ("open" / "closed") replaces the camera.
"""
from collections import deque
from typing import Optional

THRESHOLD = 0.45
WINDOW_S = 60.0
AWAKE_RATIO = 0.7
MIN_SAMPLES_S = 5.0


class AwakeDetector:
    def __init__(self, threshold: float = THRESHOLD):
        self.threshold = threshold
        self.samples: deque = deque()          # (time, eyes_open) for frames with a face
        self.override: Optional[str] = None    # "open" | "closed" | None
        self.state = {"eyes_open": None, "open_ratio": None, "awake": None}

    def set_override(self, value: Optional[str]) -> None:
        self.override = value if value in ("open", "closed") else None

    def update(self, features, now: float) -> dict:
        if self.override:
            is_open = self.override == "open"
            self.state = {"eyes_open": is_open, "open_ratio": 1.0 if is_open else 0.0, "awake": is_open}
            return self.state
        while self.samples and now - self.samples[0][0] > WINDOW_S:
            self.samples.popleft()
        if features is None:                   # no face: unknown, keep the history
            self.state = {"eyes_open": None, "open_ratio": self._ratio(), "awake": None}
            return self.state
        blink = (float(features.get("eyeBlinkLeft", 0.0)) + float(features.get("eyeBlinkRight", 0.0))) / 2
        is_open = blink < self.threshold
        self.samples.append((now, is_open))
        ratio = self._ratio()
        enough = self.samples and now - self.samples[0][0] >= MIN_SAMPLES_S
        self.state = {"eyes_open": is_open, "open_ratio": ratio,
                      "awake": (ratio >= AWAKE_RATIO) if enough else None}
        return self.state

    def _ratio(self) -> Optional[float]:
        if not self.samples:
            return None
        return round(sum(1 for _, o in self.samples if o) / len(self.samples), 2)


if __name__ == "__main__":
    d = AwakeDetector()
    t, ok = 0.0, True
    for i in range(300):                       # 30 s, a blink every 4 s
        t += 0.1
        d.update({"eyeBlinkLeft": 0.9 if i % 40 < 2 else 0.1, "eyeBlinkRight": 0.1}, t)
    ok &= d.state["awake"] is True
    for _ in range(600):                       # 60 s eyes closed
        t += 0.1
        d.update({"eyeBlinkLeft": 0.9, "eyeBlinkRight": 0.9}, t)
    ok &= d.state["awake"] is False
    ok &= d.update(None, t)["awake"] is None
    d.set_override("open")
    ok &= d.update(None, t)["awake"] is True
    print("PASS" if ok else "FAIL", d.state)
