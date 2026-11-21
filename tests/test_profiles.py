"""The two demo patients on the panel, and "Reset demo" (profiles kept, the AI's history deleted)."""
import pytest

from lookat.agent import Agent
from lookat.clock import clock
from lookat.devices import Room, SimPlug
from lookat.live import Live
from lookat.memory import PROFILES, profile_memory, routine_time
from tests.test_agent import Scripted


@pytest.fixture(autouse=True)
def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr("lookat.state.LOG_PATH", str(tmp_path / "events.jsonl"))
    yield
    clock.reset()


def start(tmp_path, *answers, pid="margaret", temp=22.0, at="14:00"):
    room = Room(plug_factory=SimPlug)
    live = Live(room, patient="t")
    live.awake_state = {"awake": True, "open_ratio": 0.9}
    room.set_temp(temp)
    clock.set_time(at)
    agent = Agent(live, profile_memory(pid, str(tmp_path)), screens=Scripted(*answers), client=None,
                  sleep=lambda s: None, profile=pid, memory_folder=str(tmp_path))
    agent.client = None
    agent.last_choice = -1e9
    return agent, live, room


def test_two_distinct_profiles_with_rich_charts(tmp_path):
    assert len(PROFILES) == 2
    for pid in PROFILES:
        m = profile_memory(pid, str(tmp_path))
        L = m.learn()
        assert len(m.data["facts"]) >= 10
        assert "cold_below_c" in L["comfort"] and "warm_above_c" in L["comfort"]
        assert routine_time(m, "bedtime") and routine_time(m, "wake")
    a, b = (profile_memory(p, str(tmp_path)) for p in PROFILES)
    assert a.learn()["comfort"] != b.learn()["comfort"]
    assert routine_time(a, "bedtime") != routine_time(b, "bedtime") and routine_time(a, "wake") != routine_time(b, "wake")


def test_switching_profile_changes_what_the_agent_knows(tmp_path):
    agent, live, room = start(tmp_path, temp=20.5)
    assert agent.trigger(agent.perceive()) is None                 # Margaret: cold only below 20
    live.demo_action({"action": "profile", "value": "james"})
    assert agent.profile == "james" and live.memory is agent.memory
    assert live.panel_json()["profile"]["current"] == "james"
    assert any("James Okafor" in f["text"] for f in live.panel_json()["memory"]["facts"])
    assert agent.trigger(agent.perceive())[0] == "cold"             # James: cold below 21
    assert not live.demo_action({"action": "profile", "value": "nobody"})["ok"]


def test_reset_demo_keeps_both_profiles_and_deletes_the_ai_history(tmp_path):
    agent, live, room = start(tmp_path, "No, thanks.", temp=19.0)
    agent.episode(*agent.trigger(agent.perceive()))                 # the AI asks something, it is logged
    agent.memory.add_fact("Likes the radio on", "preference", "agent")
    live.demo_action({"action": "one_week_later"})
    live.demo_action({"action": "profile", "value": "james"})
    agent.memory.log("actions", {"device": "lamp", "cmd": "on", "by": "patient"})
    gaze, week = live.gaze_mode, live.demo_week
    assert live.demo_action({"action": "reset"})["ok"]
    for pid, prof in PROFILES.items():
        m = profile_memory(pid, str(tmp_path))
        assert [f["text"] for f in m.data["facts"]] == [f["text"] for f in prof["chart"]]
        assert not any(m.data[k] for k in ("actions", "questions", "suggestions", "pages", "checkins"))
    assert agent.profile == "james" and agent.backoff == {} and agent.done_today == {}
    assert live.gaze_mode == gaze and live.demo_week is week         # the buttons still work after a reset
    assert len(live.panel_json()["memory"]["facts"]) == len(PROFILES["james"]["chart"])


def test_good_morning_check_in_once_at_the_patients_wake_up_time(tmp_path):
    agent, live, room = start(tmp_path, "Turn the lamp on", at="06:59")
    name, why = agent.trigger(agent.perceive())
    assert name == "wake" and "07:00" in why
    agent.episode(name, why)
    assert room.state()["lamp_on"]
    agent.hush_until = 0
    room.execute("lamp", "off", by="nurse")
    assert (agent.trigger(agent.perceive()) or ("",))[0] != "wake"   # once a morning
    live.demo_action({"action": "profile", "value": "james"})
    assert agent.trigger(agent.perceive()) is None                    # James wakes at 08:30, not now


def test_demo_week_follows_the_profiles_bedtime(tmp_path):
    m = profile_memory("james", str(tmp_path))
    m.load_demo_week()
    bed = [h for h in m.habits() if h["bedtime"]]
    assert bed and bed[0]["usual"].startswith("23:")
