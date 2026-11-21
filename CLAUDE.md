# LookAt: project rules (read before every task)

## What it is
LookAt is an AI agent that talks first, for patients who can only move their eyes (ALS, locked-in syndrome, ventilated ICU patients). It watches the room, learns the patient and puts the right question in front of them before they have to ask. The patient answers by LOOKING left / center / right at a screen (projector, fallback monitor) and confirming with a short EYEBROW RAISE. The agent fixes what it can itself (lamp, fan acting as the AC), checks its work, speaks up for the patient to the nurse, and writes the shift summary. Tagline: "LookAt speaks first, so patients need fewer glances, and nurses need fewer calls."

## Controls (constants in config.py)
- Look at a spot for DWELL_S = 1.0 s -> it is highlighted (state ASKING, spot locked). Short eyebrow raise held >= CONFIRM_S = 0.3 s within ASK_TIMEOUT_S = 3 s = choose it. Then COOLDOWN_S = 2 s. Looking away, timeout or losing the face cancels.
- LONG eyebrow raise held >= EMERGENCY_S = 2.0 s = CALL NURSE, on any screen, checked before everything else.
- At most 2 answers on screen at a time (left, right). Center = "Return to main menu", "Back", "Engage", "Talk to AI" or "More options" (pages through longer answer lists).
- Backup confirm for someone whose eyebrows fail calibration: mouth open (jawOpen) held >= 0.7 s.

## The patient screens (lookat/screens.py = logic, lookat/display.py = page). Labels exactly as written.
- IDLE: "You're all set." / "I'm here if you need anything." Top-left card "Room Controls" (look left + short raise). Center pill "Talk to AI" (look center + short raise). Bottom banner "Call Nurse • Long eyebrow raise anywhere".
- SUGGESTION (AI notification pending): Room Controls still top-left; center card badge "AI SUGGESTION" + the agent's text + "Look center + raise eyebrows to engage". Fades back to IDLE after SUGGESTION_FADE_S if ignored.
- CHECKIN (active AI): badge "AI CHECK-IN", the question, left/right answers (default "Yes, please." / "No, thanks."), center "Return to main menu". Open questions (more than 2 answers, "Something else" last) page 2 at a time: center = "More options", and "Return to main menu" on the last page. NO Room Controls. "No, thanks" may lead to another fitting suggestion; "Return to main menu" stops the stream.
- ASSISTANT (patient chose Talk to AI): "How can I help you?", left = the current option from the agent's ranked list (default "Get more comfortable", "Quiet for 2 hours", "Adjust the room", "Something else"), right = "More suggestions" (next option), center "Return to main menu".
- ROOM: "What would you like to adjust?" left "Adjust the lights", right "Adjust the temperature", center "Return to main menu". LIGHTS: Lamp on / Lamp off / Back. TEMPERATURE: Cooler (fan on) / Warmer (fan off) / Back.
- NURSE (session opened by the nurse): pill "From your nurse", "Connected", her question, her answers 2 per page, center "More options", page dots. Room Controls, Return and the Call Nurse banner are hidden. Only the nurse ends the session; then IDLE and the agent resumes.
- Style: deep navy gradient, glassy glowing rounded cards, white bold text, light-blue hints, LookAt eye logo. Readable from 2-3 m.

## Devices (lookat/plugs.py + lookat/devices.py)
- lamp = REAL Kasa plug 1 (LAMP_IP). fan = REAL Kasa plug 2 (FAN_IP) acting as the AC; empty FAN_IP -> simulated, shown as "(simulated)".
- Temperature = a demo sensor value (set from the demo panel; falls while the fan runs, drifts to the setpoint otherwise). Say so honestly: an ESP32 + DHT22 would replace it.
- Every action from anyone goes through Room.execute(device, cmd, by="patient"|"agent"|"routine"|"nurse") and is logged to memory.

## Time (lookat/clock.py)
- Everything that uses time of day uses lookat.clock (never datetime.now() directly), so the demo can "Set 02:00", "Set 07:30", "Skip to 21:59". When the demo clock is set, the panel shows a DEMO TIME badge.

## The agent (AI agent challenge: must NOT be a thin AI wrapper)
- awake.py: eyes open / awake from eyeBlinkLeft/Right (no face = unknown, never "asleep").
- memory.py (pure Python + JSON, no AI): memory/<patient>.json (git-ignored): comfort facts ("Runs warm at night", "Feels cold below 20 °C"), actions, questions + answers, suggestions (engaged/ignored/declined), check-ins, routines, nurse visits, pages, deferred items. learn() is deterministic: comfort thresholds, routine candidates (same action on >= 3 days in a 60-min window), option ranking (never-picked options hidden, used ones move up; "Something else" never hidden), silence (3 ignored night check-ins -> stop them).
- agent.py: deterministic TRIGGERS decide WHEN (night + awake + warm + runs warm; cold; nurse setpoint below the patient's cold threshold -> flag; routine candidate; patient-initiated). Claude decides WHAT through tools: get_context, suggest, ask, act, check, page_nurse, defer, remember, propose_routine, finish. Every tool call has a "why" for the reasoning panel (panel.py).
- It speaks first, checks its work (after acting: re-read the sensor, ask "Better now?", try one other fix or page the nurse), respects silence (ignored = fade/stop, "Quiet for 2 hours"), speaks up ("Patient feels cold below 20 °C. Ask them first?"), and explains its pages ("Wants repositioning. Not temperature-related. Last visit 3h ago."). What can wait is deferred to the next visit / shift summary (summary.py).
- routines.py: runs approved routines at their time (e.g. lamp off 22:00), announced on screen, logged by="routine". demo_week.py loads a LABELLED demo week for the "One week later" demo step; the real detection and scheduler code runs on it.

## Safety (enforced IN CODE)
- Comfort, environment and communication only. Pain, breathing, distress or anything clinical -> page_nurse urgent at once. Never medical advice.
- act() only after the patient's matching Yes; routines only after the patient approved them. Nothing automatic runs during a nurse session or while the patient is choosing.
- "Call Nurse" (long raise) works on every screen; every open question offers "Something else".
- Any AI failure (no key, error, timeout) falls back to rules, marked "(fallback)" on the panel.

## Nurse app (lookat/nurse.py, phone) + Ask anything (lookat/ask.py)
- Alerts (calls, explained pages, flags with [Ask them] [Set anyway], "For your next visit"), conversation (type any question -> AI suggests answers -> edit, any number -> send -> live answers with seconds -> AI follow-up suggestions -> End session), room controls (setpoint, lamp, fan), patient (facts, requests, routines, "I'm here" visit, shift summary).
- Pages: /patient (projector), /nurse (phone), /panel (reasoning + demo controls, operator/judges), all on config.PAGE_PORT. The pages are Lea's, in web/ (layouts lookat/server.py finds: docs/FRONTEND.md). They only talk to the backend through the JSON API in docs/API.md: change that file first whenever a shape changes. The backend has NO design of its own: lookat/fallback/ is a bare team test page used only while a page of Lea's is missing; never style it or build features into it. lookat/server.py (make_app) is the one server for both `py -3.11 -m lookat.fake_api` (fake data) and main.py (real data). `py -3.11 -m lookat.check_frontend` must PASS before web/ changes are merged.

## Hard rules
- Windows 11 with Smart App Control ON. Run Python as `py -3.11`. Pinned: mediapipe==0.10.14, opencv-contrib-python==4.10.0.84, numpy<2, python-kasa==0.12.0, plus anthropic, fastapi, uvicorn[standard], pyttsx3, pydantic, pytest. NEVER add scikit-learn, scipy or other new dependencies (they get blocked). If an import fails with "An Application Control policy has blocked this file", do not upgrade; use plain Python/NumPy instead.
- The camera loop runs in the MAIN thread (cv2.imshow must). Plugs, web server, agent, scheduler and Claude calls each run in their own worker thread. Communicate only through the queues and the lock-protected State in lookat/state.py. Nothing may block the camera loop.
- Secrets only from environment variables: ANTHROPIC_API_KEY, KASA_USER, KASA_PASSWORD, LAMP_IP (default 172.20.10.2), FAN_IP. Never print or commit them.
- Claude: official `anthropic` Python SDK, model "claude-opus-5-5". ask.py: output_config effort "low", structured output via client.messages.parse with a Pydantic model, 8 s timeout. agent.py: manual tool-use loop, thinking adaptive, 15 s per call, max 6 turns. summary.py: 8 s timeout, template fallback, never invent events.
- Every module has an `if __name__ == "__main__":` self-test that a person can run with `py -3.11 -m lookat.<module>` and that prints PASS/FAIL or shows a window/page.
- Pure logic (selection engine, screens, classifier, gesture timing, awake, memory/learn, triggers, scheduler, clock, temperature model) gets pytest tests in tests/. Run `py -3.11 -m pytest -q` before every commit.
- Log every event as one JSON line to logs/events.jsonl (time, type, details).
- Keep code small and readable. Only edit files your task names. Owners: web/ (the 3 pages) (P1 Lea); vision, gaze, gestures, awake, machine, screens, clock, server (Python side of the API), fake_api, fallback/, check_frontend, speech (spoken feedback), docs/API.md, docs/FRONTEND.md, main, live, plugs, devices, memory, agent, routines, summary, demo_week (P2 Belinda); ask, README/DEVPOST (P3). lamp.py, voice.py, page.py, sim.py, bench.py are unused leftovers from the old design: don't build them.
- Work on your own branch (p1-vision, p2-brain, p3-actions). NEVER commit, merge or push to main directly.
- After each task: run its self-test, show the output, append one line to progress/<your branch>.md, e.g. progress/p2-brain.md ("<time> <prompt id>: <what works>"; one file per branch so PRs never conflict on it; PROGRESS.md is the old shared log, read it but don't append), `git pull origin main` into your branch, commit and push to your branch, then open a PULL REQUEST into main (`gh pr create --base main --title "<prompt id>: <what>" --body "<what changed, self-test output, how to verify>"`; without gh, give the person the "Create a pull request" link that git push printed). Do not merge it.
- Every PR gets a code review BEFORE merging: run the engineering code-review skill (/code-review-engineering, or /engineering:code-review) on the PR's changes, fix real findings on the same branch, push, and note the review result in the PR. Merging into main happens only on GitHub ("Merge pull request"), by P2, after the review and the prompt's Verify step both pass.
- Hackathon rule: all code is written during the event (after Fri 7 PM).
- Commits: never add Co-Authored-By or any Claude attribution lines to commit messages.
