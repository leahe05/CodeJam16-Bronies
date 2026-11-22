"""The real patient behind Paco's nurse dashboard (lookat/nurse_bridge.py)."""
import threading
import time

import pytest
from fastapi.testclient import TestClient

from lookat import nurse_ai
from lookat.agent import Agent
from lookat.awake import AwakeDetector
from lookat.clock import clock
from lookat.devices import Room, SimPlug
from lookat.live import Live
from lookat.memory import profile_memory
from lookat.nurse_bridge import NurseBridge
from lookat.server import make_app
from main import web_step


@pytest.fixture(autouse=True)
def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr("lookat.state.LOG_PATH", str(tmp_path / "events.jsonl"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    yield
    clock.reset()


def setup(tmp_path, decide=None, interval_s=None, assess=None):
    live = Live(Room(plug_factory=SimPlug), patient="t", now=time.monotonic)
    live.profile = "margaret"
    live.memory = profile_memory("margaret", str(tmp_path))
    live.awake_state = {"awake": True, "open_ratio": 0.9}
    live.nurse = NurseBridge(live, decide=decide, assess=assess)
    if interval_s is not None:
        live.nurse.supervisor.interval_s = interval_s
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


def test_patient_agent_page_round_trips_through_dashboard_and_browser_gaze(tmp_path, monkeypatch):
    def decide(request, profile, confirmed):
        assert "Which side is unclear" in request
        assert profile["patient"]["id"] == "margaret"
        assert "Feels cold below 20 °C" in profile["preferences"]
        assert not confirmed
        return {"priority": "soon", "action": "ask", "rationale": "Needs clarification.",
                "recommendation": "Ask which side.", "steps": [
                    {"question": "Would you like to turn right?", "request": "Patient asks to turn right."}]}

    def generate(instruction, payload):
        assert payload["patient_request"] == "Needs help changing position."
        assert "Feels cold below 20 °C" in payload["patient_context"]["preferences"]
        return {"question": "Which side would you prefer?", "branches": [
            {"label": "Left side", "next_step": "Review the patient's preference for the left side."},
            {"label": "Right side", "next_step": "Review the patient's preference for the right side."},
        ]}

    live = setup(tmp_path, decide=decide, interval_s=.02)
    monkeypatch.setattr(nurse_ai, "_request_json", generate)
    now = [time.monotonic()]
    live.now = lambda: now[0]
    agent = Agent(live, live.memory, profile="margaret", client=object(), memory_folder=str(tmp_path))
    client = TestClient(make_app(live))
    awake = AwakeDetector()
    try:
        agent.call("page_nurse", {"message": "Needs help changing position.", "reason": "Which side is unclear"})
        end = time.monotonic() + 3
        while not (live.nurse.supervisor.latest_report("margaret") or {}).get("suggested_question") and time.monotonic() < end:
            time.sleep(.01)
        data = client.get("/api/nurse").json()
        assert data["agent_checkin"]["needs_attention"] and data["agent_checkin"]["suggested_question"]
        assert live.nurse.supervisor.latest_report("james") is None
        alert = data["alerts"][0]
        assert alert["priority_source"] == "claude"
        body = {"patient_id": "margaret", "id": alert["id"]}
        draft = client.post("/api/nurse/question/draft", json=body).json()
        assert data["question_drafts"] and draft["ok"] and draft["source"] == "ai"
        assert client.get("/api/screen").json()["screen"] == "idle"
        edit = "Would you prefer the left or right side?"
        sent = client.post("/api/nurse/alert/question/send", json={**draft, **body, "question": edit}).json()
        assert sent["ok"] and client.get("/api/nurse").json()["proposal"] is None
        screen = client.get("/api/screen").json()
        assert screen["title"] == edit and screen["right"]["label"] == "Right side"
        for _ in range(8):
            now[0] += .2
            client.post("/api/gaze", json={"spot": "right", "face": True, "eyes_open": True})
            web_step(live, awake, now[0])
        now[0] += .2
        client.post("/api/gaze", json={"spot": "right", "face": True, "events": ["confirm"]})
        web_step(live, awake, now[0])
        data = client.get("/api/nurse").json()
        assert data["alerts"][0]["question_reply"]["answer"] == "Right side"
        assert data["session"]["thread"][-1]["next_step"] == draft["branches"][1]["next_step"]
        assert client.post("/api/nurse/session/end", json={"patient_id": "margaret"}).json()["ok"]
        assert client.get("/api/screen").json()["screen"] == "idle"
        assert agent.use_profile("james") == "ok"
        live.nurse.supervisor.check_patients()
        changed = client.get("/api/nurse").json()
        assert changed["profile"]["patient"]["id"] == "james"
        assert all(h["text"] != "Needs help changing position." for h in changed["history"])
        assert not client.post("/api/nurse/send", json={"patient_id": "margaret", "question": "Hello?",
                                                        "options": ["Yes", "No"]}).json()["ok"]
    finally:
        live.nurse.stop()


def test_ending_session_cancels_a_pending_supervisor_follow_up(tmp_path, monkeypatch):
    live = setup(tmp_path)
    complete = threading.Event()
    follow_up = live.nurse._follow_up

    def track_follow_up(*args):
        try:
            follow_up(*args)
        finally:
            complete.set()

    monkeypatch.setattr(live.nurse, "_follow_up", track_follow_up)
    try:
        alert = live.add_alert("page", "Needs help getting comfortable.")
        live.nurse.supervisor.queue_question("margaret", {
            "id": "comfort-question", "origin_alert": alert["id"], "source": "ai", "steps": [
                {"question": "Would another pillow help?", "request": "Patient requests a pillow."},
                {"question": "Would another blanket help?", "request": "Patient requests a blanket."},
            ],
        })
        assert live.nurse_action("proposal/approve", {"id": "comfort-question", "source": "ai"})["ok"]
        with live.lock:
            answer(live, "No")
            assert live.nurse_action("session/end", {})["ok"]
        assert complete.wait(2)
        data = live.nurse_json()
        assert not data["session"]["active"] and data["alerts"][0]["review"] == "needs_review"
        assert live.screen_json()["screen"] == "idle"
    finally:
        live.nurse.stop()


def test_status_stream_only_surfaces_actionable_updates_with_a_question(tmp_path, monkeypatch):
    monkeypatch.setattr("lookat.nurse_bridge.STATUS_SAMPLE_S", .02)
    monkeypatch.setattr("lookat.nurse_bridge.STATUS_REVIEW_GAP_S", .05)
    status_calls = []

    def assess(instruction, payload):
        profile, changes = payload["patient_context"], payload["changes"]
        assert all("Margaret Chen" not in fact for fact in profile["preferences"])
        status_calls.append(changes)
        if any("usual bedtime" in change for change in changes):
            assert "When warm, prefers the fan over removing a blanket" in profile["preferences"]
            return {"action": "ask", "rationale": "A fan is their usual preference when warm.",
                    "question": "Would a fan on low help you get comfortable?", "choice": "fan"}
        if any("next visit" in change for change in changes):
            return {"action": "flag", "rationale": "The patient agent deferred a pillow check.",
                    "question": "Would you like fewer pillows?", "choice": "remove_pillow"}
        return {"action": "ignore"}

    def wait_for(predicate):
        stop = time.monotonic() + 3
        while time.monotonic() < stop:
            if predicate():
                return
            time.sleep(.01)
        assert predicate()

    clock.set_time("21:30")
    monkeypatch.setattr(nurse_ai, "_request_json", assess)
    live = setup(tmp_path, interval_s=30, assess=nurse_ai.assess_status)
    supervisor = live.nurse.supervisor
    try:
        wait_for(lambda: supervisor.latest_profile("margaret"))
        live.room.set_temp(22.3)
        wait_for(lambda: supervisor.latest_profile("margaret")["room"]["temp_c"] == 22.3)
        assert status_calls == [] and live.nurse.proposal is None
        live.room.execute("lamp", "on", by="patient")
        wait_for(lambda: len(status_calls) == 1)
        assert live.nurse.proposal is None and not live.nurse_json()["alerts"]
        clock.set_time("22:30")
        live.room.set_temp(24)
        wait_for(lambda: live.nurse.proposal is not None)
        proposal = live.nurse.proposal
        assert "22.3 to 24 °C" in proposal["evidence"]
        assert "fan on low" in proposal["steps"][0]["question"]
        assert live.screen_json()["screen"] == "idle"
        assert live.nurse_action("proposal/dismiss", {"id": proposal["id"]})["ok"]
        note = "For the next visit: Check whether the patient needs fewer pillows."
        live.add_feed("outcome", note)
        wait_for(lambda: bool(live.nurse_json()["alerts"]))
        alert, = live.nurse_json()["alerts"]
        assert alert["delivery"] == "status-update" and alert["source"] == "ai"
        assert alert["suggested_question"] == "Would you like fewer pillows?"
        assert live.nurse_action("alert", {"id": alert["id"], "action": "ack"})["ok"]
        live.add_feed("outcome", note)
        time.sleep(.12)
        assert len(status_calls) == 3 and len(live.nurse_json()["alerts"]) == 1
        assert supervisor.latest_report("margaret") is None  # the scheduled check-in has not run
    finally:
        live.nurse.stop()


def test_arrival_estimate_reaches_patient_without_replacing_their_question(tmp_path):
    live = setup(tmp_path)
    now = [100.0]
    live.now = lambda: now[0]
    client = TestClient(make_app(live))
    try:
        alert = live.add_alert("page", "Requests a blanket.")
        body = {"patient_id": "margaret", "id": alert["id"]}
        assert not client.post("/api/nurse/alert/eta", json={**body, "eta_minutes": True}).json()["ok"]
        assert client.post("/api/nurse/send", json={"question": "Would a blanket help?", "options": ["Yes", "No"]}).json()["ok"]
        assert client.post("/api/nurse/alert/eta", json={**body, "eta_minutes": 5}).json()["ok"]
        assert not client.post("/api/nurse/alert/eta", json={**body, "eta_minutes": 1}).json()["ok"]
        screen = client.get("/api/screen").json()
        assert screen["title"] == "Would a blanket help?" and screen["left"]["label"] == "Yes"
        assert "about 5 minutes" in screen["subtitle"]
        assert live.nurse.link.drain_responses()[0]["eta_minutes"] == 5
        assert client.get("/api/nurse").json()["alerts"][0]["eta_minutes"] == 5
        second = live.add_alert("page", "Requests a pillow.")
        assert client.post("/api/nurse/alert/eta", json={**body, "id": second["id"], "eta_minutes": 10}).json()["ok"]
        now[0] += 299
        assert client.get("/api/nurse").json()["eta_reminders"] == []
        now[0] += 1
        reminder, = client.get("/api/nurse").json()["eta_reminders"]
        assert reminder["id"] == alert["id"] and reminder["request"] == "Requests a blanket."
        assert "about 5 minutes" in client.get("/api/screen").json()["subtitle"]  # the second estimate
        extend = {**reminder, "action": "extend", "eta_minutes": 3}
        assert client.post("/api/nurse/alert/eta/follow-up", json=extend).json()["ok"]
        assert not client.post("/api/nurse/alert/eta/follow-up", json=extend).json()["ok"]
        assert client.get("/api/nurse").json()["eta_reminders"] == []
        assert "about 3 minutes" in client.get("/api/screen").json()["subtitle"]
        now[0] += 180
        reminder, = client.get("/api/nurse").json()["eta_reminders"]
        assert client.post("/api/nurse/alert/eta/follow-up", json={**reminder, "action": "attended"}).json()["ok"]
        assert client.get("/api/nurse").json()["eta_reminders"] == []
        assert "about 2 minutes" in client.get("/api/screen").json()["subtitle"]
        now[0] += 120
        assert client.get("/api/nurse").json()["eta_reminders"][0]["id"] == second["id"]
        assert client.get("/api/screen").json()["subtitle"] is None
    finally:
        live.nurse.stop()


def test_eta_for_a_native_call_cannot_go_to_a_different_profile(tmp_path):
    live = setup(tmp_path)
    try:
        with live.lock:
            live._handle(("call_nurse",), live.now())
        call, = live.nurse_json()["alerts"]
        assert call["eta_available"] and call["urgency"] == "urgent"
        live.profile = "james"
        assert not live.nurse_json()["alerts"][0]["eta_available"]
        estimate = {"id": call["id"], "eta_minutes": 4}
        assert not live.nurse_action("alert/eta", {**estimate, "patient_id": "james"})["ok"]
        assert live.nurse.link.eta_message(live.now()) is None
        live.profile = "margaret"
        assert live.nurse_action("alert/eta", {**estimate, "patient_id": "margaret"})["ok"]
        assert "about 4 minutes" in live.screen_json()["subtitle"]
    finally:
        live.nurse.stop()
