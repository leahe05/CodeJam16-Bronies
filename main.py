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


def draw_operator(frame, live: Live, gaze_conf: float, det, fps: float, keyboard: bool = False):
    h, w = frame.shape[:2]
    with live.lock:
        m, screen = live.machine, live.sm.kind
        spot, face = live.spot, live.face
        focus = m.view()
    color = (0, 200, 0) if spot else (0, 165, 255)
    cv2.putText(frame, (spot or ("no face" if not face else "unsure")).upper(), (30, 90),
                cv2.FONT_HERSHEY_SIMPLEX, 2.5, color, 6)
    lines = [f"confidence {gaze_conf:.2f}   machine {m.state}   screen {screen}",
             f"focus {focus}   fps {fps:.0f}"]
    if keyboard:
        lines.append("KEYBOARD MODE: a/s/d look  x away  space raise  e long raise  q quit")
    else:
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


def run_camera(args, live: Live) -> None:
    from lookat.gaze import GazeModel, LocalSpeaker
    from lookat.gestures import GestureDetector, load_settings
    from lookat.vision import FaceTracker

    speaker = LocalSpeaker()
    tracker = FaceTracker(args.camera)
    live.camera_ok = True
    try:
        gaze = (calibrate(tracker, live, speaker, args.patient, "gaze")
                if args.recalibrate or not GazeModel.exists(args.patient) else GazeModel.load(args.patient))
        settings = None if args.recalibrate else load_settings(args.patient)
        if settings is None:
            settings = calibrate(tracker, live, speaker, args.patient, "gestures")
        det = GestureDetector(settings, on_release=True)
        print(f"Ready. Eyebrow threshold {det.threshold:.2f} ({settings['gesture']}).")
        fps, last = 0.0, time.monotonic()
        while True:
            frame, f = tracker.read()
            if frame is None:
                live.camera_ok = False
                print("Camera stopped giving frames.")
                break
            now = time.monotonic()
            spot, conf = gaze.predict(f)
            events = det.update(f, now)
            live.frame(now, spot, events, face=f is not None, confidence=conf)
            fps = 0.9 * fps + 0.1 / max(now - last, 1e-3)
            last = now
            if f is not None and f["face_px"] < config.MIN_FACE_PX:
                cv2.putText(frame, "FACE TOO SMALL: move the camera closer", (30, frame.shape[0] - 100),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 3)
            draw_operator(frame, live, conf, det, fps)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("c"):
                gaze = calibrate(tracker, live, speaker, args.patient, "gaze")
            elif key == ord("g"):
                settings = calibrate(tracker, live, speaker, args.patient, "gestures")
                det = GestureDetector(settings, on_release=True)
    finally:
        live.camera_ok = False
        tracker.close()
        speaker.close()


def run_keyboard(args, live: Live) -> None:
    """No camera: drive the real screens, lamp and fan from the keyboard."""
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
        print("LookAt stopped.")


if __name__ == "__main__":
    main()
