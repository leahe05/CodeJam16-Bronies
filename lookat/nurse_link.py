"""Bedside status and the in-process interface between a patient agent and nurses."""

from collections import deque
from threading import Lock

from lookat.clock import clock

UNKNOWN = "unknown"
EYE_SAMPLE_GAP_S = 2.0
OPEN_S = 1.0
CLOSED_S = 30.0


class NurseLink:
    def __init__(self, add_alert, *, demo_awareness=None):
        self._add_alert = add_alert
        self._lock = Lock()
        self._demo_awareness = demo_awareness
        self.reset()

    def reset(self):
        with self._lock:
            self._responses = deque(maxlen=100)
            self._history = deque(maxlen=100)
            self._summary = None
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
