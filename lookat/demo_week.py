"""The labelled demo week for the "One week later" demo button (sample data, never a real patient).

    demo_week.load(memory)            # wipes the memory, then writes 7 nights before today

What it writes (every item carries "demo": true, and a fact says it is sample data):
  - nights 1-3: the patient turns the lamp off herself around 22:00 (21:55, 22:05, 22:00);
  - night 3: she approves the routine "Lamp off at 22:00";
  - nights 4-7: the routine turns it off at 22:00, except night 5 when she chose "Keep it on";
    -> the panel shows "approved Nov 14; done 6 of the last 7 nights";
  - comfort answers (so the ranking has a history: Adjust pillows 4x, Water 2x, TV on never picked);
  - night check-ins (answered) and nurse visits.
The REAL code runs on it: routines.find_candidates / evidence and the scheduler, memory.learn().
"""
from datetime import datetime, timedelta

from lookat.clock import clock
from lookat.memory import DEMO_CHART, LISTS

LAMP_OFF = [(21, 55), (22, 5), (22, 0), (22, 0), None, (22, 0), (22, 0)]   # night 5: kept on
APPROVED_NIGHT = 3


def _at(day: datetime, hm: tuple) -> str:
    return day.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0).isoformat(timespec="seconds")


def load(memory, today=None) -> dict:
    """Replace memory with the demo week ending yesterday. Returns a short summary."""
    today = (today or clock.now()).replace(hour=12, minute=0, second=0, microsecond=0)
    data = {k: [] for k in LISTS}
    if "routines" not in data:
        data["routines"] = []
    start = today - timedelta(days=len(LAMP_OFF))
    data["facts"] = [{"time": _at(start, (8, 0)), "text": f["text"], "kind": f.get("kind", "comfort"),
                      "source": "chart", "data": f.get("data") or {}} for f in DEMO_CHART]
    data["facts"].append({"time": _at(start, (8, 0)), "text": "Demo week: labelled sample data, not a real patient",
                          "kind": "note", "source": "demo", "data": {}, "demo": True})

    for i, hm in enumerate(LAMP_OFF, start=1):
        day = start + timedelta(days=i - 1)
        if hm is None:                                         # she kept it on tonight
            data["actions"].append({"time": _at(day, (22, 0)), "device": "lamp", "cmd": "off", "by": "routine",
                                    "result": "Lamp OFF", "demo": True})
            data["actions"].append({"time": _at(day, (22, 1)), "device": "lamp", "cmd": "on", "by": "patient",
                                    "result": "Lamp ON (Keep it on)", "demo": True})
            continue
        by = "patient" if i <= APPROVED_NIGHT else "routine"
        data["actions"].append({"time": _at(day, hm), "device": "lamp", "cmd": "off", "by": by,
                                "result": "Lamp OFF", "demo": True})
        if i == APPROVED_NIGHT:
            data["routines"].append({"time": _at(day, (22, 10)), "device": "lamp", "cmd": "off", "at": "22:00",
                                     "status": "approved", "approved_on": day.date().isoformat(),
                                     "label": "Lamp off at 22:00", "demo": True})
        # a night check-in and a comfort question most nights
        data["checkins"].append({"time": _at(day, (2, 10)), "night": True, "outcome": "answered", "demo": True})
        opts = ["Adjust pillows", "Water", "TV on", "Something else"]
        answer = "Adjust pillows" if i % 2 else ("Water" if i in (2, 6) else None)
        data["questions"].append({"time": _at(day, (2, 11)), "kind": "comfort", "question": "How can I help you?",
                                  "options": opts, "answer": answer, "demo": True})
        data["nurse_visits"].append({"time": _at(day, (6, 30)), "note": "Morning round", "demo": True})

    memory.data.clear()
    memory.data.update(data)
    memory.save()
    return {"nights": len(LAMP_OFF), "from": start.date().isoformat(), "to": (today - timedelta(days=1)).date().isoformat()}


if __name__ == "__main__":
    import tempfile
    from lookat import routines
    from lookat.memory import Memory

    m = Memory("demo-week-selftest", folder=tempfile.mkdtemp())
    print(load(m))
    cands = routines.find_candidates(m.data["actions"])
    ev = routines.evidence(m, "lamp", "off", "22:00")
    print("candidates:", cands)
    print("evidence:", ev)
    print("learn:", {k: v for k, v in m.learn().items() if k in ("option_ranking", "hidden_options")})
    print("PASS" if cands and cands[0]["at"] == "22:00" and ev.startswith("done 6") else "FAIL")
