"""The real backend behind the web API (docs/API.md): selection engine + screens +
room + nurse session + demo controls. lookat/server.py serves it; main.py feeds it
one camera frame at a time with frame().

Threads: frame() runs in the camera loop (main thread); the GET handlers and the
POST actions run in the web server thread. Everything shared is guarded by self.lock.
Slow work (AI option suggestions) runs OUTSIDE the lock.

Patient events (from lookat/screens.py) are handled here (room, nurse answers,
call nurse) and also passed to every function in self.listeners: that is where the
agent (M4) plugs in.
"""
import itertools
import os
import threading
import time
from typing import Callable, Optional

from lookat import config
from lookat.clock import clock
from lookat.machine import Machine
from lookat.screens import ScreenManager
from lookat.state import log_event

# A face missing for less than this (a dropped frame, a blink, a hand passing) keeps the look, so
# the highlight doesn't reset; no choice is ever made on a frame without a face.
FACE_GRACE_S = 0.4

YES_NO_START = ("is", "are", "do", "did", "does", "would", "will", "can", "could", "should", "have", "has")


def fallback_options(question: str) -> list:
    """Answers when the AI is unavailable (ask.py replaces this when it exists)."""
    first = question.strip().lower().split(" ")[0] if question.strip() else ""
    return ["Yes", "No"] if first in YES_NO_START else ["Yes", "No", "Something else"]


def request_text(question: str, answer: str) -> str:
    return f'Answered "{answer}" to: {question}'


class Live:
    def __init__(self, room, patient: str = "demo", now: Callable[[], float] = time.monotonic):
        self.lock = threading.RLock()
        self.room = room
        self.patient = patient
        self.now = now
        self.listeners: list = []        # callbacks(event_tuple) - the agent subscribes here
        self.camera_ok = False
        self.calibration: Optional[dict] = None   # {"text", "spot"} while calibrating
        self.announcer = None            # lookat.speech.Announcer: spoken feedback (main.py sets it)
        self.voice_out = None            # lookat.speech.WebVoice: lines the patient page reads aloud
        self._ids = itertools.count(1)
        # set once by main.py / the agent; "Reset demo" keeps them (it used to drop them, which broke the buttons)
        self.gaze_mode = "camera"                  # "web": the patient page tracks gaze itself (main.py --gaze web)
        self.demo_week: Optional[Callable] = None  # the agent: loads the labelled demo week ("One week later")
        self.switch_profile: Optional[Callable] = None   # the agent: switch to another demo patient
        self.reset_memory: Optional[Callable] = None     # the agent: every profile back to its chart
        self.profile: Optional[str] = None         # the demo patient the agent cares for (memory.PROFILES)
        self._reset_state()

    def _reset_state(self) -> None:
        self.sm = ScreenManager(room=self.room.state)
        self.machine = Machine()
        self.face = False
        self._face_seen: Optional[float] = None   # when a face was last in view
        self._last_spot: Optional[str] = None     # where it was looking then
        self.brow = {"up": False, "held_s": 0.0}
        self.chosen: Optional[str] = None
        self.spot: Optional[str] = None
        self.confidence = 0.0
        self.alerts: list = []
        self.next_visit: list = []
        self.session = {"active": False, "thread": [], "follow_ups": [], "waiting": False}
        self.recent: list = []
        self.facts: list = []
        self.feed: list = []
        self.last_visit: Optional[str] = None
        self.summary: Optional[str] = None
        self.eyes_override: Optional[str] = None
        self.awake_state: dict = {"eyes_open": None, "open_ratio": None, "awake": None}   # main.py, from awake.py
        self.memory_view: Optional[dict] = None   # the agent publishes what it learned (panel)
        self._say_nurse_called = False   # nurse_called(): the voice says it on the next frame
        self.agent_status = "Starting"             # the agent: what it is doing / why it is quiet (panel)
        self.quiet_until: Optional[str] = None
        self.web_gaze = {"spot": None, "x": None, "y": None, "at": -1e9,   # from the browser (main.py --gaze web)
                         "face": False, "eyes_open": None, "brow": {"up": False, "held_s": 0.0}}
        self.web_events: list = []      # eyebrow events from the browser, taken by main.py's web loop

    # ----- camera loop ----------------------------------------------------
    def frame(self, now: float, spot: Optional[str], events=(), face: bool = True, confidence: float = 0.0,
              brow: Optional[dict] = None) -> list:
        """One camera frame. brow = {"up": bool, "held_s": float} from the gesture detector.
        Returns the patient events it produced."""
        with self.lock:
            if face:
                self._face_seen, self._last_spot = now, spot
            else:
                events = [e for e in (events or ()) if e != "confirm"]   # never choose without a face
                if self._face_seen is not None and now - self._face_seen < FACE_GRACE_S:
                    face, spot = True, self._last_spot                     # a blip: keep the look
            self.face, self.spot, self.confidence = face, spot, confidence
            self.brow = brow or {"up": False, "held_s": 0.0}
            if self.calibration is not None:
                return []
            out = list(self.sm.tick(now, busy=self.machine.busy))
            self.chosen = None                    # label chosen this frame (operator feedback)
            holding = bool(self.brow.get("up"))   # a raise in progress: wait for it to come down
            for action in self.machine.step(now, spot, events, face, self.sm.active_spots(), holding=holding):
                if action[0] == "call_nurse":
                    out += self.sm.call_nurse(now)
                elif action[0] == "select":
                    opt = self.sm.view(now).get(action[1])
                    self.chosen = opt["label"] if opt else action[1]
                    out += self.sm.select(action[1], now)
            self.room.tick(now)
            for ev in out:
                self._handle(ev, now)
            if self.announcer is not None:
                try:
                    spoken = out + ([("call_nurse",)] if self._say_nurse_called else [])
                    self._say_nurse_called = False
                    self.announcer.update(self.sm.view(now), self.machine.view(), self.chosen, spoken, now=now)
                except Exception as e:   # noqa: BLE001 - the voice must never stop the camera loop
                    log_event("voice_error", error=repr(e))
        for ev in out:
            self._notify(ev)
        return out

    def _handle(self, ev: tuple, now: float) -> None:
        kind = ev[0]
        log_event("patient", event=list(ev), screen=self.sm.kind)
        if kind == "device":
            _, device, cmd = ev
            result = self.room.execute(device, cmd, by="patient")
            self._recent(f"Patient: {result}")
        elif kind == "call_nurse":
            self.add_alert("call", "Patient used the call signal", urgency="urgent",
                           reason="Long eyebrow raise" + (" during your conversation" if self.session["active"] else ""),
                           actions=[{"id": "ack", "label": "On my way"}])
        elif kind == "nurse_answer":
            _, qid, text = ev
            for q in self.session["thread"]:
                if q["qid"] == qid and q["answer"] is None:
                    q["answer"] = text
                    q["seconds"] = round(now - q["_sent"], 1)
                    self._recent(request_text(q["question"], text))
            self.session["waiting"] = False

    def _notify(self, ev: tuple) -> None:
        for cb in list(self.listeners):
            try:
                cb(ev)
            except Exception as e:   # noqa: BLE001 - a listener must never stop the camera loop
                log_event("listener_error", error=repr(e))

    # ----- shared helpers (agent + nurse) ---------------------------------
    def add_alert(self, kind: str, text: str, urgency: str = "normal", reason: Optional[str] = None,
                  actions: Optional[list] = None, ask: Optional[dict] = None) -> dict:
        with self.lock:
            a = {"id": f"a{next(self._ids)}", "time": clock.hhmm(), "kind": kind, "urgency": urgency,
                 "text": text, "reason": reason, "actions": actions or [{"id": "ack", "label": "OK"}],
                 "done": False, "_ask": ask}
            self.alerts.insert(0, a)
            log_event("nurse_alert", kind=kind, text=text, urgency=urgency, reason=reason)
            return a

    def nurse_called(self) -> None:
        """Show the patient the "Your nurse has been called" screen, the same one as a long eyebrow raise
        (and the voice says it), when someone else already paged the nurse: no second alert."""
        with self.lock:
            self.sm.call_nurse(self.now())
            self._say_nurse_called = True

    def add_feed(self, stage: str, text: str, why: Optional[str] = None, fallback: bool = False) -> None:
        with self.lock:
            self.feed.append({"id": next(self._ids), "time": clock.hhmm(), "stage": stage, "text": text,
                              "why": why, "fallback": fallback})
            del self.feed[:-200]

    def _recent(self, text: str) -> None:
        self.recent.insert(0, {"time": clock.hhmm(), "text": text})
        del self.recent[30:]

    def set_calibration(self, text: Optional[str], spot: Optional[str] = None) -> None:
        with self.lock:
            self.calibration = None if text is None else {"text": text, "spot": spot}

    # ----- GET /api/screen --------------------------------------------------
    def screen_json(self) -> dict:
        now = self.now()
        v = self.sm.view(now)
        if self.calibration is not None:
            v["overlay"] = {"kind": "calibrate", **self.calibration}
        r = self.room.state()
        return {"v": 1, **v, "focus": None if self.calibration else self.machine.view(),
                "clock": {"time": clock.hhmm(), "demo": clock.demo},
                "room": {k: r[k] for k in ("temp_c", "setpoint_c", "lamp_on", "fan_on")},
                "face": self.face, "ask_s": config.ASK_TIMEOUT_S,
                "voice": self.voice_out.recent() if self.voice_out is not None else [],
                "brow": {"up": bool(self.brow.get("up")), "held_s": round(float(self.brow.get("held_s", 0.0)), 2),
                         "long_s": config.EMERGENCY_S}}

    # ----- GET /api/nurse ---------------------------------------------------
    def nurse_json(self) -> dict:
        r = self.room.state()
        return {"v": 1, "patient": {"name": self.patient, "room": "Room 12"}, "connected": True,
                "alerts": self.alerts[:30], "next_visit": self.next_visit, "session": self.session,
                "room": {"temp_c": r["temp_c"], "setpoint_c": r["setpoint_c"], "lamp_on": r["lamp_on"],
                         "fan_on": r["fan_on"], "fan_simulated": r["fan"]["simulated"]},
                "facts": self.facts, "routines": [], "recent": self.recent, "last_visit": self.last_visit,
                "summary": self.summary}

    # ----- GET /api/panel ---------------------------------------------------
    def panel_json(self) -> dict:
        r = self.room.state()
        fan = r["fan"]
        return {"v": 1, "clock": {"time": clock.hhmm(), "day": clock.now().strftime("%a %b %d"), "demo": clock.demo},
                "perception": {"temp_c": r["temp_c"], "setpoint_c": r["setpoint_c"], "lamp_on": r["lamp_on"],
                               "fan_on": r["fan_on"], "eyes_open": self.awake_state.get("eyes_open"),
                               "awake": self.awake_state.get("awake"), "open_ratio": self.awake_state.get("open_ratio"),
                               "face": self.face, "last_visit": self.last_visit, "screen": self.sm.kind,
                               "quiet_until": self.quiet_until, "agent": self.agent_status},
                "memory": self.memory_view or {"facts": self.facts, "routines": [], "top_options": [],
                                               "hidden_options": [], "night_checkins": True},
                "feed": self.feed[-30:],
                "profile": self._profile_json(),
                "status": {"camera": self.camera_ok, "lamp": r["lamp"]["online"],
                           "fan": "simulated" if fan["simulated"] else fan["online"],
                           "ai": bool(os.environ.get("ANTHROPIC_API_KEY"))}}

    def _profile_json(self) -> Optional[dict]:
        """The demo patients for the panel: who is selected, and each one's bedtime and wake-up time."""
        from lookat.memory import PROFILES
        if self.profile is None:
            return None
        out = []
        for pid, p in PROFILES.items():
            times = {k: v for f in p["chart"] for k, v in (f.get("data") or {}).items() if k in ("bedtime", "wake")}
            out.append({"id": pid, "name": p["name"], "about": p["about"], **times})
        return {"current": self.profile, "all": out}

    # ----- POST /api/nurse/... ---------------------------------------------
    def nurse_action(self, path: str, body: dict) -> dict:
        if path == "suggest":            # may call the AI: never hold the lock here
            q = str(body.get("question") or "").strip()
            if not q:
                return {"ok": False, "error": "empty question"}
            return {"ok": True, **self._suggest(q)}
        if path == "summary":            # may call the AI (up to 8 s): never hold the lock here
            return self._shift_summary()
        now = self.now()
        with self.lock:
            if path == "session/start":
                self._start_session(now)
            elif path == "session/end":
                self.sm.nurse_end(now)
                self.machine.reset(now)
                self.session = {"active": False, "thread": [], "follow_ups": [], "waiting": False}
                log_event("nurse_session", state="end")
            elif path == "send":
                q = str(body.get("question") or "").strip()
                opts = [str(o).strip() for o in (body.get("options") or []) if str(o).strip()]
                if not q or len(opts) < 2:
                    return {"ok": False, "error": "need a question and at least 2 answers"}
                return {"ok": True, "qid": self.send_question(q, opts, now)}
            elif path == "alert":
                a = next((a for a in self.alerts if a["id"] == body.get("id")), None)
                if a is None:
                    return {"ok": False, "error": "no such alert"}
                a["done"] = True
                action = body.get("action")
                if action == "ask" and a.get("_ask"):
                    self.send_question(a["_ask"]["question"], a["_ask"]["options"], now)
                elif action == "set_anyway" and (a.get("_ask") or {}).get("setpoint_c") is not None:
                    self.room.set_setpoint(a["_ask"]["setpoint_c"], by="nurse", apply=True)
            elif path == "room":
                if "setpoint_c" in body:
                    sp = float(body["setpoint_c"])
                    self.room.set_setpoint(sp, by="nurse")
                    self._recent(f"Nurse set the AC to {sp:g} °C")
                    ev = ("nurse_setpoint", sp)
                    threading.Thread(target=self._notify, args=(ev,), daemon=True).start()
                elif body.get("device") in ("lamp", "fan") and body.get("cmd") in ("on", "off"):
                    self._recent("Nurse: " + self.room.execute(body["device"], body["cmd"], by="nurse"))
                else:
                    return {"ok": False, "error": "bad room command"}
            elif path == "visit":
                self.last_visit = clock.hhmm()
                self.next_visit = []
                log_event("nurse_visit", time=self.last_visit)
            elif path == "fact":
                text = str(body.get("text") or "").strip()
                if not text:
                    return {"ok": False, "error": "empty fact"}
                self.facts.append({"text": text, "source": "nurse"})
            else:
                return {"ok": False, "error": "unknown action"}
            return {"ok": True}

    def _start_session(self, now: float) -> None:
        if self.session["active"]:
            return
        self.sm.nurse_start(now)
        self.machine.reset(now)
        self.session.update(active=True)
        log_event("nurse_session", state="start")

    def send_question(self, question: str, options: list, now: float) -> str:
        with self.lock:
            self._start_session(now)
            qid = f"q{next(self._ids)}"
            self.sm.nurse_question(qid, question, options, now)
            self.machine.reset(now)
            self.session["thread"].append({"qid": qid, "time": clock.hhmm(), "question": question,
                                           "options": list(options), "answer": None, "seconds": None,
                                           "_sent": now})
            self.session["waiting"] = True
            self.session["follow_ups"] = []
            log_event("nurse_question", qid=qid, question=question, options=options)
            return qid

    def _suggest(self, question: str) -> dict:
        try:
            from lookat import ask   # C3; until it exists the fallback is used
            res = ask.make_options(question, "")
            return {"options": list(res["options"]), "source": res.get("source", "ai")}
        except Exception:   # noqa: BLE001 - no ask.py yet, no key, or an AI error
            return {"options": fallback_options(question), "source": "fallback"}

    def _shift_summary(self) -> dict:
        """lookat/summary.py from the patient's memory (main.py sets live.memory) + the agent's checks.
        Without a memory (tests, fake data): the recent events, as before."""
        memory = getattr(self, "memory", None)
        with self.lock:
            feed, recent = list(self.feed), list(self.recent)
        if memory is None:
            text = (" ".join(f"{r['time']} {r['text']}." for r in reversed(recent[:10]))
                    or "Nothing to report yet.")
            source = "template"
        else:
            from lookat.summary import shift_summary
            s = shift_summary(memory, feed)
            text, source = s["text"], s["source"]
        with self.lock:
            self.summary = text
        return {"ok": True, "text": text, "source": source}

    # ----- POST /api/gaze (browser eye tracking) ------------------------------
    WEB_GAZE_FRESH_S = 0.6

    def gaze_action(self, body: dict) -> dict:
        """The browser's gaze zone + face + eyebrows (web/webgaze.js). events: ["confirm"] / ["emergency"]."""
        spot = body.get("spot")
        if spot not in (None, "left", "middle", "right"):
            return {"ok": False, "error": "spot must be left, middle, right or null"}
        events = [e for e in (body.get("events") or []) if e in ("confirm", "emergency")]
        brow = body.get("brow") or {}
        with self.lock:
            self.web_gaze = {"spot": spot, "x": body.get("x"), "y": body.get("y"), "at": self.now(),
                             "face": bool(body.get("face", spot is not None)),
                             "eyes_open": body.get("eyes_open"),
                             "brow": {"up": bool(brow.get("up")), "held_s": float(brow.get("held_s") or 0.0)}}
            self.web_events += events
        return {"ok": True}

    def pop_web_events(self) -> list:
        with self.lock:
            out, self.web_events = self.web_events, []
        return out

    def web_fresh(self, now: float) -> bool:
        return now - self.web_gaze["at"] <= self.WEB_GAZE_FRESH_S

    def web_spot(self, now: float) -> Optional[str]:
        """The browser's latest gaze zone, or None when it is stale (page closed, face lost)."""
        g = self.web_gaze
        return g["spot"] if now - g["at"] <= self.WEB_GAZE_FRESH_S else None

    # ----- POST /api/demo ---------------------------------------------------
    def demo_action(self, body: dict) -> dict:
        action, value = body.get("action"), body.get("value")
        if action == "one_week_later":           # outside the lock: the agent writes memory and the panel
            if self.demo_week is None:
                return {"ok": False, "error": "the agent is not running"}
            self.demo_week()
            return {"ok": True}
        if action == "profile":                  # outside the lock: the agent loads memory and writes the panel
            if self.switch_profile is None:
                return {"ok": False, "error": "the agent is not running"}
            res = self.switch_profile(str(value))
            return {"ok": True} if res == "ok" else {"ok": False, "error": res}
        if action == "reset":
            with self.lock:
                clock.reset()
                self._reset_state()
                self.room.set_temp(22.0)
                log_event("demo_reset")
            if self.reset_memory is not None:    # outside the lock: the agent rewrites the memory files
                self.reset_memory()              # every profile back to its chart; the AI's history is deleted
            return {"ok": True}
        with self.lock:
            if action == "set_time":
                clock.set_time(str(value))
                self.add_feed("noticed", f"Demo clock set to {clock.hhmm()}", "demo control")
            elif action == "set_temp":
                self.room.set_temp(float(value))
            elif action == "eyes":
                if value not in ("auto", "open", "closed"):
                    return {"ok": False, "error": "eyes must be auto, open or closed"}
                self.eyes_override = None if value == "auto" else value
            else:
                return {"ok": False, "error": "unknown action"}
            return {"ok": True}
