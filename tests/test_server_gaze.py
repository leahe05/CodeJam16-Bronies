"""Browser eye tracking: POST /api/gaze -> Live.web_spot() (main.py --gaze web)."""
from fastapi.testclient import TestClient

from lookat.devices import Room, SimPlug
from lookat.live import Live
from lookat.server import make_app


def make(now):
    live = Live(Room(plug_factory=SimPlug), patient="t", now=lambda: now[0])
    return live, TestClient(make_app(live))


def test_web_gaze_zone_reaches_live_and_goes_stale():
    now = [100.0]
    live, client = make(now)
    assert client.post("/api/gaze", json={"spot": "left", "x": 0.1, "y": 0.4}).json()["ok"]
    assert live.web_spot(100.2) == "left"
    assert live.web_spot(100.0 + Live.WEB_GAZE_FRESH_S + 0.1) is None      # page closed / face lost


def test_web_gaze_rejects_bad_zones_and_accepts_null():
    now = [5.0]
    live, client = make(now)
    assert not client.post("/api/gaze", json={"spot": "up"}).json()["ok"]
    assert client.post("/api/gaze", json={"spot": None}).json()["ok"] and live.web_spot(5.0) is None


def test_fake_backend_has_no_web_gaze():
    from lookat.fake_api import Fake
    from lookat.fake_api import make_app as fake_app
    assert not TestClient(fake_app(Fake())).post("/api/gaze", json={"spot": "left"}).json()["ok"]
