# Plugging in the frontend (Lea's pages)

The pages are Lea's. The backend never has its own design; until her files exist it shows a bare **team test page** (`lookat/fallback/`, only so gaze and the lamp can be tested). As soon as her files are in `web/`, they are used automatically: no switch, no setting.

## Where to put the pages (any one layout works)
| Plain files (simplest) | Or a build tool (React/Vite/...) |
|---|---|
| `web/patient.html` → `/patient` | `web/dist/patient.html` or `web/dist/patient/index.html` |
| `web/nurse.html` → `/nurse` | `web/dist/nurse.html` ... |
| `web/panel.html` → `/panel` | `web/dist/panel.html` ... |
| other files (css, js, svg, images): anywhere in `web/`, loaded as `/web/<path>` | built assets in `web/dist/assets/` load as `/assets/...` |

With a build tool, **commit the built `web/dist`** (the laptop at the demo won't run npm).

## How a page gets live data
Poll the JSON, then draw it. Use **relative** URLs only:
```js
async function poll() {
  try {
    const d = await (await fetch("/api/screen", { cache: "no-store" })).json();
    render(d);                       // the shape is in docs/API.md
  } catch (e) { /* show "reconnecting", keep the last screen */ }
  setTimeout(poll, 150);
}
poll();
```
Patient: `/api/screen` every 150 ms. Nurse: `/api/nurse` every 1 s, plus the POSTs. Panel: `/api/panel` every 500 ms, plus `POST /api/demo`. Everything is listed in `docs/API.md`.

**Mockup text → data:** in the mockups the words are fixed. In the real page every label comes from the JSON (`d.title`, `d.left.label`, `d.left.hint`...), because the agent writes the questions. The layout for each `d.screen` value (idle, suggestion, checkin, assistant, room, lights, temperature, nurse) is the mockup.

## Developing
- `py -3.11 -m lookat.fake_api` serves your pages exactly like the real program, with fake data that cycles through every screen and highlight. `--still checkin` freezes one screen.
- Using your own dev server (e.g. Vite on port 5173)? The API allows it (CORS). Call `http://localhost:8000/api/screen` only through a dev proxy, so the committed code still says `/api/screen`.

## Before you merge (one command)
```
py -3.11 -m lookat.check_frontend
```
It must say **PASS**. It checks that:
- your pages are found;
- they call the API;
- there are no hard-coded `localhost` or IP addresses (the nurse phone can't reach them);
- nothing loads from the internet (CDN, Google Fonts; the hotspot may be offline). Use system fonts (Segoe UI) and inline SVG.

## Rules
- Never crash on `null` or on fields you don't know.
- Need a field that isn't in the JSON? Ask Belinda: `docs/API.md` changes first, then both sides.
- Match the mockups: deep navy, glassy glowing cards, big white text, light-blue hints, LookAt eye logo. Readable from 2-3 m.
