"""The patient-side AI agent (task C4b, patient side): it speaks first, fixes what it can,
checks its work, respects silence, and explains when it pages the nurse.

WHEN is decided by deterministic triggers (cheap, explainable, demo-safe):
    warm_night   night + awake + room >= night warm threshold + memory "runs warm" + night check-ins on
    warm         awake + room >= memory "feels warm above" (daytime, or anyone who doesn't run warm)
    cold         awake + room <= memory "feels cold below"
    room_change  the room changed (temperature, time of day, a switch by someone else, waking up): Claude
                 decides whether it is worth a suggestion
    habit        it is about the time of a habit memory found (e.g. usual bedtime: lamp off ~22:00) and the
                 switch isn't in that state yet: offer it (from 15 min before to 30 min after)
    bedtime      about the patient's bedtime (a learned habit, else the chart, else 22:00), once a night:
                 Claude checks in for the night in its own words (lamp, fan, anything for the nurse)
    extreme      room >= 30 or <= 16 °C: page the nurse at once (awake or not)
A suggestion shows its 2 concrete choices on the notification ("Turn the fan off" / "Ask nurse for a blanket"),
"Not now" in the center; ignoring it fades it. Ignored / declined topics back off (respecting silence).
    patient      the patient chose an AI ASSISTANT option ("Get more comfortable", "Something else")
WHAT is decided by Claude through tools, inside limits enforced in code:
    get_context  suggest  ask  act  check  page_nurse  defer  remember  finish
    - act() runs only after the patient's "Yes" to that exact device+command (code-enforced)
    - at most MAX_QUESTIONS patient questions per episode; ignored = stop (respect silence)
    - nothing starts while the patient is choosing, in a menu, in a nurse session, or in quiet time
No API key, an error, a refusal or a timeout -> the same episode runs on rules ("(fallback)").
Every step is written to the reasoning feed (live.add_feed) with its "why".

    agent = Agent(live, Memory("demo")); agent.start()      # main.py does this
Self-test without camera or key: py -3.11 -m lookat.agent
"""
import itertools
import json
import os
import re
import queue
import threading
import time
from datetime import timedelta
from typing import Callable, Optional

from pydantic import BaseModel

from lookat import config
from lookat.clock import clock
from lookat.memory import DEMO_CHART, Memory
from lookat.state import log_event

MODEL = "claude-opus-5-5"
CALL_TIMEOUT_S = 15
MAX_TURNS = 8
MAX_QUESTIONS = 3
TICK_S = 1.0
# Pacing. The default is DEMO pacing: the agent reacts within seconds to every change the team makes on the
# panel (temperature, clock), because the demo must show what it can do. LOOKAT_CALM=1 gives the real-life
# pacing (the first value of each pair), which waits much longer before speaking again.
CALM = os.environ.get("LOOKAT_CALM") == "1"
COOLDOWN_MIN = 20 if CALM else 2   # a trigger stays quiet this long after it ran (demo clock minutes)
SETTLE_S = 10 if CALM else 3       # wait this long after the patient's last choice before speaking first
YES_WORDS = ("yes", "ok", "please", "sure")
COMFORT_OPTIONS = ["Adjust pillows", "Change position", "Water", "Too warm", "Too cold", "Something else"]
TEMP_CHANGE_C = 1.0 if CALM else 0.5   # a room change worth a look: the temperature moved this much since Claude last looked
CHANGE_GAP_S = 60 if CALM else 5       # at most one room-change look per minute (demo: every 5 s)
PERIODS = [(7, "morning"), (12, "afternoon"), (17, "evening"), (22, "night")]
MENU_MAX = 3                 # options Claude writes; "Quiet for 2 hours" and "Something else" are always added
QUIET_OPTION = "Quiet for 2 hours"
# Respecting silence (all on the demo clock):
AFTER_EPISODE_MIN = 3 if CALM else 0.25    # after any episode, wait this long before speaking first again
IGNORED_HUSH_MIN = 10 if CALM else 0.5     # a suggestion the patient ignored: nothing new for this long
IGNORED_BACKOFF_MIN = 30 if CALM else 2    # ...and that topic waits 30, then 60, 120, 240 min after each ignore in a row
DECLINED_BACKOFF_MIN = 60 if CALM else 2   # "No, thanks." / Return to main menu: that topic waits an hour
OWN_CHOICE_MIN = 30 if CALM else 2         # don't offer to undo a switch the patient chose themselves this recently...
CLEARLY_C = 1.5              # ...unless the room is now clearly past their threshold (e.g. 18 °C for "cold below 20")
HABIT_BEFORE_MIN, HABIT_AFTER_MIN = 15, 30   # offer a habit from 15 min before its usual time to 30 min after
BEDTIME_DEFAULT = "22:00"    # when neither a learned habit nor the chart says when the patient goes to bed
BEDTIME_BEFORE_MIN, BEDTIME_AFTER_MIN = 15, 60   # the bedtime check-in runs once a night, in this window
EXTREME_HIGH_C = 30.0        # outside these the room is unsafe (or the sensor is wrong): page the nurse at once,
EXTREME_LOW_C = 16.0         # awake or not, screen or not

SYSTEM = """You are LookAt, an assistant for a patient who cannot speak or move and answers only by \
looking left/center/right at a screen and raising their eyebrows. You speak first so they need fewer \
glances, and you speak up for them to the nurse.

How to work:
- Call get_context first. Use what memory says (comfort facts, what they usually pick, last nurse visit) \
so your first question is the right one.
- Ask ONE short question at a time (at most 8 words when you can), with 2 answers. Use more answers only \
for open questions; the screen pages them 2 at a time and always adds "Something else".
- Fix comfort and environment yourself (fan = cooling, lamp). To act, first ask the patient with \
if_yes set to that exact action; act() is refused without that Yes.
- After acting, check() the room, then ask "Better now?". If not better, try one other fix or page the nurse.
- Anything clinical (pain, breathing, distress, feeling unwell) -> page_nurse urgency "urgent" at once. \
Never give medical advice.
- What a nurse must do but can wait (water, pillows, position) -> page_nurse "normal" with a short \
explanation the nurse can act on, e.g. "Wants repositioning. Not temperature-related. Last visit 3h ago." \
Use defer only for things that can wait for the next visit.
- Speak first only when it clearly helps. Every question costs the patient effort; an odd or wrong one \
costs trust. If you are not confident they need something right now, call finish without asking.
- If a question is ignored or dismissed, stop: respect silence. Do not repeat it, and do not bring up a \
topic they ignored or declined recently (RECENT SUGGESTIONS in get_context).
- Never contradict what the patient chose themselves (PATIENT'S OWN CHOICES in get_context): if they turned \
the fan on, do not offer to turn it off unless they are now clearly cold and a while has passed.
- What you can fix: the fan cools the room only 1-2 °C, the lamp gives light. A blanket, water, position, \
pillows, pain or anything else needs the nurse.
- HABIT lines in get_context are habits memory found in the patient's own switches over several days (e.g. \
"Usual bedtime: lamp off around 22:02"). Use them: around that time, offer it ("It's about your usual \
bedtime." -> "Turn the lamp off" / "Not yet"); at other times, let them shape what you offer.
- BEDTIME trigger: it is about the patient's bedtime. Check in for the night with ONE suggestion in your own \
words, built from the room right now and memory (lamp still on -> offer to turn it off; warm -> the fan; or \
what the nurse can do before sleep: water, position).
- A TEMPERATURE change the room just went through is new information: look at it even if you asked about \
temperature before, and say what you noticed in the suggestion. If nothing would help, finish without asking.
- remember() only a durable preference the patient has shown at least twice, on different occasions, or \
chose to tell you. One or two switches are not a preference (the team also tests the room).
- A suggestion shows its 2 options right on the notification ("Not now" is always in the center). \
text: one short plain sentence about what you noticed ("It's getting cold in here."). question: a short \
prompt ("What would help?"). options: exactly 2 concrete choices, 2 to 5 words each, that DO something: \
what you can do yourself ("Turn the fan off", "Turn the lamp on") and/or what the nurse can do ("Ask nurse \
for a blanket", "Ask nurse for water"). Use "Yes, please." / "No, thanks." only when there is one sensible \
fix. Plain words, no jargon; a number only if it helps (say "cold", not "18.2 °C").
- The patient choosing a fan or lamp option IS their approval: act() it, then check(). Choosing a nurse \
option: page_nurse (normal) with a message the nurse can act on ("Feels cold, would like a blanket. Room 18 °C, \
fan already off.").
- If memory holds few facts, you do not know this patient yet: say so in your "why", offer the common needs \
of a bedbound patient (comfort, position, water, temperature, light, rest, company), and remember() what they pick.
- End with finish(): one sentence on what happened. Every tool takes a short "why" for the reasoning panel.
"""

MENU_SYSTEM = """You write the "Talk to AI" menu for a patient who cannot speak or move and picks options by \
looking at a screen. Give exactly 3 options, each 2 to 4 words, written as what the patient would say \
("I'm too warm", "Change my position", "Some water"), the most likely need first, fitting the moment (time of \
day, room, what they picked before). If you know little about the patient, offer common needs of a bedbound \
patient (comfort, position, water, temperature, light, company).
Rules: the screen already has Room Controls (lamp and fan switches) and Call Nurse (long eyebrow raise), so do \
not offer bare switches ("Turn fan on") or "Call the nurse". "Quiet for 2 hours" and "Something else" are \
always added: do not include them. Do not repeat an option the patient just declined. The menu should feel \
steady: keep options from the CURRENT MENU that still fit, in the same order; change only what the moment \
changed. Plain words, no emojis."""


def _tool(name: str, description: str, props: dict, required: list) -> dict:
    props = {**props, "why": {"type": "string", "description": "one short reason, shown to the team"}}
    return {"name": name, "description": description, "strict": True,
            "input_schema": {"type": "object", "properties": props, "required": required + ["why"],
                             "additionalProperties": False}}


ACTION = {"type": ["object", "null"], "properties": {"device": {"type": "string", "enum": ["fan", "lamp"]},
                                                       "cmd": {"type": "string", "enum": ["on", "off"]}},
          "required": ["device", "cmd"], "additionalProperties": False,
          "description": "the room action a Yes approves, or null"}
TOOLS = [
    _tool("get_context", "Time, room, eyes, memory and recent events. Call first.", {}, []),
    _tool("suggest", "Show a gentle AI SUGGESTION notification. If the patient engages, they see the "
          "question with the options. Returns their answer, or 'ignored' / 'dismissed'.",
          {"text": {"type": "string", "description": "the notification, e.g. 'You seem to be awake. Too warm?'"},
           "question": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}},
           "if_yes": ACTION}, ["text", "question", "options", "if_yes"]),
    _tool("ask", "Ask the patient now (AI CHECK-IN). Returns the answer, or 'ignored' / 'dismissed'.",
          {"question": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}},
           "if_yes": ACTION}, ["question", "options", "if_yes"]),
    _tool("act", "Switch the fan (cooling) or the lamp. Only after the patient's Yes to this exact action.",
          {"device": {"type": "string", "enum": ["fan", "lamp"]}, "cmd": {"type": "string", "enum": ["on", "off"]}},
          ["device", "cmd"]),
    _tool("check", "Wait, then re-read the room: temperature before/after, fan and lamp.",
          {"seconds": {"type": "integer", "description": "5 to 60"}}, ["seconds"]),
    _tool("page_nurse", "Send the nurse a page that explains itself.",
          {"message": {"type": "string"}, "reason": {"type": "string"},
           "urgency": {"type": "string", "enum": ["normal", "urgent"]}}, ["message", "reason", "urgency"]),
    _tool("defer", "Save a non-urgent note for the next nurse visit / shift summary.",
          {"note": {"type": "string"}}, ["note"]),
    _tool("remember", "Store a durable fact about the patient.",
          {"fact": {"type": "string"}}, ["fact"]),
    _tool("finish", "End this episode.", {"summary": {"type": "string"}}, ["summary"]),
]


class Menu(BaseModel):
    options: list[str]
    why: str


def action_in(text: str) -> Optional[tuple]:
    """The room action a patient's own choice names, e.g. "Turn fan on" -> ("fan", "on"). Else None."""
    words = set(str(text).lower().replace(",", " ").replace(".", " ").split())
    device = "fan" if words & {"fan", "cooler", "cool"} else "lamp" if words & {"lamp", "light", "lights"} else None
    if device is None:
        return None
    if words & {"off", "warmer", "dim", "dark"}:
        return device, "off"
    if words & {"on", "cooler", "cool", "brighter"}:
        return device, "on"
    return None


def is_yes(answer: str) -> bool:
    return str(answer).strip().lower().startswith(YES_WORDS)


# ----- the patient screen, as the agent sees it -------------------------------------------
class LiveScreens:
    """suggest()/ask() on the real patient screen; blocks until answered, ignored or dismissed."""

    def __init__(self, live, next_event: Callable[[float], Optional[tuple]]):
        self.live, self.next_event = live, next_event
        self._ids = itertools.count(1)

    def suggest(self, text: str, question: str, options: list) -> str:
        sid = f"ag{next(self._ids)}"
        with self.live.lock:
            ok = self.live.sm.show_suggestion(sid, text, self.live.now(), question=question, options=options)
            if ok:
                self.live.machine.reset(self.live.now())
        return self._wait(sid, config.SUGGESTION_FADE_S + config.CHECKIN_TIMEOUT_S + 10) if ok else "busy"

    def ask(self, question: str, options: list) -> str:
        qid = f"ag{next(self._ids)}"
        with self.live.lock:
            ok = self.live.sm.show_checkin(qid, question, options, self.live.now())
            if ok:
                self.live.machine.reset(self.live.now())
        return self._wait(qid, config.CHECKIN_TIMEOUT_S + 10) if ok else "busy"

    def _wait(self, qid: str, limit: float) -> str:
        end = time.monotonic() + limit
        while time.monotonic() < end:
            ev = self.next_event(0.5)
            if ev is None:
                if self.live.sm.kind == "nurse":
                    return "interrupted (the nurse started a conversation)"
                continue
            if ev[0] == "call_nurse":
                return "interrupted (the patient called the nurse)"
            if len(ev) > 1 and ev[1] == qid:
                if ev[0] == "answer":
                    return ev[2]
                if ev[0] in ("dismissed", "ignored"):
                    return ev[0]
        return "ignored"


# ----- the agent ----------------------------------------------------------------------------
class Agent:
    def __init__(self, live, memory: Memory, screens=None, client=None, sleep: Callable = time.sleep):
        self.live, self.memory, self.sleep = live, memory, sleep
        self.events: "queue.Queue[tuple]" = queue.Queue()
        self.screens = screens or LiveScreens(live, self._next_event)
        self.client = client if client is not None else self._make_client()
        self.last_run: dict = {}          # trigger -> clock datetime
        self.last_choice = -1e9           # monotonic time of the patient's last choice
        self.quiet_until = None           # clock datetime
        self.last_change_look = -1e9
        self.seen: Optional[dict] = None   # the room as Claude last looked at it (room-change triggers)
        self.bedtime_night = None          # the night (date it started) the bedtime check-in already ran
        self.menu_sig = None              # the situation the Talk to AI menu was written for
        self.hush_until = 0.0             # clock timestamp: no speaking first before this
        self.backoff: dict = {}           # trigger -> clock timestamp it may run again
        self.ignored_streak: dict = {}    # trigger -> suggestions ignored in a row
        self.answers: list = []           # this episode's answers
        self.choice: Optional[str] = None
        self._stop = False
        self.memory.load_chart(DEMO_CHART)
        live.listeners.append(self.events.put)
        live.demo_week = self.load_demo_week
        live.room.on_action.append(self._log_action)
        self._publish()

    @staticmethod
    def _make_client():
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return None
        try:
            import anthropic
            return anthropic.Anthropic(timeout=CALL_TIMEOUT_S, max_retries=1)
        except Exception:   # noqa: BLE001 - no SDK: rules only
            return None

    def start(self) -> None:
        threading.Thread(target=self.run, daemon=True, name="agent").start()

    def stop(self) -> None:
        self._stop = True

    # ----- loop ------------------------------------------------------------------------------
    def run(self) -> None:
        while not self._stop:
            try:
                self.step()
            except Exception as e:   # noqa: BLE001 - the agent must never take the app down
                log_event("agent_error", error=repr(e))
                self.feed("outcome", "Agent error, resting", repr(e)[:120], fallback=True)
                self.sleep(5)

    def step(self) -> None:
        ev = self._next_event(TICK_S)
        if ev is not None:
            self._on_patient_event(ev)
            return
        if self.client is not None and self.can_speak() and self.situation() != self.menu_sig:
            self.refresh_menu()
        p = self.perceive()
        self.live.agent_status = self.status(p)
        if self.extreme(p):
            return
        trig = self.trigger(p)
        if trig:
            self.episode(*trig)

    def _next_event(self, timeout: float) -> Optional[tuple]:
        try:
            ev = self.events.get(timeout=timeout)
        except queue.Empty:
            return None
        self.last_choice = time.monotonic()
        if ev[0] == "assistant_choice":
            self._log_question("assistant", "How can I help you?", self.live.sm.assistant["options"], ev[1])
        return ev

    def _on_patient_event(self, ev: tuple) -> None:
        if ev[0] != "assistant_choice":
            return
        choice = ev[1]
        self.choice = choice
        if choice.lower().startswith("quiet"):
            self.quiet_until = clock.now().timestamp() + config.QUIET_S
            self.feed("decided", "Quiet for 2 hours: no suggestions until " +
                      time.strftime("%H:%M", time.localtime(self.quiet_until)), "the patient asked for quiet")
        else:   # any option, including the ones Claude wrote: Claude decides what to do with it
            act = action_in(choice)
            note = (f' Choosing it IS their approval to {act[0]} {act[1]}: act() now, no need to ask first.'
                    if act else "")
            self.episode("patient", f'The patient opened Talk to AI and chose "{choice}". Act on it.{note}',
                         approved=act)
        self._publish()

    # ----- perception + triggers ---------------------------------------------------------------
    def perceive(self) -> dict:
        r = self.live.room.state()
        a = getattr(self.live, "awake_state", None) or {}
        return {"time": clock.hhmm(), "night": clock.is_night(), "temp_c": r["temp_c"],
                "setpoint_c": r["setpoint_c"], "fan_on": r["fan_on"], "lamp_on": r["lamp_on"],
                "awake": a.get("awake"), "eyes_open_ratio": a.get("open_ratio"),
                "screen": self.live.sm.kind}

    def can_speak(self) -> bool:
        live = self.live
        quiet = self.quiet_until and clock.now().timestamp() < self.quiet_until
        return (live.calibration is None and live.sm.can_interrupt() and not live.machine.busy
                and not live.session.get("active") and not quiet
                and clock.now().timestamp() >= self.hush_until
                and time.monotonic() - self.last_choice >= SETTLE_S)

    def _backed_off(self, name: str) -> bool:
        """True while the patient's last answer to this topic (ignored / declined) still says: not now."""
        return clock.now().timestamp() < self.backoff.get(name, 0)

    def own_choice(self, device: str, cmd: str) -> Optional[str]:
        """When the patient themselves switched device to cmd within OWN_CHOICE_MIN ("HH:MM"), else None.
        by="agent" counts too: the agent only acts on the patient's Yes."""
        for a in reversed(self.memory.data["actions"]):
            if a.get("device") != device:
                continue
            if a.get("cmd") != cmd or a.get("by") not in ("patient", "agent"):
                return None
            try:
                mins = (clock.now() - clock.now().fromisoformat(a["time"])).total_seconds() / 60
            except (KeyError, ValueError, TypeError):
                return None
            return a["time"][11:16] if 0 <= mins < OWN_CHOICE_MIN else None
        return None

    def _cooled_down(self, name: str) -> bool:
        if self._backed_off(name):
            return False
        last = self.last_run.get(name)
        if last is None:
            return True
        mins = (clock.now() - last).total_seconds() / 60
        return mins < 0 or mins >= COOLDOWN_MIN        # < 0: the demo clock jumped back

    @staticmethod
    def period(hhmm: str) -> str:
        h = int(hhmm[:2])
        name = "night"
        for start, label in PERIODS:
            if h >= start:
                name = label
        return name

    def room_changes(self, p: dict) -> list:
        """What changed in the room since Claude last looked (temperature, time of day, lamp/fan switched by
        someone other than the patient, the patient waking up). Updates the snapshot when it reports."""
        now = {"temp_c": p["temp_c"], "lamp_on": p["lamp_on"], "fan_on": p["fan_on"],
               "period": self.period(p["time"]), "awake": p["awake"]}
        if self.seen is None:
            self.seen = now
            return []
        old, out = self.seen, []
        if abs(now["temp_c"] - old["temp_c"]) >= TEMP_CHANGE_C:
            out.append(f"temperature {old['temp_c']} -> {now['temp_c']} °C")
        if now["period"] != old["period"]:
            out.append(f"it is now {now['period']} ({p['time']})")
        last_by = (self.memory.data["actions"][-1].get("by") if self.memory.data["actions"] else None)
        for dev in ("lamp", "fan"):
            if now[f"{dev}_on"] != old[f"{dev}_on"] and last_by not in ("patient", "agent"):
                out.append(f"{dev} turned {'on' if now[dev + '_on'] else 'off'}" + (f" (by {last_by})" if last_by else ""))
        if now["awake"] and not old["awake"]:
            out.append("the patient just opened their eyes")
        if out or now["lamp_on"] != old["lamp_on"] or now["fan_on"] != old["fan_on"]:
            self.seen = now                         # the patient's own switches update quietly
        return out

    def bedtime(self, L: dict) -> tuple:
        """("HH:MM", where it comes from): a learned bedtime habit, else the chart, else BEDTIME_DEFAULT."""
        for h in L["habits"]:
            if h.get("bedtime"):
                return h["usual"], f"learned: {h['text']}"
        for f in reversed(self.memory.data["facts"]):
            bt = (f.get("data") or {}).get("bedtime")
            if bt:
                return str(bt), f"{f.get('source', 'memory')}: {f['text']}"
        return BEDTIME_DEFAULT, "default"

    def bedtime_due(self, p: dict, L: dict) -> Optional[tuple]:
        """("bedtime", reason) once a night, from BEDTIME_BEFORE_MIN before the bedtime to BEDTIME_AFTER_MIN after."""
        from lookat.memory import _diff, _mins
        if any(h.get("bedtime") for h in L["habits"]):
            return None              # a learned bedtime habit: habit_due offers it (the "One week later" story)
        bt, source = self.bedtime(L)
        d = _diff(_mins(p["time"]), _mins(bt))
        if not -BEDTIME_BEFORE_MIN <= d <= BEDTIME_AFTER_MIN:
            return None
        night = (clock.now() - timedelta(hours=12)).date()    # 21:50 and 00:30 belong to the same night
        if self.bedtime_night == night or self._backed_off("bedtime"):
            return None
        self.bedtime_night = night
        return "bedtime", (f"BEDTIME: it is {p['time']}, about the patient's usual bedtime {bt} ({source}). "
                           f"Room {p['temp_c']} °C, lamp {'on' if p['lamp_on'] else 'off'}, "
                           f"fan {'on' if p['fan_on'] else 'off'}. Check in for the night now with one suggestion "
                           "in your own words: 2 options that would help them settle, from the room and memory "
                           "('Not now' is always added). If they choose a switch, act() it, then finish.")

    def habit_due(self, p: dict, L: dict) -> Optional[tuple]:
        """("habit", reason) when it is about the time of a habit and its switch isn't in that state yet."""
        from lookat.memory import _diff, _mins
        now = _mins(p["time"])
        for h in L["habits"]:
            d = _diff(now, _mins(h["usual"]))
            if not -HABIT_BEFORE_MIN <= d <= HABIT_AFTER_MIN or p[f"{h['device']}_on"] == (h["cmd"] == "on"):
                continue
            if not self._cooled_down("habit") or (p["night"] and not L["night_checkins_enabled"]):
                return None
            self.habit = h
            return "habit", (f"Habit from memory: {h['text']} (on {h['days']} days: {', '.join(h['times'])}). "
                             f"It is {p['time']} and the {h['device']} is still {'on' if h['cmd'] == 'off' else 'off'}. "
                             f"Offer it now in one suggestion, e.g. options \"Turn the {h['device']} {h['cmd']}\" / "
                             "\"Not yet\". If they choose it, act() then finish (no need to check the room).")
        return None

    def load_demo_week(self) -> str:
        """Demo "One week later": a labelled week of bedtimes, so memory finds the habit (the real code runs on it)."""
        n = self.memory.load_demo_week()
        habits = self.memory.habits()
        self._publish()
        self.feed("learned", "One week later: " + ("; ".join(h["text"] for h in habits) if habits else "no habit yet"),
                  f"demo week loaded ({n} labelled actions)" if n else "demo week was already loaded", fallback=False)
        return "ok"

    def status(self, p: dict) -> str:
        """Why the agent is quiet right now, or what it is doing (one line for the panel)."""
        live = self.live
        if self.client is None:
            note = " (rules only: no API key)"
        else:
            note = ""
        if live.calibration is not None:
            return "Quiet: calibrating" + note
        if live.session.get("active"):
            return "Quiet: the nurse is talking with the patient" + note
        if self.quiet_until and clock.now().timestamp() < self.quiet_until:
            return "Quiet: the patient asked for quiet until " + time.strftime("%H:%M", time.localtime(self.quiet_until)) + note
        if not live.sm.can_interrupt() or live.machine.busy:
            return "Waiting: the patient is using the screen" + note
        if p["awake"] is None:
            return "Quiet: the patient's face is not seen (is the patient tab visible?)" + note
        if not p["awake"]:
            return "Quiet: the patient seems asleep" + note
        if clock.now().timestamp() < self.hush_until:
            return "Giving the patient a break until " + time.strftime("%H:%M", time.localtime(self.hush_until)) + note
        if time.monotonic() - self.last_choice < SETTLE_S:
            return "Waiting a moment after the patient's last choice" + note
        held = [k for k in self.backoff if self._backed_off(k)]
        return "Watching the room" + (f" (on hold: {', '.join(held)})" if held else "") + note

    def extreme(self, p: dict) -> bool:
        """Room far too hot or cold: page the nurse at once (code, no AI, awake or not). True = it paged."""
        t = p["temp_c"]
        if EXTREME_LOW_C < t < EXTREME_HIGH_C or not self._cooled_down("extreme"):
            return False
        self.last_run["extreme"] = clock.now()
        self.fallback = False
        hot = t >= EXTREME_HIGH_C
        self.feed("noticed", f"Room reads {t} °C: far too {'hot' if hot else 'cold'}", "trigger: extreme")
        self.call("page_nurse", {"message": f"Room reads {t} °C. Please check the room (or the sensor).",
                                 "reason": f"Outside the safe range {EXTREME_LOW_C:g}-{EXTREME_HIGH_C:g} °C; "
                                           "paged at once, whether the patient is awake or not.",
                                 "urgency": "urgent", "why": "unsafe temperature"})
        return True

    def trigger(self, p: dict) -> Optional[tuple]:
        """(name, reason) when it is time to speak first, else None. Pure rules, no AI."""
        if not self.can_speak() or not p["awake"]:
            if self.seen is not None and not p["awake"]:
                self.seen["awake"] = p["awake"]      # so waking up later counts as a change
            return None
        L = self.memory.learn()
        comfort = L["comfort"]
        warm = L["warm_night_above_c"] if p["night"] else L["warm_above_c"]
        if (p["temp_c"] >= warm and (L["night_checkins_enabled"] or not p["night"])
                and not (self.own_choice("fan", "off") and p["temp_c"] < warm + CLEARLY_C)):
            if p["night"] and comfort.get("runs_warm") and self._cooled_down("warm_night"):
                return "warm_night", (f"Night ({p['time']}), eyes open {int((p['eyes_open_ratio'] or 1) * 100)}%, "
                                      f"room {p['temp_c']} °C >= {warm} °C, memory: runs warm at night. "
                                      "Make one suggestion now.")
            if not (p["night"] and comfort.get("runs_warm")) and self._cooled_down("warm"):
                return "warm", (f"Room {p['temp_c']} °C >= {warm} °C, memory: feels warm above {warm} °C. "
                                "Make one suggestion now.")
        cold = comfort.get("cold_below_c")
        if (cold is not None and p["temp_c"] <= cold and self._cooled_down("cold")
                and not (self.own_choice("fan", "on") and p["temp_c"] > cold - CLEARLY_C)):
            own = self.own_choice("fan", "on")
            return "cold", (f"Room {p['temp_c']} °C <= {cold} °C, memory: feels cold below {cold} °C"
                            + (f"; they turned the fan on themselves at {own}, so ask kindly, don't assume" if own else "")
                            + ". Make one suggestion now.")
        habit = self.habit_due(p, L) or self.bedtime_due(p, L)
        if habit:
            return habit
        changes = self.room_changes(p)
        temp_moved = any(c.startswith("temperature") for c in changes)   # new information: look even if on hold
        if changes and self.client is not None and time.monotonic() - self.last_change_look >= CHANGE_GAP_S \
                and (temp_moved or not self._backed_off("room_change")) \
                and (L["night_checkins_enabled"] or not p["night"]):
            self.last_change_look = time.monotonic()
            return "room_change", ("The room changed: " + "; ".join(changes) + ". If that is worth offering the "
                                   "patient something right now, make ONE suggestion (a short question); otherwise "
                                   "call finish without asking. Don't repeat recent questions.")
        return None

    # ----- one episode -------------------------------------------------------------------------
    def episode(self, name: str, reason: str, approved: Optional[tuple] = None) -> None:
        """approved = a room action the patient already chose themselves (e.g. "Turn fan on" in the menu)."""
        self.last_run[name] = clock.now()
        self.approved: Optional[tuple] = approved
        self.questions = 0
        self.answers = []
        self.night = clock.is_night()
        self.trigger_name = name
        log_event("agent_episode", trigger=name, reason=reason)
        self.feed("noticed", reason, f"trigger: {name}")
        done = False
        if self.client is not None:
            try:
                done = self._claude(reason)
            except Exception as e:   # noqa: BLE001 - any AI failure -> rules
                log_event("agent_ai_error", error=repr(e))
                self.feed("decided", "AI unavailable, using rules", type(e).__name__, fallback=True)
        if not done and self.questions == 0 and name != "room_change":
            self._rules(name)
        self._respect_silence(name)
        if self.client is not None and self.situation() != self.menu_sig:
            self.refresh_menu()
        self.seen = None                 # the room as it is now becomes the new baseline (not a "change")
        self.room_changes(self.perceive())
        self._publish()

    def _claude(self, reason: str) -> bool:
        """The manual tool-use loop. True = the episode finished (or reached the patient)."""
        self.fallback = False
        messages = [{"role": "user", "content": f"Trigger: {reason}\nDecide what to do."}]
        for _ in range(MAX_TURNS):
            resp = self.client.beta.messages.create(
                model=MODEL, max_tokens=4096, system=SYSTEM, tools=TOOLS, messages=messages,
                output_config={"effort": "low"},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default")
            if resp.stop_reason == "refusal":
                raise RuntimeError("refusal")
            messages.append({"role": "assistant", "content": resp.content})
            calls = [b for b in resp.content if b.type == "tool_use"]
            if not calls:
                return True
            results, finished = [], False
            for b in calls:
                try:
                    out = self.call(b.name, dict(b.input))
                except Exception as e:   # noqa: BLE001 - report the tool error to Claude
                    out = f"Error: {e}"
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": str(out)})
                finished = finished or b.name == "finish"
            messages.append({"role": "user", "content": results})
            if finished:
                return True
        self.feed("outcome", "Stopped after the turn limit", f"{MAX_TURNS} turns")
        return True

    # ----- rules (no AI): the same tools, a fixed script ---------------------------------------
    def _rules(self, name: str) -> None:
        self.fallback = True
        c = self.call
        if name in ("warm_night", "warm"):
            if not self.live.room.state()["fan_on"]:
                ans = c("suggest", {"text": "You seem to be awake. Too warm?" if name == "warm_night"
                                    else "It's getting warm in here.", "question": "Cool the room down?",
                                    "options": ["Turn the fan on", "No, thanks."], "if_yes": None,
                                    "why": "warm room, above the patient's threshold; the fan can fix it"})
                if action_in(ans) == ("fan", "on"):
                    self._fix_and_check({"device": "fan", "cmd": "on"}, "Still warm after the fan")
                    return
            else:
                ans = c("suggest", {"text": "Still warm in here.", "question": "The fan is already on.",
                                    "options": ["Ask nurse for help", "No, thanks."], "if_yes": None,
                                    "why": "warm with the fan running: only a nurse can do more"})
                if ans == "Ask nurse for help":
                    c("page_nurse", {"message": "Feels warm with the fan already on.",
                                     "reason": f"Room {self.live.room.state()['temp_c']} °C, above their warm threshold.",
                                     "urgency": "normal", "why": "nothing more I can switch"})
            c("finish", {"summary": f"Warm check-in: {ans}", "why": "respect the answer"})
        elif name == "habit":
            h = getattr(self, "habit", None) or {}
            dev, cmd = h.get("device", "lamp"), h.get("cmd", "off")
            ans = c("suggest", {"text": "It's about your usual bedtime." if h.get("bedtime")
                                else f"You usually turn the {dev} {cmd} about now.",
                                "question": f"Turn the {dev} {cmd}?", "options": [f"Turn the {dev} {cmd}", "Not yet"],
                                "if_yes": None, "why": h.get("text", "habit from memory")})
            if action_in(ans) == (dev, cmd):
                c("act", {"device": dev, "cmd": cmd, "why": "the patient chose it"})
            c("finish", {"summary": f"Habit ({h.get('text', '')}): {ans}", "why": "done"})
        elif name == "bedtime":
            lamp = self.live.room.state()["lamp_on"]
            first = "Turn the lamp off" if lamp else "Ask nurse to settle me"
            ans = c("suggest", {"text": "It's about your bedtime.", "question": "Ready for the night?",
                                "options": [first, "Not yet"], "if_yes": None,
                                "why": "usual bedtime" + (", lamp still on" if lamp else "")})
            if action_in(ans) == ("lamp", "off"):
                c("act", {"device": "lamp", "cmd": "off", "why": "the patient chose it"})
            elif ans == "Ask nurse to settle me":
                c("page_nurse", {"message": "Ready for the night; would like to be settled.",
                                 "reason": "Chosen at the bedtime check-in.", "urgency": "normal",
                                 "why": "needs a person"})
            c("finish", {"summary": f"Bedtime check-in: {ans}", "why": "done"})
        elif name == "cold":
            fan = self.live.room.state()["fan_on"]
            opts = ["Turn the fan off", "Ask nurse for a blanket"] if fan else ["Ask nurse for a blanket", "No, thanks."]
            ans = c("suggest", {"text": "It's getting cold in here.", "question": "What would help?",
                                "options": opts, "if_yes": None,
                                "why": "room below the patient's cold threshold" + (", fan running" if fan else ", fan off")})
            if action_in(ans) == ("fan", "off"):
                self._fix_and_check({"device": "fan", "cmd": "off"}, "Still cold with the fan off")
                return
            if ans == "Ask nurse for a blanket":
                c("page_nurse", {"message": "Feels cold, would like a blanket.",
                                 "reason": f"Room {self.live.room.state()['temp_c']} °C, below their cold threshold"
                                           + ("." if fan else "; the fan is already off."),
                                 "urgency": "normal", "why": "only a nurse can bring a blanket"})
            c("finish", {"summary": f"Cold check-in: {ans}", "why": "done"})
        elif self.approved:                                       # the patient chose a switch in the menu
            self._fix_and_check({"device": self.approved[0], "cmd": self.approved[1]}, "Still not comfortable")
        elif self.choice and self.choice not in ("Get more comfortable",):   # a menu choice, no AI
            visit = self.memory.learn()["last_nurse_visit"]
            what = "something not on the list" if self.choice == "Something else" else self.choice.lower()
            c("page_nurse", {"message": f"Patient asks: {what}.",
                             "reason": f"Chosen in Talk to AI. Last visit {visit[11:16] if visit else 'unknown'}.",
                             "urgency": "normal", "why": "needs a person"})
            c("finish", {"summary": f"Patient request: {self.choice}", "why": "done"})
        else:   # patient-initiated "Get more comfortable"
            opts = self.memory.ranked("comfort", COMFORT_OPTIONS[:-1]) + ["Something else"]
            ans = c("ask", {"question": "What would help most?", "options": opts, "if_yes": None,
                            "why": "the patient asked for help; options in the order they usually pick"})
            if ans == "Too warm":
                ans2 = c("ask", {"question": "Turn the fan on?", "options": ["Yes, please.", "No, thanks."],
                                 "if_yes": {"device": "fan", "cmd": "on"}, "why": "fixable without a nurse"})
                if is_yes(ans2):
                    self._fix_and_check({"device": "fan", "cmd": "on"}, "Still warm after the fan")
                    return
            elif ans in ("ignored", "dismissed", "busy") or ans.startswith("interrupted"):
                pass
            else:
                visit = self.memory.learn()["last_nurse_visit"]
                what = "something not on the list" if ans == "Something else" else ans.lower()
                c("page_nurse", {"message": f"Patient asks: {what}.",
                                 "reason": ("Not temperature-related. " if ans != "Too cold" else "") +
                                           f"Last visit {visit[11:16] if visit else 'unknown'}.",
                                 "urgency": "normal", "why": "needs a person"})
            c("finish", {"summary": f"Patient request: {ans}", "why": "done"})

    def _fix_and_check(self, action: dict, still_text: str) -> None:
        c = self.call
        c("act", {**action, "why": "the patient said yes"})
        c("check", {"seconds": 20, "why": "check my own fix"})
        ans = c("ask", {"question": "Better now?", "options": ["Yes", "No"], "if_yes": None, "why": "did it work?"})
        if ans == "No":
            t = self.live.room.state()["temp_c"]
            c("page_nurse", {"message": f"{still_text}. Room {t} °C.", "reason": "My fix did not help; needs a person.",
                             "urgency": "normal", "why": "not better after the fix"})
            c("finish", {"summary": "Not better after the fix; nurse paged", "why": "escalated"})
        else:
            c("finish", {"summary": f"Fixed ({action['device']} {action['cmd']}); no nurse needed" if ans == "Yes"
                         else f"Better now? -> {ans}", "why": "checked"})

    # ----- respecting silence ---------------------------------------------------------------------
    def _respect_silence(self, name: str) -> None:
        """After an episode the agent speaking first was ignored or declined, it backs off (code, not AI)."""
        if name == "patient" or not self.answers:  # the patient asked, or nothing reached the screen: no break
            return
        now = clock.now().timestamp()
        self.hush_until = max(self.hush_until, now + AFTER_EPISODE_MIN * 60)
        if "ignored" in self.answers:
            n = self.ignored_streak.get(name, 0) + 1
            self.ignored_streak[name] = n
            mins = min(IGNORED_BACKOFF_MIN * 2 ** (n - 1), 240)
            self.backoff[name] = now + mins * 60
            self.hush_until = max(self.hush_until, now + IGNORED_HUSH_MIN * 60)
            self.feed("learned", f"Ignored: no '{name}' suggestions for {mins} min", "respect silence")
            return
        self.ignored_streak[name] = 0
        declined = [a for a in self.answers                 # "No, thanks." / "No" / back out; not "Not too warm"
                    if a == "dismissed" or (isinstance(a, str) and re.match(r"no\b", a.strip().lower()))]
        if declined:
            self.backoff[name] = now + DECLINED_BACKOFF_MIN * 60
            self.feed("learned", f"Declined: no '{name}' suggestions for {DECLINED_BACKOFF_MIN} min", "respect the answer")

    def situation(self) -> tuple:
        """What the Talk to AI menu depends on; it is rewritten only when this changes."""
        p = self.perceive()
        return (self.period(p["time"]), int(p["temp_c"] // 2), p["fan_on"], p["lamp_on"], bool(p["awake"]),
                len(self.memory.data["facts"]))

    def _agent_context(self) -> str:
        """Extra lines for get_context: what was offered lately and what the patient chose themselves."""
        now = clock.now()
        lines = []

        def ago(iso):
            try:
                return f"{int((now - now.fromisoformat(iso)).total_seconds() // 60)} min ago"
            except (ValueError, TypeError):
                return "?"
        sug = self.memory.data["suggestions"][-5:]
        if sug:
            lines.append("RECENT SUGGESTIONS: " + "; ".join(f"'{s.get('text')}' -> {s.get('outcome')} ({ago(s.get('time'))})"
                                                             for s in sug))
        own = [a for a in self.memory.data["actions"] if a.get("by") in ("patient", "agent")][-4:]
        if own:
            lines.append("PATIENT'S OWN CHOICES: " + "; ".join(f"{a.get('device')} {a.get('cmd')} ({ago(a.get('time'))})"
                                                                for a in own))
        quiet = [k for k in self.backoff if self._backed_off(k)]
        if quiet:
            lines.append("TOPICS ON HOLD (ignored or declined): " + ", ".join(quiet))
        return "\n".join(lines)

    # ----- the tools (the only way the agent changes anything) ----------------------------------
    def call(self, name: str, a: dict) -> str:
        why = a.get("why", "")
        if name == "get_context":
            out = self.memory.context_text(self.perceive())
            extra = self._agent_context()
            out = out + ("\n" + extra if extra else "")
            self.feed("noticed", "Read memory + room", why)
            return out
        if name in ("suggest", "ask"):
            if self.questions >= MAX_QUESTIONS:
                return "REFUSED: question limit reached; finish now"
            self.questions += 1
            opts = [str(o) for o in a.get("options") or []][:6] or ["Yes, please.", "No, thanks."]
            if name == "suggest":
                self.feed("decided", f'Suggest: "{a["text"]}"', why)
                ans = self.screens.suggest(a["text"], a["question"], opts)
                self.memory.log("suggestions", {"text": a["text"], "outcome": ans})
                q = a["question"]
            else:
                self.feed("decided", f'Ask: "{a["question"]}" {opts}', why)
                ans = self.screens.ask(a["question"], opts)
                q = a["question"]
            self.answers.append(ans)
            self._log_question(self.trigger_name, q, opts, ans if ans in opts else None)
            if self.trigger_name == "warm_night" or (self.night and name == "suggest"):
                self.memory.log("checkins", {"night": self.night, "outcome": "ignored" if ans == "ignored" else "answered"})
                self._silence_check()
            act = a.get("if_yes")       # "Yes" to if_yes, or choosing an option that names the switch itself
            self.approved = ((act["device"], act["cmd"]) if act and is_yes(ans)
                             else action_in(ans) if ans in opts else None)
            self.feed("checked", f"Patient: {ans}", "their answer" + (" approves " + " ".join(self.approved)
                                                                       if self.approved else ""))
            return ans
        if name == "act":
            want = (a["device"], a["cmd"])
            if self.approved != want:
                self.feed("decided", f"Refused: {a['device']} {a['cmd']} without the patient's Yes", "safety rule")
                log_event("agent_refused_act", device=a["device"], cmd=a["cmd"])
                return "REFUSED: the patient has not said Yes to this exact action. Ask first with if_yes."
            self.approved = None
            res = self.live.room.execute(a["device"], a["cmd"], by="agent")
            self.feed("did", res, why)
            return res
        if name == "check":
            secs = max(5, min(60, int(a.get("seconds") or 20)))
            before = self.live.room.state()
            self.sleep(secs)
            after = self.live.room.state()
            res = (f"After {secs} s: room {before['temp_c']} -> {after['temp_c']} °C, "
                   f"fan {'on' if after['fan_on'] else 'off'}, lamp {'on' if after['lamp_on'] else 'off'}")
            self.feed("checked", res, why)
            return res
        if name == "page_nurse":
            urgent = a.get("urgency") == "urgent"
            self.live.add_alert("page", a["message"], urgency="urgent" if urgent else "normal", reason=a.get("reason"),
                                actions=[{"id": "ack", "label": "On my way"}])
            self.memory.log("pages", {"message": a["message"], "reason": a.get("reason"), "urgency": a.get("urgency")})
            self.feed("outcome", f"Paged the nurse: {a['message']}", a.get("reason") or why)
            return "paged"
        if name == "defer":
            with self.live.lock:
                self.live.next_visit.append(a["note"])
            self.memory.log("deferred", {"note": a["note"]})
            self.feed("outcome", f"For the next visit: {a['note']}", why)
            return "saved for the next visit"
        if name == "remember":
            known = {f["text"].strip().lower() for f in self.memory.data["facts"]}
            if a["fact"].strip().lower() in known:
                return "already known"
            self.memory.add_fact(a["fact"], "preference", "agent")
            self.feed("learned", f"Remembered: {a['fact']}", why)
            return "remembered"
        if name == "finish":
            self.feed("outcome", a.get("summary", "done"), why)
            return "finished"
        return f"Error: unknown tool {name}"

    # ----- the Talk to AI menu, written by Claude ------------------------------------------------
    def refresh_menu(self) -> None:
        """Claude writes the menu options for this moment; the screen shows them when the patient opens it.
        Any failure keeps the current menu (the default list until Claude has written one)."""
        self.menu_sig = self.situation()
        current = [o for o in self.live.sm.assistant["options"] if o not in (QUIET_OPTION, "Something else")]
        try:
            resp = self.client.messages.parse(
                model=MODEL, max_tokens=2048, system=MENU_SYSTEM, output_format=Menu,
                messages=[{"role": "user", "content": self.memory.context_text(self.perceive())
                           + "\nCURRENT MENU: " + " · ".join(current)}])
            menu = resp.parsed_output
        except Exception as e:   # noqa: BLE001 - keep the current menu
            log_event("agent_menu_error", error=repr(e))
            return
        opts = []
        for o in menu.options:
            o = " ".join(str(o).split()[:5]).strip(" .")
            if o and o.lower() not in ("something else", QUIET_OPTION.lower()) and o not in opts:
                opts.append(o)
        if len(opts) < 2:
            return
        opts = opts[:MENU_MAX] + [QUIET_OPTION, "Something else"]
        with self.live.lock:
            self.live.sm.show_assistant(opts)
        self.feed("decided", "Talk to AI menu: " + " · ".join(opts), menu.why, fallback=False)

    # ----- learning + reporting ------------------------------------------------------------------
    def _log_question(self, kind: str, question: str, options: list, answer: Optional[str]) -> None:
        self.memory.log("questions", {"kind": kind, "question": question, "options": list(options), "answer": answer})
        if kind == "assistant":
            ranked = self.memory.ranked("assistant", list(options))
            with self.live.lock:
                self.live.sm.show_assistant(ranked)

    def _silence_check(self) -> None:
        L = self.memory.learn()
        if not L["night_checkins_enabled"] and L["night_ignored_streak"] == 3:
            self.feed("learned", "Stopping night check-ins (ignored 3×)", "respect silence")

    def _log_action(self, device: str, cmd: str, by: str, result: str) -> None:
        self.memory.log("actions", {"device": device, "cmd": cmd, "by": by, "result": result})

    def feed(self, stage: str, text: str, why: Optional[str] = None, fallback: Optional[bool] = None) -> None:
        fb = getattr(self, "fallback", False) if fallback is None else fallback
        self.live.add_feed(stage, text, why, fallback=fb)
        log_event("agent_step", stage=stage, text=text, why=why, fallback=fb)

    def _publish(self) -> None:
        """What /api/panel shows under Memory."""
        L = self.memory.learn()
        with self.live.lock:
            self.live.facts = [{"text": f["text"], "source": f["source"]} for f in self.memory.data["facts"]]
            self.live.memory_view = {
                "facts": self.live.facts,
                "routines": [{"text": h["text"], "status": "habit", "evidence": ", ".join(h["times"])}
                             for h in L["habits"]],
                "top_options": L["option_ranking"].get("comfort", [])[:3],
                "hidden_options": L["hidden_options"].get("comfort", []),
                "night_checkins": L["night_checkins_enabled"]}
            self.live.quiet_until = (time.strftime("%H:%M", time.localtime(self.quiet_until))
                                     if self.quiet_until else None)


# ----- self-test: no camera, no key, a scripted patient -------------------------------------------
if __name__ == "__main__":
    import tempfile

    from lookat.devices import Room, SimPlug
    from lookat.live import Live

    class ScriptedScreens:
        def __init__(self, answers):
            self.answers = list(answers)

        def suggest(self, text, question, options):
            ans = self.answers.pop(0)
            print(f"   SCREEN suggestion '{text}' -> '{question}' {options} -> patient: {ans}")
            return ans

        def ask(self, question, options):
            ans = self.answers.pop(0)
            print(f"   SCREEN check-in '{question}' {options} -> patient: {ans}")
            return ans

    def fast_sleep(room):
        def sleep(s):
            for _ in range(int(s)):
                room.tick(time.monotonic() + _)
        return sleep

    ok = True
    with tempfile.TemporaryDirectory() as d:
        room = Room(plug_factory=SimPlug)
        live = Live(room, patient="selftest")
        live.awake_state = {"awake": True, "open_ratio": 0.92}
        room.set_temp(24.5)
        clock.set_time("02:00")
        agent = Agent(live, Memory("selftest", d), screens=ScriptedScreens(["Turn the fan on", "Yes"]),
                      client=None, sleep=lambda s: [room.set_temp(room.state()["temp_c"] - 0.08 * s), None][1])
        agent.last_choice = -1e9
        trig = agent.trigger(agent.perceive())
        print("trigger:", trig)
        agent.episode(*trig)
        for f in live.feed:
            print(f"   [{f['stage']:<8}] {f['text']}  -- {f['why']}{' (fallback)' if f['fallback'] else ''}")
        ok &= room.state()["fan_on"] and any("no nurse needed" in f["text"] for f in live.feed)
        print("act without a Yes:", agent.call("act", {"device": "lamp", "cmd": "on", "why": "test"})[:40])
        ok &= not room.state()["lamp_on"]
        clock.reset()
    print("PASS" if ok else "FAIL")
