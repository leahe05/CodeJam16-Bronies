"""What LookAt remembers about one patient. Pure Python + JSON, no AI (task C4a, patient side).

    mem = Memory("demo")                    # memory/demo.json (git-ignored); missing/corrupt -> empty
    mem.load_chart(DEMO_CHART)              # starting facts, if the file has none
    mem.add_fact("Feels cold below 20 °C", "comfort", "chart", {"cold_below_c": 20})
    mem.log("checkins", {"night": True, "outcome": "ignored"})
    mem.learn() -> deterministic, explainable conclusions (below)
    mem.context_text(perception) -> short text the agent reads first

learn():
    comfort       cold_below_c / warm_above_c / runs_warm from facts (latest wins);
                  warm_night_above_c = 1 °C lower at night for someone who runs warm
    night_checkins_enabled   False after NIGHT_SILENCE ignored night check-ins in a row
    option_ranking           per question kind: options by how often they were picked
    hidden_options           shown >= HIDE_AFTER times, never picked ("Something else" never hidden)
    habits                   the same switch by the patient at about the same time (within HABIT_SPAN_MIN) on
                             >= HABIT_DAYS different days, e.g. "Usual bedtime: lamp off around 22:02 (6 nights)".
                             The agent offers it at that time; Claude reads it in the context.
load_demo_week() adds a LABELLED week of bedtimes (actions marked "demo") for the "One week later" demo step.
Every timestamp comes from lookat.clock (the demo can jump to 2 AM).
"""
import datetime as dt
import json
import os
import time
from collections import Counter
from typing import Optional

from lookat.clock import clock

DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory")
LISTS = ("facts", "actions", "questions", "suggestions", "checkins", "pages", "deferred", "nurse_visits")
NIGHT_SILENCE = 3
HIDE_AFTER = 5
NEVER_HIDE = ("Something else", "Return to main menu", "Nurse")
WARM_DEFAULT_C = 23.0
HABIT_DAYS = 3              # a habit = the same switch on at least this many different days...
HABIT_SPAN_MIN = 60         # ...all within this many minutes of each other (time of day)
# The labelled demo week ("One week later"): lamp off at bedtime most nights, lamp on in the early evening.
DEMO_WEEK = {("lamp", "off"): ["21:55", "22:10", "22:00", "22:15", "21:50", "22:05"],
             ("lamp", "on"): ["17:40", None, "17:25", "17:50", None, "17:35"]}


def _mins(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[3:5])


def _diff(a: int, b: int) -> int:
    """a - b in minutes on a 24-hour clock, in -720..719 (21:50 - 22:10 = -20, 00:10 - 23:50 = 20)."""
    return (a - b + 720) % 1440 - 720

# The patient's chart (demo data, labelled "chart" on the panel). Comfort facts carry the numbers the triggers
# use; care facts give Claude something real to work with (the Talk to AI menu, explained pages).
DEMO_CHART = [
    {"text": "Runs warm at night", "kind": "comfort", "data": {"runs_warm": True}},
    {"text": "Feels cold below 20 °C", "kind": "comfort", "data": {"cold_below_c": 20}},
    {"text": "Feels warm above 24 °C (23 °C at night)", "kind": "comfort", "data": {"warm_above_c": 24}},
    {"text": "Answers only with the eyes: keep questions short", "kind": "care"},
    {"text": "Repositioned every 2 hours (care plan)", "kind": "care"},
    {"text": "Likes the lamp on in the evening, off to sleep", "kind": "preference"},
    {"text": "Usual bedtime around 22:00", "kind": "routine", "data": {"bedtime": "22:00"}},
    {"text": "Drinks water through a straw; the nurse helps", "kind": "care"},
]


class Memory:
    def __init__(self, patient: str, folder: str = DIR):
        self.patient = patient
        self.path = os.path.join(folder, f"{patient}.json")
        self.data = {k: [] for k in LISTS}
        try:
            with open(self.path, encoding="utf-8") as f:
                loaded = json.load(f)
            for k in LISTS:
                if isinstance(loaded.get(k), list):
                    self.data[k] = loaded[k]
        except (OSError, ValueError, AttributeError):
            pass                                  # missing or corrupt: start empty, never crash

    # ----- writing ----------------------------------------------------------
    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=1, ensure_ascii=False)
        for attempt in range(10):                 # atomic: a crash never leaves half a file
            try:
                os.replace(tmp, self.path)
                return
            except PermissionError:               # Windows: a virus scan / indexer briefly holds the
                if attempt == 9:                  # file we just wrote; wait a moment and try again
                    raise
                time.sleep(0.05 * (attempt + 1))

    def log(self, kind: str, item: dict) -> dict:
        item = {"time": clock.now().isoformat(timespec="seconds"), **item}
        self.data[kind].append(item)
        del self.data[kind][:-500]
        self.save()
        return item

    def add_fact(self, text: str, kind: str = "note", source: str = "agent", data: Optional[dict] = None) -> dict:
        return self.log("facts", {"text": text, "kind": kind, "source": source, "data": data or {}})

    def load_chart(self, facts: list) -> None:
        """Adds every chart fact the memory doesn't have yet (so a newer chart reaches an older memory file)."""
        known = {f["text"] for f in self.data["facts"]}
        for f in facts:
            if f["text"] not in known:
                self.add_fact(f["text"], f.get("kind", "comfort"), "chart", f.get("data"))

    # ----- learning (deterministic) ------------------------------------------
    def learn(self) -> dict:
        comfort = {}
        for f in self.data["facts"]:              # latest fact wins
            for key in ("cold_below_c", "warm_above_c", "runs_warm"):
                if key in (f.get("data") or {}):
                    comfort[key] = f["data"][key]

        streak = 0                                # ignored night check-ins in a row
        for c in reversed(self.data["checkins"]):
            if not c.get("night"):
                continue
            if c.get("outcome") == "ignored":
                streak += 1
            else:
                break

        shown, picked = {}, {}
        for q in self.data["questions"]:
            kind = q.get("kind", "other")
            shown.setdefault(kind, Counter()).update(q.get("options") or [])
            if q.get("answer"):
                picked.setdefault(kind, Counter())[q["answer"]] += 1
        ranking, hidden = {}, {}
        for kind, seen in shown.items():
            got = picked.get(kind, Counter())
            ranking[kind] = sorted(seen, key=lambda o: (-got[o], list(seen).index(o)))
            hidden[kind] = [o for o, n in seen.items() if n >= HIDE_AFTER and not got[o] and o not in NEVER_HIDE]

        warm = comfort.get("warm_above_c", WARM_DEFAULT_C)
        return {"comfort": comfort,
                "habits": self.habits(),
                "warm_above_c": warm,
                "warm_night_above_c": warm - 1 if comfort.get("runs_warm") else warm,
                "night_ignored_streak": streak,
                "night_checkins_enabled": streak < NIGHT_SILENCE,
                "option_ranking": ranking, "hidden_options": hidden,
                "last_nurse_visit": (self.data["nurse_visits"][-1]["time"] if self.data["nurse_visits"] else None),
                "deferred": [d["note"] for d in self.data["deferred"] if not d.get("done")]}

    def habits(self) -> list:
        """Switches the patient makes at about the same time on several days (patient or agent-on-their-Yes)."""
        per = {}                                  # (device, cmd) -> {date: [minutes]}
        for a in self.data["actions"]:
            t = a.get("time") or ""
            if a.get("by") not in ("patient", "agent") or len(t) < 16 or not a.get("device"):
                continue
            per.setdefault((a["device"], a.get("cmd")), {}).setdefault(t[:10], []).append(_mins(t[11:16]))
        out = []
        for (device, cmd), days in per.items():
            best = []
            for c in (m for ms in days.values() for m in ms):
                near = []                         # the day's action closest to c, if within half the span
                for ms in days.values():
                    d = min((_diff(m, c) for m in ms), key=abs)
                    if abs(d) <= HABIT_SPAN_MIN // 2:
                        near.append(c + d)
                if len(near) > len(best):
                    best = near
            if len(best) < HABIT_DAYS:
                continue
            best.sort()
            usual = best[len(best) // 2] % 1440
            usual_s = f"{usual // 60:02d}:{usual % 60:02d}"
            bedtime = device == "lamp" and cmd == "off" and (usual >= 20 * 60 or usual < 3 * 60)
            n = len(best)
            text = (f"Usual bedtime: lamp off around {usual_s} ({n} nights)" if bedtime
                    else f"Usually turns the {device} {cmd} around {usual_s} ({n} days)")
            out.append({"device": device, "cmd": cmd, "usual": usual_s, "days": n, "bedtime": bedtime, "text": text,
                        "times": [f"{(m % 1440) // 60:02d}:{m % 60:02d}" for m in best]})
        return out

    def load_demo_week(self) -> int:
        """Adds the labelled demo week before today (once). Returns how many actions were added."""
        if any(a.get("demo") for a in self.data["actions"]):
            return 0
        today = clock.now().date()
        added = []
        for (device, cmd), times in DEMO_WEEK.items():
            for i, hhmm in enumerate(times):
                if hhmm:
                    day = today - dt.timedelta(days=len(times) - i)
                    added.append({"time": f"{day.isoformat()}T{hhmm}:00", "device": device, "cmd": cmd,
                                  "by": "patient", "result": "demo week", "demo": True})
        added.sort(key=lambda a: a["time"])
        self.data["actions"] = added + self.data["actions"]
        self.save()
        return len(added)

    def ranked(self, kind: str, options: list) -> list:
        """options in learned order, hidden ones removed (keeps at least 2)."""
        L = self.learn()
        order = L["option_ranking"].get(kind, [])
        hidden = set(L["hidden_options"].get(kind, []))
        out = sorted(options, key=lambda o: order.index(o) if o in order else len(order) + options.index(o))
        kept = [o for o in out if o not in hidden]
        return kept if len(kept) >= 2 else out

    def context_text(self, perception: dict) -> str:
        """What the agent reads before deciding (compact, facts with their source)."""
        L = self.learn()
        lines = ["NOW: " + ", ".join(f"{k}={v}" for k, v in perception.items())]
        lines.append("COMFORT FACTS: " + ("; ".join(f"{f['text']} ({f['source']})" for f in self.data["facts"]) or "none"))
        lines.append(f"LEARNED: warm above {L['warm_above_c']} °C ({L['warm_night_above_c']} °C at night); "
                     f"cold below {L['comfort'].get('cold_below_c', 'unknown')} °C; ignored night check-ins in a row: "
                     f"{L['night_ignored_streak']} (stop at {NIGHT_SILENCE}); "
                     f"night check-ins {'on' if L['night_checkins_enabled'] else 'OFF'}")
        for kind, order in L["option_ranking"].items():
            hid = L["hidden_options"].get(kind)
            lines.append(f"OPTIONS for {kind}: most picked first {order[:5]}" + (f"; never picked, hidden: {hid}" if hid else ""))
        recent = (self.data["questions"][-5:] + self.data["actions"][-5:])
        recent.sort(key=lambda x: x.get("time", ""))
        for r in recent[-5:]:
            lines.append(f"RECENT {r.get('time', '')[11:16]}: " + (
                f"asked '{r['question']}' -> {r.get('answer') or 'no answer'}" if "question" in r
                else f"{r.get('device')} {r.get('cmd')} by {r.get('by')}"))
        for h in L["habits"]:
            lines.append(f"HABIT: {h['text']}; times {', '.join(h['times'])}")
        lines.append(f"LAST NURSE VISIT: {L['last_nurse_visit'] or 'unknown'}")
        if L["deferred"]:
            lines.append("FOR THE NEXT VISIT: " + "; ".join(L["deferred"]))
        return "\n".join(lines)


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        m = Memory("selftest", d)
        m.load_chart(DEMO_CHART)
        for _ in range(3):
            m.log("checkins", {"night": True, "outcome": "ignored"})
        for _ in range(5):
            m.log("questions", {"kind": "comfort", "question": "What would help?",
                                "options": ["Adjust pillows", "Water", "Fan", "Something else"], "answer": "Water"})
        L = m.learn()
        print(json.dumps(L, indent=1, ensure_ascii=False))
        print(m.context_text({"time": "02:14", "temp_c": 24.5, "awake": True}))
        ok = (L["comfort"] == {"runs_warm": True, "cold_below_c": 20, "warm_above_c": 24}
              and L["warm_night_above_c"] == 23 and not L["night_checkins_enabled"]
              and "Fan" in L["hidden_options"]["comfort"] and "Something else" not in L["hidden_options"]["comfort"]
              and Memory("selftest", d).learn()["comfort"]["cold_below_c"] == 20)
        print("PASS" if ok else "FAIL")
