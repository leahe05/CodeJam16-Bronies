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

## Patient screen designs
How the patient screen is built (one structure, several looks):

| File | What |
|---|---|
| `patient.html` | the default design, "Glow" (the mockups) |
| `patient-ui.js` | the screens and behaviour, shared by every design: home, room menus, and pop-ups that open as a **window** in the middle over the blurred home screen (a suggestion opens as a small notification window that grows into the check-in) |
| `patient-base.css` | shared layout, window, countdown, call-nurse alert; designs restyle it through CSS variables |
| `lookat.js` | polling, icons, gaze highlight, the 3 s countdown, overlay / face hint / demo tag |

Alternative looks in `designs/` (all blue, same features, same red call-nurse alert):

| Design | Open | Window opens | Countdown |
|---|---|---|---|
| Glow (default) | `/patient` | glass window sharpens into place | light drains around the option |
| Aurora | `/web/designs/aurora.html` | blooms out of a glowing orb | bright cyan light |
| Tide | `/web/designs/tide.html` | ripple from the center | water drains out of the option |
| Midnight | `/web/designs/midnight.html` | unfolds from a line of light | fine thread of light, glow fades |

Compare them side by side: `/web/designs/index.html`. Add `?noanim` to any page URL to turn animation off (screenshots).
