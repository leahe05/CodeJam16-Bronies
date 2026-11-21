"""LookAt: the real program.

    py -3.11 main.py --patient NAME [--recalibrate] [--camera 0]
    py -3.11 main.py --patient NAME --keyboard      (no camera: test with the keyboard)

Starts the web server (Lea's pages + the JSON API, docs/API.md), the plugs (lamp,
fan), calibrates gaze and eyebrows on the patient screen if needed, then runs the
camera loop in the main thread: camera -> gaze spot + eyebrow events -> Live.frame()
(selection engine -> screens -> room / nurse).

Operator window (team only) keys:  c = recalibrate gaze   g = recalibrate eyebrows   q = quit
Keyboard mode keys:  a / s / d = look left / center / right   x = look away
                     space = short eyebrow raise   e = long raise (Call Nurse)   q = quit
"""
import argparse
import os
import socket
import threading
import time

import cv2
import numpy as np

from lookat import config
from lookat.devices import Room
from lookat.live import Live
from lookat.server import PAGES, find_page, make_app

WINDOW = "LookAt operator (team only)"


def local_ip() -> str:
    """The laptop's address on the hotspot (no packet is sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            return s.getsockname()[0]
    except OSError:
        return "localhost"


def current_wifi():
    """Name of the Wi-Fi the laptop is on (Windows), or None if unknown."""
    import subprocess
    import sys
    if sys.platform != "win32":
        return None
    try:
        out = subprocess.run(["netsh", "wlan", "show", "interfaces"], capture_output=True, text=True,
                             timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        name, _, value = line.partition(":")
        if name.strip() == "SSID":
            return value.strip()
    return ""


def check_wifi() -> None:
    wifi = current_wifi()
    if wifi is not None and wifi != config.WIFI_NAME:
        print("\n" + "!" * 64)
        print(f"  WRONG WI-FI: the laptop is on '{wifi or 'nothing'}', the plugs are on '{config.WIFI_NAME}'.")
        print(f"  Connect to '{config.WIFI_NAME}' (and turn off auto-connect for other networks).")
        print("!" * 64 + "\n")


def start_server(live: Live, port: int):
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(make_app(live), host="0.0.0.0", port=port, log_level="warning"))
    threading.Thread(target=server.run, name="web", daemon=True).start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise SystemExit(f"Web server did not start on port {port} (already in use? close the other LookAt)")
    return server


def print_urls(port: int) -> None:
    ip = local_ip()
    print("\n" + "=" * 64)
    for name in PAGES:
        lea = find_page(name)[1]
        where = "localhost" if name == "patient" else ip
        print(f"  {name.upper():8} http://{where}:{port}/{name}   ({'Lea' if lea else 'test page'})")
    print("  Phone: same Wi-Fi (\"lea\"), use the address with the IP.")
    print("=" * 64 + "\n")


class ScreenPrompt:
    """The `say` callback for calibration: speaks AND shows the text on the patient screen."""

    def __init__(self, live: Live, speaker):
        self.live, self.speaker = live, speaker

    def __call__(self, text: str) -> None:
        from lookat.gaze import SPOKEN_NAMES
        spot = next((s for s, name in SPOKEN_NAMES.items() if name in text), None)
        self.live.set_calibration(text, spot)
        self.speaker(text)


def calibrate(tracker, live: Live, speaker, patient: str, what: str):
    from lookat.gaze import GazeModel, calibrate as calibrate_gaze
    from lookat.gestures import calibrate_gestures
    prompt = ScreenPrompt(live, speaker)
    try:
        if what == "gaze":
            prompt("Calibrating where you look. Keep your head still and move only your eyes.")
            time.sleep(2.5)
            return calibrate_gaze(tracker, prompt, patient)
        return calibrate_gestures(tracker, prompt, patient)
    finally:
        live.set_calibration(None)
        # the calibration windows would stay on screen, frozen, on top of the live one
        for name in ("LookAt gaze (operator)", "LookAt gestures (operator)"):
            try:
                cv2.destroyWindow(name)
            except cv2.error:
                pass
        cv2.waitKey(1)


def raise_window() -> None:
    """Bring the live operator window to the front once."""
    try:
        cv2.setWindowProperty(WINDOW, cv2.WND_PROP_TOPMOST, 1)
        cv2.waitKey(1)
        cv2.setWindowProperty(WINDOW, cv2.WND_PROP_TOPMOST, 0)
    except cv2.error:
        pass


def draw_operator(frame, live: Live, gaze_conf: float, det, fps: float, keyboard: bool = False,
                  flash: tuple = ("", 0.0)):
    h, w = frame.shape[:2]
    with live.lock:
        m, screen = live.machine, live.sm.kind
        spot, face, brow = live.spot, live.face, dict(live.brow)
        focus = m.view()
    color = (0, 200, 0) if spot else (0, 165, 255)
    cv2.putText(frame, (spot or ("no face" if not face else "unsure")).upper(), (30, 90),
                cv2.FONT_HERSHEY_SIMPLEX, 2.5, color, 6)
    # eyebrows: big and obvious, so the patient/team can see the raise was seen
    if brow.get("up"):
        held = brow.get("held_s", 0.0)
        text = f"EYEBROWS UP {held:.1f}s" + ("  -> CALL NURSE" if held >= config.EMERGENCY_S else "")
        cv2.putText(frame, text, (w // 2 - 40, 90), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (0, 255, 255), 5)
    else:
        cv2.putText(frame, "eyebrows down", (w // 2 - 40, 90), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (160, 160, 160), 2)
    text, at = flash
    if text and time.monotonic() - at < 1.5:
        cv2.putText(frame, text, (30, h // 2), cv2.FONT_HERSHEY_SIMPLEX, 2.2, (0, 255, 0), 6)
    lines = [f"confidence {gaze_conf:.2f}   machine {m.state}   screen {screen}",
             f"focus {focus}   fps {fps:.0f}"]
    if keyboard:
        lines.append("KEYBOARD MODE: a/s/d look  x away  space raise  e long raise  q quit")
    else:
        lines.append("Look at a card until it LOCKS (bright), then a short eyebrow raise.")
        lines.append("c = recalibrate gaze   g = eyebrows   q = quit")
    for i, line in enumerate(lines):
        cv2.putText(frame, line, (30, 140 + 40 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
    if det is not None:   # eyebrow bar: grey = value, red = threshold
        x0, x1, y0, y1 = 30, w - 30, h - 70, h - 35
        xpos = lambda v: int(x0 + max(0.0, min(1.0, v)) * (x1 - x0))
        cv2.rectangle(frame, (x0, y0), (x1, y1), (60, 60, 60), -1)
        cv2.rectangle(frame, (x0, y0), (xpos(det.value), y1), (0, 200, 0) if det.above_since else (200, 200, 200), -1)
        cv2.line(frame, (xpos(det.threshold), y0 - 8), (xpos(det.threshold), y1 + 8), (0, 0, 255), 3)
    cv2.imshow(WINDOW, frame)


def describe(events, out, chosen, flash: tuple) -> tuple:
    """What the last raise did, to flash in the operator window (and print)."""
    text = ""
    if any(e[0] == "call_nurse" for e in out):
        text = "CALL NURSE"
    elif chosen:
        text = f"CHOSE: {chosen}"
    elif "confirm" in events:
        text = "RAISE SEEN - look at a card until it locks first"
    if not text:
        return flash
    print(text)
    return text, time.monotonic()


def narrate(narrator, live: Live, speaker) -> None:
    """Speak what the patient screen shows (new screen, locked option, nurse called)."""
    with live.lock:
        screen = live.screen_json()
    for text, interrupt in narrator.update(screen):
        speaker.say(text, interrupt=interrupt)


def run_camera(args, live: Live) -> None:
    from lookat.gaze import GazeModel, LocalSpeaker
    from lookat.narrator import Narrator
    from lookat.gestures import GestureDetector, load_settings
    from lookat.vision import FaceTracker
    from lookat.awake import AwakeDetector

    speaker = LocalSpeaker()
    awake = AwakeDetector()
    tracker = FaceTracker(args.camera)
    live.camera_ok = True
    try:
        gaze = (calibrate(tracker, live, speaker, args.patient, "gaze")
                if args.recalibrate or not GazeModel.exists(args.patient) else GazeModel.load(args.patient))
        settings = None if args.recalibrate else load_settings(args.patient)
        if settings is None:
            settings = calibrate(tracker, live, speaker, args.patient, "gestures")
        det = GestureDetector(settings, on_release=True)
        print(f"Ready. Eyebrow threshold {det.threshold:.2f} ({settings['gesture']}). "
              f"Watch the '{WINDOW}' window; press q in it to quit.")
        fps, last, raised, flash = 0.0, time.monotonic(), False, ("", 0.0)
        narrator = Narrator()
        while True:
            frame, f = tracker.read()
            if frame is None:
                live.camera_ok = False
                print("Stopped: the camera stopped giving frames (unplugged, or used by another app?).")
                break
            now = time.monotonic()
            spot, conf = gaze.predict(f)
            events = det.update(f, now)
            brow = {"up": det.above_since is not None, "held_s": det.held(now)}
            awake.set_override(live.eyes_override)
            live.awake_state = awake.update(f, now)
            out = live.frame(now, spot, events, face=f is not None, confidence=conf, brow=brow)
            flash = describe(events, out, live.chosen, flash)
            narrate(narrator, live, speaker)
            fps = 0.9 * fps + 0.1 / max(now - last, 1e-3)
            last = now
            if f is not None and f["face_px"] < config.MIN_FACE_PX:
                cv2.putText(frame, "FACE TOO SMALL: move the camera closer", (30, frame.shape[0] - 100),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 3)
            draw_operator(frame, live, conf, det, fps, flash=flash)
            if not raised:
                raise_window()
                raised = True
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                print("Stopped: q pressed.")
                break
            if key == ord("c"):
                gaze = calibrate(tracker, live, speaker, args.patient, "gaze")
                raised = False
            elif key == ord("g"):
                settings = calibrate(tracker, live, speaker, args.patient, "gestures")
                det = GestureDetector(settings, on_release=True)
                raised = False
    finally:
        live.camera_ok = False
        tracker.close()
        speaker.close()


def run_keyboard(args, live: Live) -> None:
    """No camera: drive the real screens, lamp and fan from the keyboard."""
    from lookat.gaze import LocalSpeaker
    from lookat.narrator import Narrator
    speaker, narrator = LocalSpeaker(), Narrator()
    try:
        _keyboard_loop(live, speaker, narrator)
    finally:
        speaker.close()


def _keyboard_loop(live: Live, speaker, narrator) -> None:
    spot, look = None, {ord("a"): "left", ord("s"): "middle", ord("d"): "right"}
    fps, last = 0.0, time.monotonic()
    while True:
        now = time.monotonic()
        key = cv2.waitKey(30) & 0xFF
        events = []
        if key == ord("q"):
            break
        if key in look:
            spot = look[key]
        elif key == ord("x"):
            spot = None
        elif key == ord(" "):
            events = ["confirm"]
        elif key == ord("e"):
            events = ["emergency"]
        live.frame(now, spot, events, face=True, confidence=1.0 if spot else 0.0)
        is_open = live.eyes_override != "closed"   # keyboard: no camera, the patient counts as awake
        live.awake_state = {"eyes_open": is_open, "open_ratio": 1.0 if is_open else 0.0, "awake": is_open}
        narrate(narrator, live, speaker)
        fps = 0.9 * fps + 0.1 / max(now - last, 1e-3)
        last = now
        draw_operator(np.zeros((480, 1000, 3), np.uint8), live, 1.0 if spot else 0.0, None, fps, keyboard=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="LookAt")
    ap.add_argument("--patient", default="demo")
    ap.add_argument("--recalibrate", action="store_true")
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--keyboard", action="store_true", help="no camera: test with the keyboard")
    ap.add_argument("--port", type=int, default=config.PAGE_PORT)
    args = ap.parse_args()

    check_wifi()
    room = Room()
    live = Live(room, patient=args.patient)
    from lookat.agent import MODEL, Agent
    from lookat.memory import Memory
    agent = Agent(live, Memory(args.patient))
    agent.start()
    print(f"Agent: {'Claude ' + MODEL if agent.client else 'rules only (no ANTHROPIC_API_KEY)'}")
    server = start_server(live, args.port)
    print_urls(args.port)
    print(f"Lamp: {room.lamp.ip or 'simulated'}   Fan: {room.fan.ip or 'simulated (set FAN_IP for plug 2)'}")
    try:
        (run_keyboard if args.keyboard else run_camera)(args, live)
    except KeyboardInterrupt:
        pass
    finally:
        cv2.destroyAllWindows()
        server.should_exit = True
        room.close()
        print("LookAt stopped.", flush=True)
        # end the process for sure: a stuck camera/voice/web thread kept it alive before, holding
        # port 8000 and the camera, so the next start failed with "port already in use"
        os._exit(0)


if __name__ == "__main__":
    main()
