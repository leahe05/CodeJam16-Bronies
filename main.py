"""LookAt: the patient screen driven by the real camera (task M2-1, first version).

    py -3.11 main.py --patient NAME [--recalibrate] [--camera N]

Starts the web server (same URLs and JSON as lookat.fake_api, docs/API.md), calibrates
gaze + eyebrows if needed, then runs the camera loop in the MAIN thread:

    FaceTracker -> GazeModel.predict -> GestureDetector.update
      -> Machine.step(active = ScreenManager.active_spots())
      -> ("select", spot) -> ScreenManager.select -> events (logged; devices simulated)
      -> ("call_nurse",)   -> ScreenManager.call_nurse (overlay)

Open http://<laptop>:8000/patient on the screen the patient looks at.
Operator window keys (team only):
    q quit   c recalibrate gaze   g recalibrate eyebrows
    s show an AI suggestion   n nurse asks a sample question   e nurse ends the session
    2 set the demo clock to 02:00 (night look)   7 set it to 07:30 (day look)

Not wired yet: real lamp/fan plugs (room is simulated), the agent, the nurse app page.
"""
import argparse
import itertools
import socket
import threading
import time

from lookat import config
from lookat.clock import clock
from lookat.machine import Machine
from lookat.screens import ScreenManager
from lookat.state import log_event

SAMPLE_NURSE_Q = ("What would help you feel more comfortable?",
                  ["Adjust pillows", "Change position", "Water", "Something else"])
SAMPLE_SUGGESTION = "You seem to be awake. Too warm?"


class LiveBackend:
    """The data behind /api/screen (and minimal /api/nurse, /api/demo) for lookat.server.make_app.
    Everything is changed under self.lock: the camera loop and the web server both use it."""

    def __init__(self):
        self.lock = threading.Lock()
        self.room = {"temp_c": 24.5, "setpoint_c": 22.0, "lamp_on": False, "fan_on": False}
        self.sm = ScreenManager(room=lambda: self.room)
        self.machine = Machine()
        self.face = False
        self.feed = []                       # what happened, newest last (shown on /api/nurse)
        self._ids = itertools.count(1)

    # ----- one camera frame (pure logic, testable without a camera) -------
    def frame(self, now: float, spot, events, face: bool) -> list:
        """Run one frame through the selection engine and the screens. Caller holds self.lock."""
        self.face = face
        out = []
        for action in self.machine.step(now, spot, events, face, active=self.sm.active_spots()):
            if action[0] == "select":
                out += self.sm.select(action[1], now)
            elif action[0] == "call_nurse":
                out += self.sm.call_nurse(now)
        out += self.sm.tick(now, busy=self.machine.busy)
        for ev in out:
            self._handle(ev)
        return out

    def _handle(self, ev: tuple) -> None:
        if ev[0] == "device":                # simulated room until the plugs are wired
            _, device, cmd = ev
            self.room[f"{device}_on"] = cmd == "on"
        self.feed.append({"time": clock.hhmm(), "event": list(ev)})
        del self.feed[:-50]
        log_event("patient", event=list(ev))
        print(f"  -> {ev}")

    # ----- operator / nurse actions -------------------------------------------
    def suggest(self, now: float) -> None:
        self.sm.show_suggestion(f"s{next(self._ids)}", SAMPLE_SUGGESTION, now)
        self.machine.reset(now)

    def nurse_send(self, question: str, options: list, now: float) -> str:
        qid = f"q{next(self._ids)}"
        self.sm.nurse_question(qid, question, options, now)
        self.machine.reset(now)
        return qid

    def nurse_end(self, now: float) -> None:
        self.sm.nurse_end(now)
        self.machine.reset(now)

    # ----- what lookat.server calls (under self.lock) -------------------------
    def screen_json(self, now: float = None) -> dict:
        now = time.monotonic() if now is None else now
        return {"v": 1, **self.sm.view(now), "focus": self.machine.view(),
                "clock": {"time": clock.hhmm(), "demo": clock.demo}, "room": dict(self.room),
                "face": self.face}

    def nurse_json(self) -> dict:
        return {"v": 1, "connected": True, "screen": self.sm.kind, "feed": self.feed[-20:]}

    def panel_json(self) -> dict:
        return {"v": 1, "clock": {"time": clock.hhmm(), "demo": clock.demo}, "room": dict(self.room)}

    def nurse_action(self, path: str, body: dict) -> dict:
        now = time.monotonic()
        if path == "session/start":
            self.sm.nurse_start(now)
            self.machine.reset(now)
        elif path == "send":
            q, opts = (body.get("question") or "").strip(), list(body.get("options") or [])
            if not q or len(opts) < 2:
                return {"ok": False, "error": "need a question and at least 2 options"}
            return {"ok": True, "qid": self.nurse_send(q, opts, now)}
        elif path == "session/end":
            self.nurse_end(now)
        else:
            return {"ok": False, "error": "not wired yet in main.py"}
        return {"ok": True}

    def demo_action(self, body: dict) -> dict:
        action = body.get("action")
        if action == "set_time":
            clock.set_time(str(body.get("value") or body.get("time")))
        elif action == "set_temp":
            self.room["temp_c"] = float(body.get("value"))
        elif action == "call_nurse":
            self.sm.call_nurse(time.monotonic())
        else:
            return {"ok": False, "error": "not wired yet in main.py"}
        return {"ok": True}


# ----- startup ---------------------------------------------------------------
def start_server(backend: LiveBackend, port: int = config.PAGE_PORT) -> None:
    import uvicorn
    from lookat.server import make_app
    server = uvicorn.Server(uvicorn.Config(make_app(backend), host="0.0.0.0", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True, name="web").start()
    for _ in range(50):
        if server.started:
            break
        time.sleep(0.1)


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))     # no packet is sent; picks the LAN interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def screen_speaker():
    """Calibration voice: the old prompts named the room cards; the patient now looks at the screen."""
    from lookat.gaze import LocalSpeaker
    say = LocalSpeaker()
    words = {"the YES card": "the LEFT side of the screen", "the lamp": "the CENTER of the screen",
             "the NO card": "the RIGHT side of the screen"}

    def speak(text: str) -> None:
        for old, new in words.items():
            text = text.replace(old, new)
        say(text)
    speak.close = say.close
    return speak


def run(patient: str, recalibrate: bool, camera: int) -> None:
    import cv2
    from lookat.gaze import GazeModel, calibrate
    from lookat.gestures import GestureDetector, calibrate_gestures, load_settings
    from lookat.vision import FaceTracker

    backend = LiveBackend()
    start_server(backend)
    url = f"http://{lan_ip()}:{config.PAGE_PORT}/patient"
    print(f"\nPatient screen: {url}   (also http://localhost:{config.PAGE_PORT}/patient)\n")

    say = screen_speaker()
    tracker = FaceTracker(camera)
    try:
        gaze = (calibrate(tracker, say, patient) if recalibrate or not GazeModel.exists(patient)
                else GazeModel.load(patient))
        settings = None if recalibrate else load_settings(patient)
        brows = GestureDetector(settings or calibrate_gestures(tracker, say, patient))
        print("Live. Look at a box on the screen for 1 s, then raise your eyebrows. Hold 2 s = Call Nurse.")
        print("Keys in the camera window: q quit, c/g recalibrate, s suggestion, n/e nurse, 2/7 clock.\n")

        fps_t, fps_n, fps = time.monotonic(), 0, 0.0
        while True:
            frame, f = tracker.read()
            if frame is None:
                print("camera returned no frame; stopping")
                break
            now = time.monotonic()
            spot, conf = gaze.predict(f)
            events = brows.update(f, now)
            with backend.lock:
                backend.frame(now, spot, events, f is not None)
                state, kind = backend.machine.state, backend.sm.kind

            # operator window (team only)
            fps_n += 1
            if now - fps_t >= 1:
                fps, fps_t, fps_n = fps_n / (now - fps_t), now, 0
            lines = [f"looking: {'no face' if f is None else (spot or 'unsure')}  ({conf:.2f})",
                     f"{brows.key} {brows.value:.2f} / {brows.threshold:.2f}  held {brows.held(now):.1f}s",
                     f"machine {state}   screen {kind}   {fps:.0f} fps"]
            for i, text in enumerate(lines):
                cv2.putText(frame, text, (20, 50 + 45 * i), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 220, 255), 3)
            cv2.imshow("LookAt (operator)", frame)

            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            with backend.lock:
                now = time.monotonic()
                if key == ord("s"):
                    backend.suggest(now)
                elif key == ord("n"):
                    backend.nurse_send(*SAMPLE_NURSE_Q, now)
                elif key == ord("e"):
                    backend.nurse_end(now)
                elif key in (ord("2"), ord("7")):
                    clock.set_time("02:00" if key == ord("2") else "07:30")
            if key == ord("c"):
                gaze = calibrate(tracker, say, patient)
            elif key == ord("g"):
                brows = GestureDetector(calibrate_gestures(tracker, say, patient))
    finally:
        tracker.close()
        say.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="LookAt: patient screen by gaze + eyebrows")
    ap.add_argument("--patient", required=True, help="name for the calibration files in calib/")
    ap.add_argument("--recalibrate", action="store_true")
    ap.add_argument("--camera", type=int, default=0)
    a = ap.parse_args()
    run(a.patient, a.recalibrate, a.camera)
