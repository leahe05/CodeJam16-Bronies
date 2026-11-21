"""The patient's room: real lamp (plug 1), real fan acting as the AC (plug 2, or
simulated), and a demo temperature sensor.

    room = Room()                                  # plugs from config.LAMP_IP / FAN_IP
    room.execute("fan", "on", by="patient")        -> "Fan ON"
    room.tick(now)                                 # every frame: temperature model
    room.set_temp(24.5); room.set_setpoint(18, by="nurse")
    room.state()

Temperature (honest wording for judges: a demo sensor value; an ESP32 + DHT22
would replace it): the demo panel sets the room temperature ("ambient"). While the
fan runs the room cools (COOL_RATE per second, down to FLOOR_C, or to the setpoint
when the nurse applied one); with the fan off it drifts back toward ambient.
Every action from anyone goes through execute(..., by=...) and is logged.
"""
import threading
import time
from typing import Callable, Optional

from lookat import config
from lookat.state import log_event

DEVICES = ("lamp", "fan")
COOL_RATE = 0.15     # °C per second while the fan runs (fast, so the demo shows it)
WARM_RATE = 0.03     # °C per second back toward ambient with the fan off
FLOOR_C = 19.0
START_C = 22.0


class Room:
    def __init__(self, lamp_ip: Optional[str] = None, fan_ip: Optional[str] = None, plug_factory=None):
        if plug_factory is None:
            from lookat.plugs import PlugWorker as plug_factory
        self.lamp = plug_factory("lamp", config.LAMP_IP if lamp_ip is None else lamp_ip)
        self.fan = plug_factory("fan", config.FAN_IP if fan_ip is None else fan_ip)
        self.lock = threading.Lock()
        self.temp_c = START_C
        self.ambient_c = START_C
        self.setpoint_c = START_C
        self.thermostat = False          # True after the nurse applies a setpoint
        self.on_action: list = []        # callbacks(device, cmd, by, result) e.g. memory
        self._last_tick: Optional[float] = None
        self._thermo_sent = (None, -1e9)   # last thermostat command and when

    def plug(self, device: str):
        if device not in DEVICES:
            raise ValueError(f"unknown device {device!r}")
        return getattr(self, device)

    def execute(self, device: str, cmd: str, by: str) -> str:
        """The one way anyone changes the room. Returns a short result text."""
        p = self.plug(device)
        if cmd not in ("on", "off", "toggle", "flash"):
            raise ValueError(f"unknown command {cmd!r}")
        if device == "fan" and by != "thermostat":
            self.thermostat = False      # a person took control of the fan
        p.send(cmd)
        result = f"{device.capitalize()} {cmd.upper()}" + (" (simulated)" if p.simulated else "")
        log_event("action", device=device, cmd=cmd, by=by, result=result)
        for cb in list(self.on_action):
            try:
                cb(device, cmd, by, result)
            except Exception:   # noqa: BLE001 - a hook must never break the room
                pass
        return result

    def set_temp(self, temp_c: float) -> None:
        """Demo panel: the room is now this warm (and stays near it with the fan off)."""
        t = float(temp_c)
        with self.lock:
            self.temp_c = self.ambient_c = t
        log_event("temp_set", temp_c=t)

    def set_setpoint(self, setpoint_c: float, by: str, apply: bool = False) -> None:
        """The AC setting. apply=True runs the fan as a thermostat toward it."""
        sp = float(setpoint_c)
        with self.lock:
            self.setpoint_c = sp
            self.thermostat = apply
        log_event("setpoint", setpoint_c=sp, by=by, applied=apply)

    def tick(self, now: Optional[float] = None) -> None:
        now = time.monotonic() if now is None else now
        with self.lock:
            dt = 0.0 if self._last_tick is None else max(0.0, min(now - self._last_tick, 1.0))
            self._last_tick = now
            fan_on = self.fan.is_on
            if fan_on:
                target = max(FLOOR_C, self.setpoint_c) if self.thermostat else FLOOR_C
                self.temp_c = max(target, self.temp_c - COOL_RATE * dt) if self.temp_c > target else self.temp_c
            elif self.temp_c < self.ambient_c:
                self.temp_c = min(self.ambient_c, self.temp_c + WARM_RATE * dt)
            temp, sp, thermostat = self.temp_c, self.setpoint_c, self.thermostat
        if thermostat:   # simple thermostat with a small band, so the plug doesn't chatter
            want = "on" if (not fan_on and temp > sp + 0.3) else ("off" if fan_on and temp <= max(FLOOR_C, sp) else None)
            # a real plug reports its new state a moment later: don't resend every frame
            if want and (want != self._thermo_sent[0] or now - self._thermo_sent[1] > 3.0):
                self._thermo_sent = (want, now)
                self.execute("fan", want, by="thermostat")

    def state(self) -> dict:
        with self.lock:
            temp, sp, ambient = round(self.temp_c, 1), self.setpoint_c, self.ambient_c
        return {"temp_c": temp, "setpoint_c": sp, "ambient_c": ambient,
                "lamp_on": self.lamp.is_on, "fan_on": self.fan.is_on,
                "lamp": self.lamp.status(), "fan": self.fan.status()}

    def close(self) -> None:
        for p in (self.lamp, self.fan):
            try:
                p.close()
            except Exception:   # noqa: BLE001
                pass


class SimPlug:
    """In-memory plug with the PlugWorker API (tests, and fan without a second plug)."""

    def __init__(self, name: str, ip: str = ""):
        self.name, self.ip, self.simulated, self.online = name, ip, True, True
        self.is_on, self.last_ms, self.sent = False, 0.0, []

    def send(self, cmd: str) -> None:
        self.sent.append(cmd)
        if cmd in ("on", "off"):
            self.is_on = cmd == "on"
        elif cmd == "toggle":
            self.is_on = not self.is_on

    def status(self) -> dict:
        return {"on": self.is_on, "online": True, "simulated": True, "last_ms": 0.0}

    def close(self) -> None:
        pass


if __name__ == "__main__":
    # Real plugs from LAMP_IP / FAN_IP (FAN_IP empty = simulated fan).
    room = Room()
    print(f"lamp {room.lamp.ip or '(simulated)'}   fan {room.fan.ip or '(simulated)'}")
    time.sleep(6)
    ok = True
    for device, cmd in (("lamp", "on"), ("lamp", "off"), ("fan", "on")):
        print(room.execute(device, cmd, by="selftest"))
        time.sleep(1.5)
        p = room.plug(device)
        print(f"   is_on={p.is_on}  {p.last_ms} ms  online={p.online}")
        ok = ok and p.online and (p.last_ms or 0) < 1000
    room.set_temp(24.5)
    t0 = time.monotonic()
    while time.monotonic() - t0 < 20:
        room.tick()
        time.sleep(0.5)
        if int((time.monotonic() - t0) * 2) % 8 == 0:
            print(f"   {time.monotonic() - t0:4.1f}s  {room.state()['temp_c']} °C")
    falling = room.state()["temp_c"] < 24.0
    room.execute("fan", "off", by="selftest")
    time.sleep(1.5)
    room.close()
    print("temperature falls with the fan:", falling)
    print("PASS" if ok and falling else "FAIL (see PLAN.md R1 network check)")
