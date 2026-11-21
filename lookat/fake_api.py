"""Fake backend for building the 3 pages without a camera (see docs/API.md).

Run:  py -3.11 -m lookat.fake_api [--port 8000] [--still SCREEN]
Then open http://localhost:8000/patient , /nurse , /panel

Serves Lea's pages from web/ exactly like the real program (lookat/server.py
finds them), with fake JSON that changes every few seconds. Every POST in docs/API.md works
(in memory). The real program serves the same URLs and the same JSON shapes.
"""
import argparse
import itertools
import threading
import time

from lookat import config

CYCLE_S = 3.0


def opt(id, label, hint=None, icon=None):
    return {"id": id, "label": label, "hint": hint, "icon": icon}


HOME = opt("home", "Return to main menu", "Look center + raise eyebrows", "home")
BACK = opt("back", "Back", "Look center + raise eyebrows", "back")
ROOM_CARD = opt("room", "Room Controls", "Look left + short eyebrow raise", "lamp")
NURSE_FOOTER = ("Look left or right and raise your eyebrows to choose, "
                "or look center and raise your eyebrows to see more options.")


def screen(screen_id, title, badge=None, subtitle=None, icon=None, left=None, middle=None,
           right=None, footer=None, page=None, show_call_nurse=True):
    return {"screen": screen_id, "badge": badge, "title": title, "subtitle": subtitle,
            "icon": icon, "left": left, "middle": middle, "right": right, "footer": footer,
            "page": page, "show_call_nurse": show_call_nurse}


# One example of every screen, in demo order. Labels match the mockups exactly.
SCREENS = [
    screen("idle", "You're all set.", subtitle="I'm here if you need anything.", icon="moon",
           left=ROOM_CARD, middle=opt("talk", "Talk to AI", "Look center + short eyebrow raise", "chat")),
    screen("suggestion", "Would you like help getting comfortable?", badge="AI SUGGESTION", icon="sparkle",
           left=ROOM_CARD, middle=opt("engage", "Look center + raise eyebrows to engage", None, "target")),
    screen("checkin", "Would you like help adjusting your pillows?", badge="AI CHECK-IN", icon="pillow",
           left=opt("yes", "Yes, please.", "Look left + raise eyebrows", "check"), middle=HOME,
           right=opt("no", "No, thanks.", "Look right + raise eyebrows", "x")),
    screen("assistant", "How can I help you?", badge="AI ASSISTANT", icon="sparkle",
           left=opt("comfort", "Get more comfortable", "Look left + raise eyebrows", "bed"), middle=HOME,
           right=opt("more", "More suggestions", "Look right + raise eyebrows", "grid")),
    screen("room", "What would you like to adjust?", badge="ROOM CONTROLS", icon="sparkle",
           left=opt("lights", "Adjust the lights", "Look left + raise eyebrows", "bulb"), middle=HOME,
           right=opt("temperature", "Adjust the temperature", "Look right + raise eyebrows", "thermometer")),
    screen("lights", "The lamp is ON", badge="ROOM CONTROLS", icon="lamp",
           left=opt("lamp_on", "Lamp on", "Look left + raise eyebrows", "sun"), middle=BACK,
           right=opt("lamp_off", "Lamp off", "Look right + raise eyebrows", "moon")),
    screen("temperature", "The room is 24.5 °C", badge="ROOM CONTROLS", icon="thermometer",
           left=opt("cooler", "Cooler", "Look left + raise eyebrows", "snow"), middle=BACK,
           right=opt("warmer", "Warmer", "Look right + raise eyebrows", "sun")),
    screen("checkin", "You seem to be awake. Too warm?", badge="AI CHECK-IN", icon="thermometer",
           left=opt("yes", "Yes, please.", "Look left + raise eyebrows", "check"), middle=HOME,
           right=opt("no", "No, thanks.", "Look right + raise eyebrows", "x")),
    screen("checkin", "What would help most?", badge="AI CHECK-IN", icon="sparkle",
           left=opt("o0", "Adjust pillows", "Look left + raise eyebrows", "pillow"),
           middle=opt("more", "More options", "Look center + raise eyebrows", "more"),
           right=opt("o1", "Change position", "Look right + raise eyebrows", "bed"),
           page={"index": 0, "count": 2}),
    screen("nurse", "What would help you feel more comfortable?", badge="From your nurse", icon="nurse",
           left=opt("o0", "Adjust pillows", None, "pillow"),
           middle=opt("more", "More options", None, "more"),
           right=opt("o1", "Change position", None, "bed"),
           footer=NURSE_FOOTER, page={"index": 0, "count": 2}, show_call_nurse=False),
    screen("nurse", "What would help you feel more comfortable?", badge="From your nurse", icon="nurse",
           left=opt("o2", "Water", None, "water"),
           middle=opt("more", "More options", None, "more"),
           right=opt("o3", "Something else", None, "question"),
           footer=NURSE_FOOTER, page={"index": 1, "count": 2}, show_call_nurse=False),
]

# Highlight sequence shown on each screen: nothing -> looking -> locked -> chosen.
FOCUS = [None, ("left", "dwell"), ("left", "asking"), ("left", "chosen"),
         ("right", "dwell"), ("middle", "dwell"), ("middle", "asking")]


class Fake:
    """All fake data, changed by a ticker thread and by the POSTs."""

    def __init__(self, still=None):
        self.lock = threading.Lock()
        self.start = time.time()
        self.still = still
        self.temp_c, self.setpoint_c = 24.5, 22.0
        self.lamp_on, self.fan_on = True, False
        self.overlay_until = 0.0
        from lookat.speech import Announcer, WebVoice
        self.voice = WebVoice()
        self.announcer = Announcer(self.voice.say, queue=self.voice)
        self.alert_ids = itertools.count(4)
        self.qids = itertools.count(2)
        self.alerts = [
            {"id": "a3", "time": "07:31", "kind": "flag", "urgency": "normal",
             "text": "Patient feels cold below 20 °C. Ask them first?",
             "reason": "You set the AC to 18 °C. Memory: feels cold below 20 °C (chart).",
             "actions": [{"id": "ask", "label": "Ask them"}, {"id": "set_anyway", "label": "Set anyway"}],
             "done": False},
            {"id": "a2", "time": "05:02", "kind": "page", "urgency": "normal",
             "text": "Wants repositioning.", "reason": "Not temperature-related. Last visit 3h ago.",
             "actions": [{"id": "ack", "label": "On my way"}], "done": False},
            {"id": "a1", "time": "02:20", "kind": "call", "urgency": "urgent",
             "text": "Patient used the call signal", "reason": None,
             "actions": [{"id": "ack", "label": "On my way"}], "done": True},
        ]
        self.session = {"active": False, "thread": [], "follow_ups": [], "waiting": False}
        self.facts = [{"text": "Feels cold below 20 °C", "source": "chart"},
                      {"text": "Runs warm at night", "source": "chart"}]
        self.recent = [{"time": "02:16", "text": "Said Yes to: Too warm? -> fan on (24.5 -> 22.8 °C)"}]
        self.last_visit = "04:10"
        self.summary = None
        self.feed = []
        self.feed_script = itertools.cycle([
            ("noticed", "Awake at 2:14 AM, room 24.5 °C",
             "night + eyes open 92% of the last minute + above 23 °C + memory: runs warm"),
            ("decided", 'Ask: "You seem to be awake. Too warm?"', "one short question; fixable without a nurse"),
            ("did", "Patient said Yes -> fan ON", "approved by the patient's answer"),
            ("checked", "Room 24.5 -> 22.8 °C after 60 s. Asked \"Better now?\" -> Yes", "checking my own fix"),
            ("outcome", "No nurse needed", "fixed by the room; logged for the shift summary"),
            ("learned", "Night warmth episode #3 this week", "memory updated"),
        ])
        self.feed_ids = itertools.count(1)

    # ----- what the GETs return -------------------------------------------
    def clock(self):
        return {"time": time.strftime("%H:%M"), "demo": True}

    def room(self):
        return {"temp_c": round(self.temp_c, 1), "setpoint_c": self.setpoint_c,
                "lamp_on": self.lamp_on, "fan_on": self.fan_on}

    def screen_json(self):
        elapsed = time.time() - self.start
        if self.session["waiting"] and self.session["thread"]:
            q = self.session["thread"][-1]
            o = q["options"]
            base = screen("nurse", q["question"], badge="From your nurse", icon="nurse",
                          left=opt("o0", o[0], None, None), right=opt("o1", o[1], None, None) if len(o) > 1 else None,
                          middle=opt("more", "More options", None, "more") if len(o) > 2 else None,
                          footer=NURSE_FOOTER, page={"index": 0, "count": (len(o) + 1) // 2} if len(o) > 2 else None,
                          show_call_nurse=False)
        elif self.still:
            base = next((s for s in SCREENS if s["screen"] == self.still), SCREENS[0])
        else:
            base = SCREENS[int(elapsed // (CYCLE_S * 2)) % len(SCREENS)]
        f = FOCUS[int(elapsed // (CYCLE_S / 2.5)) % len(FOCUS)]
        focus = {"spot": f[0], "state": f[1]} if f and base.get(f[0]) else None
        overlay = None
        if self.still == "calibrate":   # preview the calibration overlay: left, middle, right, eyebrows
            steps = [("Look at the LEFT card", "left"), ("Look at the MIDDLE card", "middle"),
                     ("Look at the RIGHT card", "right"), ("Raise your eyebrows", None)]
            text, spot = steps[int(elapsed // 3) % len(steps)]
            overlay = {"kind": "calibrate", "text": text, "spot": spot}
            focus = None
        if time.time() < self.overlay_until:
            overlay = {"kind": "call_nurse", "text": "Your nurse has been called"}
        up = bool(focus and focus["state"] == "asking")
        self.announcer.update(base, focus, None, now=elapsed)
        return {"v": 1, **base, "focus": focus, "overlay": overlay, "clock": self.clock(),
                "room": self.room(), "face": True, "ask_s": config.ASK_TIMEOUT_S, "voice": self.voice.recent(),
                "brow": {"up": up, "held_s": 0.6 if up else 0.0, "long_s": 2.0}}

    def nurse_json(self):
        return {"v": 1, "patient": {"name": "Demo patient", "room": "Room 12"}, "connected": True,
                "alerts": self.alerts, "next_visit": ["Would like the blinds open in the morning"],
                "session": self.session, "room": {**self.room(), "fan_simulated": False},
                "facts": self.facts,
                "routines": [{"text": "Lamp off at 22:00", "status": "approved", "evidence": "6 of the last 7 nights"}],
                "recent": self.recent, "last_visit": self.last_visit, "summary": self.summary}

    def panel_json(self):
        return {"v": 1, "clock": {**self.clock(), "day": "Night 1"},
                "perception": {**self.room(), "eyes_open": True, "awake": True, "open_ratio": 0.92,
                               "face": True, "last_visit": self.last_visit, "screen": self.screen_json()["screen"],
                               "quiet_until": None},
                "memory": {"facts": self.facts,
                           "routines": [{"text": "Lamp off at 22:00", "status": "candidate",
                                         "evidence": "patient turned it off 21:55, 22:05, 22:00 on Nov 14-16"}],
                           "top_options": ["Adjust pillows", "Water"], "hidden_options": ["TV on"],
                           "night_checkins": True},
                "feed": self.feed[-30:],
                "status": {"camera": True, "lamp": True, "fan": "simulated", "ai": True}}

    # ----- background changes ---------------------------------------------
    def tick(self):
        with self.lock:
            self.temp_c += -0.1 if self.fan_on else 0.02
            self.temp_c = max(19.0, min(26.0, self.temp_c))
            stage, text, why = next(self.feed_script)
            self.feed.append({"id": next(self.feed_ids), "time": time.strftime("%H:%M"), "stage": stage,
                              "text": text, "why": why, "fallback": False})
            # the fake patient answers a waiting nurse question after a few seconds
            s = self.session
            if s["waiting"] and s["thread"] and time.time() - s["thread"][-1]["_sent"] > 4:
                q = s["thread"][-1]
                q["answer"], q["seconds"] = q["options"][0], round(time.time() - q["_sent"], 1)
                s["waiting"] = False
                s["follow_ups"] = [{"question": "What would help most?",
                                    "options": ["Adjust pillows", "Change position", "Water", "Something else"]}]

    # ----- POST handlers ----------------------------------------------------
    def nurse_action(self, path, body):
        with self.lock:
            if path == "session/start":
                self.session.update(active=True)
            elif path == "session/end":
                self.session = {"active": False, "thread": [], "follow_ups": [], "waiting": False}
            elif path == "suggest":
                q = (body.get("question") or "").strip()
                if not q:
                    return {"ok": False, "error": "empty question"}
                yes_no = q.lower().split(" ")[0] in ("is", "are", "do", "did", "would", "can", "should", "does")
                options = ["Yes", "No"] if yes_no else ["Adjust pillows", "Water", "Change position", "Something else"]
                return {"ok": True, "options": options, "source": "fallback"}
            elif path == "send":
                q, options = (body.get("question") or "").strip(), list(body.get("options") or [])
                if not q or len(options) < 2:
                    return {"ok": False, "error": "need a question and at least 2 options"}
                qid = f"q{next(self.qids)}"
                self.session["active"] = True
                self.session["waiting"] = True
                self.session["follow_ups"] = []
                self.session["thread"].append({"qid": qid, "time": time.strftime("%H:%M"), "question": q,
                                               "options": options, "answer": None, "seconds": None,
                                               "_sent": time.time()})
                return {"ok": True, "qid": qid}
            elif path == "alert":
                for a in self.alerts:
                    if a["id"] == body.get("id"):
                        a["done"] = True
                        if body.get("action") == "ask":
                            self.session["active"] = True
                        return {"ok": True}
                return {"ok": False, "error": "no such alert"}
            elif path == "room":
                if "setpoint_c" in body:
                    self.setpoint_c = float(body["setpoint_c"])
                    if self.setpoint_c < 20:
                        self.alerts.insert(0, {
                            "id": f"a{next(self.alert_ids)}", "time": time.strftime("%H:%M"), "kind": "flag",
                            "urgency": "normal", "text": "Patient feels cold below 20 °C. Ask them first?",
                            "reason": f"You set the AC to {self.setpoint_c:g} °C. Memory: feels cold below 20 °C.",
                            "actions": [{"id": "ask", "label": "Ask them"}, {"id": "set_anyway", "label": "Set anyway"}],
                            "done": False})
                elif body.get("device") in ("lamp", "fan") and body.get("cmd") in ("on", "off"):
                    setattr(self, body["device"] + "_on", body["cmd"] == "on")
                else:
                    return {"ok": False, "error": "bad room command"}
            elif path == "visit":
                self.last_visit = time.strftime("%H:%M")
            elif path == "fact":
                text = (body.get("text") or "").strip()
                if not text:
                    return {"ok": False, "error": "empty fact"}
                self.facts.append({"text": text, "source": "nurse"})
            elif path == "summary":
                self.summary = ("Woke at 2:14 AM feeling warm; fan on, 24.5 -> 22.8 °C; no nurse needed. "
                                "Requested repositioning at 5:02 AM. Felt too cold when the AC was set to 18 °C.")
                return {"ok": True, "text": self.summary}
            else:
                return {"ok": False, "error": "unknown action"}
            return {"ok": True}

    def demo_action(self, body):
        with self.lock:
            action = body.get("action")
            if action == "set_temp":
                self.temp_c = float(body.get("value", self.temp_c))
            elif action == "reset":
                self.__init__(self.still)
            elif action == "call_nurse":  # fake only: preview the overlay
                self.overlay_until = time.time() + 6
            elif action not in ("set_time", "one_week_later", "eyes"):
                return {"ok": False, "error": "unknown action"}
            return {"ok": True}


def make_app(fake: Fake):
    """The same server the real program uses (lookat/server.py)."""
    from lookat.server import make_app as server_app
    return server_app(fake, title="LookAt fake API")


def main():
    import uvicorn

    ap = argparse.ArgumentParser(description="Fake LookAt backend for building the pages")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--still", help="always show this screen id (idle, checkin, nurse, ...; calibrate = the calibration overlay)")
    args = ap.parse_args()
    fake = Fake(args.still)

    def ticker():
        while True:
            time.sleep(CYCLE_S)
            fake.tick()

    threading.Thread(target=ticker, daemon=True).start()
    print(f"Fake LookAt API on http://localhost:{args.port}  ->  /patient  /nurse  /panel   (Ctrl+C to stop)")
    uvicorn.run(make_app(fake), host="0.0.0.0", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
