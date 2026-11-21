"""The patient-side AI agent (task C4b, patient side): it speaks first, fixes what it can,
checks its work, respects silence, and explains when it pages the nurse.

WHEN is decided by deterministic triggers (cheap, explainable, demo-safe):
    warm_night   night + awake + room >= warm threshold + memory "runs warm" + night check-ins on
    cold         awake + room <= memory "feels cold below"
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
import queue
import threading
import time
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
COOLDOWN_MIN = 20            # a trigger stays quiet this long after it ran (demo clock minutes)
SETTLE_S = 10                # wait this long after the patient's last choice before speaking first
YES_WORDS = ("yes", "ok", "please", "sure")
COMFORT_OPTIONS = ["Adjust pillows", "Change position", "Water", "Too warm", "Too cold", "Something else"]
TEMP_CHANGE_C = 1.0          # a room change worth a look: the temperature moved this much since Claude last looked
CHANGE_GAP_S = 60            # at most one room-change look per minute
PERIODS = [(7, "morning"), (12, "afternoon"), (17, "evening"), (22, "night")]
MENU_EVERY_S = 600           # the Talk to AI menu is rewritten by Claude this often (and after each episode)
MENU_MAX = 4                 # options Claude writes; "Something else" is always added last

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
- If a question is ignored or dismissed, stop: respect silence. Do not repeat it.
- remember() durable preferences you learn (e.g. "Prefers the fan at night").
- Write every suggestion text as a short question the patient can answer with their eyes, e.g. \
"Feeling cold? Turn the fan off?" - never a bare statement.
- If memory holds few facts, you do not know this patient yet: say so in your "why", offer the common needs \
of a bedbound patient (comfort, position, water, temperature, light, rest, company), and remember() what they pick.
- End with finish(): one sentence on what happened. Every tool takes a short "why" for the reasoning panel.
"""

MENU_SYSTEM = """You write the "Talk to AI" menu for a patient who cannot speak or move and picks options by \
looking at a screen. Give 3 or 4 options, each 2 to 4 words, the most likely need first, that fit the moment \
(time of day, room, what they picked before, what was just offered). If you know little about the patient, \
offer common needs of a bedbound patient. Do not include "Something else" (it is always added). Do not repeat \
an option the patient just declined. Plain words, no emojis."""


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
        self.last_menu = -1e9
        self.choice: Optional[str] = None
        self._stop = False
        self.memory.load_chart(DEMO_CHART)
        live.listeners.append(self.events.put)
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
        if self.client is not None and time.monotonic() - self.last_menu >= MENU_EVERY_S and self.can_speak():
            self.refresh_menu()
        trig = self.trigger(self.perceive())
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
                and time.monotonic() - self.last_choice >= SETTLE_S)

    def _cooled_down(self, name: str) -> bool:
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

    def trigger(self, p: dict) -> Optional[tuple]:
        """(name, reason) when it is time to speak first, else None. Pure rules, no AI."""
        if not self.can_speak() or not p["awake"]:
            if self.seen is not None and not p["awake"]:
                self.seen["awake"] = p["awake"]      # so waking up later counts as a change
            return None
        L = self.memory.learn()
        comfort = L["comfort"]
        if (p["night"] and comfort.get("runs_warm") and p["temp_c"] >= L["warm_above_c"]
                and L["night_checkins_enabled"] and self._cooled_down("warm_night")):
            return "warm_night", (f"Night ({p['time']}), eyes open {int((p['eyes_open_ratio'] or 1) * 100)}%, "
                                  f"room {p['temp_c']} °C >= {L['warm_above_c']} °C, memory: runs warm at night")
        cold = comfort.get("cold_below_c")
        if cold is not None and p["temp_c"] <= cold and self._cooled_down("cold"):
            return "cold", f"Room {p['temp_c']} °C <= {cold} °C, memory: feels cold below {cold} °C"
        changes = self.room_changes(p)
        if changes and self.client is not None and time.monotonic() - self.last_change_look >= CHANGE_GAP_S \
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
        if self.client is not None:
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
        if name == "warm_night":
            ans = c("suggest", {"text": "You seem to be awake. Too warm?", "question": "Turn the fan on to cool the room?",
                                "options": ["Yes, please.", "No, thanks."], "if_yes": {"device": "fan", "cmd": "on"},
                                "why": "night + awake + warm room + runs warm"})
            if is_yes(ans):
                self._fix_and_check({"device": "fan", "cmd": "on"}, "Still warm after the fan")
            else:
                c("finish", {"summary": f"Warm-night check-in: {ans}", "why": "respect the answer"})
        elif name == "cold":
            fan = self.live.room.state()["fan_on"]
            if fan:
                ans = c("suggest", {"text": "Feeling cold?", "question": "Turn the fan off?",
                                    "options": ["Yes, please.", "No, thanks."], "if_yes": {"device": "fan", "cmd": "off"},
                                    "why": "room below the patient's cold threshold, fan running"})
                if is_yes(ans):
                    self._fix_and_check({"device": "fan", "cmd": "off"}, "Still cold with the fan off")
                    return
            else:
                ans = c("suggest", {"text": "Feeling cold?", "question": "Ask your nurse for a blanket?",
                                    "options": ["Yes, please.", "No, thanks."], "if_yes": None,
                                    "why": "room below the patient's cold threshold, nothing to switch"})
                if is_yes(ans):
                    c("page_nurse", {"message": "Feels cold, would like a blanket.",
                                     "reason": f"Room {self.live.room.state()['temp_c']} °C, below their cold threshold.",
                                     "urgency": "normal", "why": "only a nurse can fix this"})
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

    # ----- the tools (the only way the agent changes anything) ----------------------------------
    def call(self, name: str, a: dict) -> str:
        why = a.get("why", "")
        if name == "get_context":
            out = self.memory.context_text(self.perceive())
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
            self._log_question(self.trigger_name, q, opts, ans if ans in opts else None)
            if self.trigger_name == "warm_night" or (self.night and name == "suggest"):
                self.memory.log("checkins", {"night": self.night, "outcome": "ignored" if ans == "ignored" else "answered"})
                self._silence_check()
            act = a.get("if_yes")
            self.approved = (act["device"], act["cmd"]) if act and is_yes(ans) else None
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
        self.last_menu = time.monotonic()
        try:
            resp = self.client.messages.parse(
                model=MODEL, max_tokens=2048, system=MENU_SYSTEM, output_format=Menu,
                messages=[{"role": "user", "content": self.memory.context_text(self.perceive())}])
            menu = resp.parsed_output
        except Exception as e:   # noqa: BLE001 - keep the current menu
            log_event("agent_menu_error", error=repr(e))
            return
        opts = []
        for o in menu.options:
            o = " ".join(str(o).split()[:5]).strip(" .")
            if o and o.lower() != "something else" and o not in opts:
                opts.append(o)
        if len(opts) < 2:
            return
        opts = opts[:MENU_MAX] + ["Something else"]
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
                "facts": self.live.facts, "routines": [],
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
        agent = Agent(live, Memory("selftest", d), screens=ScriptedScreens(["Yes, please.", "Yes"]),
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
