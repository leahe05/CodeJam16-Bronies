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
import logging
import math
import threading
import time
from collections import deque
from datetime import timedelta
from typing import Optional

from lookat import nurse_ai
from lookat.clock import clock, parse_hhmm
from lookat.memory import PROFILES, routine_time
from lookat.nurse_link import NurseLink, eta_text, valid_eta_minutes
from lookat.nurse_patient_profile import CARE_CHOICES
from lookat.screens import SEEN
from lookat.nurse_supervisor import NurseSupervisor
from lookat.state import log_event

ROOMS = {"margaret": "Room 12", "james": "Room 14"}
# Simulated vitals for the demo (labelled "Simulated vitals" on the dashboard): heart rate, SpO2, breathing, BP.
VITALS = {"margaret": ((74, 97, 16, 124, 76), (76, 97, 16, 126, 77), (75, 98, 15, 125, 76)),
          "james": ((68, 98, 14, 132, 84), (70, 98, 15, 130, 83), (69, 97, 14, 131, 84))}
FEED_ACTIONS = {"decided": "Decided", "did": "Did", "outcome": "Outcome", "learned": "Learned"}
STATUS_SAMPLE_S = 2.0
STATUS_REVIEW_GAP_S = 20.0
STATUS_NOTE_MAX_AGE_S = 60.0
log = logging.getLogger(__name__)


def observation_changes(previous, current):
    changes = []
    overdue = current.get("minutes_past_bedtime")
    if overdue and not previous.get("minutes_past_bedtime"):
        changes.append(f"Patient appears awake {overdue} minutes past their usual bedtime.")
    before, after = previous["room"], current["room"]
    if abs(after["temp_c"] - before["temp_c"]) >= 1:
        changes.append(f"Room reading changed from {before['temp_c']:g} to {after['temp_c']:g} °C.")
    for device in ("fan", "lamp"):
        if before[f"{device}_on"] != after[f"{device}_on"]:
            changes.append(f"{device.capitalize()} is now {'on' if after[f'{device}_on'] else 'off'}.")
    old_awareness, awareness = previous["awareness"]["state"], current["awareness"]["state"]
    if old_awareness != awareness and awareness in ("awake", "asleep"):
        changes.append(f"Patient now appears {'awake' if awareness == 'awake' else 'to be resting'}.")
    if previous["preferences"] != current["preferences"] or previous["habits"] != current["habits"]:
        changes.append("The patient agent updated the patient's preferences or habits.")
    return changes


class NurseBridge:
    def __init__(self, live, decide=None, *, assess=None):
        self.live = live
        self._links = {pid: NurseLink(None) for pid in PROFILES}
        for link in self._links.values():
            link.set_visitors(False)
        if decide is None and nurse_ai.available():
            decide = nurse_ai.decide_request
        if assess is None and nurse_ai.available():
            assess = nurse_ai.assess_status
        self.supervisor = NurseSupervisor(decide=decide)
        for pid in PROFILES:
            self.supervisor.register(pid, lambda prompt, pid=pid: self.check_in(pid, prompt))
        self.refined: dict = {}                    # proposal id -> the version Claude reworded
        self._ids = itertools.count(1)
        self._stop = threading.Event()
        self._assess = assess
        self._status_changed = threading.Event()
        self._status_notes = {pid: deque(maxlen=4) for pid in PROFILES}
        self._status_profiles = {}
        self._status_reviewed_at = {}
        self._status_failure = {}
        self._status_generation = 0
        self._status_inflight = set()
        self._attach_eta_view()
        self._track_eta_recipients()

    def start(self) -> None:
        threading.Thread(target=self.supervisor.run, args=(self._stop,), daemon=True, name="nurse-check-in").start()
        threading.Thread(target=self._monitor_status, daemon=True, name="nurse-status").start()
        if self.supervisor.ai_enabled:
            for _ in range(2):
                threading.Thread(target=self.supervisor.run_reviews, args=(self._stop,), daemon=True,
                                 name="nurse-review").start()

    def stop(self) -> None:
        self._stop.set()
        self._status_changed.set()

    def reset(self) -> None:
        """Reset demo: the dashboard's history, requests and proposals go too (the profiles stay)."""
        for link in self._links.values():
            link.reset()
            link.set_visitors(False)
        for pid in PROFILES:
            self.supervisor.reset_patient(pid)
            self._status_notes[pid].clear()
        self._status_profiles.clear()
        self._status_reviewed_at.clear()
        self._status_failure.clear()
        self._status_generation += 1
        self.refined.clear()
        self._attach_eta_view()

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

    def _attach_eta_view(self) -> None:
        """Present the nurse message through the patient screen's existing subtitle."""
        screen = self.live.sm
        if getattr(self, "_eta_screen", None) is screen:
            return
        original_view = screen.view

        def view(now):
            data = original_view(now)
            message = self.link.eta_message(now)
            if message:
                data["subtitle"] = " ".join(filter(None, (message, data.get("subtitle"))))
            return data

        screen.view = view
        self._eta_screen = screen

    def _track_eta_recipients(self) -> None:
        """Bind native calls and urgent pages to their original patient profile."""
        add_alert = self.live.add_alert

        def tagged_alert(*args, **kwargs):
            with self.live.lock:
                patient_id = self.pid
                alert = add_alert(*args, **kwargs)
                if alert["kind"] in ("call", "page") and not alert["id"].startswith("agent-"):
                    alert["_eta_patient_id"] = patient_id
                return alert

        self.live.add_alert = tagged_alert

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
        now = clock.now()
        minutes_past_bedtime = None
        bedtime = routine_time(memory, "bedtime") if memory is not None else None
        if bedtime and aware["state"] == "awake":
            try:
                hour, minute = parse_hhmm(bedtime)
                elapsed = (now.hour * 60 + now.minute - hour * 60 - minute) % (24 * 60)
                if 30 <= elapsed <= 180:
                    minutes_past_bedtime = elapsed
            except ValueError:
                pass  # The patient chart may contain an invalid time.
        return {"patient": self.patient(), "_observed_at": time.monotonic(), "reported_at": now.strftime("%H:%M:%S"),
                "status": status + (f" · LookAt: {self.live.agent_status}" if getattr(self.live, "agent_status", None) else ""),
                "minutes_past_bedtime": minutes_past_bedtime,
                "posture": "In bed", "awareness": aware, "visitors_present": self.link.snapshot(time.monotonic())["visitors_present"],
                "room": {"temp_c": r["temp_c"], "setpoint_c": r["setpoint_c"], "lamp_on": r["lamp_on"], "fan_on": r["fan_on"]},
                "vitals": self._vitals(),
                "preferences": [f["text"] for f in facts if f.get("kind") != "routine"],
                "_comfort_preferences": [f["text"] for f in facts if f.get("kind") in ("comfort", "preference")],
                "habits": [f["text"] for f in facts if f.get("kind") == "routine"] + habits,
                "care_choices": [{"id": k, "label": v[0]} for k, v in CARE_CHOICES.items()]}

    def _monitor_status(self) -> None:
        while not self._stop.is_set():
            self._status_changed.clear()
            try:
                self._review_status_change()
            except Exception as error:
                log.warning("Patient status sampling failed (%s)", type(error).__name__)
            self._status_changed.wait(STATUS_SAMPLE_S)

    def _status_review_paused(self) -> bool:
        return (self.live.session["active"] or self.live.calibration is not None or not self.live.sm.can_interrupt()
                or self.live.machine.busy
                or self.live.agent_status.startswith("Quiet: the patient asked for quiet")
                or any(alert.get("urgency") == "urgent" and not alert.get("done")
                       for alert in self.live.alerts + self.supervisor.alerts(self.pid)))

    def _review_status_change(self) -> None:
        with self.live.lock:
            profile = self.profile()
            pid = profile["patient"]["id"]
            self.supervisor.publish_profile(pid, profile)
            previous = self._status_profiles.setdefault(pid, {**profile, "minutes_past_bedtime": None})
            changes = observation_changes(previous, profile)
            if changes or profile["awareness"]["state"] != "awake":
                self.supervisor.clear_status_questions(pid)
            now = time.monotonic()
            last_review = self._status_reviewed_at.get(pid, -math.inf)
            notes = self._status_notes[pid]
            changes.extend(text for at, text, pending in notes if pending and now - at <= STATUS_NOTE_MAX_AGE_S)
            token = (pid, self._status_generation)
            if (self._assess is None or not changes or now - last_review < STATUS_REVIEW_GAP_S
                    or token in self._status_inflight or self._status_review_paused()):
                return
            self._status_profiles[pid] = profile
            self._status_reviewed_at[pid] = now
            self._status_notes[pid] = deque(((at, text, False) for at, text, _ in notes), maxlen=notes.maxlen)
            self._status_inflight.add(token)
        threading.Thread(target=self._assess_status, args=(token, profile, changes), daemon=True,
                         name="nurse-status-review").start()

    def _assess_status(self, token, profile, changes) -> None:
        try:
            assessment = self._assess(profile, changes)
            failure = None
        except Exception as error:
            assessment, failure = None, nurse_ai.failure_reason(error)
        with self.live.lock:
            self._status_inflight.discard(token)
            pid, generation = token
            if self._stop.is_set() or self._status_generation != generation or pid != self.pid:
                return
            self._status_failure[pid] = failure
            if not assessment or self._status_review_paused():
                return
            current = self.profile()
            if assessment["action"] == "ask" and current["awareness"]["state"] != "awake":
                return
            if not observation_changes(profile, current):
                self.supervisor.publish_status_assessment(pid, assessment, profile, changes)

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
        latest = self.supervisor.latest_profile(self.pid) or {}
        data.update(patient=self.patient(), profile=self.profile(), bedside=snap,
                    history=self.link.interaction_history(), proposal=self.proposal,
                    agent_checkin={"interval_s": self.supervisor.interval_s, "time": report.get("time"),
                                   "last_update": latest.get("reported_at"),
                                   "status_review_error": self._status_failure.get(self.pid),
                                   "needs_attention": report.get("needs_attention", False),
                                   "suggested_question": report.get("suggested_question", False)},
                    request_question_branches=True, question_drafts=True, arrival_eta=True,
                    eta_reminders=self.link.eta_reminders(self.live.now()),
                    ai_drafts=nurse_ai.available(), patient_id_guard=True,
                    alerts=self.supervisor.alerts(self.pid) + [
                        {**alert, "eta_available": alert.get("_eta_patient_id") == self.pid}
                        for alert in data.get("alerts") or []
                    ])
        return data

    # ----- what happened (the dashboard's timeline) ---------------------------------------------
    def record(self, actor: str, action: str, text: str) -> None:
        self.link.record_interaction(actor, action, text)

    def on_feed(self, stage: str, text: str) -> None:
        if stage in FEED_ACTIONS:
            self.record("Patient agent", FEED_ACTIONS[stage], text)
        if ((stage == "noticed" and text != "Read memory + room")
                or (stage == "outcome" and text.startswith("For the next visit:"))
                or (stage == "learned" and text.startswith("Remembered:"))):
            with self.live.lock:
                notes = self._status_notes[self.pid]
                now = time.monotonic()
                text = text[:300]
                if not any(note == text and now - at <= STATUS_NOTE_MAX_AGE_S for at, note, _ in notes):
                    notes.append((now, text, True))
                self._status_changed.set()

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
        if path == "alert/eta/follow-up":
            with self.live.lock:
                patient_id = self.pid
                if body.get("patient_id", patient_id) != patient_id:
                    return {"ok": False, "error": "The active patient changed. Review their profile before continuing."}
                return self._links[patient_id].follow_up_eta(body, self.live.now())
        if path == "alert/eta":
            minutes = body.get("eta_minutes")
            if not valid_eta_minutes(minutes):
                return {"ok": False, "error": "Enter a whole number of minutes from 1 to 120."}
            with self.live.lock:
                patient_id = self.pid
                if body.get("patient_id", patient_id) != patient_id:
                    return {"ok": False, "error": "The active patient changed. Review their profile before continuing."}
                alert = self._find_alert(body.get("id"), patient_id=patient_id)
                if alert is None or alert.get("kind") not in ("page", "call"):
                    return {"ok": False, "error": "This patient request is no longer open."}
                if alert["id"].startswith("agent-"):
                    if not self.supervisor.acknowledge(patient_id, alert["id"], eta_minutes=minutes):
                        return {"ok": False, "error": "This patient request is no longer open."}
                    self._resolve_alert_proposal(alert["id"], patient_id=patient_id)
                else:
                    if alert.get("_eta_patient_id") != patient_id:
                        return {"ok": False, "error": "This request belongs to a different patient profile."}
                    alert.update(done=True, eta_minutes=minutes)
                now = self.live.now()
                link = self._links[patient_id]
                request = alert.get("request") or alert["text"]
                link.send_eta(alert["id"], minutes, now, request=request)
                link.record_interaction("Nurse", "Sent ETA", f"{request} · {eta_text(minutes)}")
                return {"ok": True}
        if path == "presence":
            if "present" not in body:
                return {"ok": False, "error": "present is required"}
            res = self.link.set_visitors(body["present"])
            if res["ok"]:
                self.record("Nurse", "Updated", {True: "Visitors present", False: "No visitors"}.get(body["present"], "Visitors unknown"))
            return res
        if path == "visit":
            self.link.clear_eta()
            self.record("Nurse", "Visited", "Nurse visit logged")
            memory = getattr(self.live, "memory", None)
            if memory is not None:
                memory.log("nurse_visits", {"note": "logged on the nurse dashboard"})
            return None                                   # Live also records it (last visit, next-visit list)
        if path == "alert" and str(body.get("id", "")).startswith("agent-"):
            ok = body.get("action") == "ack" and self.supervisor.acknowledge(self.pid, body["id"])
            if ok:
                self._resolve_alert_proposal(body["id"])
                alert = next((a for a in self.supervisor.alerts(self.pid) if a["id"] == body["id"]), {})
                self.record("Nurse", "Acknowledged" if alert.get("delivery") == "status-update" else "On my way",
                            alert.get("text", ""))
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
                    if p.get("status_update"):
                        current = self.profile()
                        prior = self._status_profiles.get(self.pid, current)
                        if (self._status_review_paused() or current["awareness"]["state"] != "awake"
                                or observation_changes(prior, current)):
                            return {"ok": False, "error": "The bedside status changed. Review the patient before sending."}
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
                conversation = [{"question": q["question"], "answer": q["answer"]}       # narrow down from these
                                for q in self.live.session["thread"]
                                if q.get("kind", "question") == "question" and q.get("answer")]
            request = (alert.get("request") or alert["text"]) if alert is not None else None
            draft = nurse_ai.draft_question(profile, request=request, topic=body.get("topic", ""),
                                            conversation=conversation)
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
                if len(set(options)) < 2:                  # no answer choices: a plain message, the patient says OK
                    qid = self.live.send_question(q, [SEEN], self.live.now(), kind="message")
                    self.live.session["thread"][-1]["_patient_id"] = self.pid
                    self.record("Nurse", "Messaged", q)
                    return {"ok": True, "kind": "message", "qid": qid}
                qid = self.live.send_question(q, nurse_ai.with_other(options), self.live.now())
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

    def _find_alert(self, alert_id, *, patient_id=None) -> Optional[dict]:
        for a in self.supervisor.alerts(patient_id or self.pid) + list(self.live.alerts):
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
            if nurse_ai.with_other([b["label"] for b in branches]) != [b["label"] for b in branches]:
                branches = branches + [dict(nurse_ai.OTHER_BRANCH)]          # the patient can always say Other
            qid = self.live.send_question(question, [b["label"] for b in branches], self.live.now())
            self.live.session["thread"][-1].update(_request_alert=alert["id"], _branches=branches,
                                                   _patient_id=self.pid,
                                                   _branch_source="ai" if body.get("source") == "ai" else "fallback")
            self._update_request(alert["id"], "waiting_answer")
            self._resolve_alert_proposal(alert["id"])
            self.record("Nurse", "Asked", question)
            return {"ok": True, "qid": qid}

    def _resolve_alert_proposal(self, alert_id: str, *, patient_id=None) -> None:
        proposal_id = f"{alert_id}-question"
        self.supervisor.resolve_question(patient_id or self.pid, proposal_id)
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
                self.live.nurse_called()                   # the patient sees "nurse is coming"
                if origin:
                    self._update_request(origin, "clarified")
            elif rest:
                q["_following_up"] = True
                threading.Thread(target=self._follow_up, args=(q, rest, origin), daemon=True).start()
            elif origin:
                self._update_request(origin, "needs_review")
