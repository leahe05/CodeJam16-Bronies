"""M5-1 hardening: LookAt must survive a 10-minute demo with no one touching the laptop."""
import threading
import time
from types import SimpleNamespace

import pytest

import main
from lookat import config, plugs, speech
from lookat.devices import Room, SimPlug
from lookat.live import FACE_GRACE_S, Live
from lookat.server import LastGood, safe


@pytest.fixture(autouse=True)
def _tmp_log(tmp_path, monkeypatch):
    monkeypatch.setattr("lookat.state.LOG_PATH", str(tmp_path / "events.jsonl"))


def make_live():
    return Live(Room(lamp_ip="", fan_ip="", plug_factory=SimPlug), now=lambda: 0.0)


def lock_on(live, spot, t=0.0):
    """Look at spot until it is ASKING (locked). Returns the time after."""
    for _ in range(4):
        live.frame(t, spot, [], True)
        t += 0.4
    assert live.machine.view()["state"] == "asking"
    return t


# ----- face lost mid-selection ----------------------------------------------
def test_face_blip_keeps_the_highlight():
    live = make_live()
    t = lock_on(live, "left") - 0.4 + 0.03                 # just after the last face frame
    live.frame(t, None, [], face=False)                    # one dropped frame
    live.frame(t + FACE_GRACE_S / 2, None, [], face=False)
    assert live.machine.view()["state"] == "asking"
    live.frame(t + FACE_GRACE_S * 0.8, "left", ["confirm"], True)
    assert live.chosen == "Room Controls", "the choice still works right after a blip"


def test_no_choice_on_a_frame_without_a_face():
    live = make_live()
    t = lock_on(live, "left")
    out = live.frame(t, None, ["confirm"], face=False)     # raise seen while the face is lost
    assert out == [] and live.chosen is None


def test_face_gone_cancels_and_return_needs_a_new_dwell():
    live = make_live()
    t = lock_on(live, "left")
    live.frame(t, None, [], face=False)
    live.frame(t + FACE_GRACE_S + 0.1, None, [], face=False)
    assert live.machine.view() is None or live.machine.view()["state"] != "asking"
    out = live.frame(t + FACE_GRACE_S + 0.2, "left", ["confirm"], True)   # back, raise at once
    assert out == [] and live.chosen is None                              # no stray select


# ----- plug offline and back --------------------------------------------------
class FakeDev:
    def __init__(self):
        self.is_on, self.fail, self.hang = False, False, False

    async def update(self):
        if self.hang:
            import asyncio
            await asyncio.sleep(60)
        if self.fail:
            raise ConnectionError("unplugged")

    async def turn_on(self):
        self.is_on = True

    async def turn_off(self):
        self.is_on = False

    async def disconnect(self):
        pass


def wait_for(cond, limit=6.0):
    end = time.monotonic() + limit
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def fake_plug(monkeypatch):
    dev = FakeDev()
    tries = []

    async def connect(self):
        tries.append(1)
        self._next_try = time.monotonic() + plugs.RETRY_S
        if dev.fail:
            self._offline(ConnectionError("unplugged"))
            return False
        self._dev, self.online, self._warned = dev, True, False
        self._next_check = time.monotonic() + plugs.HEALTH_S
        return True

    monkeypatch.setattr(plugs.PlugWorker, "_connect", connect)
    monkeypatch.setattr(plugs, "HEALTH_S", 0.2)
    monkeypatch.setattr(plugs, "RETRY_S", 0.2)
    monkeypatch.setattr(plugs, "CALL_LIMIT_S", 0.5)
    w = plugs.PlugWorker("fan", "10.0.0.9")
    yield w, dev, tries
    w.close()


def test_unplugged_plug_shows_offline_then_comes_back(fake_plug):
    w, dev, _ = fake_plug
    assert wait_for(lambda: w.online)
    dev.fail = True                                       # unplugged, nobody pressed anything
    assert wait_for(lambda: not w.online), "health check must notice"
    dev.fail = False                                      # plugged back in
    assert wait_for(lambda: w.online), "must reconnect by itself"
    w.send("on")
    assert wait_for(lambda: w.is_on)


def test_a_plug_that_hangs_never_blocks(fake_plug):
    w, dev, _ = fake_plug
    assert wait_for(lambda: w.online)
    dev.hang = True
    t = time.monotonic()
    w.send("on")                                          # send never blocks the caller
    assert time.monotonic() - t < 0.1
    assert wait_for(lambda: not w.online, limit=4), "hung call -> time limit -> offline"
    dev.hang = False
    assert wait_for(lambda: w.online)
    assert w._thread.is_alive()


class DownPlug(SimPlug):
    """A real plug that is offline (what PlugWorker.status() reports)."""
    def status(self):
        return {"on": False, "online": False, "simulated": False, "last_ms": None}


def test_room_state_reports_plug_offline_for_the_pages():
    room = Room(lamp_ip="", fan_ip="", plug_factory=lambda n, ip: (DownPlug if n == "fan" else SimPlug)(n, ip))
    assert room.state()["fan"]["online"] is False


# ----- voice failure falls back silently ---------------------------------------
def test_edge_voice_falls_back_and_survives_errors(monkeypatch):
    v = speech.EdgeVoice()
    said = []
    v._fallback = said.append
    calls = []

    def play(path, gen):
        calls.append(path)
        if len(calls) == 1:
            raise OSError("audio device gone")
        return True

    monkeypatch.setattr(v, "_file", lambda text: None if text == "offline" else "x.mp3")
    monkeypatch.setattr(v, "_play", play)
    v("first")             # _play throws: logged, the worker lives on
    v("offline")           # no audio file: the laptop voice says it
    v("third")
    assert wait_for(lambda: len(calls) == 2 and said == ["offline"], limit=3)
    assert v.failures == 1
    v.close()


def test_broken_mp3_is_said_by_laptop_voice(monkeypatch, tmp_path):
    v = speech.EdgeVoice()
    said = []
    v._fallback = said.append
    bad = tmp_path / "bad.mp3"
    bad.write_bytes(b"not audio")
    monkeypatch.setattr(v, "_file", lambda text: str(bad))
    monkeypatch.setattr(v, "_play", lambda path, gen: False)
    v("Lamp on.")
    assert wait_for(lambda: said == ["Lamp on."], limit=3)
    assert not bad.exists(), "a broken cached file is removed so it is fetched again"
    v.close()


def test_offline_skips_the_network_wait(monkeypatch):
    v = speech.EdgeVoice()
    v._offline_until = time.monotonic() + 60
    assert v._file("a line never cached " + str(time.time())) is None
    assert "offline" in v.status()
    v.close()


def test_dead_laptop_voice_restarts(monkeypatch):
    s = speech.Speech(enabled=False)
    s.enabled = True
    started = []

    class Dead:
        def poll(self):
            return 1

    monkeypatch.setattr(speech.sys, "platform", "win32")
    s.proc = Dead()
    monkeypatch.setattr(s, "_start", lambda: started.append(1))
    s("hello")
    assert started == [1] and s.proc is None


# ----- server keeps serving -----------------------------------------------------
def test_api_serves_last_good_screen_when_building_it_fails():
    backend = SimpleNamespace(lock=threading.RLock())
    good = LastGood(backend)
    assert good.get("screen", lambda: {"title": "ok", "_x": 1}) == {"title": "ok"}

    def broken():
        raise RuntimeError("bug")
    assert good.get("screen", broken) == {"title": "ok"}
    assert good.get("panel", broken)["ok"] is False


def test_post_bug_answers_ok_false():
    def bug():
        raise RuntimeError("boom")
    assert safe(bug) == {"ok": False, "error": "server error (logged)"}


def test_camera_loop_crash_is_restarted_and_pages_keep_ticking():
    live = make_live()
    runs = []

    def run(args, live):
        runs.append(1)
        if len(runs) < 3:
            raise RuntimeError("Camera 0 did not open")

    main.supervise(run, SimpleNamespace(), live, retry_s=0.05, sleep=lambda s: time.sleep(0.01))
    assert len(runs) == 3 and live.camera_ok is False


def test_reopen_camera(monkeypatch):
    released = []
    tracker = SimpleNamespace(cap=SimpleNamespace(release=lambda: released.append(1)))
    monkeypatch.setattr("lookat.vision._open_camera", lambda i: "new cap")
    assert main.reopen_camera(tracker, 0) and tracker.cap == "new cap" and released == [1]

    def gone(i):
        raise RuntimeError("Camera 0 did not open")
    monkeypatch.setattr("lookat.vision._open_camera", gone)
    assert main.reopen_camera(tracker, 0) is False


# ----- status line ----------------------------------------------------------------
def test_status_line_and_printer():
    live = Live(Room(lamp_ip="", fan_ip="", plug_factory=lambda n, ip: (DownPlug if n == "fan" else SimPlug)(n, ip)),
                now=lambda: 0.0)
    voice = SimpleNamespace(status=lambda: "Ava")
    line = main.status_line(live, voice)
    assert "camera LOST" in line and "face no" in line and "lamp simulated" in line
    assert "fan OFFLINE" in line and "voice Ava" in line
    got = []
    stop = main.start_status(live, voice, every=0.05, out=got.append)
    assert wait_for(lambda: len(got) >= 2, limit=2)
    stop.set()
