# LookAt web API (the contract between the Python backend and the 3 pages)

The Python program serves everything on one port (`config.PAGE_PORT`, 8000):

| Page | URL | File (Lea) | Polls |
|---|---|---|---|
| Patient screen (projector) | `/patient` | `web/patient.html` | `GET /api/screen` every 150 ms |
| Nurse app (phone) | `/nurse` | `web/nurse.html` | `GET /api/nurse` every 1 s |
| Reasoning + demo panel (judges/operator) | `/panel` | `web/panel.html` | `GET /api/panel` every 500 ms |

Anything else in `web/` (css, js, svg, images) is served at `/web/<file>`. Other page layouts (folders, a build tool's `web/dist`) also work: see `docs/FRONTEND.md`. Pages use **relative URLs** (`fetch("/api/screen")`), no external files (no CDN: the hotspot may have no internet). Check with `py -3.11 -m lookat.check_frontend`.

**Develop without the camera:** `py -3.11 -m lookat.fake_api` serves your pages with fake data that changes every few seconds (all screens, highlights, nurse alerts, panel feed) and accepts every POST below. Open `http://localhost:8000/patient`. The real program (`main.py`) serves the exact same URLs and JSON.

Rules for both sides:
- A field can be `null`; the page must not crash on null or on extra fields it doesn't know.
- Spots are always `"left"`, `"middle"`, `"right"` (the patient's left/center/right).
- Times are display strings (`"02:14"`) unless the name ends in `_s` (seconds) or `_c` (°C).
- `v` is the contract version. If it changes, the shape changed.

---

## 1. `GET /api/screen`: what the patient screen shows

```json
{
  "v": 1,
  "screen": "checkin",
  "badge": "AI CHECK-IN",
  "title": "Would you like help adjusting your pillows?",
  "subtitle": null,
  "icon": "pillow",
  "left":   {"id": "yes",  "label": "Yes, please.",        "hint": "Look left + raise eyebrows",   "icon": "check"},
  "middle": {"id": "home", "label": "Return to main menu", "hint": "Look center + raise eyebrows", "icon": "home"},
  "right":  {"id": "no",   "label": "No, thanks.",         "hint": "Look right + raise eyebrows",  "icon": "x"},
  "footer": null,
  "page": null,
  "show_call_nurse": true,
  "focus": {"spot": "left", "state": "asking"},
  "overlay": null,
  "clock": {"time": "02:14", "demo": true},
  "room": {"temp_c": 24.5, "setpoint_c": 22.0, "lamp_on": true, "fan_on": false},
  "face": true,
  "brow": {"up": false, "held_s": 0.0, "long_s": 2.0},
  "ask_s": 4.0,
  "voice": [{"id": 17951234, "text": "Lamp on.", "cut": true}, {"id": 17951235, "text": "Raise your eyebrows.", "cut": false}]
}
```

| Field | Meaning |
|---|---|
| `screen` | `idle`, `suggestion`, `checkin`, `assistant`, `room`, `lights`, `temperature`, `nurse` (layouts in R2 of PLAN.md and the mockups) |
| `badge` | small label above the title: `"AI SUGGESTION"`, `"AI CHECK-IN"`, `"AI ASSISTANT"`, `"ROOM CONTROLS"`, `"From your nurse"`, or null |
| `title`, `subtitle` | the big text and the smaller line under it |
| `icon` | picture for the title card (keyword, see icons below) or null |
| `left`, `middle`, `right` | the selectable options at each spot, or null when that spot has nothing. On `idle` and `suggestion`, **`left` is the small "Room Controls" card in the top-left corner** and `middle` is the big pill ("Talk to AI" / "Look center + raise eyebrows to engage") |
| `footer` | instruction line under the options (nurse mode), or null |
| `page` | `{"index": 0, "count": 2}` when answers are paged 2 at a time (draw page dots), or null |
| `show_call_nurse` | draw the bottom "Call Nurse • Long eyebrow raise anywhere" banner (false in nurse mode) |
| `focus` | where the patient is looking: `state` = `"dwell"` (looking, soft glow), `"asking"` (locked, bright fill + "Raise your eyebrows"), `"chosen"` (flash, ~0.5 s). null = nothing |
| `overlay` | `{"kind": "call_nurse", "text": "Your nurse has been called"}` for ~6 s after a long raise (full-screen red-orange glow). `{"kind": "calibrate", "text": "Look at the LEFT card", "spot": "left"}` during calibration: draw the 3 option cards **in exactly the same places as on the check-in screen** (empty), and make the `spot` card glow with a big dot in its centre and the text. The patient is calibrated on those positions, so they must match the real cards. `spot` null = just the text (e.g. "Raise your eyebrows"). Otherwise null |
| `clock` | current (demo) time; `demo` true = show a small "DEMO TIME" tag |
| `room` | live room state (for "Lamp is ON", "Room is 24.5 °C") |
| `face` | false = no face in view: show a subtle "Move into view of the camera" hint |
| `ask_s` | seconds the patient has to START the eyebrow raise once an option is `asking` (the countdown length). While `brow.up` is true the backend waits for the eyebrows to come down, so pause the countdown |
| `voice` | the last ~20 lines to say aloud, oldest first; ids only grow. Speak each line with an id you haven't spoken yet (on page load, skip the ones already there). `cut` true = stop what is being said first; false = wait for it to finish. `text` "" with `cut` = just stop. Browsers allow speech only after one click on the page. `web/lookat.js` already does all of this |
| `brow` | `{"up": true, "held_s": 0.8, "long_s": 2.0}`: the eyebrows are raised right now, for `held_s` seconds. Show it so the patient knows the raise was seen (e.g. a small "Eyebrows up" badge; on the `asking` option a check mark). While held, fill a ring from 0 to `long_s`: at `long_s` the nurse is called. A short raise is counted when the eyebrows come back down |

**Icons** (keywords the backend sends; draw each as an inline SVG): `lamp`, `bulb`, `thermometer`, `fan`, `moon`, `chat`, `sparkle`, `target`, `home`, `back`, `check`, `x`, `bed`, `pillow`, `water`, `grid`, `clock`, `nurse`, `bell`, `more`, `snow`, `sun`, `quiet`, `question`. Unknown keyword → no icon.

Examples of each screen are in `lookat/fake_api.py` (`SCREENS`).

---

## 2. `GET /api/nurse`: the nurse app

```json
{
  "v": 1,
  "patient": {"name": "Demo patient", "room": "Room 12"},
  "connected": true,
  "alerts": [
    {"id": "a3", "time": "07:31", "kind": "flag", "urgency": "normal",
     "text": "Patient feels cold below 20 °C. Ask them first?",
     "reason": "You set the AC to 18 °C. Memory: feels cold below 20 °C (chart).",
     "actions": [{"id": "ask", "label": "Ask them"}, {"id": "set_anyway", "label": "Set anyway"}],
     "done": false},
    {"id": "a2", "time": "05:02", "kind": "page", "urgency": "normal",
     "text": "Wants repositioning.", "reason": "Not temperature-related. Last visit 3h ago.",
     "actions": [{"id": "ack", "label": "On my way"}], "done": false},
    {"id": "a1", "time": "02:20", "kind": "call", "urgency": "urgent",
     "text": "Patient used the call signal", "reason": null,
     "actions": [{"id": "ack", "label": "On my way"}], "done": true}
  ],
  "next_visit": ["Would like the blinds open in the morning"],
  "session": {
    "active": true,
    "thread": [
      {"qid": "q1", "time": "07:33", "question": "Did you sleep okay?",
       "options": ["Yes", "No", "Something else"], "answer": "No", "seconds": 3.1}
    ],
    "follow_ups": [
      {"question": "What kept you awake?", "options": ["Too warm", "Pain", "Noise", "Something else"]}
    ],
    "waiting": false
  },
  "room": {"temp_c": 21.0, "setpoint_c": 18.0, "lamp_on": false, "fan_on": false, "fan_simulated": false},
  "facts": [{"text": "Feels cold below 20 °C", "source": "chart"}, {"text": "Runs warm at night", "source": "chart"}],
  "routines": [{"text": "Lamp off at 22:00", "status": "approved", "evidence": "6 of the last 7 nights"}],
  "recent": [{"time": "02:16", "text": "Said Yes to: Too warm? → fan on (24.5 → 22.8 °C)"}],
  "last_visit": "04:10",
  "summary": null
}
```
- `kind`: `call` (long raise, always urgent: sound + vibrate), `page` (the agent asks for a nurse and explains why), `flag` (the agent speaks up for the patient), `info`.
- `session.answer` null = waiting for the patient; `waiting` true = a question is on the patient screen now.
- `summary` = the last generated shift summary text, or null.

### Nurse actions (POST, JSON body, every one returns `{"ok": true, ...}` or `{"ok": false, "error": "..."}`)
| POST | Body | Does |
|---|---|---|
| `/api/nurse/session/start` | `{}` | patient screen switches to "From your nurse" |
| `/api/nurse/session/end` | `{}` | patient screen back to normal, agent resumes |
| `/api/nurse/suggest` | `{"question": "Did you sleep okay?"}` | returns `{"ok": true, "options": ["Yes", "No", "Something else"], "source": "ai"\|"fallback"}` (takes up to ~8 s: show a spinner) |
| `/api/nurse/send` | `{"question": "...", "options": ["...", "..."]}` | puts the question on the patient screen (starts a session if none); returns `{"ok": true, "qid": "q2"}` |
| `/api/nurse/alert` | `{"id": "a3", "action": "ask"}` | runs an alert button; marks it done |
| `/api/nurse/room` | `{"device": "lamp"\|"fan", "cmd": "on"\|"off"}` or `{"setpoint_c": 18}` | changes the room (the 18 °C flag comes from this) |
| `/api/nurse/visit` | `{}` | "I'm here": logs a nurse visit |
| `/api/nurse/fact` | `{"text": "Prefers the blinds open"}` | adds a preference to memory |
| `/api/nurse/summary` | `{}` | returns `{"ok": true, "text": "..."}` (up to ~8 s) |

---

## 3. `GET /api/panel`: reasoning + demo panel

```json
{
  "v": 1,
  "clock": {"time": "02:14", "day": "Night 1", "demo": true},
  "perception": {"temp_c": 24.5, "setpoint_c": 22.0, "lamp_on": true, "fan_on": false,
                 "eyes_open": true, "awake": true, "open_ratio": 0.92, "face": true,
                 "last_visit": "23:40", "screen": "idle", "quiet_until": null},
  "memory": {
    "facts": [{"text": "Runs warm at night", "source": "chart"}, {"text": "Feels cold below 20 °C", "source": "chart"}],
    "routines": [{"text": "Lamp off at 22:00", "status": "candidate", "evidence": "patient turned it off 21:55, 22:05, 22:00 on Nov 14-16"}],
    "top_options": ["Adjust pillows", "Water"],
    "hidden_options": ["TV on"],
    "night_checkins": true
  },
  "feed": [
    {"id": 7, "time": "02:14", "stage": "noticed", "text": "Awake at 2:14 AM, room 24.5 °C",
     "why": "night + eyes open 92% of the last minute + above 23 °C + memory: runs warm", "fallback": false},
    {"id": 8, "time": "02:14", "stage": "decided", "text": "Ask: \"You seem to be awake. Too warm?\"", "why": "one short question; fixable without a nurse", "fallback": false}
  ],
  "status": {"camera": true, "lamp": true, "fan": "simulated", "ai": true}
}
```
- `feed`: newest **last**; `stage` is `noticed`, `decided`, `did`, `checked`, `outcome`, `learned` or `error`. Show each agent episode as a card going Noticed → Decided → Did → Checked → Outcome; mark `fallback: true` steps "(rule-based)".

### Demo controls (POST `/api/demo`, operator only)
| Body | Does |
|---|---|
| `{"action": "set_time", "value": "02:00"}` | demo clock to 02:00 (also `"07:30"`, `"21:59"`) |
| `{"action": "one_week_later"}` | loads the labelled demo week (routine evidence) into a fresh demo patient (`memory/<patient>-demo-week.json`); answers `{"ok": true, "nights": 7, "from", "to"}`. Then **Skip to 21:59**: at 22:00 the approved routine "Lamp off at 22:00" runs (banner "It's 10 PM. Turning off your lamp, as usual.", OK / Keep it on). `memory.routines` in `/api/panel` lists approved routines and candidates with their evidence. |
| `{"action": "set_temp", "value": 24.5}` | room temperature |
| `{"action": "eyes", "value": "auto"\|"open"\|"closed"}` | eyes-open override |
| `{"action": "reset"}` | reset the demo (page asks "Are you sure?" first) |
