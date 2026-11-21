# LookAt: CodeJam 16 plan (final idea)

**One-sentence pitch:** *For people trapped in their own bodies, who can only move their eyes and silently wait hours for someone to notice, LookAt is an AI that speaks before they have to ask, and speaks up for them when no one else does.*

**Tagline:** *"LookAt speaks first, so patients need fewer glances, and nurses need fewer calls."*

**How to use this plan:** Part 1 is the setup steps and the Claude Code prompts, in order, each with a **Verify** check. Part 2 is reference: the exact screens, the agent design, the demo script and judge answers.

**What is decided (don't reopen these):**
- **Who:** patients who can only move their eyes (ALS, locked-in syndrome, ventilated ICU patients).
- **Controls:** look **left / center / right** + **short eyebrow raise** (≥ 0.3 s) = choose. **Long eyebrow raise** (≥ 2 s), anywhere = **Call Nurse**. **At most 2 answers on screen at a time** (left + right); center = "Return to main menu" or "More options".
- **Patient screen** (projector, fallback monitor): exactly the 6 mockups in R2: Idle · AI Suggestion · AI Check-in · AI Assistant · Room Controls · From your nurse.
- **The agent speaks first:** it notices (time, temperature, eyes open, memory), asks the right question, fixes what it can, **checks its work** ("Better now?"), respects silence, **learns** (routines, reasons, which options get picked), **speaks up** for the patient to the nurse, and writes the shift summary.
- **Nurse app** (phone): alerts and pages with explanations, flags ("feels cold below 20 °C, ask first?"), start a conversation: type any question → AI suggests answers → edit → send → live answers → AI suggests follow-ups → end session. History, preferences, shift summary, room controls, "I'm here" visit button.
- **Real hardware:** plug 1 = **lamp**, plug 2 = **fan (acts as the AC)**. Temperature = a **demo sensor** (one number, set from the demo panel; it also drifts down while the fan runs).
- **Safety:** comfort, environment and communication only; anything clinical goes to the nurse. Every action is confirmed by the patient, except routines they already approved. **"Call Nurse"** and **"Something else"** are always available.
- **Not building:** ESP32 sensor, dwell-only mode (listed as a limitation), benchmark dashboard, more devices.

**Team (3 people):** **P1 = Lea** (vision → agent brain) · **P2 = Belinda** (integrator: menus, patient screen, main program) · **P3** (devices, nurse app, Ask anything, Devpost).

**Already done:** ✅ Prompt 0 skeleton · ✅ A1 face features · ✅ A2 gaze classifier · ✅ B1 eyebrow confirm + long raise · ✅ B2 old state machine (B3 replaces its menus).

---

# PART 1. SETUP + PROMPT PACK

## How every prompt works (read once)
1. `cd C:\Users\<you>\CodeJam16-Bronies`, switch to **your branch** (`git switch p1-vision` / `p2-brain` / `p3-actions`; P3's first time: `git switch -c p3-actions`), then `git pull origin main`.
2. Run `claude`, click **Copy** on your next prompt, paste, press Enter, approve its commands.
3. When it finishes, **do the Verify step yourself**. Fails → paste **X1** with the error.
4. Passes → Claude Code has pushed your branch and opened a **pull request** into `main`. Run **`/code-review-engineering`** (or `/engineering:code-review`) with the PR link, fix real findings, then tell **Belinda**. She merges on GitHub. **Nobody pushes to `main`.**
5. Before your next prompt: `git pull origin main` again (you need everyone's merged work).

## Timeline (Sat noon → Sun)
| Milestone | Deadline | Prompts | Result |
|---|---|---|---|
| **M1** parts work alone | **Sat 5 PM** | B3, A3, C1, C4a | menus logic, eyes-open, both plugs + temperature, memory |
| **M2** patient screen by gaze | **Sat 8 PM** | D1, M2-1, C2 | the 6 screens on the projector; lamp + fan by eyes |
| **M3** nurse conversation | **Sat 11 PM** | C3, M3-1 | nurse asks anything → patient answers → follow-ups |
| **M4** the agent | **Sun 3 AM** | C4b, C4c, M4-1, M4-2 | speaks first, checks its work, speaks up, learns, summary |
| **M5** freeze + demo | **Sun 7 AM** | M5-1, M5-2 | hardened, README + Devpost, rehearsed |

**Who runs what (in order):**
| P1 Lea | P2 Belinda | P3 |
|---|---|---|
| A3 eyes-open | B3 menus engine + clock | S5 plug 2 + fan (by hand) |
| C4a memory + learning | D1 patient screen | C1 plugs + temperature |
| C4b the agent | M2-1 screen by gaze | C2 nurse app |
| C4c reasoning panel + summary | M3-1 nurse session | C3 Ask anything + follow-ups |
| (helps M4) | M4-1, M4-2 agent wiring + demo | M5-2 README + Devpost |
| | M5-1 hardening | pitch + rehearsal lead |

**If you're more than 2 h behind, cut in this order:** (1) "More suggestions" beyond 2 pages, (2) learned option ordering, (3) routine *proposal* (keep the approved routine running at 10 PM), (4) shift summary by Claude (keep the template). **Never cut:** gaze menus + real lamp, the 2 AM "Too warm?" → fan → "Better now?" loop, the nurse 18 °C flag, nurse Ask anything, the 10 PM routine.

---

## Setup S5: plug 2 + fan (P3, by hand, 20 min)
1. Plug the **fan** into the **second Kasa plug**, fan switch ON. Fan into plug, plug into the wall.
2. On the **"lea" phone**: Kasa app → **+** → add the plug → join the **"lea"** hotspot (Settings → Personal Hotspot open, **Maximize Compatibility ON**). Name it **"Fan"**.
3. On the laptop (on "lea"): `py -3.11 -m kasa.cli --target 172.20.10.15 discover` → note the fan plug's IP (e.g. `172.20.10.3`).
4. `setx FAN_IP "172.20.10.3"` (your IP), close and reopen PowerShell. The lamp stays `LAMP_IP=172.20.10.2`.
5. **No second plug or fan?** Set `FAN_IP` to nothing: the code then shows the fan as **simulated** and the demo still works.

## Setup S6: every teammate's laptop (5 min)
- GitHub CLI: `winget install --id GitHub.cli -e`, then in a new window `gh auth login` (GitHub.com → HTTPS → Yes → web browser).
- Pull the latest rules: `git pull origin main` on your branch.

---

## Track P2 (Belinda): menus, patient screen, main program

### ☐ B3: menus engine + demo clock
```text
Read CLAUDE.md first. Task B3 (owner P2). Replace the old ROOM layout in lookat/machine.py with a generic selection engine, add lookat/screens.py (the patient's menus as pure logic) and lookat/clock.py. Keep dwell, eyebrow confirm, cooldown, the "lock the spot while asking" rule and "long raise first".
- lookat/clock.py: Clock with now() -> datetime (demo wall clock), speed (default 1), set_time("HH:MM", day_offset=0), advance(seconds), is_night() (22:00-07:00). Module-level `clock = Clock()`; every module uses it for time of day.
- config.py: remove ROOM_MEANING, MENU_ITEMS, MENU_STEP_S, QUESTION_TIMEOUT_S. Add SUGGESTION_FADE_S = 20, CHECKIN_TIMEOUT_S = 30, QUIET_S = 7200, NIGHT_START = "22:00", NIGHT_END = "07:00".
- machine.py: Machine.step(now, spot, events, face, active) where active = the set of spots that are selectable on the current screen. Dwell DWELL_S on an active spot -> state ASKING (that spot highlighted) -> short raise (event "confirm") within ASK_TIMEOUT_S -> returns ("select", spot) -> COOLDOWN_S. Looking away / timeout / no face -> IDLE. Event "emergency" (long raise, from gestures.py) in ANY state -> ("call_nurse",) first. view() -> {"dwell": spot|None, "asking": spot|None, "state": ...}.
- screens.py: dataclass Screen(id, kind, badge, title, subtitle, left, center, right, options, page, icon, footer) where left/center/right are Option(id, label, hint) or None. ScreenManager (pure, time passed in) with these screens (labels exactly as written):
  IDLE: kind "idle". title "You're all set.", subtitle "I'm here if you need anything.". left = Option("room", "Room Controls", "Look left + short eyebrow raise") (shown as the top-left card), center = Option("talk", "Talk to AI", "Look center + short eyebrow raise"), right = None.
  SUGGESTION (pending AI notification): like IDLE but badge "AI SUGGESTION", title = the suggestion text, center = Option("engage", "Look center + raise eyebrows to engage"). Fades back to IDLE after SUGGESTION_FADE_S with no selection -> event ("suggestion_ignored", sid).
  CHECKIN (active AI): badge "AI CHECK-IN", title = the agent's question, optional icon, left/right = the two current answers (default "Yes, please." / "No, thanks."), center = Option("home", "Return to main menu"). No room controls. Times out after CHECKIN_TIMEOUT_S -> ("checkin_ignored", qid) and IDLE.
  ASSISTANT (patient chose Talk to AI): badge "AI ASSISTANT", title "How can I help you?", options from a ranked list the agent provides (default ["Get more comfortable", "Quiet for 2 hours", "Adjust the room", "Something else"]): left = the current option, right = "More suggestions" (shows the next option, wrapping), center = "Return to main menu". If the list has only 2 options, left/right are both options.
  CHECKIN paging rule: 2 answers -> left/right + center "Return to main menu". More than 2 answers (open questions; "Something else" kept last) -> 2 per page, center = "More options", and the last page's center = "Return to main menu".
  ROOM: badge "ROOM CONTROLS", title "What would you like to adjust?", left "Adjust the lights", right "Adjust the temperature", center "Return to main menu". LIGHTS: title "Lamp is ON/OFF" (from state), left "Lamp on", right "Lamp off", center "Back". TEMPERATURE: title "Room is 24 °C" (from state), left "Cooler" (fan on), right "Warmer" (fan off), center "Back".
  NURSE (session opened by the nurse): badge "From your nurse", title = her question, options = her full answer list; shows 2 per page (left, right); center = Option("more", "More options") when there are more than 2 (cycles pages, wraps around), footer "Look left or right and raise your eyebrows to choose, or look center and raise your eyebrows to see more options.", page dots. NO room controls, NO return, call-nurse banner hidden. Only nurse_end() leaves it.
  Methods: select(spot, now) -> list of events (("device", name, cmd), ("answer", qid, option_text), ("engaged", sid), ("talk_to_ai",), ("home",), ("more",)), show_suggestion(sid, text, now), show_checkin(qid, question, options, now, icon=None) (options of any length >= 2, paged by the CHECKIN paging rule), show_assistant(options), nurse_start(qid, question, options), nurse_question(qid, question, options), nurse_end(), tick(now) for fades/timeouts, active_spots(), view() -> JSON-ready dict of the current screen.
Rewrite tests/test_machine.py and add tests/test_screens.py and tests/test_clock.py (at least 35 tests total): every screen's labels, navigation, Back/Return, paging + wrap, fade and timeout events, nurse mode hiding room/return, nurse_end returns to IDLE, long raise works on every screen, the selection engine's dwell/confirm/cancel/cooldown rules. Follow the after-task rules.
```
**Verify:**
1. `py -3.11 -m pytest -q`: all pass, 35+ new tests.
2. `py -3.11 -m lookat.screens` prints a scripted walk: Idle → Room Controls → Adjust the temperature → Cooler → `("device", "fan", "on")` → Back → Return → Talk to AI → nurse session with 4 options paged 2 at a time.

### ☐ D1: the patient screen (looks exactly like the mockups)
```text
Read CLAUDE.md first. Task D1 (owner P2): lookat/display.py: a FastAPI APIRouter with GET /patient (full-screen page for the projector) and GET /api/screen (JSON = ScreenManager.view() + Machine.view() + clock + room state), polled every 150 ms. No external files; no build step.
Match the LookAt mockups exactly:
- Deep navy background (#071a3a -> #0b2a5c radial gradient) with soft blue glows; glassy rounded cards (border 2px rgba(110,180,255,.55), background rgba(20,60,120,.35), border-radius 32px, outer glow); white bold geometric sans-serif (Segoe UI Semibold / system), hints in light blue (#9cc8ff).
- Top-right: an eye logo (inline SVG) + "LookAt". Bottom: a wide card with a pink-orange bell icon, "Call Nurse • Long eyebrow raise anywhere" (hidden in NURSE mode).
- IDLE: top-left small card "Room Controls / Look left + short eyebrow raise" with a lamp icon; centre: glowing crescent moon + stars, huge "You're all set.", subtitle "I'm here if you need anything.", a glowing pill "Talk to AI / Look center + short eyebrow raise" with a chat icon.
- SUGGESTION: top-left Room Controls card stays; centre big glass card with a sparkle icon + badge "AI SUGGESTION", the suggestion in very large text, and a pill "Look center + raise eyebrows to engage" with a target icon.
- CHECKIN / ASSISTANT / ROOM / LIGHTS / TEMPERATURE: one big glass card: sparkle + badge, question, two large option cards side by side (left, right) each with an icon, label and "Look left/right + raise eyebrows" hint, and below them a wide "Return to main menu / Look center + raise eyebrows" card with a house icon (or "Back"). Pick simple inline-SVG icons by keyword (pillow, bed, lamp/bulb, thermometer, fan, grid, x, check, water, clock).
- NURSE: top centre pill with a nurse icon "From your nurse"; top-left logo; top-right green dot "Connected"; the question; two big option cards; the instruction card; page dots under it.
- Live feedback: the dwelled option gets a bright glow; the ASKING option fills brighter with "Raise your eyebrows" under it; a confirmed option flashes. Smooth 200 ms transitions. Text must be readable from 2-3 m: question >= 6vh, option labels >= 4.5vh.
- CALL NURSE overlay (after a long raise): full-screen soft red-orange glow, bell, "Your nurse has been called" for 6 s.
- Self-test (py -3.11 -m lookat.display --demo): serves /patient on config.PAGE_PORT with a fake ScreenManager cycling every 3 s through all screens, a dwell, an asking highlight, the call-nurse overlay and a nurse question with 4 options paged. Print the URL.
Follow the after-task rules.
```
**Verify:** open the URL full screen (F11) on the projector. **Side by side with the 6 mockups, every screen matches** (layout, badges, wording, icons, glow). Readable from the patient chair.

### ☐ M2-1: the patient screen by gaze, with the real lamp and fan
```text
Read CLAUDE.md and the progress/ files first. Task M2-1 (owner P2): wire everything in main.py.
- `py -3.11 main.py --patient NAME [--recalibrate]`: start the page server (display router + later nurse app), lookat.devices.Room (plugs + temperature), FaceTracker, gaze calibration using the patient screen (for each spot show a full-screen "Look at the LEFT edge of the screen" / "CENTER" / "RIGHT edge" target with a dot, then record), gesture calibration (on-screen instructions), ScreenManager + Machine.
- Camera loop (main thread): features -> GazeModel.predict -> GestureDetector.update -> Machine.step(active=ScreenManager.active_spots()) -> ("select", spot) -> ScreenManager.select -> execute events: ("device", ...) -> room.execute(..., by="patient"); ("call_nurse",) -> overlay + an alert into state for the nurse app; everything logged. ScreenManager.tick every frame. Keep State.screen / State.machine in sync for /api/screen.
- Operator window (team only): camera, spot + confidence, eyebrow bar, machine state, screen id, fps. Keys: c = recalibrate gaze, g = gestures, q = quit cleanly.
Run pytest, tell me exactly how to test it by hand, follow the after-task rules. When I confirm, tag m2-working.
```
**Verify:**
1. Idle → look **left** + raise → Room Controls → **Adjust the lights → Lamp on**: the **real lamp** turns on. 5/5.
2. **Adjust the temperature → Cooler**: the **fan** turns on and the temperature starts dropping on screen.
3. **Return to main menu** works from every screen; a **long raise** shows "Your nurse has been called" from every screen.
4. Then: *"Tag m2-working and push the tag."*

### ☐ M3-1: nurse conversation, end to end (P2 + P3)
```text
Read CLAUDE.md and the progress/ files first. Task M3-1: connect the nurse app (lookat/nurse.py) and lookat/ask.py to main.py and the patient screen.
- Nurse "Start conversation" -> ScreenManager.nurse_start: the patient screen switches to NURSE mode ("From your nurse", Connected). Each question the nurse sends appears there with her answers 2 per page (center = More options). The patient's choice goes to the nurse app within 1 s, with seconds-to-answer, and into memory (lookat.memory) as an answer to that question.
- After each answer, the nurse app shows AI follow-up suggestions from ask.follow_ups(...) (one tap = load into the composer, still editable).
- "End session" -> ScreenManager.nurse_end(): the patient screen returns to IDLE and the agent resumes.
- A long raise during a session still alerts the nurse ("Patient used the call signal during your conversation").
- If the patient chooses "Something else", the nurse app highlights it and suggests asking an open follow-up.
Run pytest, tell me how to test by hand, follow the after-task rules.
```
**Verify:**
1. Nurse: Start conversation → "Would you like help getting more comfortable?" → AI answers (Yes / No) → Send → **patient screen shows it** → patient picks **Yes** by gaze → nurse sees "Yes (2.4 s)".
2. AI suggests the follow-up **"What would help most?"** with "Adjust pillows / Change position / Water / Something else" → Send → patient pages with **center** and picks **Adjust pillows** → nurse sees **"Patient requests pillow adjustment."**
3. End session → patient screen back to **"You're all set."** 5 different questions work.

### ☐ M4-1: the agent, live (P2 + P1)
```text
Read CLAUDE.md and the progress/ files first. Task M4-1: run lookat/agent.py inside main.py.
- Start the Agent thread with: a perceive() function (clock time, room temperature + setpoint + fan/lamp state, eyes-open from lookat.awake, current screen, quiet-until, last nurse visit from memory), the ScreenManager hooks (show_suggestion, show_checkin, show_assistant) and their answer/ignore events fed back to the agent, room.execute(..., by="agent"|"routine"), and nurse-app hooks (page, flag, defer).
- The agent never interrupts: no suggestions while the patient is mid-selection, in ROOM screens, in a nurse session or within 10 s of their last selection. Patient selections of "Talk to AI" / "Get more comfortable" / "More suggestions" / "Quiet for 2 hours" are routed to the agent.
- Every agent step is pushed to the reasoning panel (lookat/panel.py) live.
Run pytest, tell me how to test by hand, follow the after-task rules.
```
**Verify (the core demo):**
1. Demo panel: **Set 02:00**, temperature **24.5 °C**, eyes open. Within ~10 s the patient screen shows **AI SUGGESTION "You seem to be awake. Too warm?"** and the panel shows **why** (night, eyes open, 24.5 °C, memory: runs warm).
2. Engage → **Yes, please.** → the **real fan turns on** → temperature drops → **"Better now?"** → Yes → panel: **"No nurse needed."**
3. Same again but answer **No** to "Better now?" → it tries something else (e.g. lamp off / "Something else") or **pages the nurse with an explanation**.
4. Ignore a suggestion → it **fades on its own**; ignore 3 night check-ins → panel: **"Stopping night check-ins (ignored 3×)"**.

### ☐ M4-2: speaks up + learns + one week later (P2 + P1)
```text
Read CLAUDE.md and the progress/ files first. Task M4-2: the nurse flag, routines and the demo controls.
- Nurse app room controls: when the nurse sets the AC setpoint below a remembered comfort fact ("feels cold below 20 °C"), the agent immediately flags on the nurse app: "Patient feels cold below 20 °C. Ask them first?" with [Ask them] (opens a nurse session with "Is 18 °C too cold for you?" Too cold / It's fine) and [Set anyway].
- Routines: the routines scheduler (lookat/routines.py) runs approved routines at their time with a SUGGESTION-style banner "It's 10 PM. Turning off your lamp, as usual." (patient can engage -> "Keep it on" / "OK") and logs by="routine"; the panel shows "Routine: lamp off at 22:00 (approved Nov 14; done 6 of 7 nights)".
- Demo controls on the panel page (operator only): Set 02:00 · Set 07:30 · Skip to 21:59 · One week later (loads the labelled demo week from lookat/demo_week.py into a fresh demo patient: nightly lamp-off ~22:00, the routine approval, answer counts, a nurse visit log) · Temperature − / + and a number box · Eyes: auto / open / closed · Reset demo (asks to confirm).
Run pytest (scheduler with a fake clock: runs at time, not during a nurse session or check-in, runs once per night), tell me how to test by hand, follow the after-task rules. Tag agent-working when I confirm.
```
**Verify:**
1. Morning (Set 07:30): nurse sets **18 °C** → nurse app flag **"Patient feels cold below 20 °C. Ask them first?"** → Ask them → patient answers **"Too cold"** → nurse sees it.
2. **One week later → Skip to 21:59** → at 22:00 **the real lamp turns off by itself**; the panel shows the routine and its evidence.
3. Shift summary button (C4c) writes the night: "Woke at 2:14 AM feeling warm, fan on, 22.8 °C, no nurse needed…".

### ☐ M5-1: hardening + freeze (P2)
```text
Read CLAUDE.md and the progress/ files first. Task M5-1 (owner P2): make the demo unbreakable. No new features.
- Startup checklist (green/red): camera + face size, lamp plug, fan plug (or "simulated"), ANTHROPIC_API_KEY, page URLs (patient, nurse, panel) printed big, calibration files, memory readable.
- Key r: quick 3 s/spot recalibration on the patient screen. Key d: reset demo state (asks YES in the terminal).
- Any exception in a worker, a frame or an API call is logged and survived; every AI step has its rule-based fallback.
- 10-minute soak test with someone moving around; fix only what it shows.
Run pytest, follow the after-task rules, then tag final when I confirm.
```
**Verify:** all green; **the full 3-minute demo (R6) 5 times in a row without restarting**; soak: no crash. *"Tag final and push."* **Feature freeze.**

---

## Track P1 (Lea): eyes-open + the agent's brain

### ☐ A3: eyes-open (awake) detector
```text
Read CLAUDE.md first. Task A3 (owner P1): lookat/awake.py using vision.py features (eyeBlinkLeft/Right).
- AwakeDetector.update(features, now) -> {"eyes_open": bool, "open_ratio_60s": float, "awake": bool}. Eyes open = mean blink < a calibrated threshold (calibrate(open_samples, closed_samples) or default 0.45). awake = eyes open >= 70% of the last 60 s with a face present. No face -> awake False (and "no face" reported, never assumed asleep). Demo override: set_override("open"|"closed"|None).
- Self-test (py -3.11 -m lookat.awake): live window with the blink value, open/closed, the 60 s ratio and AWAKE/ASLEEP; keys o/c/a set the override.
pytest with synthetic series (blinks every 4 s still awake; eyes closed 50 s -> not awake; override). Follow the after-task rules.
```
**Verify:** eyes open for 60 s → **AWAKE**; close them for ~45 s → **ASLEEP**; normal blinking never flips it.

### ☐ C4a: memory + learning
```text
Read CLAUDE.md first. Task C4a (owner P1): lookat/memory.py. Pure Python + JSON, no AI, no camera. Use lookat.clock for every timestamp. One file per patient: memory/<patient>.json (git-ignored), atomic writes, missing/corrupt file -> empty memory, never crash.
- Stores: facts [{text, kind "comfort"|"preference"|"routine"|"note", source "chart"|"patient"|"nurse"|"agent", time, data}] e.g. {"text": "Feels cold below 20 °C", "kind": "comfort", "data": {"cold_below_c": 20}}, {"text": "Runs warm at night", "data": {"runs_warm": true}}; actions [{time, device, cmd, by, result}]; questions [{id, time, source "agent"|"nurse", question, options, answer|null, seconds, ignored}]; suggestions [{id, time, text, outcome "engaged"|"ignored"|"declined"}]; checkins (night/day, ignored); routines [{id, device, cmd, at "HH:MM", approved, active, runs, skips}]; nurse_visits [{time}]; pages [{time, message, reason, urgency}]; deferred [{time, note}] (for the next visit / shift summary).
- learn() -> deterministic, explainable:
  comfort: cold_below_c / warm_above_c / runs_warm from facts (latest wins).
  routine_candidates: same device+cmd by the patient on >= 3 different days in the last 7, within a 60-min window -> typical time (median, rounded to 5 min) + evidence dates; skip if an active routine exists or it was declined in the last 3 days.
  option_ranking: per question kind, options ordered by how often they were picked when shown; options shown >= 5 times and never picked -> "hidden" (but "Something else" and "Nurse" are never hidden).
  silence: night check-ins ignored in a row; >= 3 -> night_checkins_enabled False (until the patient engages at night again).
  last_nurse_visit, open deferred items, counts for the panel.
- context_text(perception) -> compact text (< 1200 words) for the agent: time, perception, comfort facts, learned items with evidence, the last 5 interactions, last nurse visit, deferred items.
- Chart seeding: load_chart(patient, facts) for the demo patient's starting facts ("Runs warm at night", "Feels cold below 20 °C") marked source "chart".
- Self-test (py -3.11 -m lookat.memory --demo): temp patient, 3 nights of lamp off at 21:55/22:05/22:00, 5 shows of an option never picked, 3 ignored night check-ins; print learn() and context_text(). Delete the temp file.
pytest: comfort facts, routine detection edge cases, option ranking + hiding (never hides Something else), silence rule, corrupt file. Follow the after-task rules.
```
**Verify:** the demo prints **routine: lamp off 22:00** with evidence, a **hidden option**, **night_checkins_enabled: False**, and the comfort facts.

### ☐ C4b: the agent (speaks first, fixes, checks, speaks up, learns)
```text
Read CLAUDE.md first. Task C4b (owner P1): lookat/agent.py. Official anthropic Python SDK, manual tool-use loop (use the claude-api skill; do not guess SDK calls). Works without a camera via a fake perceive() in the self-test.
ARCHITECTURE: deterministic TRIGGERS decide WHEN (cheap, explainable, demo-safe); Claude decides WHAT to ask and do, inside hard limits enforced in code; every step is reported to on_step for the reasoning panel.
- Agent(patient, perceive, screens, room, nurse, on_step, client=None) thread; tick every 2 s of real time.
- Triggers (each returns a reason string shown in the panel), checked only when the patient is idle on IDLE and not quiet:
  T1 warm-at-night: clock.is_night() and awake and temp >= warm threshold (23 °C, or memory warm_above_c) and memory runs_warm and night check-ins enabled and none in the last 20 min.
  T2 cold: temp <= memory cold_below_c (any time) and awake.
  T3 nurse-setpoint: a nurse sets the AC setpoint below cold_below_c -> flag on the nurse app (no patient interruption).
  T4 routine-candidate: memory.learn() has one and the patient is awake -> propose once.
  T5 patient-initiated: Talk to AI / Get more comfortable / More suggestions / Something else.
- Episode = one Claude tool-use loop (model "claude-opus-5-5", thinking adaptive, max_tokens 2048, 15 s per call, max 6 turns, max 3 patient questions):
  tools: get_context() -> memory.context_text(perception) + the trigger reason; suggest(text) -> shows an AI SUGGESTION and waits for engage (or "ignored" after the fade); ask(question, options[2-6]) -> CHECKIN (yes/no = 2 answers; open questions get "Something else" appended if missing and are paged 2 at a time), returns the choice or "ignored"; act(device, cmd, because_answer) -> only allowed if the patient's last answer approved this exact action (code-enforced), runs room.execute(by="agent"); check(seconds) -> waits (demo-clock aware), re-reads temperature/state and returns before/after; page_nurse(message, reason, urgency) -> nurse app page that EXPLAINS (e.g. "Wants repositioning. Not temperature-related. Last visit 3h ago."); defer(note) -> grouped into the next visit / shift summary instead of paging; remember(fact, kind, data) -> memory; propose_routine(device, cmd, at, evidence) -> one CHECKIN "Turn the lamp off at 10 PM every night?" Yes/Not now; finish(summary_for_panel).
  Every tool call has a short "why" argument the panel shows.
- System prompt essentials: you speak first for a patient who can only move their eyes; ask ONE short question at a time with 2 answers (more only if needed, paged); use memory to make the first question the right one; fix comfort/environment yourself (fan, lamp), then CHECK with check() and ask "Better now?"; if not better try one other fix or page the nurse with an explanation; anything clinical (pain, breathing, distress) -> page_nurse urgent immediately; what can wait -> defer; respect silence (if ignored, stop); never give medical advice; always keep "Something else".
- Rule-based fallback for every trigger when the API fails (no key/error/timeout): T1 -> suggest "You seem to be awake. Too warm?" -> Yes -> fan on -> check(30) -> "Better now?" -> No -> page nurse "Still warm after fan. Temp X." ; T3 -> the flag text; T4 -> the routine question; T5 -> ranked options from memory. The panel marks steps "(fallback)".
- Self-test (py -3.11 -m lookat.agent --simulate night-warm|nurse-18|routine|assistant): fake perceive, fake screens (auto-answers from a script), fake room with the temperature model; prints every step with its why. night-warm must go: suggest -> Yes -> fan on -> check shows temp falling -> "Better now?" -> Yes -> finish "No nurse needed".
pytest with a fake client: triggers fire/not fire, act() refused without approval, ask limit, ignored -> stops, fallback paths, page vs defer. Follow the after-task rules.
```
**Verify:** all 4 simulations behave as described; with `ANTHROPIC_API_KEY` unset the same simulations still work, marked **(fallback)**; `act()` without a Yes is **refused** in the log.

### ☐ C4c: reasoning panel + shift summary
```text
Read CLAUDE.md first. Task C4c (owner P1): lookat/panel.py (FastAPI APIRouter) + summary functions.
- GET /panel: an operator/judge page (dark, same LookAt style as the patient screen) with: a big clock (DEMO TIME badge when set), Perception (temperature + setpoint, fan/lamp, eyes open %, awake, last nurse visit), Memory (comfort facts, routines with evidence, hidden/top options, night check-ins on/off), and a live Reasoning feed: each agent step as a card "Noticed → Decided → Did → Checked → Outcome", each with its why and "(fallback)" when rule-based. GET /api/panel for the data, polled every 500 ms.
- The demo-control buttons from M4-2 live on this page (P2 wires their actions).
- lookat/summary.py: shift_summary(memory, since) -> 3-6 plain sentences in time order ("Woke at 2:14 AM feeling warm; fan on, 24.5 → 22.8 °C; no nurse needed. Requested repositioning at 5:00 AM (deferred to the morning visit)."). Claude writes it from the event list (8 s timeout); fallback = a template from the same events. Never invent events.
- Self-test (py -3.11 -m lookat.panel --demo): serves /panel with a scripted feed of the 2 AM episode and prints the shift summary (AI and template).
Follow the after-task rules.
```
**Verify:** `/panel` shows the 2 AM episode as clear Noticed → Decided → Did → Checked → Outcome cards; the shift summary is **accurate** (only events that happened).

---

## Track P3: devices, nurse app, Ask anything, Devpost

### ☐ C1: plugs + temperature (real lamp, real fan, demo sensor)
```text
Read CLAUDE.md first. Task C1 (owner P3): lookat/plugs.py + lookat/devices.py.
- plugs.py: PlugWorker(name, ip) daemon thread with its own asyncio loop; python-kasa 0.12 KLAP: Device.connect(config=DeviceConfig(host=ip, credentials=Credentials(KASA_USER, KASA_PASSWORD), connection_type=DeviceConnectionParameters(DeviceFamily.IotSmartPlugSwitch, DeviceEncryptionType.Klap, login_version=2))), then update(). Commands on its own queue: on/off/toggle/flash. Reports is_on. Offline -> log "PLUG <name> OFFLINE - run the network check", retry every 5 s, never crash or block. Empty ip -> SIMULATED plug (same API, in memory).
- devices.py: Room with lamp = PlugWorker("lamp", LAMP_IP), fan = PlugWorker("fan", FAN_IP) acting as the AC, and a demo temperature sensor: temp_c (start 22.0), setpoint_c (22), fan cools the room 0.15 °C per demo-second while on (to a floor of 19), otherwise it drifts toward the setpoint 0.05 °C/s; set_temp(x) and set_setpoint(x, by) for the demo panel and nurse; all time via lookat.clock. execute(device, cmd, by) -> short result text and calls on_action hooks (memory). state() -> {"lamp": {"on", "simulated"}, "fan": {"on", "simulated"}, "temp_c", "setpoint_c"}.
- Self-test (py -3.11 -m lookat.devices): toggles the REAL lamp twice and the fan once (or simulated), runs the temperature model for 20 s with the fan on and prints the readings and every switch time in ms. PASS if all < 1000 ms.
pytest for the temperature model and Room with simulated plugs. Follow the after-task rules.
```
**Verify:** on "lea": the **lamp** and the **fan** really switch (each < 1 s); the temperature **falls** while the fan runs; unplugging one prints **OFFLINE** without crashing.

### ☐ C2: the nurse app (phone)
```text
Read CLAUDE.md first. Task C2 (owner P3): lookat/nurse.py: FastAPI APIRouter + one mobile page GET /nurse (same LookAt navy/glass style, big touch targets, no external files), JSON API polled every 1 s (GET /api/nurse).
Sections:
1. Alerts feed: patient called (long raise), agent pages WITH their explanation ("Wants repositioning. Not temperature-related. Last visit 3h ago."), agent flags ("Patient feels cold below 20 °C. Ask them first?" [Ask them] [Set anyway]), with sound + vibration for calls/urgent pages; deferred items grouped under "For your next visit".
2. Conversation: [Start conversation] / [End session]; a composer: question text box -> [Suggest answers] (calls ask.make_options) -> an editable list of answers (add/remove/reorder, any number, "Something else" kept last) -> [Send]. The live thread shows each question and the patient's answer with seconds; after each answer, AI follow-up suggestions appear as chips (ask.follow_ups) that load into the composer.
3. Room: temperature + setpoint with − / + (calls Room.set_setpoint(by="nurse")), lamp and fan toggles.
4. Patient: comfort facts and preferences (add a fact), recent requests and answers, routines, last visit; [I'm here] logs a nurse visit; [Shift summary] (calls summary.shift_summary).
Until M3-1 connects it, the router works with stub callbacks. Self-test (py -3.11 -m lookat.nurse --demo): serves /nurse with fake data and prints the phone URL (detect the Wi-Fi IP).
Follow the after-task rules.
```
**Verify:** on the 2nd phone (on "lea") open the printed URL: all 4 sections work with fake data; the composer edits answers; alerts vibrate. If it can't load: set "lea" to **Private** and allow Python in the firewall.

### ☐ C3: Ask anything + follow-ups
```text
Read CLAUDE.md first. Task C3 (owner P3): lookat/ask.py. Official anthropic SDK, client.messages.parse with Pydantic models, model "claude-opus-5-5", output_config {"effort": "low"}, 8 s timeout, check stop_reason.
- make_options(question, memory_context) -> {question, options[2-6], source, seconds}: short answers (1-4 words) a patient who can only move their eyes can pick; personalised from memory (e.g. preferred items first, hidden options left out); yes/no questions -> ["Yes", "No"]; open questions -> the likely answers + "Something else" last; never answer for the patient; never add clinical options the nurse did not imply. Fallback: ["Yes", "No", "Something else"].
- follow_ups(thread, memory_context) -> up to 3 suggested next questions, each with its options, that narrow the need ("Would you like help getting more comfortable?" -> Yes -> "What would help most?" Adjust pillows / Change position / Water / Something else). Fallback: none.
- request_text(question, answer) -> a short nurse-facing line ("Patient requests pillow adjustment.").
- Self-test (py -3.11 -m lookat.ask): 10 questions incl. "Did you sleep okay?", "What would make you more comfortable?", "Is 18 °C too cold for you?", "Do you want the TV on?" -> table with options + seconds + source; then the pillow follow-up chain.
Follow the after-task rules.
```
**Verify:** the table looks sensible, every open question ends with **"Something else"**, the follow-up chain matches the example, **average time** written down; with the key unset everything says **fallback**.

### ☐ M5-2: README + Devpost (P3)
```text
Read CLAUDE.md, the progress/ files and the code first. Task M5-2 (owner P3): write README.md and DEVPOST.md.
README: the problem, the idea ("an AI agent that talks first"), the 6 screens, controls, the agent loop (notice -> connect facts -> ask -> fix -> check -> learn -> speak up -> summarise), what is real (webcam gaze + eyebrows, the lamp and fan plugs, Claude) vs demo (the temperature sensor value, the demo clock, the labelled demo week), safety rules, limitations (coarse gaze -> 2 answers at a time; not a replacement for eye-tracking AAC; dwell mode not built), setup on Windows with the exact pip line, how to run (patient, nurse, panel URLs, demo controls), research (R9), built with Claude Code during CodeJam 16.
DEVPOST.md: Inspiration, What it does, How we built it, Challenges, Accomplishments, What we learned, What's next, plus an "AI agent" section: perception, memory, tools, code-enforced safety, fallbacks, why it is not a wrapper. Use ONLY facts from the repo; mark anything unsure [CHECK].
```
**Verify:** fix every `[CHECK]` by hand; paste DEVPOST.md into Devpost.

---

## Sunday (by hand)
| ✓ | When | Step |
|---|---|---|
| ☐ | after M5-1 | **Backup video** of the full 3-min demo on a phone, saved on 2 devices |
| ☐ | 2–7 AM | Sleep in shifts, at least 3 h each |
| ☐ | 7–10 AM | Rehearse R6 **10×** with the panel controls; judge questions (R7); network check (R1) |
| ☐ | **10–11 AM** | ⚠️ Team check-in + website check-in form |
| ☐ | **by 11 AM** | ⚠️ **Submit:** repo **public**, Devpost text + video (deadline 12 PM) |
| ☐ | 12–1 PM | Set up: projector, lamp, fan, camera, phone; `py -3.11 main.py --patient demo --recalibrate` in the real light |
| ☐ | **1–3:30 PM** | Judging. Before each judge: **Reset demo**, press **r** if anything moved |

## X1. Debug prompt (paste when a Verify fails)
```text
The Verify step for <prompt id> failed. What I did: <steps>. What I expected: <expected>. What happened (exact output / error): <paste>. Read CLAUDE.md and the relevant files, find the root cause, explain it in 2 sentences, fix it with the smallest change, run pytest and the self-test, and show me the output. Do not change files outside this task.
```

## X2. Fresh-session prompt (Claude Code got confused or restarted)
```text
Read CLAUDE.md and the progress/ files, then `git status` and `git log --oneline -10`. Summarise in 5 lines what is built, what is half-done on this branch, and what my next prompt should be. Do not change anything yet.
```

## X3. Roll back (a change broke everything)
```text
The last commit on this branch broke <what>. Show me `git log --oneline -5`, then revert the bad commit with `git revert` (never reset or force-push), run pytest, push this branch and update the PR.
```

---

# PART 2. REFERENCE

## R1. Network check (before every setup and every demo)
1. Laptop + nurse phone on **"lea"** (iPhone hotspot, Maximize Compatibility ON, set to **Private** on Windows).
2. `py -3.11 -m kasa.cli --target 172.20.10.15 discover` → **lamp 172.20.10.2** and the **fan plug** listed. "Authentication failed" in discover is normal.
3. Phone opens `http://<laptop IP>:8000/nurse`.

## R2. The patient screens (from the final idea; build exactly these)
| Screen | When | Left | Center | Right | Bottom |
|---|---|---|---|---|---|
| **Idle** "You're all set. I'm here if you need anything." | nothing pending | Room Controls (top-left card) | **Talk to AI** | none | Call Nurse • long raise |
| **AI Suggestion** (pending notification) | the agent has something | Room Controls | **Engage** ("Look center + raise eyebrows to engage") | none | Call Nurse |
| **AI Check-in** e.g. "Would you like help adjusting your pillows?" | engaged / agent question | **Yes, please.** | Return to main menu | **No, thanks.** | Call Nurse |
| **AI Assistant** "How can I help you?" | Talk to AI | Get more comfortable | Return to main menu | More suggestions | Call Nurse |
| **Room Controls** "What would you like to adjust?" | Room Controls | Adjust the lights | Return to main menu | Adjust the temperature | Call Nurse |
| **From your nurse** | nurse session | answer 1 | **More options** (pages, dots) | answer 2 | hidden |

**Rules:** Room Controls disappear once the patient engages with the AI (left/right become answers). "No, thanks" may lead to another suggestion the agent thinks fits; "Return to main menu" stops the stream. An ignored suggestion fades by itself. In a nurse session, room controls, return and the call-nurse banner are hidden; only the nurse ends it.

## R3. The agent: why it isn't a wrapper
**The loop:** *Notice* (time, temperature, eyes open, room, memory, last nurse visit) → *Connect facts* ("2 AM + awake + 24.5 °C + runs warm") → *Ask one question* → *Fix* (fan) → *Check its work* (sensor, "Better now?") → *Escalate with an explanation or defer* → *Learn* → *Summarise for the shift*.

| Part | Done by |
|---|---|
| Seeing the choice (gaze, eyebrows, eyes open) | **Our code** (MediaPipe + our classifier) |
| When to speak (triggers), respecting silence, quiet mode | **Our code** (deterministic, explainable) |
| Memory, routines, option ranking, comfort facts | **Our code** |
| Safety: no action without the patient's Yes, clinical → nurse | **Our code** (enforced) |
| Switching the real lamp and fan, checking the sensor | **Our code** |
| What to ask, how to word it, what to try next, the explanation to the nurse, the shift summary | **Claude**, through 10 tools, with a rule-based fallback for each |

**It learns:** routines (asks once, then does it) · reasons ("feels cold below 20 °C" protects the patient's preference) · answers (never-picked options disappear, used ones move up) · silence (ignored night check-ins stop).

**Less work for nurses:** fixes temperature/lights itself · pages explain ("Wants repositioning. Not temperature-related. Last visit 3h ago.") · non-urgent items grouped for the next visit · automatic shift summary.

**Honest wording for judges:** the lamp and fan are real; the temperature is a demo sensor value (an ESP32 + DHT22 would replace it); "one week later" loads a labelled demo week so the real routine code can run in 3 minutes; Claude decides wording and next steps, our code decides when and what is allowed.

## R4. Physical setup
```
   WALL / SCREEN:   [ projected LookAt patient screen, as wide as possible ]
                         ↑ projector beside/behind the patient (beam never in eyes or camera)
                         📷 camera ~50 cm from face, just below eye level
                         🙂 patient (chair reclined, pillow, blanket)
   💡 lamp (plug 1)      🌀 fan (plug 2) blowing toward the patient      📱 nurse phone (judge)
   💻 laptop 2 / second screen facing judges: /panel (reasoning + demo controls)
```
- Gaze uses 3 **directions**: calibrate on the left edge, center and right edge of the projected screen. Bigger projection = better accuracy. **< ~80% in the gaze test → switch to a monitor placed closer, or move the projector back.**
- Face ≥ 250 px at 1280×720; light the face from the front, dim enough for the projector.

## R5. Demo controls (on /panel, operator only)
Set 02:00 · Set 07:30 · Skip to 21:59 · One week later · Temperature − / + · Eyes auto/open/closed · Reset demo.

## R6. Demo (3 minutes)
| Time | Host | Patient | What judges see |
|---|---|---|---|
| 0:00 | **Hook:** "Imagine you can only move your eyes. It's 2 AM, you're too warm, and you can't tell anyone." | still | Idle screen: "You're all set." |
| 0:20 | **Direct control:** "One glance." | Room Controls → lights → Lamp on | **real lamp ON** |
| 0:40 | **The agent speaks first.** Panel: **Set 02:00**, temp 24.5 °C. "Nobody touches anything." | eyes open | AI SUGGESTION **"You seem to be awake. Too warm?"**; panel shows *why* |
| 1:10 | **It fixes and checks.** | Engage → Yes, please. | **real fan ON**, temperature falls → "Better now?" → Yes → panel **"No nurse needed."** |
| 1:40 | **It speaks up.** Panel: **Set 07:30**. Judge (nurse phone) sets **18 °C** | none | Nurse app: **"Patient feels cold below 20 °C. Ask them first?"** → Ask them |
| 1:55 | | answers **Too cold** | Nurse sees it. Judge types **"Did you sleep okay?"** → AI answers → Send → patient answers by gaze |
| 2:20 | **It learns.** Panel: **One week later → Skip to 21:59** | none | 22:00: **lamp turns off by itself**; panel shows the routine + evidence |
| 2:45 | **Close.** Shift summary on the nurse phone. Tagline. | none | "LookAt speaks first, so patients need fewer glances, and nurses need fewer calls." |

## R7. Judge questions
| Question | Answer |
|---|---|
| Isn't this just a ChatGPT wrapper? | Our code sees the choice, decides **when** to speak, keeps the memory, learns routines and option rankings, enforces safety and switches real devices; Claude decides **what** to ask and how to explain, through tools, with a fallback for every step |
| Why only 2 answers at a time? | Webcam gaze is coarse: left/center/right is reliable, more is not. Center pages through more answers, so the nurse can still offer 4+ |
| What if the patient can't raise their eyebrows? | A dwell-only mode is the next step (listed as a limitation); the gesture calibration also supports mouth-open as a backup |
| Won't it nag? | It respects silence: ignored suggestions fade, ignored night check-ins stop, "Quiet for 2 hours" is always available. Research shows patients dislike scripted, repetitive rounds |
| Is the temperature real? | The lamp and fan are real plugs; the temperature is a demo sensor value we control; an ESP32 + DHT22 (a few dollars) would replace it |
| Is "one week later" fake? | It loads a labelled demo week so the real routine code can run in 3 minutes; the 10 PM switch-off is the real scheduler and the real lamp |
| Is it safe? | Comfort, environment and communication only; clinical → nurse. Every action needs the patient's Yes except routines they approved. Nurse and "Something else" always available |
| Why would patients use it if they don't call nurses? | Studies show patients avoid calling because nurses are busy. LookAt asks first and fixes small things without a nurse |
| Does it replace eye-tracking AAC? | No. Those let patients type freely with special hardware. LookAt anticipates routine needs with a webcam and minimal effort |
| Privacy? | Video stays on the laptop; only text goes to Claude |

## R8. If something breaks
| Problem | Fix |
|---|---|
| Lamp/fan offline | R1 network check; the demo continues with the device shown as offline |
| Gaze wrong | press **r** (quick recalibration); check face size and light |
| Claude slow/down | every step falls back to rules, marked "(fallback)" on the panel |
| Phone can't load | "lea" set to Private; allow Python in the firewall |
| Anything else | **Reset demo**; worst case play the backup video |

## R9. Research (from the final idea doc)
1. **Patients avoid calling nurses:** *Through a Filter of Empathy and Understanding: Patients' Experiences of Missed Nursing Care* (2026, PMC). Patients delayed or minimised needs so as not to burden busy nurses.
2. **Patients value attention, not fixed schedules:** *Patient and Carer Perspectives of Intentional Rounding*. Needs met and respectful attention mattered more than set intervals.
3. **Against excessive check-ins:** *The delivery of compassionate nursing care in a tick-box culture*. Patients disliked scripted, repetitive rounds and preferred judgment and knowing the individual

## R10. CodeJam 16 facts
Hacking Fri 7 PM → Sun 12 PM. Submit on Devpost by **12 PM Sunday** (aim 11 AM). Judging **1–3:30 PM**. No code before Fri 7 PM. Say on Devpost it was built with Claude Code.
