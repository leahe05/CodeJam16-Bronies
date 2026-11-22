"""The real LookAt behind Paco's nurse dashboard (web/nurse.js), task M3-1 (backend side).

His dashboard was built against a simulated patient (lookat/supervisor_demo.py). This bridge gives the real
patient (Live + the agent's memory) the same contract, so the dashboard works on the live demo:

    GET /api/nurse adds:  patient {id, name, room}, profile (the patient-agent profile card), bedside
                          (awake/asleep, visitors, simulated vitals), history (timeline), proposal,
                          request_question_branches, and the AI-triaged requests merged into alerts
    POST /api/nurse/...:  presence, check-in/suggest, proposal/approve|dismiss|refine,
                          alert/question/draft|send, patient/request, alert (for triaged "agent-N" ids)

The agent's normal pages go through Paco's nurse supervisor: Claude (nurse_ai.decide_request) ranks each one
urgent / soon / routine, explains it, and may draft a yes/no question for the nurse to approve. Urgent pages
(unsafe temperature) and the patient's own call signal skip the review and show at once.

    live.nurse = NurseBridge(live); live.nurse.start()      # main.py does this
"""
import itertools
import threading
import time
from datetime import timedelta
from typing import Optional

from lookat import nurse_ai
from lookat.clock import clock
from lookat.memory import PROFILES
from lookat.nurse_link import NurseLink
from lookat.nurse_patient_profile import CARE_CHOICES
from lookat.nurse_supervisor import NurseSupervisor
from lookat.state import log_event

ROOMS = {"margaret": "Room 12", "james": "Room 14"}
# Simulated vitals for the demo (labelled "Simulated vitals" on the dashboard): heart rate, SpO2, breathing, BP.
VITALS = {"margaret": ((74, 97, 16, 124, 76), (76, 97, 16, 126, 77), (75, 98, 15, 125, 76)),
          "james": ((68, 98, 14, 132, 84), (70, 98, 15, 130, 83), (69, 97, 14, 131, 84))}
FEED_ACTIONS = {"decided": "Decided", "did": "Did", "outcome": "Outcome", "learned": "Learned"}


class NurseBridge:
    def __init__(self, live, decide=None):
        self.live = live
        self._links = {pid: NurseLink(None) for pid in PROFILES}
        for link in self._links.values():
            link.set_visitors(False)
        if decide is None and nurse_ai.available():
            decide = nurse_ai.decide_request
        self.supervisor = NurseSupervisor(decide=decide)
        for pid in PROFILES:
            self.supervisor.register(pid, lambda prompt, pid=pid: self.check_in(pid, prompt))
        self.refined: dict = {}                    # proposal id -> the version Claude reworded
        self._ids = itertools.count(1)
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self.supervisor.run, args=(self._stop,), daemon=True, name="nurse-check-in").start()
        if self.supervisor.ai_enabled:
            for _ in range(2):
                threading.Thread(target=self.supervisor.run_reviews, args=(self._stop,), daemon=True,
                                 name="nurse-review").start()

    def stop(self) -> None:
        self._stop.set()

    def reset(self) -> None:
        """Reset demo: the dashboard's history, requests and proposals go too (the profiles stay)."""
        for link in self._links.values():
            link.reset()
            link.set_visitors(False)
        for pid in PROFILES:
            self.supervisor.reset_patient(pid)
        self.refined.clear()

    @property
    def proposal(self) -> Optional[dict]:
        """The first check-in waiting for the nurse's approval (Claude's, or one the nurse picked)."""
        queued = self.supervisor.pending_questions(self.pid)
        return (self.refined.get(queued[0]["id"]) or queued[0]) if queued else None

    # ----- who and what -------------------------------------------------------------------------
    @property
    def pid(self) -> str:
        return self.live.profile or "margaret"

    @property
    def link(self) -> NurseLink:
        return self._links[self.pid]

    def patient(self) -> dict:
        return {"id": self.pid, "name": PROFILES[self.pid]["name"], "room": ROOMS.get(self.pid, "Room 12")}

    def _awareness(self) -> dict:
        a = getattr(self.live, "awake_state", None) or {}
        override = getattr(self.live, "eyes_override", None)
        if override in ("open", "closed"):
            return {"state": "awake" if override == "open" else "asleep", "source": "demo"}
        awake = a.get("awake")
        return {"state": "unknown" if awake is None else "awake" if awake else "asleep", "source": "camera"}

    def _vitals(self) -> dict:
        keys = ("heart_rate_bpm", "spo2_pct", "respiratory_rate_bpm", "systolic_mmhg", "diastolic_mmhg")
        now = clock.now()
        samples = [{"time": (now - timedelta(minutes=10 * (2 - i))).strftime("%H:%M"), **dict(zip(keys, v))}
                   for i, v in enumerate(VITALS.get(self.pid, VITALS["margaret"]))]
        return {"source": "simulated", "current": samples[-1], "samples": samples}

    def profile(self) -> dict:
        """The patient-agent profile card: from the agent's real memory and the room."""
        memory = getattr(self.live, "memory", None)
        facts = memory.data["facts"] if memory is not None else []
        habits = [h["text"] for h in memory.habits()] if memory is not None else []
        r = self.live.room.state()
        aware = self._awareness()
        status = {"awake": "Awake in bed", "asleep": "Resting with eyes closed"}.get(aware["state"], "In bed")
        return {"patient": self.patient(), "_observed_at": time.monotonic(), "reported_at": clock.now().strftime("%H:%M:%S"),
                "status": status + (f" · LookAt: {self.live.agent_status}" if getattr(self.live, "agent_status", None) else ""),
                "posture": "In bed", "awareness": aware, "visitors_present": self.link.snapshot(time.monotonic())["visitors_present"],
                "room": {"temp_c": r["temp_c"], "setpoint_c": r["setpoint_c"], "lamp_on": r["lamp_on"], "fan_on": r["fan_on"]},
                "vitals": self._vitals(),
                "preferences": [f["text"] for f in facts if f.get("kind") != "routine"],
                "habits": [f["text"] for f in facts if f.get("kind") == "routine"] + habits,
                "care_choices": [{"id": k, "label": v[0]} for k, v in CARE_CHOICES.items()]}

    def check_in(self, patient_id: str, prompt: str) -> Optional[dict]:
        with self.live.lock:
            if patient_id != self.pid:
                return None
            profile = self.profile()
            attention = bool(self.live.next_visit) or any(
                not alert.get("done") for alert in self.supervisor.alerts(patient_id) + self.live.alerts
            )
            proposal = self.proposal
            if patient_id != self.pid:
                return None
            return {"prompt": prompt, "time": profile["reported_at"], "profile": profile,
                    "status": profile["status"], "needs_attention": attention,
                    "suggested_question": bool(proposal)}

    # ----- GET /api/nurse -----------------------------------------------------------------------
    def extend(self, data: dict) -> dict:
        snap = self.link.snapshot(time.monotonic())
        snap["awareness"] = self._awareness()
        snap["vitals"] = {"simulated": True, **{k: v for k, v in self._vitals()["current"].items() if k != "time"}}
        report = self.supervisor.latest_report(self.pid) or {}
        data.update(patient=self.patient(), profile=self.profile(), bedside=snap,
                    history=self.link.interaction_history(), proposal=self.proposal,
                    agent_checkin={"interval_s": self.supervisor.interval_s, "time": report.get("time"),
                                   "needs_attention": report.get("needs_attention", False),
                                   "suggested_question": report.get("suggested_question", False)},
                    request_question_branches=True, question_drafts=True, ai_drafts=nurse_ai.available(), patient_id_guard=True,
                    alerts=self.supervisor.alerts(self.pid) + list(data.get("alerts") or []))
        return data

    # ----- what happened (the dashboard's timeline) ---------------------------------------------
    def record(self, actor: str, action: str, text: str) -> None:
        self.link.record_interaction(actor, action, text)

    def on_feed(self, stage: str, text: str) -> None:
        if stage in FEED_ACTIONS:
            self.record("Patient agent", FEED_ACTIONS[stage], text)

    # ----- the agent's pages go through the nurse supervisor (Claude triage) ---------------------
    def page(self, text: str, reason: Optional[str]) -> dict:
        """A non-urgent page from the agent -> a reviewed request ("agent-N"). Returns {"id": ...}."""
        with self.live.lock:
            profile = self.profile()
            alert_id, _ = self.supervisor.review_request(profile["patient"]["id"], text, at=clock.hhmm(),
                                                         profile=profile, reason=reason)
            self.record("Patient agent", "Requested help", text)
        log_event("nurse_request", id=alert_id, text=text, reason=reason)
        return {"id": alert_id, "kind": "page", "text": text, "reason": reason}

    # ----- POST /api/nurse/... --------------------------------------------------------------------
    def action(self, path: str, body: dict) -> Optional[dict]:
        """Handles the dashboard's paths that Live doesn't know; None = not mine (Live handles it)."""
        if body.get("patient_id", self.pid) != self.pid:
            return {"ok": False, "error": "The active patient changed. Review their profile before continuing."}
        if path == "presence":
            if "present" not in body:
                return {"ok": False, "error": "present is required"}
            res = self.link.set_visitors(body["present"])
            if res["ok"]:
                self.record("Nurse", "Updated", {True: "Visitors present", False: "No visitors"}.get(body["present"], "Visitors unknown"))
            return res
        if path == "visit":
            self.record("Nurse", "Visited", "Nurse visit logged")
            memory = getattr(self.live, "memory", None)
            if memory is not None:
                memory.log("nurse_visits", {"note": "logged on the nurse dashboard"})
            return None                                   # Live also records it (last visit, next-visit list)
        if path == "alert" and str(body.get("id", "")).startswith("agent-"):
            ok = body.get("action") == "ack" and self.supervisor.acknowledge(self.pid, body["id"])
            if ok:
                self._resolve_alert_proposal(body["id"])
                self.record("Nurse", "On my way", next((a["text"] for a in self.supervisor.alerts(self.pid)
                                                        if a["id"] == body["id"]), ""))
            return {"ok": True} if ok else {"ok": False, "error": "No such alert action."}
        if path == "patient/request":
            req, reason = body.get("request"), body.get("reason")
            confirmed, time_sensitive = body.get("confirmed", False), body.get("time_sensitive", False)
            if not isinstance(req, str) or not 0 < len(req.strip()) <= 300:
                return {"ok": False, "error": "Enter a request."}
            if (reason is not None and not isinstance(reason, str)
                    or not isinstance(confirmed, bool) or not isinstance(time_sensitive, bool)):
                return {"ok": False, "error": "Invalid patient request details."}
            req = req.strip()
            with self.live.lock:
                profile = self.profile()
                alert_id, created = self.supervisor.review_request(
                    profile["patient"]["id"], req, at=clock.hhmm(), profile=profile, reason=reason,
                    confirmed=confirmed, request_id=body.get("request_id"), time_sensitive=time_sensitive)
                if created:
                    self.record("Patient agent", "Requested help", req)
            return {"ok": True, "id": alert_id}
        if path == "check-in/suggest":
            choice = body.get("choice")
            if choice not in CARE_CHOICES:
                return {"ok": False, "error": "Choose a care topic."}
            label, question, request = CARE_CHOICES[choice]
            self.supervisor.queue_question(self.pid, {
                "id": f"{self.pid}-care-{choice}-{next(self._ids)}", "evidence": self.profile()["status"],
                "preference": " · ".join(self.profile()["preferences"][1:3]),
                "steps": [{"question": question, "request": request}]})
            return {"ok": True}
        if path == "proposal/refine":
            p = self.proposal
            if not p or p["id"] != body.get("id") or p.get("source") == "ai":
                return {"ok": False, "error": "This proposal is no longer available for drafting."}
            try:
                refined = nurse_ai.refine_checkin(p)          # outside the live lock
            except Exception:   # noqa: BLE001
                return {"ok": False, "error": "Claude could not draft the questions. The original proposal is available."}
            if self.proposal != p:
                return {"ok": False, "error": "The proposal changed during drafting."}
            self.refined[p["id"]] = refined
            return {"ok": True, "proposal": refined}
        if path in ("proposal/approve", "proposal/dismiss"):
            with self.live.lock:
                p = self.proposal
                if not p or body.get("id") != p["id"]:
                    return {"ok": False, "error": "This proposal is no longer available."}
                origin = p.get("origin_alert")
                if path == "proposal/approve":
                    if p.get("source") != body.get("source"):
                        return {"ok": False, "error": "Review the updated proposal before sending it."}
                    if self._awaiting_answer():
                        return {"ok": False, "error": "The patient is still answering a question."}
                    self._send_check_in(p["steps"], origin)
                    if origin:
                        self._update_request(origin, "waiting_answer")
                elif origin:
                    self._update_request(origin, "needs_review")
                self.supervisor.resolve_question(self.pid, p["id"])
                self.refined.pop(p["id"], None)
                return {"ok": True}
        if path in ("alert/question/draft", "alert/question/send"):
            return self._request_question(path, body)
        if path == "question/draft":
            with self.live.lock:
                alert = self._find_alert(body["id"]) if "id" in body else None
                if "id" in body and alert is None:
                    return {"ok": False, "error": "This request is no longer open."}
                profile = self.profile()
            request = (alert.get("request") or alert["text"]) if alert is not None else None
            draft = nurse_ai.draft_question(profile, request=request, topic=body.get("topic", ""))
            if self.pid != profile["patient"]["id"]:
                return {"ok": False, "error": "The active patient changed. Review their profile before continuing."}
            return draft
        if path == "send":
            with self.live.lock:
                if self._awaiting_answer():
                    return {"ok": False, "error": "The patient is still answering a question."}
                q = str(body.get("question") or "").strip()
                choices = body.get("options")
                if not q or not isinstance(choices, list) or any(not isinstance(choice, str) for choice in choices):
                    return {"ok": False, "error": "Enter a question and at least two answers."}
                options = [choice.strip() for choice in choices if choice.strip()]
                if len(options) < 2:
                    return {"ok": False, "error": "Enter a question and at least two answers."}
                qid = self.live.send_question(q, options, self.live.now())
                self.live.session["thread"][-1]["_patient_id"] = self.pid
                self.record("Nurse", "Asked", q)
                return {"ok": True, "qid": qid}
        if path == "session/end":
            with self.live.lock:
                thread = self.live.session["thread"]
                if thread:
                    question = thread[-1]
                    question["_cancelled"] = True
                    origin = question.get("_request_alert") or question.get("_origin_alert")
                    if origin and (self.live.session["waiting"] or question.get("_following_up")):
                        self._update_request(origin, "needs_review")
            return None                                    # Live returns the patient screen to normal
        return None

    def _find_alert(self, alert_id) -> Optional[dict]:
        for a in self.supervisor.alerts(self.pid) + list(self.live.alerts):
            if a["id"] == alert_id and not a.get("done"):
                return a
        return None

    def _request_question(self, path: str, body: dict) -> dict:
        """The nurse asks the patient about one request; each answer comes with a next step for the nurse."""
        question = str(body.get("question") or "").strip()
        if not 0 < len(question) <= 300:
            return {"ok": False, "error": "Enter a question of up to 300 characters."}
        if path == "alert/question/draft":                # Claude drafts the 2 answers + next steps (or defaults)
            with self.live.lock:
                alert = self._find_alert(body.get("id"))
                if alert is None:
                    return {"ok": False, "error": "This request is no longer open."}
                profile = self.profile()
            return {"ok": True, **nurse_ai.draft_request_branches(question, alert.get("request") or alert["text"],
                                                                 profile)}
        try:
            branches = nurse_ai.validate_request_branches(body.get("branches"))
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        with self.live.lock:
            alert = self._find_alert(body.get("id"))
            if alert is None:
                return {"ok": False, "error": "This request is no longer open."}
            if self._awaiting_answer():
                return {"ok": False, "error": "The patient is still answering a question."}
            qid = self.live.send_question(question, [b["label"] for b in branches], self.live.now())
            self.live.session["thread"][-1].update(_request_alert=alert["id"], _branches=branches,
                                                   _patient_id=self.pid,
                                                   _branch_source="ai" if body.get("source") == "ai" else "fallback")
            self._update_request(alert["id"], "waiting_answer")
            self._resolve_alert_proposal(alert["id"])
            self.record("Nurse", "Asked", question)
            return {"ok": True, "qid": qid}

    def _resolve_alert_proposal(self, alert_id: str) -> None:
        proposal_id = f"{alert_id}-question"
        self.supervisor.resolve_question(self.pid, proposal_id)
        self.refined.pop(proposal_id, None)

    def _send_check_in(self, steps: list, origin: Optional[str]) -> None:
        question = steps[0]["question"]
        self.live.send_question(question, ["Yes", "No"], self.live.now())
        self.live.session["thread"][-1].update(_check_in_steps=steps, _origin_alert=origin, _patient_id=self.pid)
        self.record("Nurse", "Asked", question)

    def _awaiting_answer(self) -> bool:
        session = self.live.session
        return bool(session["waiting"] or (session["thread"] and session["thread"][-1].get("_following_up")))

    def _follow_up(self, previous: dict, steps: list, origin: Optional[str]) -> None:
        with self.live.lock:
            session = self.live.session
            previous.pop("_following_up", None)
            if (session["thread"] and session["thread"][-1] is previous and not previous.get("_cancelled")
                    and previous["_patient_id"] == self.pid and not session["waiting"]):
                self._send_check_in(steps, origin)

    def _update_request(self, alert_id: str, state: str, reply: Optional[dict] = None) -> None:
        if str(alert_id).startswith("agent-"):
            self.supervisor.update_question_request(self.pid, alert_id, state, reply)
            return
        a = next((x for x in self.live.alerts if x["id"] == alert_id), None)
        if a and not a.get("done"):
            a["review"] = state
            if reply is not None:
                a["question_reply"] = reply

    # ----- the patient answered a nurse question (Live calls this, under its lock) ----------------
    def on_answer(self, q: dict, answer: str) -> None:
        if q.get("_cancelled") or q.get("_patient_id", self.pid) != self.pid:
            return
        if answer not in q["options"]:
            return
        self.record("Patient", "Chose", answer)
        idx = q["options"].index(answer)
        if q.get("_request_alert"):
            branch = q["_branches"][min(idx, len(q["_branches"]) - 1)]
            q["next_step"] = branch["next_step"]
            self._update_request(q["_request_alert"], "answered",
                                 {"answer": answer, "next_step": branch["next_step"], "source": q["_branch_source"]})
            self.record("Nurse supervisor", "Suggested next step", branch["next_step"])
        if q.get("_check_in_steps"):
            step, *rest = q["_check_in_steps"]
            origin = q.get("_origin_alert")
            if idx == 0:
                self.supervisor.receive_request(self.pid, step["request"], at=clock.hhmm(), delivery="check-in")
                self.record("Patient agent", "Reported to nurse", step["request"])
                if origin:
                    self._update_request(origin, "clarified")
            elif rest:
                q["_following_up"] = True
                threading.Thread(target=self._follow_up, args=(q, rest, origin), daemon=True).start()
            elif origin:
                self._update_request(origin, "needs_review")
