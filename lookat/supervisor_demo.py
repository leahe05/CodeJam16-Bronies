"""Two simulated patient agents reporting to one nurse supervisor."""
import argparse
from collections import deque
import socket
import threading
import time

from lookat import nurse_ai
from lookat.fake_api import Fake, opt, screen
from lookat.nurse_link import NurseLink
from lookat.nurse_patient_profile import CARE_CHOICES, MEASUREMENTS, PATIENT_CONTEXT
from lookat.nurse_shift import SHIFT_CAMERAS, ShiftPlayback
from lookat.nurse_supervisor import DEFAULT_INTERVAL_S, NurseSupervisor
from lookat.server import make_app


def minutes(hhmm):
    hours, mins = map(int, hhmm.split(":"))
    return hours * 60 + mins


class SimulatedPatient(Fake):
    initial_history = (
        ("22:24:00", "Patient agent", "Noticed", "Patient was still awake and the room was 24 °C."),
        ("22:24:12", "Patient agent", "Asked", "Are you too warm to sleep?"),
        ("22:24:25", "Patient", "Chose", "Yes"),
        ("22:24:26", "Patient agent", "Replied", "I’ll let your nurse know you’re warm."),
    )
    def suggest_question(self, report):
        late = (minutes(report["time"]) - minutes(report["usual_bedtime"])) % (24 * 60)
        warmer = round(report["temp_c"] - report["usual_temp_c"], 1)
        if report["awareness"] != "awake" or not 30 <= late <= 120 or warmer < 2:
            return None
        return {
            "id": f'{report["patient"]["id"]}-warm-bedtime',
            "evidence": (
                f'Awake at {report["time"]}, {late} minutes past their usual '
                f'bedtime ({report["usual_bedtime"]}). Room {report["temp_c"]:g} °C, '
                f'{warmer:g} °C above their usual {report["usual_temp_c"]:g} °C.'
            ),
            "preference": report["preference"],
            "steps": self.question_steps(report["common_wants"]),
        }

    def question_steps(self, wants):
        return [{"question": question, "request": request} for _, question, request in
                (CARE_CHOICES[want] for want in wants)]

    def __init__(self, supervisor, patient_id, name, room, preference, common_wants):
        self.supervisor = supervisor
        self.patient, self.room_name = name, room
        self.patient_id = patient_id
        self.companion_port = None
        self.preference = preference
        self.common_wants = common_wants
        self.context = PATIENT_CONTEXT[patient_id]
        super().__init__(still="idle")
        self.nurse_link = NurseLink(None, demo_awareness="awake")
        self._reset_state()

    def _reset_state(self):
        self.nurse_link.reset()
        self.temp_c = 24.0
        self.setpoint_c = 22.0
        self.lamp_on, self.fan_on = True, False
        self.overlay_until = 0.0
        self.alerts = []
        self.recent = []
        self.last_visit = None
        self.summary = None
        self.session = {"active": False, "thread": [], "follow_ups": [], "waiting": False}
        self.facts = [{"source": "demo", "text": self.preference}]
        self.nurse_link.set_visitors(False)
        self.nurse_link.publish_summary("")
        self.resolved_proposal = None
        self.refined_proposal = None
        self.approved_steps = []
        self.last_suggestion = None
        self.interaction_start = time.monotonic()
        self.patient_status = "Resting in bed"
        self.vital_samples = deque(maxlen=6)
        for at, values in zip(("22:20:00", "22:25:00", "22:29:00"), self.context["vitals"]):
            self.vital_samples.append({"time": at, **dict(zip(MEASUREMENTS, values))})
        for at, actor, action, text in self.initial_history:
            self.nurse_link.record_interaction(actor, action, text, at=at)

    def clock(self):
        return {"time": "22:30", "demo": True}

    def _interaction(self, actor, action, text):
        self.nurse_link.record_interaction(actor, action, text, at=self.interaction_time())

    def interaction_time(self):
        elapsed = int(time.monotonic() - self.interaction_start)
        return time.strftime("%H:%M:%S", time.gmtime(22 * 3600 + 30 * 60 + elapsed))

    def report(self):
        return {
            "patient": {"id": self.patient_id, "name": self.patient, "room": self.room_name},
            "time": self.clock()["time"],
            "usual_bedtime": "22:00",
            "temp_c": self.room()["temp_c"],
            "usual_temp_c": 22.0,
            "awareness": self.nurse_link.snapshot(time.monotonic())["awareness"]["state"],
            "preference": self.preference,
            "common_wants": self.common_wants,
        }

    def patient_profile(self):
        bedside = self.nurse_link.snapshot(time.monotonic())
        meal = self.context["last_meal"]
        minutes_since = meal["minutes_before_start"] + int((time.monotonic() - self.interaction_start) // 60)
        return {
            "patient": {"id": self.patient_id, "name": self.patient, "room": self.room_name},
            "_observed_at": time.monotonic(),
            "reported_at": self.interaction_time(),
            "status": self.patient_status,
            "posture": "In bed",
            "awareness": bedside["awareness"],
            "visitors_present": bedside["visitors_present"],
            "room": {**self.room(), "window": self.context["window"], "blinds": self.context["blinds"]},
            "vitals": {"source": "simulated", "current": self.vital_samples[-1],
                       "samples": list(self.vital_samples)},
            "last_meal": {"name": meal["name"], "time": meal["time"], "minutes_since": minutes_since},
            "camera": SHIFT_CAMERAS[self.patient_id],
            "preferences": [fact["text"] for fact in self.facts],
            "habits": list(self.context["habits"]),
            "care_choices": [{"id": key, "label": choice[0]} for key, choice in CARE_CHOICES.items()],
        }

    def check_in(self, prompt):
        """Answer the supervisor from this patient's own status and preferences."""
        with self.lock:
            report = self.report()
            question = self.suggest_question(report)
            if question and question["id"] != self.last_suggestion:
                self._interaction("Patient agent", "Suggested to nurse", question["steps"][0]["question"])
            self.last_suggestion = question["id"] if question else None
            if not question:
                self.resolved_proposal = self.refined_proposal = None
            return {
                "prompt": prompt,
                "time": self.interaction_time(),
                "status": report,
                "preferences": list(self.facts),
                "profile": self.patient_profile(),
                "question": question,
                "attention": None,
            }

    def proposal(self):
        queued = self.supervisor.pending_questions(self.patient_id)
        if queued:
            first = queued[0]
            return self.refined_proposal if (self.refined_proposal or {}).get("id") == first["id"] else first
        report = self.supervisor.latest_report(self.patient_id)
        proposal = report and report["question"]
        if proposal and proposal["id"] != self.resolved_proposal:
            return self.refined_proposal or proposal
        return None

    def nurse_json(self):
        data = super().nurse_json()
        data["patient"] = {"id": self.patient_id, "name": self.patient, "room": self.room_name}
        report = self.supervisor.latest_report(self.patient_id)
        data.update(simulation=True, proposal=self.proposal(), next_visit=[], routines=[],
                    request_question_branches=True,
                    profile=self.supervisor.latest_profile(self.patient_id),
                    alerts=self.supervisor.alerts(self.patient_id) + data["alerts"],
                    bedside=self.nurse_link.snapshot(time.monotonic()),
                    history=self.nurse_link.interaction_history(), ai_drafts=nurse_ai.available())
        data["agent_checkin"] = {"time": report["time"] if report else None,
                                 "interval_s": self.supervisor.interval_s,
                                 "suggested_question": bool(report and (report["question"] or report.get("questions"))),
                                 "needs_attention": bool(report and (report["attention"] or report.get("requests")))}
        if self.companion_port is not None:
            data["demo_companion_port"] = self.companion_port
        return data

    def screen_json(self):
        data = super().screen_json()
        data.update(focus=None, brow={"up": False, "held_s": 0.0, "long_s": 2.0})
        if not self.session["active"]:
            base = screen("idle", "You're all set.", icon="moon")
        elif not self.session["waiting"]:
            title = ("Thank you. Your nurse can see your answer."
                     if self.session["thread"] else "Your nurse is here.")
            base = screen("nurse", title, badge="From your nurse", show_call_nurse=False)
        else:
            q = self.session["thread"][-1]
            left, right = q["options"]
            base = screen(
                "nurse", q["question"], badge="From your nurse", icon="nurse",
                left=opt("left", left), right=opt("right", right),
                middle=opt("call", "Call nurse", icon="nurse"),
                footer="Look left or right and raise your eyebrows to choose, "
                       "or look center and raise your eyebrows to call your nurse.",
                show_call_nurse=False,
            )
        return {**data, **base}

    def nurse_action(self, path, body):
        if path in ("alert/question/draft", "alert/question/send"):
            return self._request_question(path, body)
        if path == "proposal/refine":
            return self._refine_proposal(body.get("id"))
        if path == "check-in/suggest":
            choice = body.get("choice")
            if not isinstance(choice, str) or choice not in CARE_CHOICES:
                return {"ok": False, "error": "Choose a care topic."}
            with self.lock:
                self.supervisor.queue_question(self.patient_id, {
                    "id": f"{self.patient_id}-care-{choice}",
                    "evidence": self.patient_status,
                    "preference": self.preference,
                    "steps": self.question_steps((choice,)),
                })
            return {"ok": True}
        if path == "patient/request":
            request = body.get("request")
            reason = body.get("reason")
            urgent = body.get("urgent", False)
            time_sensitive = body.get("time_sensitive", False)
            request_id = body.get("request_id")
            confirmed = body.get("confirmed", False)
            if (not isinstance(request, str) or not request.strip() or len(request) > 300
                    or (reason is not None and (not isinstance(reason, str) or len(reason) > 300))
                    or not isinstance(urgent, bool)
                    or not isinstance(time_sensitive, bool)
                    or not isinstance(confirmed, bool)
                    or (request_id is not None and (not isinstance(request_id, str) or not request_id or len(request_id) > 100))):
                return {"ok": False, "error": "Enter a request and valid optional reason, urgency and request ID."}
            with self.lock:
                alert_id, created = self._submit_request(
                    request.strip(), reason=reason, urgent=urgent, time_sensitive=time_sensitive,
                    request_id=request_id, confirmed=confirmed,
                )
                if created:
                    self._interaction("Patient agent", "Requested help", request.strip())
            return {"ok": True, "id": alert_id}
        if path == "simulate/agent-request":
            choice = body.get("choice", "water")
            if not isinstance(choice, str) or choice not in CARE_CHOICES:
                return {"ok": False, "error": "Choose a care topic."}
            return self.nurse_action("patient/request", {"request": CARE_CHOICES[choice][2], "confirmed": True})
        if path == "alert" and str(body.get("id", "")).startswith("agent-"):
            if body.get("action") == "ack" and self.supervisor.acknowledge(self.patient_id, body["id"]):
                return {"ok": True}
            return {"ok": False, "error": "No such alert action."}
        if path == "send":
            with self.lock:
                question = str(body.get("question") or "").strip()
                options = body.get("options")
                if (not question or not isinstance(options, list) or len(options) != 2
                        or not all(isinstance(option, str) and option.strip() for option in options)
                        or options[0].strip() == options[1].strip()):
                    return {"ok": False, "error": "Enter a question and exactly two distinct choices."}
                if self.session["waiting"]:
                    return {"ok": False, "error": "The patient is still answering a question."}
                self.approved_steps = []
                return {"ok": True, "qid": self._send_question(question, [o.strip() for o in options])}
        if path == "presence":
            if "present" not in body:
                return {"ok": False, "error": "present is required"}
            with self.lock:
                result = self.nurse_link.set_visitors(body["present"])
                if result["ok"]:
                    self.supervisor.publish_profile(self.patient_id, self.patient_profile())
                return result
        if path == "suggest":
            question = str(body.get("question") or "").strip()
            if not question:
                return {"ok": False, "error": "empty question"}
            try:
                return {"ok": True, **nurse_ai.make_options(question, self.preference)}
            except Exception:
                # Placeholder for Claude-generated choices when the API is unavailable.
                return {"ok": True, "options": ["Yes", "No"], "source": "fallback"}
        if path == "session/end":
            with self.lock:
                current = self.session["thread"][-1] if self.session["waiting"] else {}
                alert_id = current.get("_request_alert") or self.session.get("_origin_alert")
                if alert_id:
                    self._update_request_question(alert_id, "needs_review")
                self.session = {"active": False, "thread": [], "follow_ups": [], "waiting": False}
                self.approved_steps = []
            return {"ok": True}
        if path not in ("proposal/approve", "proposal/dismiss", "simulate/choose"):
            return super().nurse_action(path, body)
        with self.lock:
            if path == "simulate/choose":
                return self._choose(body.get("spot"), body.get("qid"))
            proposal = self.proposal()
            if not proposal or body.get("id") != proposal["id"]:
                return {"ok": False, "error": "This proposal is no longer available."}
            if path == "proposal/approve":
                if proposal.get("source") != body.get("source"):
                    return {"ok": False, "error": "Review the updated proposal before sending it."}
                if self.session["active"]:
                    return {"ok": False, "error": "End the current conversation before sending this check-in."}
                self.approved_steps = proposal["steps"]
                self._interaction("Nurse", "Approved", proposal["steps"][0]["question"])
                self._send_question(self.approved_steps[0]["question"], ["Yes", "No"], actor="Nurse supervisor")
                if proposal.get("origin_alert"):
                    self.session["_origin_alert"] = proposal["origin_alert"]
                    self.supervisor.update_question_request(self.patient_id, proposal["origin_alert"], "waiting_answer")
            elif proposal.get("origin_alert"):
                self.supervisor.update_question_request(self.patient_id, proposal["origin_alert"], "needs_review")
            self._resolve_proposal(proposal["id"])
            return {"ok": True}

    def _request_question(self, path, body):
        question = body.get("question")
        if not isinstance(question, str) or not 0 < len(question.strip()) <= 300:
            return {"ok": False, "error": "Enter a question of up to 300 characters."}
        question = question.strip()
        with self.lock:
            alert = next((item for item in self.supervisor.alerts(self.patient_id) + self.alerts
                          if item["id"] == body.get("id") and not item["done"]), None)
            if alert is None:
                return {"ok": False, "error": "This request is no longer open."}
            if path == "alert/question/send":
                if self.session["waiting"]:
                    return {"ok": False, "error": "The patient is still answering a question."}
                try:
                    branches = nurse_ai.validate_request_branches(body.get("branches"))
                except ValueError as error:
                    return {"ok": False, "error": str(error)}
                self.approved_steps = []
                qid = self._send_question(question, [item["label"] for item in branches])
                self.session["thread"][-1].update(
                    _request_alert=alert["id"], _branches=branches,
                    _branch_source="ai" if body.get("source") == "ai" else "fallback",
                )
                self._update_request_question(alert["id"], "waiting_answer")
                self.supervisor.resolve_question(self.patient_id, f'{alert["id"]}-question')
                return {"ok": True, "qid": qid}
            profile = self.patient_profile()
        return {"ok": True, **nurse_ai.draft_request_branches(question, alert.get("request") or alert["text"], profile)}

    def _update_request_question(self, alert_id, state, reply=None):
        if str(alert_id).startswith("agent-"):
            self.supervisor.update_question_request(self.patient_id, alert_id, state, reply)
        else:
            alert = next((item for item in self.alerts if item["id"] == alert_id), None)
            if alert and not alert["done"]:
                alert["review"] = state
                if reply is not None:
                    alert["question_reply"] = reply
                elif state == "waiting_answer":
                    alert.pop("question_reply", None)

    def _resolve_proposal(self, proposal_id):
        self.resolved_proposal = proposal_id
        self.refined_proposal = None
        self.supervisor.resolve_question(self.patient_id, proposal_id)

    def _submit_request(self, request, *, confirmed=False, **details):
        return self.supervisor.receive_request(self.patient_id, request, at=self.interaction_time(),
                                               profile=self.patient_profile(), **details)

    def _refine_proposal(self, proposal_id):
        with self.lock:
            draft = self.proposal()
            session = self.session
            if not draft or draft["id"] != proposal_id or self.refined_proposal:
                return {"ok": False, "error": "This proposal is no longer available for drafting."}
        try:
            refined = nurse_ai.refine_checkin(draft)
        except Exception:
            return {"ok": False, "error": "Claude could not draft the questions. The original proposal is available."}
        with self.lock:
            if self.session is not session or self.proposal() != draft or self.refined_proposal:
                return {"ok": False, "error": "The proposal changed during drafting."}
            self.refined_proposal = refined
            return {"ok": True, "proposal": self.proposal()}

    def _send_question(self, question, options, *, actor="Nurse"):
        qid = f"q{next(self.qids)}"
        self.session.update(active=True, waiting=True, follow_ups=[])
        self.session["thread"].append({"qid": qid, "time": self.clock()["time"], "question": question,
                                       "options": options, "answer": None, "seconds": None,
                                       "_sent": time.time()})
        self._interaction(actor, "Asked", question)
        return qid

    def _call_nurse(self, reason):
        self.overlay_until = time.time() + 6
        self.supervisor.receive_request(self.patient_id, "Patient used the call signal",
                                        at=self.interaction_time(), reason=reason, urgent=True, kind="call",
                                        profile=self.patient_profile())
        self._interaction("Patient", "Called nurse", reason)

    def demo_action(self, body):
        action = body.get("action")
        with self.lock:
            if action == "reset":
                self._reset_state()
                self.supervisor.reset_patient(self.patient_id)
            elif action == "call_nurse":
                self._call_nurse("Simulated call signal")
            elif action == "set_temp":
                self.temp_c = float(body.get("value", self.temp_c))
            else:
                return {"ok": False, "error": "unknown demo action"}
        return {"ok": True}

    def _choose(self, spot, qid):
        if not self.session["waiting"] or self.session["thread"][-1]["qid"] != qid:
            return {"ok": False, "error": "This question is no longer open."}
        if spot == "middle":
            self._call_nurse("Center gaze during the conversation")
            return {"ok": True}
        if spot not in ("left", "right"):
            return {"ok": False, "error": "Choose left, center, or right."}
        q = self.session["thread"][-1]
        q["answer"] = q["options"][0 if spot == "left" else 1]
        self._interaction("Patient", "Chose", q["answer"])
        q["seconds"] = round(time.time() - q["_sent"], 1)
        self.session["waiting"] = False
        outcome = f'Answered "{q["answer"]}" to: {q["question"]}'
        if q.get("_request_alert"):
            branch = q["_branches"][0 if spot == "left" else 1]
            q["next_step"] = branch["next_step"]
            self._update_request_question(q["_request_alert"], "answered",
                                          {"answer": q["answer"], "next_step": q["next_step"],
                                           "source": q["_branch_source"]})
            self._interaction("Nurse supervisor", "Suggested next step", q["next_step"])
        if self.approved_steps:
            step, *remaining = self.approved_steps
            self.approved_steps = []
            if spot == "left":
                outcome = step["request"]
                q["request"] = outcome
                self._interaction("Patient agent", "Reported to nurse", outcome)
                self._submit_request(outcome, confirmed=True)
                if self.session.get("_origin_alert"):
                    self.supervisor.update_question_request(self.patient_id, self.session.pop("_origin_alert"), "clarified")
            elif remaining:
                self.approved_steps = remaining
                self._send_question(remaining[0]["question"], ["Yes", "No"], actor="Nurse supervisor")
        if not self.session["waiting"]:
            if self.session.get("_origin_alert"):
                self.supervisor.update_question_request(self.patient_id, self.session.pop("_origin_alert"), "needs_review")
            self._interaction("Patient agent", "Replied", "Thank you. Your nurse can see your answer.")
        self.recent.insert(0, {"time": self.clock()["time"], "text": outcome})
        return {"ok": True}


class ShiftPatient(SimulatedPatient):
    initial_history = ()

    def __init__(self, *args, playback):
        self.playback = playback
        super().__init__(*args)

    def _reset_state(self):
        super()._reset_state()
        self.temp_c = 22.0
        self.shift_feed = deque(maxlen=40)

    def clock(self):
        return {"time": self.interaction_time()[:5], "demo": True}

    def emit_random_request(self, rng):
        """Placeholder for live input from the patient-end agent."""
        with self.lock:
            if not self.shift_feed:
                self.interaction_start = time.monotonic()
            choices = tuple(CARE_CHOICES)
            weights = [4 if choice in self.common_wants else 1 for choice in choices]
            choice = rng.choices(choices, weights=weights)[0]
            confirmed = rng.random() >= .25
            if confirmed:
                request = CARE_CHOICES[choice][2]
            else:
                room_choice = choice in ("window", "close_window", "fan", "open_blinds", "close_blinds")
                subject = "the room" if room_choice else "their comfort"
                request = f"Patient indicated they would like help with {subject}, but did not confirm what they want."
            timing = rng.random()
            if timing < .1:
                request += " They say they need help right away."
            elif timing < .35:
                request += " They ask if someone can come soon."
            self.patient_status = request
            self.temp_c = round(rng.uniform(22, 25), 1)
            values = rng.choice(self.context["vitals"])
            self.vital_samples.append({"time": self.interaction_time(), **dict(zip(MEASUREMENTS, values))})
            self._submit_request(request, confirmed=confirmed)
            self._interaction("Patient agent", "Reported request", request)

    def check_in(self, prompt):
        with self.lock:
            return {
                "prompt": prompt, "time": self.interaction_time(), "status": self.report(),
                "preferences": list(self.facts), "profile": self.patient_profile(),
                "question": None, "attention": None,
            }

    def _submit_request(self, request, *, confirmed=False, urgent=False, **details):
        if urgent:
            alert_id, created = super()._submit_request(request, confirmed=confirmed, urgent=True, **details)
        else:
            alert_id, created = self.supervisor.review_request(
                self.patient_id, request, at=self.interaction_time(), profile=self.patient_profile(),
                confirmed=confirmed, **details,
            )
        if created:
            self.shift_feed.append({"time": self.interaction_time(), "status": request, "_alert_id": alert_id})
        return alert_id, created

    def nurse_json(self):
        data = super().nurse_json()
        alerts = {alert["id"]: alert for alert in data["alerts"]}
        responses = {
            "analyzing": "Claude is reviewing the request",
            "local_reviewed": "Ranked by local rules",
            "needs_review": "Waiting for nurse review",
            "question_queued": "Claude drafted a question for approval",
            "waiting_answer": "Question sent. Waiting for the patient",
            "answered": "Patient answered the nurse's question",
            "clarified": "Patient clarified the request",
        }
        feed = []
        for entry in self.shift_feed:
            alert = alerts.get(entry["_alert_id"], {})
            review = alert.get("review")
            response = responses.get(review) or alert.get("recommendation") or "Request received"
            if alert.get("done") and review != "clarified":
                response = "Nurse acknowledged. On my way"
            feed.append({**entry, "response": response})
        data["shift"] = {
            "state": self.playback.state, "feed": feed, "ai_enabled": self.supervisor.ai_enabled,
            "camera": SHIFT_CAMERAS[self.patient_id],
            "queued_questions": len(self.supervisor.pending_questions(self.patient_id)),
        }
        return data

    def nurse_action(self, path, body):
        if path == "shift/start":
            self.playback.start()
            return {"ok": True}
        if path == "shift/end":
            self.playback.finish()
            return {"ok": True}
        return super().nurse_action(path, body)


def make_demo_patients(interval_s=DEFAULT_INTERVAL_S, *, playback=None, decide=None):
    if decide is None and playback and nurse_ai.available():
        decide = nurse_ai.decide_request
    supervisor = NurseSupervisor(interval_s, decide=decide)
    patient = ShiftPatient if playback else SimulatedPatient
    options = {"playback": playback} if playback else {}
    avery = patient(
        supervisor, "avery", "Avery", "Room 14",
        "Prefers a cracked window when warm at night. A lighter blanket is their second choice.",
        ("window", "lighter_blanket"), **options,
    )
    morgan = patient(
        supervisor, "morgan", "Morgan", "Room 19",
        "Prefers the window closed at night. Usually asks for a fan on low when warm.",
        ("fan", "remove_blanket"), **options,
    )
    for patient in (avery, morgan):
        supervisor.register(patient.patient_id, patient.check_in)
    supervisor.check_patients()
    return avery, morgan


def make_demo_app(companion_port, interval_s=DEFAULT_INTERVAL_S, *, playback=None, decide=None):
    avery, morgan = make_demo_patients(interval_s, playback=playback, decide=decide)
    avery.companion_port = companion_port
    app = make_app(avery)
    companion = make_app(morgan)

    async def serve(scope, receive, send):
        if scope["type"] == "lifespan":
            stop = threading.Event()
            workers = [threading.Thread(target=avery.supervisor.run, args=(stop,), daemon=True)]
            if playback:
                patients = {patient.patient_id: patient for patient in (avery, morgan)}
                workers.append(threading.Thread(target=playback.run, args=(stop, patients), daemon=True))
            if avery.supervisor.ai_enabled:
                workers.extend(threading.Thread(target=avery.supervisor.run_reviews, args=(stop,), daemon=True)
                               for _ in range(2))
            for worker in workers:
                worker.start()
            try:
                await app(scope, receive, send)
            finally:
                stop.set()
                for worker in workers:
                    worker.join()
            return
        # Each original patient page fetches its screen from its own origin.
        server = scope.get("server")
        backend = companion if server and server[1] == companion_port else app
        await backend(scope, receive, send)

    return serve


def main():
    import uvicorn

    parser = argparse.ArgumentParser(description="Run the standalone LookAt nurse demo")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8768)
    parser.add_argument("--companion-port", type=int, help="Morgan's port (defaults to --port + 1)")
    parser.add_argument("--shift-demo", action="store_true", help="enable the two live simulated shift feeds")
    parser.add_argument("--check-in-seconds", type=float,
                        help="fixed interval between check-ins (default: 30, or 10 in the shift demo)")
    parser.add_argument("--seed", type=int, help="repeat a random request stream for testing")
    args = parser.parse_args()
    interval_s = args.check_in_seconds if args.check_in_seconds is not None else (10 if args.shift_demo else DEFAULT_INTERVAL_S)
    playback = ShiftPlayback(seed=args.seed) if args.shift_demo else None
    with socket.create_server((args.host, args.port)) as primary:
        port = primary.getsockname()[1]
        with socket.create_server((args.host, args.companion_port if args.companion_port is not None else port + 1)) as secondary:
            companion_port = secondary.getsockname()[1]
            print(f"Nurse dashboard: http://{args.host}:{port}/nurse")
            print(f"Avery: http://{args.host}:{port}/patient  Morgan: http://{args.host}:{companion_port}/patient")
            try:
                app = make_demo_app(companion_port, interval_s, playback=playback)
            except ValueError as error:
                parser.error(str(error))
            server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
            server.run(sockets=[primary, secondary])


if __name__ == "__main__":
    main()
