import random
import threading
import time

import pytest

from lookat import nurse_ai
from lookat.nurse_patient_profile import CARE_CHOICES
from lookat.nurse_shift import ShiftPlayback
from lookat.nurse_supervisor import CHECK_IN_PROMPT, NurseSupervisor
from lookat.supervisor_demo import make_demo_patients


def choose(patient, spot):
    qid = patient.nurse_json()["session"]["thread"][-1]["qid"]
    return patient.nurse_action("simulate/choose", {"spot": spot, "qid": qid})


def test_same_conditions_produce_different_preference_based_proposals():
    avery, morgan = make_demo_patients()
    assert avery.supervisor is morgan.supervisor
    a, m = avery.proposal(), morgan.proposal()
    assert a["evidence"] == m["evidence"]
    assert "30 minutes" in a["evidence"] and "2 °C above" in a["evidence"]
    assert "window" in a["steps"][0]["question"]
    assert "fan on low" in m["steps"][0]["question"]
    assert not avery.session["active"] and not morgan.session["active"]

    assert avery.demo_action({"action": "set_temp", "value": 23})["ok"]
    assert avery.proposal() == a
    avery.supervisor.check_patients()
    assert avery.proposal() is None
    assert morgan.proposal() == m


def test_approval_branches_center_calls_and_answers_stay_with_the_patient():
    avery, morgan = make_demo_patients()
    proposal = avery.proposal()
    approval = {"id": proposal["id"]}
    assert not morgan.nurse_action("proposal/approve", approval)["ok"]
    assert avery.nurse_action("proposal/approve", approval)["ok"]
    assert not avery.nurse_action("proposal/approve", approval)["ok"]
    assert avery.screen_json()["middle"]["label"] == "Call nurse"
    assert avery.screen_json()["title"] == proposal["steps"][0]["question"]
    assert morgan.screen_json()["screen"] == "idle"

    first_qid = avery.session["thread"][-1]["qid"]
    assert choose(avery, "middle")["ok"]
    assert avery.nurse_json()["alerts"][0]["kind"] == "call" and not morgan.nurse_json()["alerts"]
    assert choose(avery, "right")["ok"]
    assert avery.screen_json()["title"] == proposal["steps"][1]["question"]
    assert not avery.nurse_action("simulate/choose", {"spot": "left", "qid": first_qid})["ok"]
    assert choose(avery, "left")["ok"]
    assert [question["answer"] for question in avery.session["thread"]] == ["No", "Yes"]
    assert "lighter blanket" in avery.recent[0]["text"]
    assert avery.nurse_action("session/end", {})["ok"]
    assert avery.screen_json()["screen"] == "idle"
    assert morgan.proposal() is not None and not morgan.recent
    history = avery.nurse_json()["history"]
    assert [entry["action"] for entry in history[-8:]] == [
        "Approved", "Asked", "Called nurse", "Chose", "Asked", "Chose", "Reported to nurse", "Replied",
    ]
    assert history[-2]["text"] == "Patient requests a lighter blanket."
    assert [row["time"] for row in history] == sorted(row["time"] for row in history)
    assert [entry["action"] for entry in morgan.nurse_json()["history"]] == [
        "Noticed", "Asked", "Chose", "Replied", "Suggested to nurse",
    ]


def test_dismissal_does_not_send_a_question_and_reset_restores_demo():
    avery, morgan = make_demo_patients()
    assert morgan.nurse_action("proposal/dismiss", {"id": morgan.proposal()["id"]})["ok"]
    assert morgan.proposal() is None and not morgan.session["active"]
    assert avery.proposal() is not None
    assert morgan.demo_action({"action": "reset"})["ok"]
    assert morgan.proposal() is None
    morgan.supervisor.check_patients()
    assert morgan.proposal() is not None


def test_refinement_keeps_nurse_approval_and_patient_boundaries(monkeypatch):
    monkeypatch.setattr(nurse_ai, "_draft", lambda *args: [
        "Would a slightly open window help you sleep?", "Would a lighter blanket help?",
    ])
    avery, morgan = make_demo_patients()
    proposal_id = avery.proposal()["id"]
    assert avery.nurse_action("proposal/refine", {"id": proposal_id})["ok"]
    assert avery.screen_json()["screen"] == "idle"
    assert not avery.nurse_action("proposal/approve", {"id": proposal_id})["ok"]
    assert avery.nurse_action("proposal/approve", {"id": proposal_id, "source": "ai"})["ok"]
    assert avery.screen_json()["title"] == "Would a slightly open window help you sleep?"
    assert "fan on low" in morgan.proposal()["steps"][0]["question"]


def test_periodic_prompt_and_proactive_inbox_are_independent():
    supervisor = NurseSupervisor(interval_s=.02)
    stop = threading.Event()
    complete = threading.Event()
    asked = []

    def avery(prompt):
        asked.append(("avery", prompt))
        return {"status": {"awake": True}, "preferences": ["Fan on low"]}

    def morgan(prompt):
        asked.append(("morgan", prompt))
        if len(asked) == 4:
            complete.set()
        return {"attention": {"request": "Bring water", "at": "22:30:00", "request_id": "water"}}

    supervisor.register("avery", avery)
    supervisor.register("morgan", morgan)
    pushed, created = supervisor.receive_request("avery", "Please visit", at="22:29:00", urgent=True)
    assert created
    assert supervisor.alerts("avery")[0]["id"] == pushed
    assert supervisor.alerts("avery")[0]["delivery"] == "proactive"
    assert asked == []
    thread = threading.Thread(target=supervisor.run, args=(stop,), daemon=True)
    thread.start()
    try:
        assert complete.wait(2)
    finally:
        stop.set()
        thread.join(2)
    assert asked[:4] == [(patient, CHECK_IN_PROMPT) for patient in ("avery", "morgan") * 2]
    assert supervisor.latest_report("avery")["preferences"] == ["Fan on low"]
    assert [alert["delivery"] for alert in supervisor.alerts("morgan")] == ["check-in"]
    assert not supervisor.acknowledge("morgan", pushed)


def test_patient_agent_can_push_and_retry_with_its_own_identity():
    avery, morgan = make_demo_patients()
    request = {"request": "Could you adjust my pillow?", "request_id": "pillow", "urgent": False}
    first = morgan.nurse_action("patient/request", request)
    assert first["ok"] and morgan.nurse_action("patient/request", request) == first
    assert not avery.nurse_json()["alerts"]
    alert, = morgan.nurse_json()["alerts"]
    assert alert["request"] == request["request"] and alert["delivery"] == "proactive"
    assert sum(entry["text"] == request["request"] for entry in morgan.nurse_json()["history"]) == 1
    assert morgan.nurse_action("alert", {"id": first["id"], "action": "ack"})["ok"]
    assert morgan.nurse_json()["alerts"][0]["done"]


def eventually(predicate):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    assert predicate()


def test_independent_random_streams_can_start_another_shift_without_losing_requests(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    playback = ShiftPlayback(.005, .02, seed=7)
    avery, morgan = make_demo_patients(playback=playback)
    assert avery.nurse_json()["shift"]["state"] == "ready"
    assert "Penguin Cam" in avery.nurse_json()["shift"]["camera"]["name"]
    assert "Elephant Cam" in morgan.nurse_json()["shift"]["camera"]["name"]
    stop = threading.Event()
    worker = threading.Thread(target=playback.run, args=(stop, {"avery": avery, "morgan": morgan}))
    worker.start()
    try:
        assert not avery.nurse_json()["alerts"] and not morgan.nurse_json()["alerts"]
        assert avery.nurse_action("shift/start", {})["ok"]
        eventually(lambda: all(len(patient.nurse_json()["alerts"]) >= 2 for patient in (avery, morgan)))
        assert avery.nurse_action("shift/end", {})["ok"]
        assert morgan.nurse_json()["shift"]["state"] == "complete"
        first_shift = [[alert["id"] for alert in patient.nurse_json()["alerts"]] for patient in (avery, morgan)]
        assert morgan.nurse_action("shift/start", {})["ok"]
        assert avery.nurse_json()["shift"]["state"] == "running"
        eventually(lambda: all(len(patient.nurse_json()["alerts"]) > len(previous)
                               for patient, previous in zip((avery, morgan), first_shift)))
        assert avery.nurse_action("shift/end", {})["ok"]
        for patient, previous in zip((avery, morgan), first_shift):
            assert set(previous) <= {alert["id"] for alert in patient.nurse_json()["alerts"]}
    finally:
        stop.set()
        worker.join(2)
    assert not worker.is_alive()
    assert avery.nurse_json()["shift"]["state"] == "complete"
    for patient in (avery, morgan):
        assert len({alert["request"] for alert in patient.nurse_json()["alerts"]}) >= 2
        assert all(alert["review"] == "local_reviewed" and alert["priority_source"] == "local_rules"
                   and alert["priority"]["level"] in {"urgent", "soon", "routine"}
                   for alert in patient.nurse_json()["alerts"])
        assert not patient.proposal()


def test_supervisor_uses_ai_priority_and_queues_clarification_for_approval():
    def decide(request, profile, confirmed):
        if "undecided" in request:
            assert "window closed" in " ".join(profile["preferences"])
            assert not confirmed
            return {"priority": "soon", "action": "ask", "rationale": "The patient requested a comfort change.",
                    "recommendation": "Check whether they would like the fan.",
                    "steps": [{"question": "Would a fan on low help?", "request": "Patient requests a fan on low."}]}
        return {"priority": "urgent", "action": "notify", "rationale": "The patient said they need help now.",
                "recommendation": "Notify the nurse to review the request.", "steps": []}

    avery, morgan = make_demo_patients(playback=ShiftPlayback(), decide=decide)
    stop = threading.Event()
    worker = threading.Thread(target=avery.supervisor.run_reviews, args=(stop,))
    worker.start()
    try:
        assert avery.nurse_action("patient/request", {"request": "Patient asks for help now."})["ok"]
        assert morgan.nurse_action("patient/request", {"request": "Patient is undecided about room comfort."})["ok"]
        eventually(lambda: avery.nurse_json()["alerts"][0]["review"] == "ai_reviewed")
        eventually(morgan.proposal)
        assert avery.nurse_json()["alerts"][0]["priority"]["level"] == "urgent"
        assert avery.nurse_json()["alerts"][0]["priority_source"] == "claude"
        assert morgan.nurse_json()["alerts"][0]["priority"]["level"] == "soon"
        assert "fan" in morgan.nurse_json()["alerts"][0]["recommendation"]
        proposal = morgan.proposal()
        assert not avery.proposal()
        assert morgan.screen_json()["screen"] == "idle"
        assert morgan.nurse_action("proposal/approve", {"id": proposal["id"], "source": "ai"})["ok"]
        assert morgan.screen_json()["title"] == "Would a fan on low help?"
        assert choose(morgan, "left")["ok"]
        assert any(alert["review"] == "clarified" and alert["done"] for alert in morgan.nurse_json()["alerts"])
    finally:
        stop.set()
        worker.join(2)


def test_invalid_claude_decision_is_rejected_and_calls_still_bypass_review(monkeypatch):
    monkeypatch.setattr(nurse_ai, "_request_json", lambda *args: {"priority": "whenever"})
    with pytest.raises(ValueError):
        nurse_ai.decide_request("Water", {})
    avery, _ = make_demo_patients(playback=ShiftPlayback(), decide=nurse_ai.decide_request)
    stop = threading.Event()
    worker = threading.Thread(target=avery.supervisor.run_reviews, args=(stop,))
    worker.start()
    try:
        avery.nurse_action("patient/request", {"request": "Water"})
        eventually(lambda: avery.nurse_json()["alerts"][0]["review"] == "local_reviewed")
        assert "invalid draft" in avery.nurse_json()["alerts"][0]["ai_failure"]
        assert "invalid draft" in avery.nurse_json()["shift"]["feed"][0]["response"]
        avery.demo_action({"action": "call_nurse"})
        assert avery.nurse_json()["alerts"][0]["priority"]["level"] == "urgent"
        assert avery.nurse_json()["alerts"][1]["priority"]["level"] == "routine"
        assert avery.nurse_json()["alerts"][1]["priority_source"] == "local_rules"
    finally:
        stop.set()
        worker.join(2)


def test_shift_ranks_requests_without_a_claude_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    avery, _ = make_demo_patients(playback=ShiftPlayback())
    for request in (
        "A glass of water, not urgent.",
        "Could someone come soon to adjust my pillow?",
        "I need help right away to sit up.",
    ):
        assert avery.nurse_action("patient/request", {"request": request})["ok"]
    alerts = avery.nurse_json()["alerts"]
    assert [alert["priority"]["level"] for alert in alerts] == ["urgent", "soon", "routine"]
    assert all(alert["priority_source"] == "local_rules" for alert in alerts)
    assert all(alert["priority_basis"] for alert in alerts)


def test_supervisor_ranks_requests_with_fifo_within_priority_and_preserves_legacy_calls():
    avery, _ = make_demo_patients()
    avery.nurse_action("patient/request", {"request": "Water"})
    avery.nurse_action("patient/request", {"request": "Help soon", "time_sensitive": True})
    avery.nurse_action("patient/request", {"request": "Help now", "urgent": True, "time_sensitive": True})
    avery.demo_action({"action": "call_nurse"})
    alerts = avery.nurse_json()["alerts"]
    assert [alert["text"] for alert in alerts] == ["Help now", "Patient used the call signal", "Help soon", "Water"]
    assert [alert["priority"]["level"] for alert in alerts] == ["urgent", "urgent", "soon", "routine"]


def test_patient_agents_supply_distinct_profiles_and_publish_live_updates():
    avery, morgan = make_demo_patients(playback=ShiftPlayback())
    original = avery.nurse_json()["profile"]
    other = morgan.nurse_json()["profile"]
    assert original["last_meal"]["minutes_since"] == 85
    assert other["last_meal"]["minutes_since"] == 140
    assert original["vitals"]["current"] == original["vitals"]["samples"][-1]
    assert original["vitals"]["current"] != other["vitals"]["current"]
    assert original["habits"] != other["habits"]
    assert original["camera"]["url"] != other["camera"]["url"]
    avery.emit_random_request(random.Random(6))
    latest = avery.supervisor.latest_profile("avery")
    assert latest == avery.nurse_json()["profile"]
    assert latest["room"]["temp_c"] != original["room"]["temp_c"]
    assert latest["status"] == avery.nurse_json()["alerts"][0]["request"]
    assert len(latest["vitals"]["samples"]) == 4
    assert morgan.nurse_json()["profile"] == other


def test_all_care_topics_can_be_patient_requests_or_nurse_review_drafts():
    avery, morgan = make_demo_patients(playback=ShiftPlayback())
    assert {choice["id"] for choice in avery.nurse_json()["profile"]["care_choices"]} == set(CARE_CHOICES)
    for action, (_, question, request) in CARE_CHOICES.items():
        assert avery.nurse_action("simulate/agent-request", {"choice": action})["ok"]
        assert avery.nurse_action("check-in/suggest", {"choice": action})["ok"]
        proposal = avery.proposal()
        assert proposal["steps"] == [{"question": question, "request": request}]
        assert avery.screen_json()["screen"] == "idle"
        assert avery.nurse_action("proposal/dismiss", {"id": proposal["id"]})["ok"]
    assert len(avery.nurse_json()["alerts"]) == len(CARE_CHOICES)
    assert not morgan.nurse_json()["alerts"] and not morgan.proposal()


def test_shift_default_arrival_intervals_are_doubled():
    playback = ShiftPlayback()
    assert (playback.minimum_s, playback.maximum_s) == (8, 24)


def test_request_question_defaults_keep_two_alternatives_and_route_the_selected_branch(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    avery, morgan = make_demo_patients(playback=ShiftPlayback())
    alert_id = avery.nurse_action("patient/request", {"request": "The patient asked for a drink."})["id"]
    question = "Would you like water or juice?"
    draft = avery.nurse_action("alert/question/draft", {"id": alert_id, "question": question})
    assert draft["ok"] and draft["source"] == "fallback"
    assert "No Claude API key" in draft["fallback_reason"]
    assert [branch["label"] for branch in draft["branches"]] == ["Water", "Juice"]
    assert avery.screen_json()["screen"] == "idle"
    edited = [draft["branches"][0], {**draft["branches"][1], "next_step": "Check which juice the patient prefers."}]
    send = {"id": alert_id, "question": question, "branches": edited, "source": draft["source"]}
    assert not morgan.nurse_action("alert/question/send", send)["ok"]
    assert avery.nurse_action("alert/question/send", send)["ok"]
    assert not avery.nurse_action("alert/question/send", send)["ok"]
    assert avery.screen_json()["right"]["label"] == "Juice"
    assert avery.nurse_json()["alerts"][0]["review"] == "waiting_answer"
    assert choose(avery, "right")["ok"]
    alert = avery.nurse_json()["alerts"][0]
    assert alert["review"] == "answered" and not alert["done"]
    assert alert["question_reply"] == {"answer": "Juice", "next_step": edited[1]["next_step"], "source": "fallback"}


def test_request_question_uses_claude_when_available_and_falls_back_for_invalid_drafts(monkeypatch):
    def response(instructions, payload):
        assert payload["patient_request"] == "Patient requests a drink."
        assert payload["question"] == "Would you like water?"
        return [
            {"label": "Yes", "next_step": "Review the request for water."},
            {"label": "No", "next_step": "Ask what the patient would like instead."},
        ]

    monkeypatch.setattr(nurse_ai, "_request_json", response)
    avery, _ = make_demo_patients(playback=ShiftPlayback())
    alert_id = avery.nurse_action("patient/request", {"request": "Patient requests a drink."})["id"]
    draft = avery.nurse_action("alert/question/draft", {"id": alert_id, "question": "Would you like water?"})
    assert draft["source"] == "ai"
    assert draft["branches"][1]["next_step"] == "Ask what the patient would like instead."
    assert avery.screen_json()["screen"] == "idle"
    monkeypatch.setattr(nurse_ai, "_request_json", lambda *_: [{"label": "Only one answer", "next_step": "Review."}])
    assert nurse_ai.draft_request_branches("Would you like water?", "", {})["source"] == "fallback"


def test_request_question_reports_auth_failure_without_provider_details(monkeypatch):
    class RejectedKey(Exception):
        status_code = 401
        body = {"error": {"message": "Invalid x-api-key: private-provider-detail"}}

    def reject(*args):
        raise RejectedKey()

    monkeypatch.setattr(nurse_ai, "_request_json", reject)
    draft = nurse_ai.draft_request_branches("Would you like water?", "Water", {})
    assert draft["source"] == "fallback"
    assert "rejected the API key" in draft["fallback_reason"]
    assert "private-provider-detail" not in str(draft)


def test_claude_key_prompt_requires_a_terminal_and_loads_only_into_the_process(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(nurse_ai.sys.stdin, "isatty", lambda: False)
    with pytest.raises(ValueError, match="interactive terminal"):
        nurse_ai.prompt_for_key()
    monkeypatch.setattr(nurse_ai.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(nurse_ai.getpass, "getpass", lambda prompt: "  test-fixture-value  ")
    nurse_ai.prompt_for_key()
    assert nurse_ai.os.environ["ANTHROPIC_API_KEY"] == "test-fixture-value"
