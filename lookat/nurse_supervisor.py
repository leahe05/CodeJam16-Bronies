"""Scheduled patient-agent check-ins and unsolicited requests for the nurse."""
import itertools
import logging
import math
import threading
import time
from collections import deque
from queue import Empty, Queue


CHECK_IN_PROMPT = (
    "Is there anything the nurse should attend to, or a question the nurse should ask? "
    "Include the patient's current status and relevant preferences."
)
DEFAULT_INTERVAL_S = 30.0
PRIORITY_RANKS = {"urgent": 2, "soon": 1, "routine": 0, "unrated": -1}
log = logging.getLogger(__name__)


class NurseSupervisor:
    def __init__(self, interval_s=DEFAULT_INTERVAL_S, *, decide=None):
        if not math.isfinite(interval_s) or interval_s <= 0:
            raise ValueError("Check-in interval must be positive and finite.")
        self.interval_s = interval_s
        self._agents = {}
        self._reports = {}
        self._profiles = {}
        self._requests = {}
        self._questions = {}
        self._generation = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._decide = decide
        self._review_tasks = Queue()

    def register(self, patient_id, check_in):
        with self._lock:
            self._agents[patient_id] = check_in
            self._requests.setdefault(patient_id, deque(maxlen=100))
            self._questions.setdefault(patient_id, {})
            self._generation.setdefault(patient_id, 0)

    def check_patients(self):
        with self._lock:
            agents = [(key, check_in, self._generation[key]) for key, check_in in self._agents.items()]
        for patient_id, check_in, generation in agents:
            try:
                report = check_in(CHECK_IN_PROMPT)
                with self._lock:
                    if self._generation[patient_id] != generation:
                        continue
                    self._reports[patient_id] = report
                    if report.get("profile"):
                        self._store_profile(patient_id, report["profile"])
                    if report.get("attention"):
                        self._receive_request(patient_id, **report["attention"], delivery="check-in")
                    for request in report.get("requests", ()):
                        self._receive_request(patient_id, **request, delivery="check-in")
                    for question in report.get("questions", ()):
                        self._questions[patient_id].setdefault(question["id"], question)
            except Exception:
                log.exception("Nurse check-in failed for patient agent %s", patient_id)

    def run(self, stop):
        due = time.monotonic() + self.interval_s
        while not stop.wait(max(0, due - time.monotonic())):
            self.check_patients()
            elapsed = max(0, time.monotonic() - due)
            due += (int(elapsed // self.interval_s) + 1) * self.interval_s

    def latest_report(self, patient_id):
        with self._lock:
            return self._reports.get(patient_id)

    def publish_profile(self, patient_id, profile):
        with self._lock:
            self._store_profile(patient_id, profile)

    def _store_profile(self, patient_id, profile):
        prior = self._profiles.get(patient_id, {})
        if profile.get("_observed_at", 0) >= prior.get("_observed_at", 0):
            self._profiles[patient_id] = profile

    def latest_profile(self, patient_id):
        with self._lock:
            return self._profiles.get(patient_id)

    def reset_patient(self, patient_id):
        with self._lock:
            self._reports.pop(patient_id, None)
            self._profiles.pop(patient_id, None)
            self._requests[patient_id].clear()
            self._questions[patient_id].clear()
            self._generation[patient_id] += 1

    def pending_questions(self, patient_id):
        with self._lock:
            return list(self._questions[patient_id].values())

    def queue_question(self, patient_id, question):
        with self._lock:
            self._questions[patient_id].setdefault(question["id"], question)

    def resolve_question(self, patient_id, question_id):
        with self._lock:
            self._questions[patient_id].pop(question_id, None)

    def receive_request(self, patient_id, request, *, profile=None, **details):
        with self._lock:
            if profile is not None:
                self._store_profile(patient_id, profile)
            return self._receive_request(patient_id, request, **details)

    @property
    def ai_enabled(self):
        return self._decide is not None

    def review_request(self, patient_id, request, *, at, profile, confirmed=False, request_id=None,
                       reason=None, time_sensitive=False):
        self.publish_profile(patient_id, profile)
        with self._lock:
            alert_id, created = self._receive_request(patient_id, request, at=at, request_id=request_id,
                                                      reason=reason, review=True)
            if created and self.ai_enabled:
                report = " ".join(part for part in (
                    request, reason, "The patient agent reports that this is time-sensitive." if time_sensitive else None,
                ) if part)
                self._review_tasks.put((patient_id, alert_id, report, profile, confirmed))
            return alert_id, created

    def run_reviews(self, stop):
        while not stop.is_set():
            try:
                job = self._review_tasks.get(timeout=.2)
            except Empty:
                continue
            patient_id, alert_id, request, profile, confirmed = job
            try:
                decision = self._decide(request, profile, confirmed)
            except Exception:
                log.warning("AI review unavailable for nurse request %s", alert_id)
                decision = None
            self._apply_review(patient_id, alert_id, request, profile, decision)

    def _apply_review(self, patient_id, alert_id, request, profile, decision):
        with self._lock:
            alert = next((item for item in self._requests[patient_id] if item["id"] == alert_id), None)
            if alert is None or alert["done"]:
                return
            if decision is None:
                alert["review"] = "needs_review"
                return
            level = decision["priority"]
            alert["priority"] = {**alert["priority"], "level": level, "rank": PRIORITY_RANKS[level]}
            alert["urgency"] = "urgent" if level == "urgent" else "normal"
            alert["ai_rationale"] = decision["rationale"]
            alert["recommendation"] = decision["recommendation"]
            alert["review"] = "question_queued" if decision["action"] == "ask" else "ai_reviewed"
            if decision["action"] == "ask":
                question_id = f"{alert_id}-question"
                self._questions[patient_id][question_id] = {
                    "id": question_id, "evidence": request, "preference": " · ".join(profile.get("preferences", [])),
                    "steps": decision["steps"], "source": "ai", "origin_alert": alert_id,
                }

    def update_question_request(self, patient_id, alert_id, state):
        with self._lock:
            alert = next((item for item in self._requests[patient_id] if item["id"] == alert_id), None)
            if alert and not alert["done"]:
                alert["review"] = state
                if state == "clarified":
                    alert["done"] = True

    def _receive_request(self, patient_id, request, *, at, reason=None, urgent=False, time_sensitive=False,
                         request_id=None, kind="page", delivery="proactive", review=False):
        requests = self._requests[patient_id]
        if request_id is not None:
            previous = next((item for item in requests if item["_request_id"] == request_id), None)
            if previous:
                return previous["id"], False
        order = next(self._ids)
        alert_id = f"agent-{order}"
        if review:
            priority = "unrated"
        elif kind == "call" or urgent:
            priority = "urgent"
        elif time_sensitive:
            priority = "soon"
        else:
            priority = "routine"
        alert = {
            "id": alert_id, "_request_id": request_id, "time": at, "kind": kind,
            "urgency": "urgent" if priority == "urgent" else "normal", "source": "patient",
            "priority": {"level": priority, "rank": PRIORITY_RANKS[priority], "order": order},
            "request": None if kind == "call" else request, "text": request, "reason": reason,
            "delivery": delivery, "done": False, "actions": [{"id": "ack", "label": "On my way"}],
        }
        if review:
            alert["review"] = "analyzing" if self.ai_enabled else "needs_review"
        requests.appendleft(alert)
        return alert_id, True

    def alerts(self, patient_id):
        with self._lock:
            ordered = sorted(self._requests[patient_id], key=lambda alert: (
                alert["done"], -alert["priority"]["rank"], alert["priority"]["order"],
            ))
            return [dict(alert) for alert in ordered]

    def acknowledge(self, patient_id, alert_id):
        with self._lock:
            alert = next((item for item in self._requests[patient_id] if item["id"] == alert_id), None)
            if alert is None:
                return False
            alert["done"] = True
            return True
