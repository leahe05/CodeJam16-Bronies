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
        self.link = NurseLink(None)
        self.link.set_visitors(False)
        if decide is None and nurse_ai.available():
            decide = nurse_ai.decide_request
        self.supervisor = NurseSupervisor(decide=decide)
        for pid in PROFILES:
            self.supervisor.register(pid, lambda prompt: {})
        self.refined: dict = {}                    # proposal id -> the version Claude reworded
        self.approved: list = []                    # the approved check-in's remaining steps
        self.origin_alert: Optional[str] = None     # the request an approved question clarifies
        self._ids = itertools.count(1)
        self._stop = threading.Event()

    def start(self) -> None:
        if self.supervisor.ai_enabled:
            for _ in range(2):
                threading.Thread(target=self.supervisor.run_reviews, args=(self._stop,), daemon=True,
                                 name="nurse-review").start()

    def stop(self) -> None:
        self._stop.set()

    def reset(self) -> None:
        """Reset demo: the dashboard's history, requests and proposals go too (the profiles stay)."""
        self.link.reset()
        self.link.set_visitors(False)
        for pid in PROFILES:
            self.supervisor.reset_patient(pid)
        self.refined, self.approved, self.origin_alert = {}, [], None

    @property
    def proposal(self) -> Optional[dict]:
        """The first check-in waiting for the nurse's approval (Claude's, or one the nurse picked)."""
        queued = self.supervisor.pending_questions(self.pid)
        return (self.refined.get(queued[0]["id"]) or queued[0]) if queued else None

    # ----- who and what -------------------------------------------------------------------------
    @property
    def pid(self) -> str:
        return self.live.profile or "margaret"

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

    # ----- GET /api/nurse -----------------------------------------------------------------------
    def extend(self, data: dict) -> dict:
        snap = self.link.snapshot(time.monotonic())
        snap["awareness"] = self._awareness()
        snap["vitals"] = {"simulated": True, **{k: v for k, v in self._vitals()["current"].items() if k != "time"}}
        data.update(patient=self.patient(), profile=self.profile(), bedside=snap,
                    history=self.link.interaction_history(), proposal=self.proposal,
                    request_question_branches=True, ai_drafts=nurse_ai.available(),
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
        alert_id, _ = self.supervisor.review_request(self.pid, text, at=clock.hhmm(), profile=self.profile(),
                                                     reason=reason, confirmed=True)
        self.record("Patient agent", "Requested help", text)
        log_event("nurse_request", id=alert_id, text=text, reason=reason)
        return {"id": alert_id, "kind": "page", "text": text, "reason": reason}

    # ----- POST /api/nurse/... --------------------------------------------------------------------
    def action(self, path: str, body: dict) -> Optional[dict]:
        """Handles the dashboard's paths that Live doesn't know; None = not mine (Live handles it)."""
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
                self.record("Nurse", "On my way", next((a["text"] for a in self.supervisor.alerts(self.pid)
                                                        if a["id"] == body["id"]), ""))
            return {"ok": True} if ok else {"ok": False, "error": "No such alert action."}
        if path == "patient/request":
            req = str(body.get("request") or "").strip()
            if not req or len(req) > 300:
                return {"ok": False, "error": "Enter a request."}
            alert_id, _ = self.supervisor.review_request(
                self.pid, req, at=clock.hhmm(), profile=self.profile(), reason=body.get("reason"),
                confirmed=bool(body.get("confirmed")), request_id=body.get("request_id"),
                time_sensitive=bool(body.get("time_sensitive")))
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
                refined = nurse_ai.refine_checkin(p)          # outside the live lock: Claude, up to 8 s
            except Exception:   # noqa: BLE001
                return {"ok": False, "error": "Claude could not draft the questions. The original proposal is available."}
            if self.proposal != p:
                return {"ok": False, "error": "The proposal changed during drafting."}
            self.refined[p["id"]] = refined
            return {"ok": True, "proposal": refined}
        if path in ("proposal/approve", "proposal/dismiss"):
            p = self.proposal
            if not p or body.get("id") != p["id"]:
                return {"ok": False, "error": "This proposal is no longer available."}
            if path == "proposal/approve":
                if p.get("source") != body.get("source"):
                    return {"ok": False, "error": "Review the updated proposal before sending it."}
                if self.live.session.get("waiting"):
                    return {"ok": False, "error": "The patient is still answering a question."}
                self.approved = list(p["steps"])
                self.origin_alert = p.get("origin_alert")
                self.record("Nurse", "Approved", p["steps"][0]["question"])
                self.live.send_question(p["steps"][0]["question"], ["Yes", "No"], self.live.now())
                if self.origin_alert:
                    self.supervisor.update_question_request(self.pid, self.origin_alert, "waiting_answer")
            elif p.get("origin_alert"):
                self.supervisor.update_question_request(self.pid, p["origin_alert"], "needs_review")
            self.supervisor.resolve_question(self.pid, p["id"])
            self.refined.pop(p["id"], None)
            return {"ok": True}
        if path in ("alert/question/draft", "alert/question/send"):
            return self._request_question(path, body)
        if path == "send":
            q = str(body.get("question") or "").strip()
            if q:
                self.record("Nurse", "Asked", q)
            self.approved = []
            return None                                   # Live sends it
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
        alert = self._find_alert(body.get("id"))
        if alert is None:
            return {"ok": False, "error": "This request is no longer open."}
        if path == "alert/question/draft":                # Claude drafts the 2 answers + next steps (or defaults)
            return {"ok": True, **nurse_ai.draft_request_branches(question, alert.get("request") or alert["text"],
                                                                 self.profile())}
        if self.live.session.get("waiting"):
            return {"ok": False, "error": "The patient is still answering a question."}
        try:
            branches = nurse_ai.validate_request_branches(body.get("branches"))
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        self.approved = []
        qid = self.live.send_question(question, [b["label"] for b in branches], self.live.now())
        with self.live.lock:
            self.live.session["thread"][-1].update(_request_alert=alert["id"], _branches=branches,
                                                   _branch_source="ai" if body.get("source") == "ai" else "fallback")
        self._update_request(alert["id"], "waiting_answer")
        self.record("Nurse", "Asked", question)
        return {"ok": True, "qid": qid}

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
        self.record("Patient", "Chose", answer)
        idx = q["options"].index(answer) if answer in q["options"] else 0
        if q.get("_request_alert"):
            branch = q["_branches"][min(idx, len(q["_branches"]) - 1)]
            q["next_step"] = branch["next_step"]
            self._update_request(q["_request_alert"], "answered",
                                 {"answer": answer, "next_step": branch["next_step"], "source": q["_branch_source"]})
            self.record("Nurse supervisor", "Suggested next step", branch["next_step"])
        if self.approved:                                  # an approved check-in: Yes = report it, No = next one
            step, *rest = self.approved
            self.approved = []
            if idx == 0:
                self.supervisor.receive_request(self.pid, step["request"], at=clock.hhmm(), delivery="check-in")
                self.record("Patient agent", "Reported to nurse", step["request"])
                self.live.nurse_called()                   # the patient sees "nurse is coming"
                if self.origin_alert:
                    self.supervisor.update_question_request(self.pid, self.origin_alert, "clarified")
                    self.origin_alert = None
            elif rest:
                self.approved = rest
                threading.Thread(target=self.live.send_question, args=(rest[0]["question"], ["Yes", "No"], self.live.now()),
                                 daemon=True).start()
        if not self.approved and self.origin_alert:
            self.supervisor.update_question_request(self.pid, self.origin_alert, "needs_review")
            self.origin_alert = None
