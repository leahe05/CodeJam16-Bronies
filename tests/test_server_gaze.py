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


def test_web_mode_end_to_end_room_controls_by_gaze_and_browser_eyebrows():
    """Browser says: looking at the left column, then a short eyebrow raise -> Room Controls opens."""
    from lookat.awake import AwakeDetector
    from main import web_step
    now = [0.0]
    live, client = make(now)
    awake = AwakeDetector()
    for i in range(8):                                   # 1.6 s of looking left: dwell -> locked
        now[0] = i * 0.2
        client.post("/api/gaze", json={"spot": "left", "face": True, "eyes_open": True})
        web_step(live, awake, now[0])
    assert live.machine.view() == {"spot": "left", "state": "asking"}
    now[0] += 0.2
    client.post("/api/gaze", json={"spot": "left", "face": True, "events": ["confirm"]})
    web_step(live, awake, now[0])
    assert live.sm.kind == "room"


def test_web_mode_long_raise_calls_the_nurse_and_no_face_means_no_gaze():
    from lookat.awake import AwakeDetector
    from main import web_step
    now = [1.0]
    live, client = make(now)
    client.post("/api/gaze", json={"spot": "middle", "face": True, "events": ["emergency"]})
    web_step(live, AwakeDetector(), 1.0)
    assert live.screen_json()["overlay"]["kind"] == "call_nurse"
    now[0] = 2.0
    client.post("/api/gaze", json={"spot": "left", "face": False})
    assert web_step(live, AwakeDetector(), 2.0) is None


def test_patient_page_opens_with_web_gaze_when_main_uses_it():
    from fastapi.testclient import TestClient
    live = Live(Room(lamp_ip="", fan_ip="", plug_factory=SimPlug))
    c = TestClient(make_app(live))
    assert c.get("/patient", follow_redirects=False).status_code == 200          # camera mode: as is
    live.gaze_mode = "web"
    r = c.get("/patient?theme=day", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/patient?gaze=web&theme=day"
    assert c.get("/patient?gaze=web", follow_redirects=False).status_code == 200


def test_page_scripts_are_rechecked_so_browsers_never_show_an_old_screen():
    live, client = make([0.0])
    r = client.get("/web/patient-ui.js")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache" and 'class="eyes"' in r.text
