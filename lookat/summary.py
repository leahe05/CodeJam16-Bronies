"""The shift summary for the nurse: 3-6 plain sentences, in time order, ONLY from what was logged.

    shift_summary(memory, feed=live.feed, hours=12) -> {"text", "source": "ai"|"template", "events": n}

Events come from the patient's memory (actions, questions + answers, suggestions, nurse pages,
items deferred to the next visit, nurse visits) and the agent's "checked"/"outcome" steps (e.g. the
temperature after the fan ran). Claude writes the summary from that list (8 s, no retries). It is
REJECTED, and the template is used instead, if it is empty or mentions a clock time that is not in
the list: the summary may never invent an event. No key / no internet / any error = the template.

Self-test: py -3.11 -m lookat.summary     (prints the template; the AI version too with a key)
"""
import os
import re
from datetime import datetime, timedelta
from typing import Optional

from lookat.clock import clock
from lookat.state import log_event

MODEL = "claude-opus-5-5"
TIMEOUT_S = 8
WHO = {"patient": "The patient", "agent": "MinimEyes", "routine": "The routine", "nurse": "The nurse",
       "thermostat": "The thermostat"}
SYSTEM = (
    "You write a nurse's shift handover for one patient who can only move their eyes. You get the "
    "shift's logged events, one per line, each starting with its time. Write 3 to 6 short plain "
    "sentences in time order. Use ONLY these events: never add, guess or embellish anything, never "
    "give medical advice or interpretation. Mention times as they appear (e.g. 2:14 AM). Put what is "
    "still open for the next visit last. No headings, no bullet points.")


def _when(item: dict) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(item.get("time")))
    except ValueError:
        return None


def ampm(t: datetime) -> str:
    return f"{(t.hour % 12) or 12}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"


def _line(kind: str, a: dict) -> Optional[str]:
    if kind == "actions":
        who = WHO.get(a.get("by"), "Someone")
        return f"{who} turned the {a.get('device')} {a.get('cmd')}"
    if kind == "questions":
        ans = a.get("answer")
        return f'Asked "{a.get("question")}": ' + (f'answered "{str(ans).rstrip(".")}"' if ans else "no answer")
    if kind == "suggestions":
        return f'Suggested "{a.get("text")}": answered "{str(a["outcome"]).rstrip(".")}"' if a.get("outcome") not in (None, "ignored", "dismissed", "busy")             else f'Suggested "{a.get("text")}": {a.get("outcome") or "no answer"}'
    if kind == "pages":
        why = f" ({a['reason']})" if a.get("reason") else ""
        return f"Paged the nurse, {a.get('urgency') or 'normal'}: {a.get('message')}{why}"
    if kind == "deferred":
        return f"For the next visit: {a.get('note')}"
    if kind == "nurse_visits":
        return "Nurse visit" + (f": {a['note']}" if a.get("note") else "")
    return None


def shift_events(memory, feed=(), hours: float = 12, now: Optional[datetime] = None) -> list:
    """[(datetime, text)] for the last `hours`, in time order."""
    now = now or clock.now()
    since = now - timedelta(hours=hours)
    out = []
    for kind in ("actions", "questions", "suggestions", "pages", "deferred", "nurse_visits"):
        for a in (memory.data.get(kind) or []) if memory is not None else []:
            t, text = _when(a), _line(kind, a)
            if t and text and since <= t <= now and not a.get("demo"):
                out.append((t, text))
    for f in feed or ():                                  # the agent checking its work: "Room 22.8 °C"
        if f.get("stage") not in ("checked", "outcome"):
            continue
        try:
            h, m = (int(x) for x in str(f.get("time")).split(":"))
        except ValueError:
            continue
        t = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if t > now:
            t -= timedelta(days=1)
        if since <= t:
            out.append((t, str(f.get("text"))))
    out.sort(key=lambda e: e[0])
    return out


def template(events: list) -> str:
    """The rule-based summary: one sentence per event (at most 6, most recent kept), time order."""
    if not events:
        return "Quiet shift: nothing was logged."
    shown = events[-6:]
    head = f"{len(events) - len(shown)} earlier events not shown. " if len(events) > len(shown) else ""
    return head + " ".join(f"{ampm(t)}: {text.rstrip(' .')}." for t, text in shown)


def _times_ok(text: str, events: list) -> bool:
    """Every clock time the AI wrote must be one of the events' times (never an invented event)."""
    allowed = {ampm(t).split()[0] for t, _ in events} | {t.strftime("%H:%M") for t, _ in events}
    for hh, mm in re.findall(r"\b(\d{1,2}):(\d{2})\b", text):
        if f"{int(hh)}:{mm}" not in allowed and f"{int(hh):02d}:{mm}" not in allowed:
            return False
    return True


def _client():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
        return anthropic.Anthropic(timeout=TIMEOUT_S, max_retries=0)
    except Exception:   # noqa: BLE001 - no SDK: template
        return None


def shift_summary(memory, feed=(), hours: float = 12, now: Optional[datetime] = None, client=None) -> dict:
    events = shift_events(memory, feed, hours, now)
    fallback = {"text": template(events), "source": "template", "events": len(events)}
    if not events:
        return fallback
    client = client if client is not None else _client()
    if client is None:
        return fallback
    try:
        resp = client.messages.create(
            model=MODEL, max_tokens=600, system=SYSTEM,
            messages=[{"role": "user", "content": "\n".join(f"{ampm(t)} {text}" for t, text in events)}])
        text = " ".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    except Exception as e:   # noqa: BLE001 - timeout, no internet, API error
        log_event("summary_error", error=repr(e))
        return fallback
    if not text or not _times_ok(text, events):
        log_event("summary_rejected", text=text[:300])
        return fallback
    return {"text": text, "source": "ai", "events": len(events)}


if __name__ == "__main__":
    import tempfile
    from lookat.memory import Memory

    m = Memory("summary-selftest", folder=tempfile.mkdtemp())
    night = clock.now().replace(hour=2, minute=14, second=0, microsecond=0)
    if night > clock.now():
        night -= timedelta(days=1)
    for mins, kind, item in [
        (0, "suggestions", {"text": "You seem to be awake. Too warm?", "outcome": "Yes, please."}),
        (1, "actions", {"device": "fan", "cmd": "on", "by": "agent", "result": "Fan ON"}),
        (9, "questions", {"question": "Better now?", "answer": "Yes", "kind": "comfort"}),
        (166, "deferred", {"note": "Wants repositioning (not urgent)"}),
    ]:
        m.data[kind].append({"time": (night + timedelta(minutes=mins)).isoformat(timespec="seconds"), **item})
    feed = [{"time": (night + timedelta(minutes=8)).strftime("%H:%M"), "stage": "checked", "text": "Room 22.8 °C after the fan"}]
    s = shift_summary(m, feed, now=night + timedelta(hours=4))
    print(f"[{s['source']}, {s['events']} events] {s['text']}")
    ok = s["events"] == 5 and "2:14 AM" in template(shift_events(m, feed, now=night + timedelta(hours=4)))
    print("PASS" if ok else "FAIL")
