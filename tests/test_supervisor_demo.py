import threading

from lookat import nurse_ai
from lookat.nurse_patient_profile import CARE_CHOICES
from lookat.nurse_shift import SHIFT_UPDATES, ShiftPlayback, ShiftUpdate
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


def test_shift_streams_route_requests_and_keep_patient_specific_questions_queued():
    playback = ShiftPlayback()
    avery, morgan = make_demo_patients(playback=playback)
    patients = {"avery": avery, "morgan": morgan}
    assert not avery.proposal() and not morgan.proposal()

    for event in SHIFT_UPDATES:
        patients[event.patient].receive_shift_update(event)

    assert "water" in avery.nurse_json()["alerts"][0]["request"]
    water_id = avery.nurse_json()["alerts"][0]["id"]
    assert avery.nurse_action("alert", {"id": water_id, "action": "ack"})["ok"]
    assert avery.nurse_json()["shift"]["feed"][1]["response"] == "Nurse acknowledged · On my way"
    proactive = morgan.nurse_json()["alerts"]
    assert [alert["priority"]["level"] for alert in proactive] == ["urgent", "routine"]
    assert "massage" in proactive[1]["request"]
    assert not avery.proposal() and not morgan.proposal()
    assert "Waiting" in avery.nurse_json()["shift"]["feed"][2]["response"]

    avery.supervisor.check_patients()
    assert {alert["delivery"] for alert in morgan.nurse_json()["alerts"]} == {"check-in", "proactive"}
    assert [alert["priority"]["level"] for alert in morgan.nurse_json()["alerts"]] == ["urgent", "soon", "routine"]
    morgan_requests = [alert["request"] for alert in morgan.nurse_json()["alerts"]]
    assert any("brief" in request for request in morgan_requests)
    assert any("sitting upright" in request for request in morgan_requests)
    assert avery.proposal()["evidence"] == morgan.proposal()["evidence"]
    assert "window" in avery.proposal()["steps"][0]["question"]
    assert "fan" in morgan.proposal()["steps"][0]["question"]
    assert avery.nurse_json()["shift"]["queued_questions"] == 2
    draft = avery.proposal()
    assert avery.nurse_action("proposal/dismiss", {"id": draft["id"]})["ok"]
    assert "sitting upright" in avery.proposal()["steps"][0]["question"]
    assert all("massage" not in entry["status"] for entry in avery.nurse_json()["shift"]["feed"])


def test_shift_starts_on_request_and_completes_after_both_feeds():
    playback = ShiftPlayback((ShiftUpdate(0, "avery", "Avery update"),
                              ShiftUpdate(0, "morgan", "Morgan update")))
    avery, morgan = make_demo_patients(playback=playback)
    assert avery.nurse_json()["shift"]["state"] == "ready"
    assert "Penguin Cam" in avery.nurse_json()["shift"]["camera"]["name"]
    assert "Elephant Cam" in morgan.nurse_json()["shift"]["camera"]["name"]
    assert avery.nurse_action("shift/start", {})["ok"]
    assert morgan.nurse_json()["shift"]["state"] == "running"
    playback.run(threading.Event(), {"avery": avery, "morgan": morgan})
    assert avery.nurse_json()["shift"]["state"] == "complete"
    assert [entry["status"] for entry in avery.nurse_json()["shift"]["feed"]] == ["Avery update"]
    assert [entry["status"] for entry in morgan.nurse_json()["shift"]["feed"]] == ["Morgan update"]


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
    avery.receive_shift_update(ShiftUpdate(4, "avery", "Requested water", "proactive", "Bring water", temperature=24))
    latest = avery.supervisor.latest_profile("avery")
    assert latest == avery.nurse_json()["profile"]
    assert latest["room"]["temp_c"] == 24
    assert latest["status"] == "Requested water"
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
