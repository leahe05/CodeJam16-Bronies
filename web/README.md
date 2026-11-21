# web/: the 3 pages (owner: P1 Lea)

| File | Served at | Data |
|---|---|---|
| `patient.html` | `/patient` (projector, full screen) | `GET /api/screen` every 150 ms |
| `nurse.html` | `/nurse` (phone) | `GET /api/nurse` every 1 s + the POSTs |
| `panel.html` | `/panel` (judges/operator) | `GET /api/panel` every 500 ms + `POST /api/demo` |

Shared CSS/JS/SVG files go here too and load from `/web/<file>`.

**Start:** `py -3.11 -m lookat.fake_api`, then open http://localhost:8000/patient. Add `--still checkin` to freeze one screen while styling it. The fake data cycles through every screen, highlight and alert.

**Rules:**
- The JSON shapes are in `docs/API.md`. If you need a field that isn't there, ask Belinda to change the contract (both sides change together).
- Plain HTML/CSS/JS, no build step, no CDN or Google Fonts (the hotspot may have no internet). Use system fonts (Segoe UI) and inline SVG icons.
- Never crash on `null` or on unknown fields.
- Match the mockups (deep navy, glassy glowing cards, big white text, light-blue hints, LookAt eye logo). Readable from 2-3 m.

## The patient screen ("Aurora", day + night)
| File | What |
|---|---|
| `patient.html` | the look: frosted glass over slowly drifting light, a **night** look and a **day** look |
| `patient-ui.js` | the screens and behaviour: home, room menus, and pop-ups that open as a **window** in the middle over the blurred home screen (a suggestion opens as a small notification window that grows into the check-in); the day/night switch |
| `patient-base.css` | shared layout, window, countdown, call-nurse alert (restyled by the variables in `patient.html`) |
| `lookat.js` | polling, icons, gaze highlight, the 3 s countdown, overlay / face hint / demo tag |

- **Night (17:00 to 07:00):** deep blue, white text, glowing moon. **Day (07:00 to 17:00):** light blue, near-black text, glowing sun. It follows the **backend clock** (`clock.time` in `/api/screen`), so the demo's "Set 02:00" / "Set 07:30" switch it too, and it cross-fades over 2.5 s. Times: `DAY_START` / `NIGHT_START` in `patient-ui.js`.
- Pop-ups bloom out of a glowing orb into a window; the countdown is a light that drains around the selected option in 3 s (`ASK_S` in `lookat.js` = `config.ASK_TIMEOUT_S`).
- The call-nurse alert stays red in both looks.
- Testing: add `?theme=day` or `?theme=night` to force a look, `?noanim` to turn animation off.
