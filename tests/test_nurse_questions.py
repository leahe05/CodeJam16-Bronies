"""Ready-made nurse questions (POST /api/nurse/questions): chosen by the open request, cached per request."""
from fastapi.testclient import TestClient

from lookat.ask import fallback_questions
from lookat.devices import Room, SimPlug
from lookat.live import Live
from lookat.server import make_app


def live_without_ai(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return Live(Room(lamp_ip="", fan_ip="", plug_factory=SimPlug))


def test_fallback_questions_follow_the_request():
    call = fallback_questions({"kind": "call", "text": "Patient used the call signal"})
    assert call[0]["options"] == ["I need something", "I am uncomfortable"]     # broad first
    blanket = fallback_questions({"kind": "page", "text": "Feels cold, would like a blanket."})
    assert "blanket" in blanket[0]["question"] and len(blanket[1]["options"]) == 2
    assert len(fallback_questions(None)) == 2


def test_questions_endpoint_uses_the_newest_open_request_and_caches(monkeypatch):
    live = live_without_ai(monkeypatch)
    live.add_alert("page", "Feels cold, would like a blanket.", reason="Room 19 °C")
    c = TestClient(make_app(live))
    r = c.post("/api/nurse/questions", json={}).json()
    assert r["ok"] and r["source"] == "fallback" and r["about"] == "Feels cold, would like a blanket."
    assert [len(q["options"]) for q in r["questions"]] == [2, 2]
    calls = []
    import lookat.ask as ask
    monkeypatch.setattr(ask, "fallback_questions", lambda *a: calls.append(a) or [])
    assert c.post("/api/nurse/questions", json={}).json()["questions"] == r["questions"]   # cached
    assert calls == []


def test_a_new_request_gets_new_questions(monkeypatch):
    live = live_without_ai(monkeypatch)
    c = TestClient(make_app(live))
    live.add_alert("page", "Feels cold, would like a blanket.")
    first = c.post("/api/nurse/questions", json={}).json()
    live.add_alert("call", "Patient used the call signal", urgency="urgent")
    second = c.post("/api/nurse/questions", json={}).json()
    assert second["about"] == "Patient used the call signal" and second["questions"] != first["questions"]


def test_after_an_answer_the_suggestions_follow_up(monkeypatch):
    live = live_without_ai(monkeypatch)
    c = TestClient(make_app(live))
    live.add_alert("page", "Feels cold, would like a blanket.")
    first = c.post("/api/nurse/questions", json={}).json()
    qid = live.send_question("Are you feeling too warm or too cold?", ["Too warm", "Too cold"], live.now())
    live.conversation.append({"from": "patient", "kind": "answer", "text": "Too warm", "qid": qid})
    after = c.post("/api/nurse/questions", json={}).json()
    assert after["after"] == "Too warm" and after["questions"] != first["questions"]
    assert "fan" in after["questions"][0]["question"].lower()


def test_fallback_never_repeats_the_question_just_asked():
    qs = fallback_questions({"kind": "page", "text": "wants water"}, asked="Would you like some water now?")
    assert all(q["question"] != "Would you like some water now?" for q in qs) and len(qs) == 2


def test_ai_questions_never_offer_three_alternatives(monkeypatch):
    from lookat import nurse_ai
    drafts = iter([
        [{"question": "Is it pain, breathing, or something else?", "options": ["Pain", "Breathing"]},
         {"question": "Do you need help now?", "options": ["Yes", "No"]}],
        [{"question": "Do you need something, or are you uncomfortable?", "options": ["I need something", "I am uncomfortable"]},
         {"question": "Do you need help right away?", "options": ["Yes", "No"]}],
    ])
    monkeypatch.setattr(nurse_ai, "_request_json", lambda instruction, payload: next(drafts))
    out = nurse_ai.suggest_questions({"patient_request": "Patient used the call signal"})
    assert out[0]["options"] == ["I need something", "I am uncomfortable"]     # the 3-option draft was redone
