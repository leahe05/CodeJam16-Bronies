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
