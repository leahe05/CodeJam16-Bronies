"""Demo pacing (the agent reacts to every panel change) and the bedtime check-in."""
import pytest

from lookat import agent as agent_mod
from lookat.clock import clock
from tests.test_agent import make, stages


@pytest.fixture(autouse=True)
def real_clock_after():
    yield
    clock.reset()


def test_demo_pacing_is_the_default():
    assert not agent_mod.CALM
    assert agent_mod.COOLDOWN_MIN <= 2 and agent_mod.DECLINED_BACKOFF_MIN <= 2 and agent_mod.CHANGE_GAP_S <= 5


def test_a_temperature_change_is_looked_at_even_when_the_topic_is_on_hold(tmp_path):
    agent, live, room, _ = make(tmp_path, client=object(), temp=22.0, at="14:00")   # client: "Claude is on"
    assert agent.trigger(agent.perceive()) is None            # first look: the baseline
    agent.backoff["room_change"] = clock.now().timestamp() + 3600   # e.g. the patient just declined something
    room.set_temp(22.6)                                        # the team nudges the room on the panel
    name, why = agent.trigger(agent.perceive())
    assert name == "room_change" and "temperature 22.0 -> 22.6" in why


def test_bedtime_check_in_once_a_night_from_the_chart(tmp_path):
    agent, live, room, _ = make(tmp_path, answers=["Turn the lamp off"], temp=21.0, at="21:59")
    room.execute("lamp", "on", by="nurse")
    name, why = agent.trigger(agent.perceive())
    assert name == "bedtime" and "usual bedtime 22:00" in why and "chart" in why and "lamp on" in why
    agent.episode(name, why)                                   # no key: the rules ask, the patient picks lamp off
    assert not room.state()["lamp_on"]
    assert any("Bedtime check-in: Turn the lamp off" in t for _, t in stages(live))
    agent.hush_until = 0
    agent.last_run.clear()
    clock.set_time("22:30")
    room.execute("lamp", "on", by="nurse")
    assert (agent.trigger(agent.perceive()) or ("",))[0] != "bedtime"   # already asked tonight


def test_no_bedtime_check_in_during_the_day(tmp_path):
    agent, *_ = make(tmp_path, temp=21.0, at="15:00")
    assert agent.trigger(agent.perceive()) is None
