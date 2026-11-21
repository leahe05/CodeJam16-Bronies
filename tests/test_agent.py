"""The patient-side agent: triggers, safety gates, rules fallback, the Claude tool loop (fake client)."""
import threading
import time
from types import SimpleNamespace as NS

import pytest

from lookat.agent import Agent, LiveScreens
from lookat.clock import clock
from lookat.devices import Room, SimPlug
from lookat.live import Live
from lookat.memory import Memory


class Scripted:
    """The patient: answers in order."""

    def __init__(self, *answers):
        self.answers, self.seen = list(answers), []

    def suggest(self, text, question, options):
        self.seen.append(question)
        return self.answers.pop(0)

    def ask(self, question, options):
        self.seen.append(question)
        return self.answers.pop(0)


def make(tmp_path, answers=(), client=None, temp=24.5, at="02:00", awake=True):
    room = Room(plug_factory=SimPlug)
    live = Live(room, patient="t")
    live.awake_state = {"awake": awake, "open_ratio": 0.9}
    room.set_temp(temp)
    clock.set_time(at)

    def sleep(s):                                   # the fan cools the room while we "wait"
        if room.state()["fan_on"]:
            room.set_temp(room.state()["temp_c"] - 0.1 * s)
    screens = Scripted(*answers)
    agent = Agent(live, Memory("t", str(tmp_path)), screens=screens, client=client, sleep=sleep)
    agent.client = client
    agent.last_choice = -1e9
    return agent, live, room, screens


@pytest.fixture(autouse=True)
def real_clock_after():
    yield
    clock.reset()


def stages(live):
    return [(f["stage"], f["text"]) for f in live.feed]


# ----- triggers ---------------------------------------------------------------------
def test_warm_night_trigger(tmp_path):
    agent, *_ = make(tmp_path)
    name, why = agent.trigger(agent.perceive())
    assert name == "warm_night" and "runs warm" in why


@pytest.mark.parametrize("kw", [{"awake": False}, {"awake": None}, {"at": "14:00"}, {"temp": 21.5}])
def test_no_trigger(tmp_path, kw):
    agent, *_ = make(tmp_path, **kw)
    assert agent.trigger(agent.perceive()) is None


def test_no_trigger_while_patient_in_a_menu_or_quiet(tmp_path):
    agent, live, *_ = make(tmp_path)
    live.sm.select("left", 0.0)                     # Room Controls
    assert agent.trigger(agent.perceive()) is None
    live.sm.select("middle", 0.0)                   # back home
    agent.quiet_until = clock.now().timestamp() + 60
    assert agent.trigger(agent.perceive()) is None


def test_cold_trigger(tmp_path):
    agent, *_ = make(tmp_path, temp=19.0, at="10:00")
    assert agent.trigger(agent.perceive())[0] == "cold"


def test_cooldown(tmp_path):
    agent, *_ = make(tmp_path, answers=["No, thanks."])
    agent.episode(*agent.trigger(agent.perceive()))
    assert agent.trigger(agent.perceive()) is None


# ----- safety -----------------------------------------------------------------------
def test_act_refused_without_matching_yes(tmp_path):
    agent, live, room, _ = make(tmp_path, answers=["Yes, please."])
    agent.trigger_name, agent.night, agent.questions, agent.approved = "x", True, 0, None
    assert agent.call("act", {"device": "fan", "cmd": "on", "why": ""}).startswith("REFUSED")
    agent.call("ask", {"question": "Fan on?", "options": ["Yes, please.", "No, thanks."],
                       "if_yes": {"device": "fan", "cmd": "on"}, "why": ""})
    assert agent.call("act", {"device": "lamp", "cmd": "on", "why": ""}).startswith("REFUSED")
    assert not room.state()["lamp_on"]
    assert agent.call("act", {"device": "fan", "cmd": "on", "why": ""}).startswith("Fan ON")
    assert agent.call("act", {"device": "fan", "cmd": "on", "why": ""}).startswith("REFUSED")   # used up


def test_question_limit(tmp_path):
    agent, *_ = make(tmp_path, answers=["a", "b", "c", "d"])
    agent.trigger_name, agent.night, agent.questions, agent.approved = "x", False, 0, None
    outs = [agent.call("ask", {"question": "q", "options": ["a", "b"], "if_yes": None, "why": ""}) for _ in range(4)]
    assert outs[-1].startswith("REFUSED")


# ----- rules fallback (no key) -------------------------------------------------------
def test_rules_warm_night_fixed(tmp_path):
    agent, live, room, screens = make(tmp_path, answers=["Yes, please.", "Yes"])
    agent.episode(*agent.trigger(agent.perceive()))
    assert room.state()["fan_on"] and room.state()["temp_c"] < 24.5
    assert screens.seen[-1] == "Better now?"
    assert any("no nurse needed" in t for _, t in stages(live))
    assert all(f["fallback"] for f in live.feed[1:])
    assert not live.alerts


def test_rules_not_better_pages_nurse_with_explanation(tmp_path):
    agent, live, *_ = make(tmp_path, answers=["Yes, please.", "No"])
    agent.episode(*agent.trigger(agent.perceive()))
    assert live.alerts and live.alerts[0]["kind"] == "page" and "Still warm" in live.alerts[0]["text"]


def test_three_ignored_night_checkins_stop_them(tmp_path):
    agent, live, *_ = make(tmp_path, answers=["ignored"] * 3)
    for _ in range(3):
        agent.last_run.clear()
        agent.episode(*agent.trigger(agent.perceive()))
    assert any("Stopping night check-ins" in t for _, t in stages(live))
    agent.last_run.clear()
    assert agent.trigger(agent.perceive()) is None


def test_patient_asks_for_help_nurse_gets_explained_page(tmp_path):
    agent, live, *_ = make(tmp_path, answers=["Water"], at="10:00", temp=22.0)
    agent._on_patient_event(("assistant_choice", "Get more comfortable"))
    page = live.alerts[0]
    assert page["text"] == "Patient asks: water." and "Not temperature-related" in page["reason"]


def test_quiet_for_two_hours(tmp_path):
    agent, live, *_ = make(tmp_path)
    agent._on_patient_event(("assistant_choice", "Quiet for 2 hours"))
    assert agent.trigger(agent.perceive()) is None and live.quiet_until


# ----- Claude path (fake client) -------------------------------------------------------
def tool(name, **inp):
    return NS(type="tool_use", name=name, input={**inp, "why": "test"}, id=f"t_{name}_{len(inp)}")


class FakeClient:
    def __init__(self, turns=None, error=None):
        self.turns, self.error, self.calls = list(turns or []), error, []
        self.beta = NS(messages=NS(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        return NS(stop_reason="tool_use", content=self.turns.pop(0))


def test_claude_episode_uses_tools(tmp_path):
    fan_on = {"device": "fan", "cmd": "on"}
    client = FakeClient([
        [tool("get_context")],
        [tool("suggest", text="Too warm?", question="Fan on?", options=["Yes, please.", "No, thanks."], if_yes=fan_on)],
        [tool("act", device="fan", cmd="on")],
        [tool("check", seconds=20)],
        [tool("ask", question="Better now?", options=["Yes", "No"], if_yes=None)],
        [tool("finish", summary="Cooled the room; no nurse needed")],
    ])
    agent, live, room, _ = make(tmp_path, answers=["Yes, please.", "Yes"], client=client)
    agent.episode(*agent.trigger(agent.perceive()))
    assert room.state()["fan_on"] and len(client.calls) == 6
    assert not any(f["fallback"] for f in live.feed)
    req = client.calls[0]
    assert req["model"] == "claude-opus-5-5" and all(t["strict"] for t in req["tools"])
    assert client.calls[1]["messages"][1]["role"] == "assistant"          # history appended, never edited


def test_claude_cannot_act_without_yes(tmp_path):
    client = FakeClient([[tool("act", device="fan", cmd="on")], [tool("finish", summary="x")]])
    agent, live, room, _ = make(tmp_path, client=client)
    agent.episode(*agent.trigger(agent.perceive()))
    assert not room.state()["fan_on"]
    assert any(t.startswith("Refused") for _, t in stages(live))


def test_claude_error_falls_back_to_rules(tmp_path):
    agent, live, room, _ = make(tmp_path, answers=["Yes, please.", "Yes"], client=FakeClient(error=TimeoutError()))
    agent.episode(*agent.trigger(agent.perceive()))
    assert room.state()["fan_on"] and any("AI unavailable" in t for _, t in stages(live))


# ----- the real screen ---------------------------------------------------------------
def test_live_screens_suggestion_answered(tmp_path):
    room = Room(plug_factory=SimPlug)
    live = Live(room, patient="t")
    agent = Agent(live, Memory("t", str(tmp_path)), client=None, sleep=lambda s: None)
    out = {}
    th = threading.Thread(target=lambda: out.setdefault("a", agent.screens.suggest("Too warm?", "Fan on?",
                                                                                   ["Yes, please.", "No, thanks."])))
    th.start()
    for _ in range(50):
        if live.sm.kind == "suggestion":
            break
        time.sleep(0.02)
    assert live.sm.view(0)["title"] == "Too warm?"
    for spot in ("middle", "left"):                 # engage, then "Yes, please."
        for ev in live.sm.select(spot, 0.0):
            agent.events.put(ev)
    th.join(3)
    assert out["a"] == "Yes, please." and live.sm.kind == "idle"
