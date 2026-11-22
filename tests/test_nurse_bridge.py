"""The real patient behind Paco's nurse dashboard (lookat/nurse_bridge.py)."""
import time

import pytest

from lookat.clock import clock
from lookat.devices import Room, SimPlug
from lookat.live import Live
from lookat.memory import profile_memory
from lookat.nurse_bridge import NurseBridge


@pytest.fixture(autouse=True)
def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr("lookat.state.LOG_PATH", str(tmp_path / "events.jsonl"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    yield
    clock.reset()


def setup(tmp_path, decide=None):
    live = Live(Room(plug_factory=SimPlug), patient="t", now=time.monotonic)
    live.profile = "margaret"
    live.memory = profile_memory("margaret", str(tmp_path))
    live.awake_state = {"awake": True, "open_ratio": 0.9}
    live.nurse = NurseBridge(live, decide=decide)
    live.nurse.start()
    return live


def answer(live, choice):
    q = live.session["thread"][-1]
    with live.lock:
        live._handle(("nurse_answer", q["qid"], choice), live.now())


def test_dashboard_gets_the_real_patient(tmp_path):
    d = setup(tmp_path).nurse_json()
    assert d["patient"]["name"] == "Margaret Chen" and d["request_question_branches"]
    assert "Feels cold below 20 °C" in d["profile"]["preferences"]
    assert any("bedtime" in h for h in d["profile"]["habits"])
    assert d["bedside"]["awareness"]["state"] == "awake" and d["bedside"]["vitals"]["simulated"]
    assert d["profile"]["vitals"]["source"] == "simulated" and len(d["profile"]["vitals"]["samples"]) == 3


def test_agent_page_is_triaged_urgent_is_not(tmp_path):
    live = setup(tmp_path)
    live.add_alert("page", "Feels cold, would like a blanket.", reason="Room 19 °C")
    live.add_alert("page", "Room reads 31 °C.", urgency="urgent")
    alerts = live.nurse_json()["alerts"]
    triaged = [a for a in alerts if a["id"].startswith("agent-")]
    assert triaged[0]["text"] == "Feels cold, would like a blanket." and triaged[0]["review"] == "local_reviewed"
    assert any(a["text"] == "Room reads 31 °C." and a["urgency"] == "urgent" for a in live.alerts)
    assert live.nurse_action("alert", {"id": triaged[0]["id"], "action": "ack"})["ok"]
    assert [a for a in live.nurse_json()["alerts"] if a["id"] == triaged[0]["id"]][0]["done"]


def test_claude_review_sets_priority_and_reason(tmp_path):
    decide = lambda request, profile, confirmed: {"priority": "soon", "action": "notify", "rationale": "Cold and asked.",
                                                  "recommendation": "Bring a warm blanket.", "steps": []}
    live = setup(tmp_path, decide=decide)
    live.add_alert("page", "Feels cold, would like a blanket.")
    end = time.monotonic() + 3
    while time.monotonic() < end:
        a = [x for x in live.nurse_json()["alerts"] if x["id"].startswith("agent-")][0]
        if a.get("priority_source") == "claude":
            break
        time.sleep(0.05)
    assert a["priority"]["level"] == "soon" and a["recommendation"] == "Bring a warm blanket."
    live.nurse.stop()


def test_check_in_proposal_approve_and_yes_reports_the_request(tmp_path):
    live = setup(tmp_path)
    assert live.nurse_action("check-in/suggest", {"choice": "water"})["ok"]
    p = live.nurse_json()["proposal"]
    assert p["steps"][0]["question"] == "Would you like some water?"
    assert live.nurse_action("proposal/approve", {"id": p["id"], "source": p.get("source")})["ok"]
    assert live.screen_json()["screen"] == "nurse" and live.session["waiting"]
    answer(live, "Yes")
    reqs = [a for a in live.nurse_json()["alerts"] if a["id"].startswith("agent-")]
    assert reqs and reqs[0]["text"] == "Patient requests a glass of water."
    assert live.nurse_json()["proposal"] is None


def test_question_about_a_request_shows_the_answer_and_next_step(tmp_path):
    live = setup(tmp_path)
    live.add_alert("page", "Wants repositioning.")
    aid = [a for a in live.nurse_json()["alerts"] if a["id"].startswith("agent-")][0]["id"]
    draft = live.nurse_action("alert/question/draft", {"id": aid, "question": "Left side or right side?"})
    assert draft["ok"] and draft["source"] == "fallback" and len(draft["branches"]) == 2
    branches = [{"label": "Left side", "next_step": "Turn her to the left."},
                {"label": "Right side", "next_step": "Turn her to the right."}]
    assert live.nurse_action("alert/question/send", {"id": aid, "question": "Left side or right side?",
                                                     "branches": branches, "source": "ai"})["ok"]
    answer(live, "Right side")
    a = [x for x in live.nurse_json()["alerts"] if x["id"] == aid][0]
    assert a["question_reply"] == {"answer": "Right side", "next_step": "Turn her to the right.", "source": "ai"}
    texts = [h["text"] for h in live.nurse_json()["history"]]
    assert "Left side or right side?" in texts and "Right side" in texts and "Turn her to the right." in texts


def test_visitors_visit_and_reset(tmp_path):
    live = setup(tmp_path)
    assert live.nurse_action("presence", {"present": True})["ok"]
    assert live.nurse_json()["bedside"]["visitors_present"] is True
    assert live.nurse_action("visit", {})["ok"] and live.last_visit
    live.add_alert("page", "Some water.")
    live.demo_action({"action": "reset"})
    d = live.nurse_json()
    assert d["history"] == [] and not [a for a in d["alerts"] if a["id"].startswith("agent-")]
    assert d["bedside"]["visitors_present"] is False and live.nurse is not None


def test_claudes_question_for_an_unclear_request_becomes_the_proposal(tmp_path):
    decide = lambda request, profile, confirmed: {
        "priority": "soon", "action": "ask", "rationale": "Unclear need.", "recommendation": "Ask first.",
        "steps": [{"question": "Are you thirsty?", "request": "Patient requests a glass of water."}]}
    live = setup(tmp_path, decide=decide)
    live.add_alert("page", "Wants something, did not say what.")
    end = time.monotonic() + 3
    while time.monotonic() < end and live.nurse_json()["proposal"] is None:
        time.sleep(0.05)
    p = live.nurse_json()["proposal"]
    assert p["steps"][0]["question"] == "Are you thirsty?" and p["source"] == "ai"
    assert live.nurse_action("proposal/approve", {"id": p["id"], "source": "ai"})["ok"]
    answer(live, "Yes")
    texts = [a["text"] for a in live.nurse_json()["alerts"]]
    assert "Patient requests a glass of water." in texts
    origin = [a for a in live.nurse_json()["alerts"] if a["text"] == "Wants something, did not say what."][0]
    assert origin["review"] == "clarified"
    live.nurse.stop()


def test_yes_to_a_nurse_check_in_shows_the_nurse_is_coming_screen(tmp_path):
    live = setup(tmp_path)
    live.nurse_action("check-in/suggest", {"choice": "water"})
    p = live.nurse_json()["proposal"]
    live.nurse_action("proposal/approve", {"id": p["id"], "source": p.get("source")})
    answer(live, "Yes")
    assert (live.screen_json().get("overlay") or {}).get("kind") == "call_nurse"


def test_patient_message_reaches_the_nurse_and_the_conversation(tmp_path):
    live = setup(tmp_path)
    with live.lock:
        live._handle(("patient_message", "I'm thirsty"), live.now())
    d = live.nurse_json()
    assert any(a["text"] == "Patient message: I'm thirsty." for a in d["alerts"])      # a request on the dashboard
    assert d["conversation"][-1] == {"time": d["conversation"][-1]["time"], "from": "patient", "kind": "message",
                                     "text": "I'm thirsty"}
    assert (live.screen_json().get("overlay") or {}).get("kind") == "call_nurse"       # nurse is coming


def test_nurse_message_and_question_are_one_conversation(tmp_path):
    live = setup(tmp_path)
    assert live.nurse_action("send", {"question": "I'll be there in 5 minutes.", "options": []})["kind"] == "message"
    assert live.screen_json()["middle"]["label"] == "OK"
    answer(live, "OK")
    assert live.nurse_action("send", {"question": "Are you in pain?", "options": ["Yes", "No"]})["ok"]
    answer(live, "No")
    conv = [(c["from"], c["kind"], c["text"]) for c in live.nurse_json()["conversation"]]
    assert conv == [("nurse", "message", "I'll be there in 5 minutes."), ("patient", "seen", "Seen"),
                    ("nurse", "question", "Are you in pain?"), ("patient", "answer", "No")]
