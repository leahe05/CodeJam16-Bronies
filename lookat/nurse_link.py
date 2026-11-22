"""Bedside status and the in-process interface between a patient agent and nurses."""

import math
from collections import deque
from itertools import count
from threading import Lock

from lookat.clock import clock

UNKNOWN = "unknown"
EYE_SAMPLE_GAP_S = 2.0
OPEN_S = 1.0
CLOSED_S = 30.0
MAX_ETA_MINUTES = 120


def valid_eta_minutes(minutes):
    return type(minutes) is int and 1 <= minutes <= MAX_ETA_MINUTES


def eta_text(minutes):
    return f"Your nurse expects to arrive in about {minutes} {'minute' if minutes == 1 else 'minutes'}."


class NurseLink:
    def __init__(self, add_alert, *, demo_awareness=None):
        self._add_alert = add_alert
        self._lock = Lock()
        self._demo_awareness = demo_awareness
        self._eta_versions = count(1)
        self.reset()

    def reset(self):
        with self._lock:
            self._responses = deque(maxlen=100)
            self._history = deque(maxlen=100)
            self._summary = None
            self._etas = {}
            self._visitors_present = None
            self._eye_state = None
            self._eye_since = self._eye_seen = None
            self._stable_awareness = UNKNOWN

    def observe_eyes(self, now, features):
        """Stabilize the available blink scores and discard interrupted observations."""
        left = features.get("eyeBlinkLeft") if features else None
        right = features.get("eyeBlinkRight") if features else None
        eyes = None
        if left is not None and right is not None:
            if min(left, right) >= 0.55:
                eyes = "closed"
            elif max(left, right) <= 0.35:
                eyes = "open"
        with self._lock:
            previous = self._stable_awareness
            interrupted = self._eye_seen is None or now - self._eye_seen > EYE_SAMPLE_GAP_S
            if interrupted or left is None or right is None:
                self._stable_awareness = UNKNOWN
            if interrupted or eyes != self._eye_state:
                self._eye_since = now
            self._eye_state = eyes
            self._eye_seen = now
            elapsed = now - self._eye_since
            if eyes == "open" and elapsed >= OPEN_S:
                self._stable_awareness = "awake"
            elif eyes == "closed" and elapsed >= CLOSED_S:
                self._stable_awareness = "asleep"
            awareness = self._stable_awareness
        if awareness != previous and awareness != UNKNOWN:
            self.record_interaction("Bedside camera", "Estimated", f"Patient appears {awareness}.")

    def record_interaction(self, actor, action, text, *, at=None):
        """Retain an observed event, independently of the current nurse session."""
        with self._lock:
            self._history.append({
                "time": at or clock.now().strftime("%H:%M:%S"),
                "actor": actor,
                "action": action,
                "text": text,
            })

    def interaction_history(self):
        with self._lock:
            return list(self._history)

    def publish_summary(self, text, *, demo=False):
        """Publish a short account of recent interactions grounded in the agent's events."""
        text = text.strip()
        with self._lock:
            self._summary = {"text": text, "time": clock.hhmm(), "source": "demo" if demo else "ai"} if text else None

    def request_intervention(self, request, reason=None, *, urgent=False, source="ai"):
        """Queue an intervention, using source="patient" for explicit patient requests."""
        request = request.strip()
        if not request:
            raise ValueError("An intervention needs a request")
        if source not in ("patient", "ai"):
            raise ValueError("An intervention source must be patient or ai")
        if self._add_alert is None:
            raise RuntimeError("This bedside status has no live alert handler")
        return self._add_alert("page", request, reason=reason, source=source, request=request,
                               urgency="urgent" if urgent else "normal",
                               actions=[{"id": "ack", "label": "On my way"}])["id"]

    def record_response(self, alert_id, action):
        with self._lock:
            self._responses.append({"alert_id": alert_id, "action": action, "time": clock.hhmm()})

    def drain_responses(self):
        """Return and clear nurse responses to requests published by the agent."""
        with self._lock:
            responses = list(self._responses)
            self._responses.clear()
            return responses

    def send_eta(self, alert_id, minutes, now, *, request):
        with self._lock:
            self._set_eta(alert_id, minutes, now, request)

    def _set_eta(self, alert_id, minutes, now, request):
        self._etas[alert_id] = {"version": next(self._eta_versions), "request": request,
                                "due": now + minutes * 60, "visible": True}
        self._responses.append({"alert_id": alert_id, "action": "eta", "eta_minutes": minutes,
                                "time": clock.hhmm()})

    def eta_reminders(self, now):
        with self._lock:
            due = sorted(self._etas.items(), key=lambda entry: entry[1]["due"])
            return [{"id": alert_id, "version": eta["version"], "request": eta["request"]}
                    for alert_id, eta in due if eta["due"] <= now]

    def follow_up_eta(self, body, now):
        alert_id, version = body.get("id"), body.get("version")
        action, minutes = body.get("action"), body.get("eta_minutes")
        if action not in ("attended", "extend"):
            return {"ok": False, "error": "Choose whether you attended or want to extend the estimate."}
        if action == "extend" and not valid_eta_minutes(minutes):
            return {"ok": False, "error": "Enter a whole number of minutes from 1 to 120."}
        with self._lock:
            eta = self._etas.get(alert_id) if isinstance(alert_id, str) else None
            if eta is None or type(version) is not int or version != eta["version"] or eta["due"] > now:
                return {"ok": False, "error": "This arrival reminder is no longer due."}
            request = eta["request"]
            if action == "extend":
                self._set_eta(alert_id, minutes, now, request)
                history_action, text = "Extended ETA", f"{request} · {eta_text(minutes)}"
            else:
                del self._etas[alert_id]
                self._responses.append({"alert_id": alert_id, "action": "attended", "time": clock.hhmm()})
                history_action, text = "Attended to request", request
        self.record_interaction("Nurse", history_action, text)
        return {"ok": True}

    def eta_message(self, now):
        with self._lock:
            due = min((eta["due"] for eta in self._etas.values() if eta["visible"] and eta["due"] > now), default=None)
        return eta_text(math.ceil((due - now) / 60)) if due is not None else None

    def clear_eta(self):
        with self._lock:
            for eta in self._etas.values():
                eta["visible"] = False

    def set_visitors(self, present):
        if present is not None and type(present) is not bool:
            return {"ok": False, "error": "present must be true, false or null"}
        with self._lock:
            self._visitors_present = present
        return {"ok": True}

    def _awareness(self, now, eyes_override):
        if eyes_override or self._demo_awareness:
            state = {"open": "awake", "closed": "asleep"}.get(eyes_override, self._demo_awareness)
            return state, "demo"
        if self._eye_seen is None or now - self._eye_seen > EYE_SAMPLE_GAP_S:
            return UNKNOWN, "camera"
        return self._stable_awareness, "camera"

    def snapshot(self, now, eyes_override=None):
        with self._lock:
            awareness, source = self._awareness(now, eyes_override)
            return {
                "awareness": {"state": awareness, "source": source},
                "visitors_present": self._visitors_present,
                "vitals": {"simulated": True, "heart_rate_bpm": 72, "spo2_pct": 98, "respiratory_rate_bpm": 16},
                "interaction_summary": self._summary,
            }
