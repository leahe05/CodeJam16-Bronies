"""Independent, random patient-agent arrivals for the nurse shift demo."""
import math
import random
from threading import Event
import time


SHIFT_CAMERAS = {
    "avery": {"name": "San Diego Zoo Penguin Cam", "url": "https://zoo.sandiegozoo.org/cams/penguin-cam",
              "source": "demo"},
    "morgan": {"name": "Smithsonian Elephant Cam", "url": "https://www.nationalzoo.si.edu/webcams/elephants",
               "source": "demo"},
}


class ShiftPlayback:
    def __init__(self, minimum_s=4.0, maximum_s=12.0, *, seed=None):
        if not (math.isfinite(minimum_s) and math.isfinite(maximum_s) and 0 < minimum_s < maximum_s):
            raise ValueError("Request intervals must satisfy 0 < minimum < maximum.")
        self.minimum_s = minimum_s
        self.maximum_s = maximum_s
        self.random = random.Random(seed)
        self.started = Event()
        self.finished = Event()

    @property
    def state(self):
        return "complete" if self.finished.is_set() else "running" if self.started.is_set() else "ready"

    def start(self):
        self.started.set()

    def finish(self):
        self.finished.set()

    def run(self, stop, patients):
        while not self.started.wait(.1):
            if stop.is_set():
                return
        clocks = {patient: random.Random(self.random.getrandbits(64)) for patient in patients}
        due = {patient: time.monotonic() + rng.uniform(self.minimum_s, self.maximum_s)
               for patient, rng in clocks.items()}
        while not self.finished.is_set():
            patient = min(due, key=due.get)
            remaining = max(0, due[patient] - time.monotonic())
            if stop.wait(min(remaining, .2)):
                return
            if time.monotonic() < due[patient] or self.finished.is_set():
                continue
            rng = clocks[patient]
            patients[patient].emit_random_request(rng)
            due[patient] = time.monotonic() + rng.uniform(self.minimum_s, self.maximum_s)
