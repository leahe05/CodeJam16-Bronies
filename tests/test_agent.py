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
    for _ in range(3):                              # three nights: the back-off has run out each time
        agent.last_run.clear(); agent.backoff.clear(); agent.hush_until = 0
        agent.episode(*agent.trigger(agent.perceive()))
    assert any("Stopping night check-ins" in t for _, t in stages(live))
    agent.last_run.clear(); agent.backoff.clear(); agent.hush_until = 0
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


# ----- AI-written menu + AI check-ins ----------------------------------------------------------
class MenuClient(FakeClient):
    def __init__(self, options, turns=None):
        super().__init__(turns)
        self.messages = NS(parse=lambda **kw: NS(parsed_output=NS(options=options, why="night, room warm")))


def test_claude_writes_the_talk_to_ai_menu(tmp_path):
    client = MenuClient(["Cool the room down", "Something else", "Dim the lamp please now ok", "Water"])
    agent, live, *_ = make(tmp_path, client=client)
    agent.refresh_menu()
    assert live.sm.assistant["options"] == ["Cool the room down", "Dim the lamp please now", "Water",
                                            "Quiet for 2 hours", "Something else"]
    assert any(t.startswith("Talk to AI menu") for _, t in stages(live))


def test_menu_failure_keeps_the_default(tmp_path):
    agent, live, *_ = make(tmp_path, client=FakeClient())
    before = list(live.sm.assistant["options"])
    agent.refresh_menu()
    assert live.sm.assistant["options"] == before


def test_any_menu_choice_goes_to_claude(tmp_path):
    client = MenuClient(["Water", "Rest"], turns=[[tool("finish", summary="Paged for water")]])
    agent, live, *_ = make(tmp_path, client=client, at="10:00", temp=22.0)
    agent._on_patient_event(("assistant_choice", "Water"))
    assert "Water" in client.calls[0]["messages"][0]["content"]


def test_any_menu_choice_without_ai_pages_the_nurse(tmp_path):
    agent, live, *_ = make(tmp_path, at="10:00", temp=22.0)
    agent._on_patient_event(("assistant_choice", "Read me the news"))
    assert live.alerts[0]["text"] == "Patient asks: read me the news."


def test_room_change_look_only_on_a_real_change(tmp_path):
    client = MenuClient(["Water", "Rest"], turns=[[tool("finish", summary="Nothing needed right now")]])
    agent, live, room, _ = make(tmp_path, at="10:00", temp=22.0, client=client)
    assert agent.trigger(agent.perceive()) is None           # first look: just a snapshot
    assert agent.trigger(agent.perceive()) is None           # nothing changed: no call, no timer
    room.set_temp(23.4)
    name, why = agent.trigger(agent.perceive())
    assert name == "room_change" and "temperature 22.0 -> 23.4" in why
    agent.episode(name, why)
    assert agent.questions == 0 and not live.alerts          # Claude chose silence; no rules ran


def test_room_change_time_of_day_and_nurse_switch(tmp_path):
    agent, live, room, _ = make(tmp_path, at="16:58", temp=22.0, client=MenuClient(["Water", "Rest"]))
    agent.trigger(agent.perceive())
    clock.set_time("17:01")
    room.execute("lamp", "on", by="nurse")
    _, why = agent.trigger(agent.perceive())
    assert "it is now evening" in why and "lamp turned on (by nurse)" in why


def test_patient_switches_and_no_ai_are_not_room_changes(tmp_path):
    agent, live, room, _ = make(tmp_path, at="10:00", temp=22.0, client=MenuClient(["Water", "Rest"]))
    agent.trigger(agent.perceive())
    room.execute("lamp", "on", by="patient")
    assert agent.trigger(agent.perceive()) is None
    agent2, _, room2, _ = make(tmp_path, at="10:00", temp=22.0)     # no AI: no room-change looks
    agent2.trigger(agent2.perceive())
    room2.set_temp(25.0)
    assert agent2.trigger(agent2.perceive()) is None


def test_waking_up_is_a_change(tmp_path):
    agent, live, *_ = make(tmp_path, at="10:00", temp=22.0, client=MenuClient(["Water", "Rest"]), awake=False)
    agent.trigger(agent.perceive())
    agent.seen = {**agent.perceive(), "period": "morning", "awake": False}
    live.awake_state = {"awake": True, "open_ratio": 0.9}
    _, why = agent.trigger(agent.perceive())
    assert "just opened their eyes" in why



@pytest.mark.parametrize("text,want", [("Turn fan on", ("fan", "on")), ("Turn the lights off", ("lamp", "off")),
                                       ("Make it cooler", ("fan", "on")), ("Water", None), ("Call the nurse", None)])
def test_action_in(text, want):
    from lookat.agent import action_in
    assert action_in(text) == want


def test_menu_choice_turn_fan_on_is_the_approval(tmp_path):
    client = MenuClient(["Water", "Rest"], turns=[[tool("act", device="fan", cmd="on")], [tool("finish", summary="Fan on")]])
    agent, live, room, screens = make(tmp_path, client=client, at="10:00", temp=22.0)
    agent._on_patient_event(("assistant_choice", "Turn fan on"))
    assert room.state()["fan_on"] and not screens.seen          # done without asking again
    agent.choice = None
    assert agent.call("act", {"device": "lamp", "cmd": "on", "why": ""}).startswith("REFUSED")


def test_menu_choice_fan_without_ai(tmp_path):
    agent, live, room, screens = make(tmp_path, answers=["Yes"], at="10:00", temp=22.0)
    agent._on_patient_event(("assistant_choice", "Turn fan on"))
    assert room.state()["fan_on"] and screens.seen == ["Better now?"]


def test_own_fix_is_not_a_room_change(tmp_path):
    agent, live, room, _ = make(tmp_path, answers=["Yes, please.", "Yes"], client=None)
    agent.client = MenuClient(["Water", "Rest"], turns=[])            # Claude available for room changes
    agent.client.error = TimeoutError()                               # but this episode runs on rules
    agent.episode(*agent.trigger(agent.perceive()))                   # fan on, room cools ~2 °C
    agent.last_change_look = -1e9
    assert agent.room_changes(agent.perceive()) == []


# ----- respecting silence -------------------------------------------------------------------
def test_ignored_suggestion_backs_off_and_hushes(tmp_path):
    agent, live, *_ = make(tmp_path, answers=["ignored"])
    agent.episode(*agent.trigger(agent.perceive()))
    agent.last_run.clear()                          # even without the usual cooldown...
    assert agent.trigger(agent.perceive()) is None  # ...10 min of quiet, and warm_night waits 30 min
    agent.hush_until = 0
    assert agent.trigger(agent.perceive()) is None
    assert any("Ignored: no 'warm_night' suggestions for 30 min" in t for _, t in stages(live))


def test_second_ignore_doubles_the_backoff(tmp_path):
    agent, live, *_ = make(tmp_path, answers=["ignored", "ignored"])
    for _ in range(2):
        agent.last_run.clear(); agent.backoff.clear(); agent.hush_until = 0
        agent.episode(*agent.trigger(agent.perceive()))
    assert any("for 60 min" in t for _, t in stages(live))


def test_declined_suggestion_waits_an_hour(tmp_path):
    agent, live, *_ = make(tmp_path, answers=["No, thanks."])
    agent.episode(*agent.trigger(agent.perceive()))
    assert any("Declined: no 'warm_night' suggestions for 60 min" in t for _, t in stages(live))


def test_never_undo_the_patients_own_choice(tmp_path):
    agent, live, room, _ = make(tmp_path, temp=19.0, at="10:00")
    room.execute("fan", "on", by="patient")          # they want the fan, even though it is cool
    assert agent.own_choice("fan", "on")
    assert agent.trigger(agent.perceive()) is None   # no "Feeling cold? Fan off?"
    clock.set_time("10:45")
    assert agent.trigger(agent.perceive())[0] == "cold"


def test_patient_requests_do_not_back_off(tmp_path):
    agent, live, *_ = make(tmp_path, answers=["ignored"], at="10:00", temp=22.0)
    agent._on_patient_event(("assistant_choice", "Get more comfortable"))
    assert agent.backoff == {} and agent.hush_until == 0


def test_context_lists_recent_suggestions_and_own_choices(tmp_path):
    agent, live, room, _ = make(tmp_path, answers=["ignored"])
    room.execute("lamp", "on", by="patient")
    agent.episode(*agent.trigger(agent.perceive()))
    ctx = agent.call("get_context", {"why": ""})
    assert "RECENT SUGGESTIONS" in ctx and "-> ignored" in ctx
    assert "PATIENT'S OWN CHOICES: lamp on" in ctx and "TOPICS ON HOLD" in ctx


def test_remember_skips_known_facts(tmp_path):
    agent, *_ = make(tmp_path)
    assert agent.call("remember", {"fact": "runs warm at night", "why": ""}) == "already known"


def test_menu_rewritten_only_when_the_situation_changes(tmp_path):
    client = MenuClient(["Some water", "Change my position", "I'm too warm"])
    agent, live, room, _ = make(tmp_path, client=client, at="10:00", temp=22.0)
    calls = []
    agent.refresh_menu = lambda: (calls.append(1), setattr(agent, "menu_sig", agent.situation()))
    agent.step(); agent.step()
    assert len(calls) == 1                            # written once, then steady
    room.execute("lamp", "on", by="patient")
    agent.events.get_nowait() if not agent.events.empty() else None
    agent.last_choice = -1e9
    agent.step()
    assert len(calls) == 2                            # the lamp changed: rewritten
