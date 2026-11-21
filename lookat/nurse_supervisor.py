"""Scheduled patient-agent check-ins and unsolicited requests for the nurse."""
import itertools
import logging
import math
import threading
import time
from collections import deque


CHECK_IN_PROMPT = (
    "Is there anything the nurse should attend to, or a question the nurse should ask? "
    "Include the patient's current status and relevant preferences."
)
DEFAULT_INTERVAL_S = 30.0
log = logging.getLogger(__name__)


class NurseSupervisor:
    def __init__(self, interval_s=DEFAULT_INTERVAL_S):
        if not math.isfinite(interval_s) or interval_s <= 0:
            raise ValueError("Check-in interval must be positive and finite.")
        self.interval_s = interval_s
        self._agents = {}
        self._reports = {}
        self._requests = {}
        self._questions = {}
        self._generation = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

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

    def reset_patient(self, patient_id):
        with self._lock:
            self._reports.pop(patient_id, None)
            self._requests[patient_id].clear()
            self._questions[patient_id].clear()
            self._generation[patient_id] += 1

    def pending_questions(self, patient_id):
        with self._lock:
            return list(self._questions[patient_id].values())

    def resolve_question(self, patient_id, question_id):
        with self._lock:
            self._questions[patient_id].pop(question_id, None)

    def receive_request(self, patient_id, request, **details):
        with self._lock:
            return self._receive_request(patient_id, request, **details)

    def _receive_request(self, patient_id, request, *, at, reason=None, urgent=False, time_sensitive=False,
                         request_id=None, kind="page", delivery="proactive"):
        requests = self._requests[patient_id]
        if request_id is not None:
            previous = next((item for item in requests if item["_request_id"] == request_id), None)
            if previous:
                return previous["id"], False
        order = next(self._ids)
        alert_id = f"agent-{order}"
        if kind == "call" or urgent:
            priority, rank = "urgent", 2
        elif time_sensitive:
            priority, rank = "soon", 1
        else:
            priority, rank = "routine", 0
        requests.appendleft({
            "id": alert_id, "_request_id": request_id, "time": at, "kind": kind,
            "urgency": "urgent" if priority == "urgent" else "normal", "source": "patient",
            "priority": {"level": priority, "rank": rank, "order": order},
            "request": None if kind == "call" else request, "text": request, "reason": reason,
            "delivery": delivery, "done": False, "actions": [{"id": "ack", "label": "On my way"}],
        })
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
