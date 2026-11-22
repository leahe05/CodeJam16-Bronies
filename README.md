<p align="center"><img src="web/logo.svg" width="88" alt="MinimEyes logo: a smiling chat bubble"></p>

<h1 align="center">MinimEyes</h1>

<p align="center"><b>An AI that speaks first, for patients who can only move their eyes.</b><br>
<i>MinimEyes speaks first, so patients need fewer glances, and nurses need fewer calls.</i></p>

<p align="center">Built for McGill CodeJam 16.</p>

---

## The problem

People with ALS, locked-in syndrome, or on a ventilator in the ICU can often move only their eyes. At 2 AM they may be too warm or need their pillow moved, and they can't tell anyone. They wait, sometimes for hours, for someone to notice.

Research shows that patients already hold back from calling busy nurses so they won't be a burden. Scripted, repetitive check-ins don't help either: patients value attention to their own needs more than fixed rounds. See [Research](#research).

## What MinimEyes does

MinimEyes uses an ordinary webcam. The patient answers by looking **left, center or right** and raising their **eyebrows**. There is no special eye-tracking hardware.

- **It speaks first.** The agent notices the time, the room temperature, whether the patient is awake, and what it remembers about them. It then asks one useful question, such as *"You seem to be awake. Too warm?"*
- **It fixes and checks.** With the patient's *Yes*, it turns on the real fan or lamp. Then it checks its work ("Better now?").
- **It speaks up.** When a nurse sets the room to 18 °C for a patient who feels cold below 20 °C, the nurse app shows a flag: *"Ask them first?"*
- **It learns.** It learns routines (lights off at 10 PM), comfort facts, and which answers a patient picks. It also stops check-ins that get ignored.
- **The nurse can talk to the patient.** The nurse types or asks Claude to draft a question. The questions go from broad to specific (*"Do you need something, or are you uncomfortable?"* → *"Upper body or lower body?"* → …). The patient answers with their eyes, and the nurse sees each answer live, with how many seconds it took.
- **It writes the shift summary** for the nurse.

## The three screens

| Screen | URL | Who uses it |
|---|---|---|
| **Patient screen** | `http://localhost:8000/patient` | The patient (projector or monitor), answering with their eyes |
| **Nurse app** | `http://<laptop IP>:8000/nurse` | The nurse, on a phone on the same network |
| **Agent dashboard** | `http://localhost:8000/panel` | Judges and the team: the agent's reasoning, live, plus the demo controls |

### Patient controls

| Action | How |
|---|---|
| Choose | Look **left / center / right** until the card locks, then a **short eyebrow raise** |
| Call Nurse | A **long eyebrow raise** (about 2 s), anywhere, any time |
| More answers | Center pages through them ("More options"); at most 2 answers show at a time |
| Can't find the answer | Every nurse question has an **Other** choice |

### The patient's screens

The screens are Idle ("You're all set."), AI Suggestion, AI Check-in, AI Assistant ("How can I help you?"), Room Controls, Message your nurse, and From your nurse. Whenever the nurse has been called, a "Your nurse has been called" screen shows. The screen switches between a night look and a day look with the clock.

## How the agent works (and why it isn't a wrapper)

**Notice → connect facts → ask one question → fix → check its work → escalate or defer → learn → summarise.**

| Part | Done by |
|---|---|
| Seeing the choice: gaze, eyebrows, eyes open | **Our code** (MediaPipe face landmarks + our own classifier) |
| When to speak, respecting silence | **Our code**: deterministic and explainable |
| Memory, routines, comfort facts, answer ranking | **Our code** (`lookat/memory.py`, `lookat/routines.py`) |
| Safety: no action without the patient's Yes; anything clinical goes to the nurse | **Our code**, enforced |
| Switching the real lamp and fan, reading the sensor | **Our code** (`lookat/plugs.py`, `lookat/devices.py`) |
| What to ask, how to word it, what to try next, the explanation to the nurse, the shift summary | **Claude**, through 9 tools (`get_context`, `suggest`, `ask`, `act`, `check`, `page_nurse`, `defer`, `remember`, `finish`) |

Every Claude step has a rule-based fallback, so the system keeps working when the network or the API is down.

## Safety

- Comfort, environment and communication only. Anything clinical goes to the nurse.
- Every action needs the patient's *Yes*, except routines the patient already approved.
- **Call Nurse** (long eyebrow raise) and **Other** are always available.
- Claude drafts the nurse's questions, but the **nurse approves every one** before it is sent.
- Video stays on the laptop. Only text goes to Claude.

## What is real and what is demo

| Real | Demo |
|---|---|
| Webcam gaze and eyebrow detection | The **temperature** is a demo sensor value set from the dashboard; it drifts down while the fan runs. An ESP32 + DHT22 would replace it. |
| The **lamp** and the **fan** (TP-Link Kasa smart plugs) | The **demo clock** (Set 02:00, 07:30, 21:59) |
| Claude (the agent, the nurse question drafts, the shift summary) | **"One week later"** loads a labelled demo week, so the real routine code can run in 3 minutes |

## Limitations

- Webcam gaze is coarse. Left, center and right are reliable, more zones are not, so at most 2 answers show at a time.
- MinimEyes does not replace eye-tracking AAC devices, which let patients type freely with special hardware. It anticipates routine needs with minimal effort.
- A dwell-only mode, for patients who can't raise their eyebrows, is not built yet.

## Run it (Windows)

**Needs:** Python 3.11, a webcam, and optionally two TP-Link Kasa plugs and an Anthropic API key.

```powershell
py -3.11 -m pip install -r requirements.txt
```

Save the keys once. They are read from your Windows user environment and never printed.

```powershell
setx ANTHROPIC_API_KEY "..."
setx KASA_USER "..."
setx KASA_PASSWORD "..."
setx FAN_IP "172.20.10.x"
```

The lamp defaults to `172.20.10.2`; set `LAMP_IP` to change it. If no fan plug is set, the fan is simulated.

Start MinimEyes:

```powershell
py -3.11 main.py --patient demo
```

It prints the three page addresses, calibrates gaze and eyebrows on the patient screen the first time, and then runs.

| Option | What it does |
|---|---|
| `--recalibrate` | Calibrate gaze and eyebrows again (new room, new light) |
| `--keyboard` | No camera: **a / s / d** look left / center / right, **space** short raise, **e** long raise |
| `--voice edge\|page\|windows\|off` | The spoken voice: Microsoft "Ava" (needs internet and `pip install edge-tts`), read by the page, the Windows voice, or none |
| `--profile margaret\|james` | Which demo patient's chart to load |
| `--camera N` | Which webcam |

**Operator window keys:** **c** recalibrate gaze, **g** recalibrate eyebrows, **q** quit.

**Front-end only, no camera or plugs:** `py -3.11 -m lookat.fake_api` serves the same pages with fake data.

**Tests:**

```powershell
py -3.11 -m pytest -q
```

### Demo controls (on `/panel`)

Set the time (02:00, 07:30, 14:00, 21:59) · set the temperature · eyes auto / open / closed · One week later · switch the patient profile · Reset demo.

## Project layout

| Path | What's there |
|---|---|
| `main.py` | The real program: camera loop, web server, plugs, voice |
| `lookat/vision.py`, `gaze.py`, `gestures.py`, `awake.py` | Face landmarks, gaze direction, eyebrow raises, eyes open |
| `lookat/live.py`, `screens.py`, `machine.py` | The patient screen's state, selection and menus |
| `lookat/agent.py` | The Claude agent and its tools |
| `lookat/memory.py`, `routines.py`, `summary.py` | Memory, learned routines, shift summary |
| `lookat/nurse_bridge.py`, `nurse_ai.py` | The nurse app's backend and Claude-drafted questions |
| `lookat/plugs.py`, `devices.py` | The lamp and fan plugs and the room |
| `lookat/server.py` | FastAPI server: the pages and the JSON API ([docs/API.md](docs/API.md)) |
| `web/` | The patient screen, nurse app and agent dashboard |
| `tests/` | pytest suite |

The Python package is still called `lookat`, the project's working name.

## Research

1. *Through a Filter of Empathy and Understanding: Patients' Experiences of Missed Nursing Care* (2026, PMC): patients delayed or minimised their needs so as not to burden busy nurses.
2. *Patient and Carer Perspectives of Intentional Rounding*: having needs met and getting respectful attention mattered more than set intervals.
3. *The delivery of compassionate nursing care in a tick-box culture*: patients disliked scripted, repetitive rounds and preferred judgment and being known as individuals.

## Team

Built at **McGill CodeJam 16** by Lea, Belinda, Paco and William, with [Claude Code](https://claude.com/claude-code).
