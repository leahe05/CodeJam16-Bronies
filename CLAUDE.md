# LookAt: project rules (read before every task)

## What it is
A patient who cannot speak or move (e.g. intubated ICU patient) runs their room, talks with their nurse and gets help from an AI agent by LOOKING at one of 3 tiles projected on the wall (fallback: a monitor) and confirming with an EYEBROW RAISE. Every tile is also SPOKEN. The agent remembers what the patient does, finds routines, asks before automating them, then runs them on its own.

## The 3 tiles (left, middle, right)
- MENU mode (default), tree in config.MENU_TREE:
  top: Room | Assistant | Nurse
  room: Lamp (REAL smart plug) | AC (SIMULATED submenu: Warmer | Cooler | Back) | Back
  assistant: Help me rest | Uncomfortable | Suggest   (each starts the agent)
  Nurse = call the nurse (non-urgent card on her phone).
- No selection for MENU_TIMEOUT_S = 10 s in a submenu -> back to top. After a device action, stay in that menu.
- QUESTION mode: the tiles show a question's 2-3 choices ("" = empty, not selectable). Sources: nurse (typed), ai (ask.py), agent, routine announcement. Ends on answer, cancel or timeout (default 30 s; a question may set timeout_s and silent_timeout). Then back to the top menu.
- One meaning at a time.

## Interaction rules (constants live in config.py)
- Look at a tile for DWELL_S = 1.0 s -> outline it and speak its label ("Lamp?") -> state ASKING, tile locked.
- Confirm = eyebrow raise held >= CONFIRM_S = 0.3 s within ASK_TIMEOUT_S = 3 s. Then act, then COOLDOWN_S = 2 s.
- Looking away, timeout or losing the face cancels. Nothing ever happens without the eyebrow confirm (except approved routines, below).
- EMERGENCY = eyebrow held >= EMERGENCY_S = 2.0 s, in ANY mode, checked before everything else: say "Calling the nurse", flash the lamp 3 times, display "Help is coming", HELP card on the nurse page.
- Backup confirm gesture for a person whose eyebrows fail calibration: mouth open (jawOpen) held >= 0.7 s.

## Devices (lookat/devices.py)
- lamp = REAL (python-kasa plug). ac = SIMULATED (shown as "(simulated)" everywhere). Every action from anyone goes through Room.execute(device, cmd, by=...) and is logged to memory with the clock time.

## Time (lookat/clock.py)
- Everything that uses time of day uses lookat.clock (never datetime.now() directly), so "simulated nights" can run the real system with a fast clock. Whenever speed != 1, the display and nurse page show "SIMULATED TIME xN".

## The agent (lookat/agent.py + lookat/memory.py) - AI agent challenge: must NOT be a thin AI wrapper
- memory.py (pure Python + JSON, no AI): memory/<patient>.json (git-ignored): chart, facts, actions, suggestions + answers, routines, episodes (nurse calls/emergencies with the nurse's confirmed outcome).
- learn() (deterministic): routine candidates (same patient action on >= 3 days within a 60-min window), routine health (cancelled twice -> suggest pausing), preferences by time of day, things refused twice (avoid), cause priors from nurse-confirmed outcomes, responsiveness.
- Agent tools: recall (always first), get_room_state, ask_patient, run_action, propose_routine, notify_nurse, remember, finish. Tasks: assist:rest / assist:uncomfortable / assist:suggest / propose_routine / nurse_call / emergency.
- Safety enforced IN CODE: run_action only after a matching "Yes" answer in the same run; routines only after the patient said Yes to propose_routine; every routine run is announced with a 10 s "Cancel" tile; nothing automatic runs during a question, an agent run or HELP. Never medical advice; pain/breathing/distress -> notify_nurse at once.
- routines.py Scheduler: proposes candidates when the patient is idle; runs due routines with the cancel window.
- sim.py: --simulate-nights N drives the SAME pipeline (Machine -> actions -> Room -> memory) with a scripted patient and a fast clock. Never write fake history or edit memory files by hand.
- Any AI failure falls back to rules from learn() (agent) or YES/NO (ask.py).

## Nurse page (phone) + display (projector)
- page.py (nurse, phone): status, Ask anything (AI choices or write your own), patient calls, HELP card with 3 options (AI makes choices / write your own / AI agent asks), answers, routines, patient memory tab.
- display.py (patient, projector): /display + /state, 3 huge tiles, clock, room status, banner, calibration mode.

## Hard rules
- Every tile is SHOWN and SPOKEN. The operator window (camera, debug) is for the team only.
- Windows 11 with Smart App Control ON. Run Python as `py -3.11`. Pinned: mediapipe==0.10.14, opencv-contrib-python==4.10.0.84, numpy<2, python-kasa==0.12.0, plus anthropic, fastapi, uvicorn[standard], pyttsx3, pydantic, pytest. NEVER add scikit-learn, scipy or other new dependencies (they get blocked). If an import fails with "An Application Control policy has blocked this file", do not upgrade; use plain Python/NumPy instead.
- The camera loop runs in the MAIN thread (cv2.imshow must). Voice, lamp, web server, scheduler and Claude calls each run in their own worker thread. Communicate only through the queues and the lock-protected State in lookat/state.py. Nothing may block the camera loop.
- Secrets only from environment variables: ANTHROPIC_API_KEY, KASA_USER, KASA_PASSWORD, LAMP_IP (default 172.20.10.2). Never print or commit them.
- Claude: official `anthropic` Python SDK, model "claude-opus-5-5". ask.py: output_config effort "low", structured output via client.messages.parse with a Pydantic model, 8 s timeout. agent.py: manual tool-use loop, thinking adaptive, 15 s per call. On ANY error fall back as above.
- Every module has an `if __name__ == "__main__":` self-test that a person can run with `py -3.11 -m lookat.<module>` and that prints PASS/FAIL or shows a window.
- Pure logic (state machine, classifier, gesture timing, memory/learn, scheduler, clock) gets pytest tests in tests/. Run `py -3.11 -m pytest -q` before every commit.
- Log every event as one JSON line to logs/events.jsonl (time, type, details).
- Keep code small and readable. Only edit files your task names. Owners: vision, gaze, memory, agent (P1); gestures, machine, clock, routines, sim, main (P2); lamp, voice, ask, devices, page (P3); display, bench (P4).
- Work on your own branch (p1-vision, p2-brain, p3-actions, p4-display). NEVER commit, merge or push to main directly.
- After each task: run its self-test, show the output, append one line to PROGRESS.md ("<time> <prompt id>: <what works>"), `git pull origin main` into your branch, commit and push to your branch, then open a PULL REQUEST into main (`gh pr create --base main --title "<prompt id>: <what>" --body "<what changed, self-test output, how to verify>"`; without gh, give the person the "Create a pull request" link that git push printed). Do not merge it.
- Every PR gets a code review BEFORE merging: run the engineering code-review skill (/code-review-engineering, or /engineering:code-review) on the PR's changes, fix real findings on the same branch, push, and note the review result in the PR. Merging into main happens only on GitHub ("Merge pull request"), by P2, after the review and the prompt's Verify step both pass.
- Hackathon rule: all code is written during the event (after Fri 7 PM).
- Commits: never add Co-Authored-By or any Claude attribution lines to commit messages.
