# web/: the pages (owner: P1 Lea)

How the backend finds and serves these pages, the API and the pre-merge check: `docs/FRONTEND.md` and `docs/API.md`.

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
