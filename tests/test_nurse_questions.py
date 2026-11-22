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
    assert call[0]["question"] == "Do you need help right away?"
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
    monkeypatch.setattr(ask, "fallback_questions", lambda a: calls.append(a) or [])
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
