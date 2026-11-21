from lookat.nurse_link import NurseLink
from lookat.supervisor_demo import make_demo_patients


def test_awareness_handles_blinks_and_stale_or_missing_observations():
    link = NurseLink(None)
    opened = {"eyeBlinkLeft": 0.1, "eyeBlinkRight": 0.1}
    closed = {"eyeBlinkLeft": 0.9, "eyeBlinkRight": 0.9}
    link.observe_eyes(0, opened)
    link.observe_eyes(1, opened)
    link.observe_eyes(1.1, closed)
    assert link.snapshot(1.1)["awareness"]["state"] == "awake"
    assert link.snapshot(4)["awareness"]["state"] == "unknown"

    for second in range(5, 36):
        link.observe_eyes(second, closed)
    assert link.snapshot(35)["awareness"]["state"] == "asleep"
    link.observe_eyes(36, None)
    assert link.snapshot(36)["awareness"]["state"] == "unknown"
    assert link.snapshot(36, "open")["awareness"] == {"state": "awake", "source": "demo"}
    assert [entry["text"] for entry in link.interaction_history()] == [
        "Patient appears awake.", "Patient appears asleep."
    ]


def test_demo_patients_keep_independent_status_across_reset():
    avery, morgan = make_demo_patients()
    assert avery.nurse_action("presence", {"present": False})["ok"]
    assert morgan.nurse_action("presence", {"present": None})["ok"]
    assert avery.nurse_json()["bedside"]["visitors_present"] is False
    assert morgan.nurse_json()["bedside"]["visitors_present"] is None
    assert morgan.demo_action({"action": "call_nurse"})["ok"]
    assert morgan.nurse_json()["alerts"][0]["source"] == "patient"
    assert morgan.demo_action({"action": "reset"})["ok"]
    data = morgan.nurse_json()
    assert data["patient"]["name"] == "Morgan"
    assert data["bedside"]["awareness"] == {"state": "awake", "source": "demo"}
    assert not data["alerts"]
