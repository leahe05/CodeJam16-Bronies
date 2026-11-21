"""Independent, random patient-agent arrivals for the nurse shift demo."""
import math
import random
from threading import Condition
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
        self._condition = Condition()
        self._state = "ready"
        self._generation = 0

    @property
    def state(self):
        with self._condition:
            return self._state

    def start(self):
        with self._condition:
            if self._state != "running":
                self._generation += 1
                self._state = "running"
                self._condition.notify_all()

    def finish(self):
        with self._condition:
            if self._state == "running":
                self._state = "complete"
                self._condition.notify_all()

    def run(self, stop, patients):
        while not stop.is_set():
            with self._condition:
                if self._state != "running":
                    self._condition.wait(.2)
                    continue
                generation = self._generation
            self._run_shift(stop, patients, generation)

    def _run_shift(self, stop, patients, generation):
        clocks = {patient: random.Random(self.random.getrandbits(64)) for patient in patients}
        due = {patient: time.monotonic() + rng.uniform(self.minimum_s, self.maximum_s)
               for patient, rng in clocks.items()}
        while not stop.is_set():
            patient = min(due, key=due.get)
            with self._condition:
                if self._state != "running" or self._generation != generation:
                    return
                remaining = due[patient] - time.monotonic()
                if remaining > 0:
                    self._condition.wait(min(remaining, .2))
                    continue
            rng = clocks[patient]
            patients[patient].emit_random_request(rng)
            due[patient] = time.monotonic() + rng.uniform(self.minimum_s, self.maximum_s)
