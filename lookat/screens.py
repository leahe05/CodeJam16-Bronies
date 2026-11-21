"""The patient's screens as pure logic (no camera, no threads, no I/O).
Labels match the mockups exactly; layouts are in docs/API.md and PLAN.md R2.

    sm = ScreenManager(room=lambda: {"lamp_on": True, "temp_c": 24.5})
    sm.active_spots()        -> spots the Machine may select
    sm.select(spot, now)     -> events for main.py / the agent
    sm.tick(now, busy)       -> events from fades and timeouts
    sm.view(now)             -> the screen part of GET /api/screen

Screens: idle, suggestion, checkin, assistant, room, lights, temperature, nurse.

Events (tuples):
    ("device", "lamp"|"fan", "on"|"off")   patient room control
    ("talk_to_ai",)                       patient opened the AI assistant
    ("assistant_choice", label)           patient picked an assistant option
    ("engaged", sid)                      patient opened a pending suggestion
    ("answer", qid, text)                 patient answered an agent check-in
    ("dismissed", id)                     "Return to main menu" on a check-in, or Room Controls
                                          chosen over a suggestion (stops the stream)
    ("ignored", id)                       a suggestion faded / a check-in timed out
    ("nurse_answer", qid, text)           patient answered the nurse
    ("call_nurse",)                       long eyebrow raise
"""
from dataclasses import asdict, dataclass
from typing import Callable, Optional

from lookat import config

EPS = 1e-9

TALK = "Talk to AI"
ROOM_CONTROLS = "Room Controls"
RETURN = "Return to main menu"
BACK = "Back"
MORE_OPTIONS = "More options"
MORE_SUGGESTIONS = "More suggestions"
SOMETHING_ELSE = "Something else"
YES_NO = ["Yes, please.", "No, thanks."]
ASSISTANT_DEFAULT = ["Get more comfortable", "Quiet for 2 hours", "Adjust the room", SOMETHING_ELSE]
NURSE_FOOTER = ("Look left or right and raise your eyebrows to choose, "
                "or look center and raise your eyebrows to see more options.")

HINT = {"left": "Look left + raise eyebrows", "middle": "Look center + raise eyebrows",
        "right": "Look right + raise eyebrows"}

# keyword in a label -> icon keyword (docs/API.md lists the icons)
ICON_WORDS = [("pillow", "pillow"), ("position", "bed"), ("comfortable", "bed"), ("sleep", "bed"),
              ("water", "water"), ("drink", "water"), ("light", "bulb"), ("lamp", "lamp"),
              ("temperature", "thermometer"), ("warm", "thermometer"), ("cold", "snow"), ("cool", "snow"),
              ("quiet", "quiet"), ("something else", "question"), ("more", "more"), ("room", "lamp"),
              ("nurse", "nurse"), ("yes", "check"), ("no", "x"), ("fine", "check")]


def icon_for(label: str) -> Optional[str]:
    low = label.lower()
    words = set(low.replace(",", " ").replace(".", " ").split())
    for key, icon in ICON_WORDS:
        if (" " in key and key in low) or key in words or (len(key) > 3 and key in low):
            return icon
    return None


@dataclass
class Option:
    id: str
    label: str
    hint: Optional[str] = None
    icon: Optional[str] = None


def _opt(id: str, label: str, spot: str, hint: Optional[str] = "auto", icon: Optional[str] = "auto") -> Option:
    return Option(id, label, HINT[spot] if hint == "auto" else hint,
                  icon_for(label) if icon == "auto" else icon)


def paged(options: list, page: int) -> tuple:
    """2 options per page -> (left, right, page_count)."""
    count = max(1, (len(options) + 1) // 2)
    page %= count
    pair = options[page * 2: page * 2 + 2]
    return pair[0], (pair[1] if len(pair) > 1 else None), count


class ScreenManager:
    def __init__(self, room: Callable[[], dict] = lambda: {}):
        self.room = room            # live room state for titles: lamp_on, temp_c
        self.kind = "idle"
        self.since = 0.0            # when the current screen appeared
        self.suggestion: Optional[dict] = None
        self.checkin: Optional[dict] = None
        self.assistant = {"options": list(ASSISTANT_DEFAULT), "index": 0}
        self.nurse: Optional[dict] = None       # {"qid", "question", "options", "page"} or {} while waiting
        self.nurse_thanks = False
        self.overlay_until = -1e9

    # ----- what the screen shows --------------------------------------------
    def view(self, now: float) -> dict:
        s = self._layout()
        for spot in config.SPOTS:
            s[spot] = asdict(s[spot]) if s[spot] else None
        s["overlay"] = ({"kind": "call_nurse", "text": "Your nurse has been called"}
                        if now < self.overlay_until - EPS else None)
        return s

    def active_spots(self) -> list:
        s = self._layout()
        return [spot for spot in config.SPOTS if s[spot] is not None]

    def is_idle(self) -> bool:
        return self.kind == "idle"

    def _layout(self) -> dict:
        s = {"screen": self.kind, "badge": None, "title": "", "subtitle": None, "icon": None,
             "left": None, "middle": None, "right": None, "footer": None, "page": None,
             "show_call_nurse": True}
        room_card = Option("room", ROOM_CONTROLS, "Look left + short eyebrow raise", "lamp")
        k = self.kind
        if k == "idle":
            s.update(title="You're all set.", subtitle="I'm here if you need anything.", icon="moon",
                     left=room_card, middle=Option("talk", TALK, "Look center + short eyebrow raise", "chat"))
        elif k == "suggestion":
            s.update(badge="AI SUGGESTION", title=self.suggestion["text"], icon="sparkle", left=room_card,
                     middle=Option("engage", "Look center + raise eyebrows to engage", None, "target"))
        elif k == "checkin":
            c = self.checkin
            opts = c["options"]
            if len(opts) <= 2:
                left, right, count = opts[0], (opts[1] if len(opts) > 1 else None), 1
            else:
                left, right, count = paged(opts, c["page"])
                s["page"] = {"index": c["page"] % count, "count": count}
            last = c["page"] % count == count - 1
            s.update(badge="AI CHECK-IN", title=c["question"], icon=c.get("icon") or "sparkle",
                     left=_opt("a", left, "left"), right=_opt("b", right, "right") if right else None,
                     middle=_opt("home", RETURN, "middle", icon="home") if last
                     else _opt("more", MORE_OPTIONS, "middle", icon="more"))
        elif k == "assistant":
            opts, i = self.assistant["options"], self.assistant["index"] % len(self.assistant["options"])
            if len(opts) <= 2:
                right = _opt("b", opts[1], "right") if len(opts) > 1 else None
            else:
                right = _opt("more", MORE_SUGGESTIONS, "right", icon="grid")
            s.update(badge="AI ASSISTANT", title="How can I help you?", icon="sparkle",
                     left=_opt("a", opts[i], "left"), right=right, middle=_opt("home", RETURN, "middle", icon="home"))
        elif k == "room":
            s.update(badge="ROOM CONTROLS", title="What would you like to adjust?", icon="sparkle",
                     left=_opt("lights", "Adjust the lights", "left", icon="bulb"),
                     right=_opt("temperature", "Adjust the temperature", "right", icon="thermometer"),
                     middle=_opt("home", RETURN, "middle", icon="home"))
        elif k == "lights":
            on = self.room().get("lamp_on")
            s.update(badge="ROOM CONTROLS", icon="lamp",
                     title="The lamp is ON" if on else ("The lamp is OFF" if on is not None else "Lights"),
                     left=_opt("lamp_on", "Lamp on", "left", icon="sun"),
                     right=_opt("lamp_off", "Lamp off", "right", icon="moon"),
                     middle=_opt("back", BACK, "middle", icon="back"))
        elif k == "temperature":
            t = self.room().get("temp_c")
            s.update(badge="ROOM CONTROLS", icon="thermometer",
                     title=f"The room is {t:.1f} °C" if t is not None else "Temperature",
                     left=_opt("cooler", "Cooler", "left", icon="snow"),
                     right=_opt("warmer", "Warmer", "right", icon="sun"),
                     middle=_opt("back", BACK, "middle", icon="back"))
        elif k == "nurse":
            s.update(badge="From your nurse", icon="nurse", show_call_nurse=False, footer=NURSE_FOOTER)
            n = self.nurse or {}
            if not n.get("question"):
                s.update(title="Thank you. Your nurse can see your answer." if self.nurse_thanks
                         else "Your nurse is here.", subtitle="A question is coming.", footer=None)
            else:
                left, right, count = paged(n["options"], n["page"])
                s.update(title=n["question"], left=_opt("a", left, "left", hint=None),
                         right=_opt("b", right, "right", hint=None) if right else None)
                if count > 1:
                    s["page"] = {"index": n["page"] % count, "count": count}
                    s["middle"] = _opt("more", MORE_OPTIONS, "middle", hint=None, icon="more")
                else:
                    s["footer"] = "Look left or right and raise your eyebrows to choose."
        return s

    # ----- patient choices -------------------------------------------------
    def select(self, spot: str, now: float) -> list:
        s = self._layout()
        opt = s.get(spot)
        if opt is None:
            return []
        k, oid, label = self.kind, opt.id, opt.label
        if k in ("idle", "suggestion"):
            if oid == "room":
                # choosing Room Controls is an active "not now", not silence
                events = [("dismissed", self.suggestion["sid"])] if k == "suggestion" else []
                self._go("room", now)
                return events
            if oid == "talk":
                self.assistant["index"] = 0
                self._go("assistant", now)
                return [("talk_to_ai",)]
            if oid == "engage":
                sug = self.suggestion
                self.checkin = {"qid": sug["sid"], "question": sug["question"], "options": sug["options"],
                                "icon": sug.get("icon"), "page": 0}
                self._go("checkin", now)
                return [("engaged", sug["sid"])]
        if k == "checkin":
            c = self.checkin
            if oid == "more":
                c["page"] += 1
                return []
            self._go("idle", now)
            if oid == "home":
                return [("dismissed", c["qid"])]
            return [("answer", c["qid"], label)]
        if k == "assistant":
            if oid == "home":
                self._go("idle", now)
                return []
            if oid == "more":
                self.assistant["index"] = (self.assistant["index"] + 1) % len(self.assistant["options"])
                return []
            if label == "Adjust the room":
                self._go("room", now)
                return []
            self._go("idle", now)
            return [("assistant_choice", label)]
        if k == "room":
            self._go({"lights": "lights", "temperature": "temperature", "home": "idle"}[oid], now)
            return []
        if k in ("lights", "temperature"):
            self.since = now
            actions = {"lamp_on": ("device", "lamp", "on"), "lamp_off": ("device", "lamp", "off"),
                       "cooler": ("device", "fan", "on"), "warmer": ("device", "fan", "off")}
            if oid == "back":
                self._go("room", now)
                return []
            return [actions[oid]]
        if k == "nurse":
            n = self.nurse
            if oid == "more":
                n["page"] += 1
                return []
            qid = n["qid"]
            self.nurse = {}
            self.nurse_thanks = True
            return [("nurse_answer", qid, label)]
        return []

    def call_nurse(self, now: float) -> list:
        self.overlay_until = now + config.OVERLAY_S
        return [("call_nurse",)]

    # ----- fades and timeouts ---------------------------------------------
    def tick(self, now: float, busy: bool = False) -> list:
        """Call every frame. busy = the patient is choosing right now (don't time out)."""
        if busy:
            return []
        age = now - self.since
        if self.kind == "suggestion" and age >= config.SUGGESTION_FADE_S - EPS:
            sid = self.suggestion["sid"]
            self._go("idle", now)
            return [("ignored", sid)]
        if self.kind == "checkin" and age >= config.CHECKIN_TIMEOUT_S - EPS:
            qid = self.checkin["qid"]
            self._go("idle", now)
            return [("ignored", qid)]
        if self.kind in ("assistant", "room", "lights", "temperature") and age >= config.ROOM_TIMEOUT_S - EPS:
            self._go("idle", now)
        return []

    # ----- the agent -------------------------------------------------------
    def can_interrupt(self) -> bool:
        """The agent may only show something when the patient is on the idle screen."""
        return self.kind == "idle"

    def show_suggestion(self, sid: str, text: str, now: float, question: Optional[str] = None,
                        options: Optional[list] = None, icon: Optional[str] = None) -> bool:
        """Pending AI notification. Engaging opens a check-in with question/options
        (default: the same text with Yes, please. / No, thanks.)."""
        if self.kind not in ("idle", "suggestion"):
            return False
        self.suggestion = {"sid": sid, "text": text, "question": question or text,
                           "options": _clean_options(options or YES_NO), "icon": icon}
        self._go("suggestion", now)
        return True

    def show_checkin(self, qid: str, question: str, options: Optional[list], now: float,
                     icon: Optional[str] = None) -> bool:
        """Active AI question. 2 answers -> left/right; more -> paged, "Something else" last."""
        if self.kind == "nurse":
            return False
        opts = _clean_options(options or YES_NO)
        if len(opts) > 2 and SOMETHING_ELSE not in opts:
            opts.append(SOMETHING_ELSE)
        if SOMETHING_ELSE in opts:
            opts = [o for o in opts if o != SOMETHING_ELSE] + [SOMETHING_ELSE]
        self.checkin = {"qid": qid, "question": question, "options": opts, "icon": icon, "page": 0}
        self._go("checkin", now)
        return True

    def show_assistant(self, options: list) -> None:
        """The agent's ranked options for the AI ASSISTANT screen."""
        opts = _clean_options(options) or list(ASSISTANT_DEFAULT)
        self.assistant = {"options": opts, "index": 0}

    # ----- the nurse -------------------------------------------------------
    def nurse_start(self, now: float) -> None:
        self.nurse = {}
        self.nurse_thanks = False
        self._go("nurse", now)

    def nurse_question(self, qid: str, question: str, options: list, now: float) -> None:
        if self.kind != "nurse":
            self.nurse_start(now)
        self.nurse = {"qid": qid, "question": question, "options": _clean_options(options), "page": 0}
        self.nurse_thanks = False
        self.since = now

    def nurse_end(self, now: float) -> None:
        self.nurse = None
        self.nurse_thanks = False
        self._go("idle", now)

    def _go(self, kind: str, now: float) -> None:
        self.kind = kind
        self.since = now


def _clean_options(options) -> list:
    out = []
    for o in options or []:
        o = str(o).strip()
        if o and o not in out:
            out.append(o)
    return out


if __name__ == "__main__":
    room = {"lamp_on": False, "temp_c": 24.5}
    sm = ScreenManager(room=lambda: room)
    t = 0.0

    def show(note):
        v = sm.view(t)
        spots = " | ".join(f"{sp}: {v[sp]['label']}" if v[sp] else f"{sp}: -" for sp in config.SPOTS)
        print(f"[{v['screen']:<11}] {v['title']:<45} {spots}" + (f"  page {v['page']}" if v['page'] else "")
              + f"   <- {note}")

    got = []
    show("start")
    for spot, note in [("left", "Room Controls"), ("right", "Adjust the temperature"), ("left", "Cooler"),
                       ("middle", "Back"), ("middle", "Return to main menu"), ("middle", "Talk to AI"),
                       ("right", "More suggestions"), ("middle", "Return to main menu")]:
        t += 2
        ev = sm.select(spot, t)
        got += ev
        show(f"{note} {ev or ''}")
    t += 2
    sm.nurse_question("q1", "What would help you feel more comfortable?",
                      ["Adjust pillows", "Change position", "Water", "Something else"], t)
    show("nurse sends 4 options")
    t += 2
    sm.select("middle", t)
    show("More options")
    t += 2
    got += sm.select("left", t)
    show("patient picks Water")
    sm.nurse_end(t)
    show("nurse ends the session")
    ok = ("device", "fan", "on") in got and ("nurse_answer", "q1", "Water") in got and sm.kind == "idle"
    print("PASS" if ok else "FAIL")
