"""Routines: things the patient does at about the same time every night, done for them once approved.

    find_candidates(memory.data["actions"])   # same switch, same direction, >= 3 nights, within 60 min
    approve(memory, "lamp", "off", "22:00")   # only after the patient said yes (agent / demo week)
    sched = RoutineScheduler(live, memory); sched.start()

The scheduler runs an APPROVED routine once per night at its time, by="routine":
  - only while the patient is on the idle screen, not choosing, not in a nurse session, not
    calibrating (else it waits, up to WINDOW_MIN after the time);
  - announced on the patient screen like a suggestion: "It's 10 PM. Turning off your lamp, as usual."
    Engaging offers "OK" / "Keep it on"; "Keep it on" switches it straight back (by="patient");
  - if the switch is already where the routine would put it, nothing is done (no banner).
view() is what the panel shows under Memory -> routines, with the evidence.

Self-test (no camera, no plugs): py -3.11 -m lookat.routines
"""
import itertools
import statistics
import threading
from datetime import datetime, timedelta
from typing import Optional

from lookat.clock import clock, parse_hhmm
from lookat.state import log_event

MIN_NIGHTS = 3         # the same action on at least this many different days...
WINDOW_MIN = 60        # ...within this many minutes of each other = a routine candidate
LOOK_BACK_DAYS = 7     # evidence: "done N of the last 7 nights"
NOT_ROUTINE = ("routine", "thermostat")   # actions that don't count as the patient's own habit
KEEP = {"off": "Keep it on", "on": "Keep it off"}


def _when(item: dict) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(item.get("time")))
    except ValueError:
        return None


def _minutes(t: datetime) -> int:
    return t.hour * 60 + t.minute


def _hhmm(minutes: float) -> str:
    m = int(round(minutes / 5.0) * 5) % (24 * 60)          # round to 5 minutes: "22:00", not "21:58"
    return f"{m // 60:02d}:{m % 60:02d}"


def label(device: str, cmd: str, at: str) -> str:
    return f"{device.capitalize()} {cmd} at {at}"


def find_candidates(actions: list) -> list:
    """[{device, cmd, at, nights}] for each switch the patient set the same way on >= MIN_NIGHTS
    different days within WINDOW_MIN minutes of each other."""
    groups: dict = {}
    for a in actions:
        t = _when(a)
        if t is None or a.get("by") in NOT_ROUTINE or a.get("cmd") not in ("on", "off"):
            continue
        groups.setdefault((a.get("device"), a["cmd"]), []).append(t)
    out = []
    for (device, cmd), times in groups.items():
        best = []
        for t in times:                                    # the densest window around any one action
            near = [u for u in times if abs(_minutes(u) - _minutes(t)) <= WINDOW_MIN / 2]
            if len({u.date() for u in near}) > len({u.date() for u in best}):
                best = near
        nights = sorted({u.date() for u in best})
        if len(nights) >= MIN_NIGHTS:
            out.append({"device": device, "cmd": cmd, "at": _hhmm(statistics.median(_minutes(u) for u in best)),
                        "nights": [d.isoformat() for d in nights]})
    return out


def approve(memory, device: str, cmd: str, at: str, approved_on: Optional[str] = None) -> dict:
    """Record the patient's yes to a routine (replaces an earlier one for the same switch + direction)."""
    memory.data["routines"] = [r for r in memory.data["routines"]
                               if not (r.get("device") == device and r.get("cmd") == cmd)]
    return memory.log("routines", {"device": device, "cmd": cmd, "at": at, "status": "approved",
                                   "approved_on": approved_on or clock.now().date().isoformat(),
                                   "label": label(device, cmd, at)})


def evidence(memory, device: str, cmd: str, at: str, today=None) -> str:
    """"done 6 of the last 7 nights": nights the switch ENDED UP that way within the window of `at`
    (a routine the patient undid with "Keep it on" doesn't count)."""
    today = today or clock.now().date()
    h, m = parse_hhmm(at)
    centre = h * 60 + m
    last = {}                                            # night -> (time, cmd) of the last switch in the window
    for a in memory.data["actions"]:
        t = _when(a)
        if (t and a.get("device") == device and a.get("cmd") in ("on", "off")
                and abs(_minutes(t) - centre) <= WINDOW_MIN / 2 and 0 < (today - t.date()).days <= LOOK_BACK_DAYS):
            if t.date() not in last or t >= last[t.date()][0]:
                last[t.date()] = (t, a["cmd"])
    done = sum(1 for _, c in last.values() if c == cmd)
    return f"done {done} of the last {LOOK_BACK_DAYS} nights"


def announcement(device: str, cmd: str, at: str) -> str:
    h, m = parse_hhmm(at)
    when = f"{(h % 12) or 12}{'' if m == 0 else f':{m:02d}'} {'AM' if h < 12 else 'PM'}"
    return f"It's {when}. Turning {cmd} your {device}, as usual."


class RoutineScheduler:
    def __init__(self, live, memory, now=None):
        self.live, self.memory = live, memory
        self.now = now or clock.now                      # demo clock (datetime)
        self._ids = itertools.count(1)
        self._banners: dict = {}                         # banner id -> routine it announced
        self._stop = threading.Event()
        live.listeners.append(self._on_event)

    # ----- what the panel shows ---------------------------------------------
    def view(self) -> list:
        out, approved = [], set()
        today = self.now().date()
        for r in self.memory.data["routines"]:
            if r.get("status") != "approved":
                continue
            approved.add((r["device"], r["cmd"]))
            when = datetime.fromisoformat(r["approved_on"]).strftime("%b %d") if r.get("approved_on") else "?"
            out.append({"text": r.get("label") or label(r["device"], r["cmd"], r["at"]), "status": "approved",
                        "evidence": f"approved {when}; {evidence(self.memory, r['device'], r['cmd'], r['at'], today)}"})
        for c in find_candidates(self.memory.data["actions"]):
            if (c["device"], c["cmd"]) not in approved:
                days = ", ".join(datetime.fromisoformat(d).strftime("%b %d") for d in c["nights"][-3:])
                out.append({"text": label(c["device"], c["cmd"], c["at"]), "status": "candidate",
                            "evidence": f"the patient did it on {len(c['nights'])} nights ({days})"})
        return out

    # ----- running ------------------------------------------------------------
    def _free(self) -> bool:
        live = self.live
        return (live.calibration is None and live.sm.kind == "idle" and not live.machine.busy
                and not (live.session or {}).get("active"))

    def tick(self) -> list:
        """Run every approved routine that is due tonight. Returns the routines run."""
        now, ran = self.now(), []
        for r in list(self.memory.data["routines"]):
            if r.get("status") != "approved":
                continue
            h, m = parse_hhmm(r["at"])
            due = now.replace(hour=h, minute=m, second=0, microsecond=0)
            today = now.date().isoformat()
            if not (due <= now < due + timedelta(minutes=WINDOW_MIN)) or r.get("last_run") == today:
                continue
            with self.live.lock:
                if not self._free():
                    continue                             # the patient is busy: try again next tick
                r["last_run"] = today
                already = self.live.room.state().get(f"{r['device']}_on") == (r["cmd"] == "on")
                if not already:
                    self.live.room.execute(r["device"], r["cmd"], by="routine")
                    sid = f"rt{next(self._ids)}"
                    self._banners[sid] = r
                    self.live.sm.show_suggestion(
                        sid, announcement(r["device"], r["cmd"], r["at"]), self.live.now(),
                        question=f"I turned {r['cmd']} your {r['device']}. Is that OK?",
                        options=["OK", KEEP[r["cmd"]]])
                    self.live.machine.reset(self.live.now())
                why = f"approved routine; {evidence(self.memory, r['device'], r['cmd'], r['at'], now.date())}"
                self.live.add_feed("did", f"Routine: {r['device']} {r['cmd']} at {r['at']}"
                                   + (f" (already {r['cmd']}, nothing to do)" if already else ""), why)
            self.memory.save()
            log_event("routine_run", device=r["device"], cmd=r["cmd"], at=r["at"], skipped=already)
            ran.append(r)
        return ran

    def _on_event(self, ev: tuple) -> None:
        """'Keep it on' on the routine's banner: switch it straight back."""
        if len(ev) >= 3 and ev[0] == "answer" and ev[1] in self._banners:
            r = self._banners.pop(ev[1])
            if ev[2] == KEEP[r["cmd"]]:
                back = "on" if r["cmd"] == "off" else "off"
                self.live.room.execute(r["device"], back, by="patient")
                self.live.add_feed("outcome", f"Patient kept the {r['device']} {back}",
                                   "the patient overrules a routine, always")
        elif len(ev) >= 2 and ev[0] == "dismissed" and ev[1] in self._banners:
            self._banners.pop(ev[1], None)

    def start(self) -> None:
        def run():
            while not self._stop.wait(1.0):
                try:
                    self.tick()
                except Exception as e:   # noqa: BLE001 - never stop the scheduler
                    log_event("routine_error", error=repr(e))
        threading.Thread(target=run, name="routines", daemon=True).start()

    def close(self) -> None:
        self._stop.set()


if __name__ == "__main__":
    import tempfile
    from lookat import demo_week
    from lookat.devices import Room, SimPlug
    from lookat.live import Live
    from lookat.memory import Memory

    mem = Memory("selftest", folder=tempfile.mkdtemp())
    live = Live(Room(lamp_ip="", fan_ip="", plug_factory=SimPlug))
    live.room.execute("lamp", "on", by="patient")
    demo_week.load(mem)
    clock.set_time("22:01")
    s = RoutineScheduler(live, mem)
    for r in s.view():
        print(f"  {r['status']:9} {r['text']}: {r['evidence']}")
    ran = s.tick()
    ok = bool(ran) and live.room.state()["lamp_on"] is False and live.sm.kind == "suggestion"
    print("  banner:", live.sm.suggestion["text"] if live.sm.suggestion else None)
    print("PASS" if ok else "FAIL")
