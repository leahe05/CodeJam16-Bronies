"""One Kasa smart plug per PlugWorker (python-kasa 0.12, KLAP plugs like the HS103).

    lamp = PlugWorker("lamp", "172.20.10.2")   # "" -> SIMULATED plug, same API
    lamp.send("on")      # never blocks: the command runs in the plug's own thread
    lamp.is_on, lamp.online, lamp.simulated, lamp.last_ms

Each worker has its own thread + asyncio loop. Credentials come from the
KASA_USER / KASA_PASSWORD environment variables (never printed). After a TP-Link password
change, a plug that hasn't picked up the new password yet still accepts the old one:
KASA_OLD_PASSWORD (optional) is tried when a plug refuses KASA_PASSWORD. An offline plug
logs "PLUG <name> OFFLINE", retries every RETRY_S and never crashes anything. An online plug is
re-read every HEALTH_S, so one that is unplugged (or switched from the phone) shows the truth
within seconds; every plug call has a time limit, so a plug that stops answering never hangs.
Self-test: py -3.11 -m lookat.plugs --ip 172.20.10.2
"""
import asyncio
import os
import queue
import threading
import time
from typing import Callable, Optional

from lookat.config import saved_env   # noqa: F401 - re-exported: plugs.saved_env
from lookat.state import log_event

COMMANDS = ("on", "off", "toggle", "flash")
RETRY_S = 5.0
TIMEOUT_S = 5
HEALTH_S = 10.0          # re-read an online plug this often (unplugged -> offline, phone -> state)
CALL_LIMIT_S = 25.0      # hard limit for one call: a connect may try 2 passwords x (5 s find + 5 s login)


class PlugWorker:
    def __init__(self, name: str, ip: str, on_change: Optional[Callable[[str, bool], None]] = None):
        self.name = name
        self.ip = (ip or "").strip()
        self.simulated = not self.ip
        self.is_on = False
        self.online = self.simulated
        self.last_ms: Optional[float] = None
        self.on_change = on_change
        self._q: "queue.Queue[str]" = queue.Queue()
        self._dev = None
        self._next_try = 0.0
        self._next_check = 0.0
        self._warned = False
        self._stop = False
        self._thread = threading.Thread(target=self._run, name=f"plug-{name}", daemon=True)
        self._thread.start()

    def send(self, cmd: str) -> None:
        if cmd not in COMMANDS:
            raise ValueError(f"unknown plug command {cmd!r}")
        self._q.put(cmd)

    def close(self) -> None:
        self._stop = True
        self._q.put("__stop__")
        self._thread.join(timeout=TIMEOUT_S + 2)

    def status(self) -> dict:
        return {"on": self.is_on, "online": self.online, "simulated": self.simulated, "last_ms": self.last_ms}

    # ----- worker thread -------------------------------------------------
    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            if not self.simulated:
                self._guarded(loop, self._connect())
            while not self._stop:
                try:
                    cmd = self._q.get(timeout=1.0)
                except queue.Empty:
                    cmd = None
                if cmd == "__stop__":
                    break
                if cmd is not None:
                    self._guarded(loop, self._do(cmd))
                elif self.simulated:
                    continue
                elif self._dev is None:
                    if time.monotonic() >= self._next_try:
                        self._guarded(loop, self._connect())
                elif time.monotonic() >= self._next_check:
                    self._guarded(loop, self._check())
        finally:
            if self._dev is not None:
                try:
                    loop.run_until_complete(self._dev.disconnect())
                except Exception:
                    pass
            loop.close()

    def _guarded(self, loop, coro) -> None:
        """Run one plug call with a hard time limit. A timeout or any bug drops the session and
        marks the plug offline (it reconnects by itself); the worker thread never dies."""
        try:
            loop.run_until_complete(asyncio.wait_for(coro, CALL_LIMIT_S))
        except Exception as e:   # noqa: BLE001
            dev, self._dev = self._dev, None
            if dev is not None:
                try:
                    loop.run_until_complete(asyncio.wait_for(dev.disconnect(), 2))
                except Exception:   # noqa: BLE001
                    pass
            self._next_try = time.monotonic() + RETRY_S
            self._offline(e)

    async def _check(self) -> None:
        """Health check: re-read the plug. Raises if it stopped answering (-> _guarded)."""
        self._next_check = time.monotonic() + HEALTH_S
        await self._dev.update()
        self.online = True
        self._set(bool(self._dev.is_on))

    async def _connect(self) -> bool:
        from kasa import Discover
        self._next_try = time.monotonic() + RETRY_S
        user, password = saved_env("KASA_USER"), saved_env("KASA_PASSWORD")
        if not (user and password) and not self._warned:
            print(f"PLUG {self.name}: KASA_USER / KASA_PASSWORD are not saved. "
                  'Run setx KASA_USER "..." and setx KASA_PASSWORD "..." once.')
        dev = None
        try:
            old = saved_env("KASA_OLD_PASSWORD")
            tries = [password] + ([old] if old and old != password else [])
            for i, pw in enumerate(tries):
                try:
                    dev = await Discover.discover_single(self.ip, discovery_timeout=TIMEOUT_S, timeout=TIMEOUT_S,
                                                         username=user, password=pw)
                    if dev is None:
                        raise ConnectionError("no answer")
                    await dev.update()
                    if i:
                        print(f"PLUG {self.name}: accepted KASA_OLD_PASSWORD (it hasn't picked up the new one yet)")
                    break
                except Exception as e:   # noqa: BLE001
                    if "Authentication" not in type(e).__name__ or i == len(tries) - 1:
                        raise
                    if dev is not None:   # refused: close this session, try the old password
                        try:
                            await dev.disconnect()
                        except Exception:   # noqa: BLE001
                            pass
                        dev = None
            self._dev = dev
            self._set(bool(dev.is_on))
            self.online = True
            self._warned = False
            self._next_check = time.monotonic() + HEALTH_S
            log_event("plug_online", name=self.name, ip=self.ip)
            print(f"PLUG {self.name} online at {self.ip} ({'ON' if self.is_on else 'OFF'})")
            return True
        except Exception as e:   # noqa: BLE001 - any failure means offline, never crash
            if dev is not None:      # close its web session (else "Unclosed client session")
                try:
                    await dev.disconnect()
                except Exception:   # noqa: BLE001
                    pass
            self._offline(e)
            return False

    async def _do(self, cmd: str) -> None:
        start = time.perf_counter()
        if self.simulated:
            self._apply_sim(cmd)
            self.last_ms = round((time.perf_counter() - start) * 1000, 1)
            return
        if self._dev is None and not await self._connect():
            return
        try:
            if cmd == "flash":
                was = bool(self._dev.is_on)
                for _ in range(3):
                    await self._dev.turn_off() if was else await self._dev.turn_on()
                    await asyncio.sleep(0.3)
                    await self._dev.turn_on() if was else await self._dev.turn_off()
                    await asyncio.sleep(0.3)
            else:
                target = (not self._dev.is_on) if cmd == "toggle" else cmd == "on"
                await (self._dev.turn_on() if target else self._dev.turn_off())
            await self._dev.update()
            self.last_ms = round((time.perf_counter() - start) * 1000, 1)
            self._set(bool(self._dev.is_on))
            log_event("plug", name=self.name, cmd=cmd, on=self.is_on, ms=self.last_ms)
        except Exception as e:   # noqa: BLE001
            try:
                await self._dev.disconnect()
            except Exception:   # noqa: BLE001
                pass
            self._dev = None
            self._offline(e)

    def _apply_sim(self, cmd: str) -> None:
        if cmd == "toggle":
            self._set(not self.is_on)
        elif cmd in ("on", "off"):
            self._set(cmd == "on")
        log_event("plug", name=self.name, cmd=cmd, on=self.is_on, simulated=True)

    def _set(self, on: bool) -> None:
        changed = on != self.is_on
        self.is_on = on
        if changed and self.on_change:
            try:
                self.on_change(self.name, on)
            except Exception:   # noqa: BLE001
                pass

    def _offline(self, err: Exception) -> None:
        self.online = False
        if not self._warned:
            hint = ("login refused: KASA_USER / KASA_PASSWORD must be the TP-Link account that owns the plug; "
                    "if they are (another plug works), unplug this plug 10 s, plug it back in, wait 1 min"
                    if "Authentication" in type(err).__name__
                    else "run the network check (PLAN.md R1): laptop on the right Wi-Fi? plug powered?")
            print(f"PLUG {self.name} OFFLINE at {self.ip} ({type(err).__name__}) - {hint}")
            log_event("plug_offline", name=self.name, ip=self.ip, error=type(err).__name__)
            self._warned = True


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Switch one plug on, off, on, off and time it")
    ap.add_argument("--ip", default=os.environ.get("LAMP_IP", "172.20.10.2"), help='"" = simulated')
    ap.add_argument("--name", default="lamp")
    a = ap.parse_args()
    plug = PlugWorker(a.name, a.ip)
    time.sleep(0.5)
    deadline = time.monotonic() + 8
    while not plug.online and time.monotonic() < deadline:
        time.sleep(0.2)
    times = []
    for cmd in ("on", "off", "on", "off"):
        plug.send(cmd)
        t = time.monotonic()
        while plug._q.qsize() and time.monotonic() - t < 6:
            time.sleep(0.05)
        time.sleep(1.2)
        print(f"{cmd:>3}: is_on={plug.is_on}  {plug.last_ms} ms  online={plug.online}")
        times.append(plug.last_ms)
    plug.close()
    ok = plug.online and all(t is not None and t < 1000 for t in times)
    print("PASS" if ok else "FAIL (offline or slow: see PLAN.md R1)")
