# LookAt: project rules (read before every task)

## What it is
A webcam lets a bedridden patient who cannot speak or move (e.g. intubated ICU patient) communicate by LOOKING at one of 3 spots in the room and confirming with an EYEBROW RAISE. A nurse uses a phone web page to ask questions; Claude turns any question into 2-3 short choices mapped to the 3 spots.

## The 3 spots (left, middle, right)
- ROOM mode (default): left = YES, middle = LAMP + spoken menu, right = NO.
- QUESTION mode (a nurse question is open): left/middle/right = the question's choices (middle may be empty = not selectable).
- One meaning at a time. Question mode ends on answer, nurse cancel, or 30 s timeout.

## Interaction rules (constants live in config.py)
- Look at a spot for DWELL_S = 1.0 s -> speak its name ("Lamp?", "Yes?", "Water?") -> state ASKING, spot locked.
- Confirm = eyebrow raise held >= CONFIRM_S = 0.3 s within ASK_TIMEOUT_S = 3 s. Then act, then COOLDOWN_S = 2 s.
- Looking away, timeout or losing the face cancels. Nothing ever happens without the eyebrow confirm.
- EMERGENCY = eyebrow held >= EMERGENCY_S = 2.0 s, in ANY mode, checked before everything else: say "Calling the nurse", flash the lamp 3 times, send HELP to the nurse page.
- Middle spot in ROOM mode: "Lamp?" (confirm toggles the lamp), then menu items every MENU_STEP_S = 1.5 s: "Help?", "Water?", "Pain?". Confirming a menu item sends it to the nurse page and speaks it.
- Backup confirm gesture for a person whose eyebrows fail calibration: mouth open (jawOpen) held >= 0.7 s.

## Hard rules
- The PATIENT NEVER READS A SCREEN. Every prompt to the patient is SPOKEN. Any window is for the team (operator) only.
- Windows 11 with Smart App Control ON. Run Python as `py -3.11`. Pinned: mediapipe==0.10.14, opencv-contrib-python==4.10.0.84, numpy<2, python-kasa==0.12.0, plus anthropic, fastapi, uvicorn[standard], pyttsx3, pydantic, pytest. NEVER add scikit-learn, scipy or other new dependencies (they get blocked). If an import fails with "An Application Control policy has blocked this file", do not upgrade; use plain Python/NumPy instead.
- The camera loop runs in the MAIN thread (cv2.imshow must). Voice, lamp, web server and Claude calls each run in their own worker thread. Communicate only through the queues and the lock-protected State in lookat/state.py. Nothing may block the camera loop.
- Secrets only from environment variables: ANTHROPIC_API_KEY, KASA_USER, KASA_PASSWORD, LAMP_IP (default 172.20.10.2). Never print or commit them.
- Claude: official `anthropic` Python SDK, model "claude-opus-5-5", output_config effort "low", structured output via client.messages.parse with a Pydantic model, 8 s timeout. On ANY error or refusal fall back to YES/NO.
- Every module has an `if __name__ == "__main__":` self-test that a person can run with `py -3.11 -m lookat.<module>` and that prints PASS/FAIL or shows a window.
- Pure logic (state machine, classifier, gesture timing) gets pytest tests in tests/. Run `py -3.11 -m pytest -q` before every commit.
- Log every event as one JSON line to logs/events.jsonl (time, type, details).
- Keep code small and readable. Only edit files your task names. Folder owners: vision (P1), brain + main (P2), actions + ai (P3), page + bench (P4).
- After each task: run its self-test, show the output, append one line to PROGRESS.md ("<time> <prompt id>: <what works>"), then `git pull --rebase`, commit and push.
- Hackathon rule: all code is written during the event (after Fri 7 PM).
- Commits: never add Co-Authored-By or any Claude attribution lines to commit messages.
