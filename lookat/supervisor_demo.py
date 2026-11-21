"""Two simulated patient agents reporting to one nurse supervisor."""
import argparse
import socket
import threading
import time

from lookat import nurse_ai
from lookat.fake_api import Fake, opt, screen
from lookat.nurse_link import NurseLink
from lookat.nurse_supervisor import DEFAULT_INTERVAL_S, NurseSupervisor
from lookat.server import make_app


def minutes(hhmm):
    hours, mins = map(int, hhmm.split(":"))
    return hours * 60 + mins


class SimulatedPatient(Fake):
    questions = {
        "window": ("Would opening the window a little help you sleep?",
                   "Patient requests opening the window a little."),
        "fan": ("Would a fan on low help you sleep?", "Patient requests a fan on low."),
        "lighter_blanket": ("Would you like a lighter blanket?", "Patient requests a lighter blanket."),
        "remove_blanket": ("Would you like to remove a blanket?", "Patient requests removing a blanket."),
    }

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
            "steps": [
                {"question": question, "request": request}
                for question, request in (self.questions[want] for want in report["common_wants"])
            ],
        }

    def __init__(self, supervisor, patient_id, name, room, preference, common_wants):
        self.supervisor = supervisor
        self.patient, self.room_name = name, room
        self.patient_id = patient_id
        self.companion_port = None
        self.preference = preference
        self.common_wants = common_wants
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
        for at, actor, action, text in [
            ("22:24:00", "Patient agent", "Noticed", "Patient was still awake and the room was 24 °C."),
            ("22:24:12", "Patient agent", "Asked", "Are you too warm to sleep?"),
            ("22:24:25", "Patient", "Chose", "Yes"),
            ("22:24:26", "Patient agent", "Replied", "I’ll let your nurse know you’re warm."),
        ]:
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
                "question": question,
                "attention": None,
            }

    def proposal(self):
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
                    alerts=self.supervisor.alerts(self.patient_id) + data["alerts"],
                    bedside=self.nurse_link.snapshot(time.monotonic()),
                    history=self.nurse_link.interaction_history(), ai_drafts=nurse_ai.available())
        data["agent_checkin"] = {"time": report["time"] if report else None,
                                 "interval_s": self.supervisor.interval_s,
                                 "suggested_question": bool(report and report["question"]),
                                 "needs_attention": bool(report and report["attention"])}
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
        if path == "proposal/refine":
            return self._refine_proposal(body.get("id"))
        if path == "patient/request":
            request = body.get("request")
            reason = body.get("reason")
            urgent = body.get("urgent", False)
            request_id = body.get("request_id")
            if (not isinstance(request, str) or not request.strip() or len(request) > 300
                    or (reason is not None and (not isinstance(reason, str) or len(reason) > 300))
                    or not isinstance(urgent, bool)
                    or (request_id is not None and (not isinstance(request_id, str) or not request_id or len(request_id) > 100))):
                return {"ok": False, "error": "Enter a request and valid optional reason, urgency and request ID."}
            with self.lock:
                alert_id, created = self.supervisor.receive_request(
                    self.patient_id, request.strip(), at=self.interaction_time(),
                    reason=reason, urgent=urgent, request_id=request_id,
                )
                if created:
                    self._interaction("Patient agent", "Requested help", request.strip())
            return {"ok": True, "id": alert_id}
        if path == "simulate/agent-request":
            request = "Please bring the patient a glass of water."
            return self.nurse_action("patient/request", {"request": request, "reason": "The patient asked for water."})
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
            return self.nurse_link.set_visitors(body["present"])
        if path == "suggest":
            question = str(body.get("question") or "").strip()
            if not question:
                return {"ok": False, "error": "empty question"}
            try:
                return {"ok": True, **nurse_ai.make_options(question, self.preference)}
            except Exception:
                return {"ok": True, "options": ["Yes", "No"], "source": "fallback"}
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
            self.resolved_proposal = proposal["id"]
            return {"ok": True}

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
                                        at=self.interaction_time(), reason=reason, urgent=True, kind="call")
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
        if self.approved_steps:
            step, *remaining = self.approved_steps
            self.approved_steps = []
            if spot == "left":
                outcome = step["request"]
                q["request"] = outcome
                self._interaction("Patient agent", "Reported to nurse", outcome)
                self.supervisor.receive_request(self.patient_id, outcome, at=self.interaction_time())
            elif remaining:
                self.approved_steps = remaining
                self._send_question(remaining[0]["question"], ["Yes", "No"], actor="Nurse supervisor")
        if not self.session["waiting"]:
            self._interaction("Patient agent", "Replied", "Thank you. Your nurse can see your answer.")
        self.recent.insert(0, {"time": self.clock()["time"], "text": outcome})
        return {"ok": True}


def make_demo_patients(interval_s=DEFAULT_INTERVAL_S):
    supervisor = NurseSupervisor(interval_s)
    avery = SimulatedPatient(
        supervisor, "avery", "Avery", "Room 14",
        "Prefers a cracked window when warm at night. A lighter blanket is their second choice.",
        ("window", "lighter_blanket"),
    )
    morgan = SimulatedPatient(
        supervisor, "morgan", "Morgan", "Room 19",
        "Prefers the window closed at night. Usually asks for a fan on low when warm.",
        ("fan", "remove_blanket"),
    )
    for patient in (avery, morgan):
        supervisor.register(patient.patient_id, patient.check_in)
    supervisor.check_patients()
    return avery, morgan


def make_demo_app(companion_port, interval_s=DEFAULT_INTERVAL_S):
    avery, morgan = make_demo_patients(interval_s)
    avery.companion_port = companion_port
    app = make_app(avery)
    companion = make_app(morgan)

    async def serve(scope, receive, send):
        if scope["type"] == "lifespan":
            stop = threading.Event()
            worker = threading.Thread(target=avery.supervisor.run, args=(stop,), daemon=True)
            worker.start()
            try:
                await app(scope, receive, send)
            finally:
                stop.set()
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
    parser.add_argument("--check-in-seconds", type=float, default=DEFAULT_INTERVAL_S,
                        help="fixed interval between patient-agent check-ins (default: 30)")
    args = parser.parse_args()
    with socket.create_server((args.host, args.port)) as primary:
        port = primary.getsockname()[1]
        with socket.create_server((args.host, args.companion_port if args.companion_port is not None else port + 1)) as secondary:
            companion_port = secondary.getsockname()[1]
            print(f"Nurse dashboard: http://{args.host}:{port}/nurse")
            print(f"Avery: http://{args.host}:{port}/patient  Morgan: http://{args.host}:{companion_port}/patient")
            try:
                app = make_demo_app(companion_port, args.check_in_seconds)
            except ValueError as error:
                parser.error(str(error))
            server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
            server.run(sockets=[primary, secondary])


if __name__ == "__main__":
    main()
