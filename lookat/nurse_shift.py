"""Scripted patient-agent signals for the standalone nurse shift demo."""
from dataclasses import dataclass
from threading import Event
import time


SHIFT_CAMERAS = {
    "avery": {"name": "San Diego Zoo Penguin Cam", "url": "https://zoo.sandiegozoo.org/cams/penguin-cam"},
    "morgan": {"name": "Smithsonian Elephant Cam", "url": "https://www.nationalzoo.si.edu/webcams/elephants"},
}


@dataclass(frozen=True)
class ShiftUpdate:
    seconds: int
    patient: str
    status: str
    delivery: str = "status"
    request: str = ""
    choices: tuple = ()
    temperature: int | None = None
    urgent: bool = False
    time_sensitive: bool = False


SHIFT_UPDATES = (
    ShiftUpdate(0, "avery", "Awake and settled in bed. Prefers fresh air and help sitting upright for drinks."),
    ShiftUpdate(0, "morgan", "Awake and resting. Prefers a closed window, quiet, and help with personal care."),
    ShiftUpdate(5, "avery", "Confirmed they would like water.", "proactive",
                "Please bring Avery a glass of water."),
    ShiftUpdate(12, "morgan", "Asked for help with a brief change soon and requested privacy.", "scheduled",
                "Morgan would like help changing their brief in private.", time_sensitive=True),
    ShiftUpdate(22, "avery", "Awake later than usual. Room feels warm at 24 °C.", "question",
                temperature=24),
    ShiftUpdate(22, "morgan", "Awake later than usual. Room feels warm at 24 °C.", "question",
                temperature=24),
    ShiftUpdate(36, "avery", "Indicated they would like to sit up.", "question",
                choices=("sit_up", "pillow")),
    ShiftUpdate(46, "morgan", "Asked for a gentle hand massage if the nurse is available.", "proactive",
                "Morgan requested a gentle hand massage."),
    ShiftUpdate(56, "morgan", "Asked for a nurse right away to help sit upright in bed.", "proactive",
                "Morgan requests immediate help sitting upright in bed.", urgent=True),
    ShiftUpdate(66, "avery", "No additional request at the end of the shift simulation."),
    ShiftUpdate(66, "morgan", "No additional request at the end of the shift simulation."),
)


class ShiftPlayback:
    def __init__(self, updates=SHIFT_UPDATES):
        self.updates = updates
        self.started = Event()
        self.finished = Event()

    @property
    def state(self):
        return "complete" if self.finished.is_set() else "running" if self.started.is_set() else "ready"

    def start(self):
        self.started.set()

    def run(self, stop, patients):
        while not self.started.wait(.1):
            if stop.is_set():
                return
        started_at = time.monotonic()
        for update in self.updates:
            if stop.wait(max(0, started_at + update.seconds - time.monotonic())):
                return
            patients[update.patient].receive_shift_update(update)
        self.finished.set()
