"""Patient screen for the projector. Task D1.

  GET /patient      full-screen page (everything inline: no external files, no build step)
  GET /api/screen   JSON the page polls every 150 ms:

  {"screen":  {"kind": "idle|suggestion|checkin|assistant|room|lights|temperature|nurse",
               "badge": str|None, "title": str, "subtitle": str|None, "icon": str|None, "footer": str|None,
               "left" / "center" / "right": {"id": str, "label": str, "hint": str|None} | None,
               "options": [str], "page": int, "pages": int},
   "machine": {"dwell": spot|None, "asking": spot|None, "state": "IDLE|ASKING|COOLDOWN"},
   "call_nurse": bool,                 # shows "Your nurse has been called"
   "clock": {...}, "room": {...}}      # passed through for later use

main.py plugs the live data in with set_source(fn). spot = "left" | "center" | "right".

Self-test: py -3.11 -m lookat.display --demo [--frame N]
"""
import argparse
import time
from typing import Callable, Optional

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

KINDS = {"idle", "suggestion", "checkin", "assistant", "room", "lights", "temperature", "nurse"}
SPOTS = ("left", "center", "right")

router = APIRouter()


def opt(id: str, label: str, hint: Optional[str] = None) -> dict:
    return {"id": id, "label": label, "hint": hint}


def screen(kind: str, title: str, badge=None, subtitle=None, icon=None, footer=None,
           left=None, center=None, right=None, options=(), page=0) -> dict:
    options = list(options)
    return {"kind": kind, "badge": badge, "title": title, "subtitle": subtitle, "icon": icon,
            "footer": footer, "left": left, "center": center, "right": right,
            "options": options, "page": page, "pages": max(1, (len(options) + 1) // 2)}


ROOM_CARD = opt("room", "Room Controls", "Look left + short eyebrow raise")
HOME = opt("home", "Return to main menu")
IDLE = screen("idle", "You're all set.", subtitle="I'm here if you need anything.",
              left=ROOM_CARD, center=opt("talk", "Talk to AI", "Look center + short eyebrow raise"))


def frame(scr: dict, dwell=None, asking=None, state="IDLE", call_nurse=False) -> dict:
    return {"screen": scr, "machine": {"dwell": dwell, "asking": asking, "state": state},
            "call_nurse": call_nurse, "clock": {}, "room": {}}


_source: Callable[[], dict] = lambda: frame(IDLE)


def set_source(fn: Callable[[], dict]) -> None:
    """main.py: fn() returns the /api/screen dict."""
    global _source
    _source = fn


@router.get("/patient", response_class=HTMLResponse)
def patient_page() -> str:
    return PAGE


@router.get("/api/screen")
def api_screen() -> dict:
    return _source()


# ----- demo frames: every screen, a dwell, an asking highlight, a confirm, the overlay -----
def demo_frames() -> list:
    sugg = screen("suggestion", "You seem to be awake. Too warm?", badge="AI SUGGESTION",
                  left=ROOM_CARD, center=opt("engage", "Look center + raise eyebrows to engage"))
    pillows = screen("checkin", "Would you like help adjusting your pillows?", badge="AI CHECK-IN",
                     icon="pillows", left=opt("yes", "Yes, please."), right=opt("no", "No, thanks."), center=HOME)
    assistant = screen("assistant", "How can I help you?", badge="AI ASSISTANT",
                       left=opt("o0", "Get more comfortable"), right=opt("more", "More suggestions"), center=HOME)
    room = screen("room", "What would you like to adjust?", badge="ROOM CONTROLS",
                  left=opt("lights", "Adjust the lights"), right=opt("temp", "Adjust the temperature"), center=HOME)
    back = opt("back", "Back")
    lights = screen("lights", "Lamp is OFF", badge="ROOM CONTROLS",
                    left=opt("on", "Lamp on"), right=opt("off", "Lamp off"), center=back)
    temp = screen("temperature", "Room is 24 °C", badge="ROOM CONTROLS",
                  left=opt("cooler", "Cooler"), right=opt("warmer", "Warmer"), center=back)
    answers = ["Adjust pillows", "Change position", "Water", "Something else"]
    more = opt("more", "More options")
    footer = ("Look left or right and raise your eyebrows to choose, "
              "or look center and raise your eyebrows to see more options.")

    def nurse(page):
        a = answers[2 * page: 2 * page + 2]
        return screen("nurse", "What would help you feel more comfortable?", badge="From your nurse",
                      left=opt("a%d" % (2 * page), a[0]), right=opt("a%d" % (2 * page + 1), a[1]),
                      center=more, footer=footer, options=answers, page=page)

    open_q = screen("checkin", "What would help most?", badge="AI CHECK-IN",
                    left=opt("a0", answers[0]), right=opt("a1", answers[1]), center=more, options=answers)
    return [
        frame(IDLE),
        frame(IDLE, dwell="left"),
        frame(IDLE, asking="left", state="ASKING"),
        frame(room),
        frame(room, dwell="right"),
        frame(temp, asking="left", state="ASKING"),
        frame(lights),
        frame(IDLE, call_nurse=True),
        frame(sugg),
        frame(sugg, asking="center", state="ASKING"),
        frame(pillows, dwell="left"),
        frame(pillows, asking="left", state="ASKING"),
        frame(pillows, state="COOLDOWN"),
        frame(assistant, dwell="right"),
        frame(open_q),
        frame(nurse(0), dwell="right"),
        frame(nurse(1), asking="left", state="ASKING"),
    ]


def check_frames(frames: list) -> list:
    """Problems that would break the page (empty list = fine)."""
    bad = []
    for i, f in enumerate(frames):
        s, m = f.get("screen", {}), f.get("machine", {})
        if s.get("kind") not in KINDS:
            bad.append(f"frame {i}: unknown kind {s.get('kind')!r}")
        if not s.get("title"):
            bad.append(f"frame {i}: no title")
        for key in ("dwell", "asking"):
            if m.get(key) and not s.get(m[key]):
                bad.append(f"frame {i}: {key} on {m[key]!r} but that spot is empty")
    return bad


# ----- the page -------------------------------------------------------------
PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LookAt</title>
<style>
  :root {
    --u: min(1vh, 0.5625vw);               /* 1u = 1% of a 16:9 screen's height */
    --navy: #071a3a; --navy2: #0b2a5c;
    --card: rgba(20, 60, 120, .35); --card-hi: rgba(45, 110, 215, .45);
    --edge: rgba(110, 180, 255, .55); --edge-hi: #cfe6ff;
    --glow: rgba(70, 150, 255, .45); --hint: #9cc8ff; --text: #ffffff;
    --bell: #ff9f8a; --ok: #4ade80;
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; margin: 0; }
  body {
    overflow: hidden; color: var(--text); background: radial-gradient(ellipse 85% 75% at 50% 42%, var(--navy2), var(--navy) 72%);
    font-family: "Segoe UI Variable Display", "Segoe UI", system-ui, -apple-system, "Helvetica Neue", sans-serif;
    display: grid; grid-template-rows: calc(var(--u) * 8) 1fr auto; gap: calc(var(--u) * 2);
    padding: calc(var(--u) * 3) calc(var(--u) * 4) calc(var(--u) * 3.5);
    -webkit-font-smoothing: antialiased; cursor: none;
  }
  svg.i { width: 1em; height: 1em; fill: none; stroke: currentColor; stroke-width: 1.6; stroke-linecap: round; stroke-linejoin: round; flex: none; }
  #bg { position: fixed; inset: 0; width: 100%; height: 100%; z-index: -1; }

  /* ---------- header ---------- */
  header { display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; }
  .brand { display: flex; align-items: center; gap: calc(var(--u) * 1); font-weight: 600; font-size: calc(var(--u) * 3); justify-self: end; }
  .brand svg { font-size: calc(var(--u) * 4.2); color: #8fc8ff; }
  .brand.start { justify-self: start; }
  .corner {
    justify-self: start; display: flex; align-items: center; gap: calc(var(--u) * 1.4);
    padding: calc(var(--u) * 1.1) calc(var(--u) * 2.4) calc(var(--u) * 1.1) calc(var(--u) * 1.6);
    border: 2px solid var(--edge); border-radius: calc(var(--u) * 2); background: var(--card);
  }
  .corner .ic { font-size: calc(var(--u) * 3.8); color: #cfe6ff; }
  .corner b { display: block; font-size: calc(var(--u) * 2.2); font-weight: 600; }
  .corner small { display: block; font-size: calc(var(--u) * 1.45); color: var(--hint); }
  .nursepill {
    display: flex; align-items: center; gap: calc(var(--u) * 1.2); font-size: calc(var(--u) * 2.4); font-weight: 500;
    padding: calc(var(--u) * .9) calc(var(--u) * 3.6); border: 2px solid var(--edge-hi); border-radius: 99px;
    background: var(--card-hi); box-shadow: 0 0 calc(var(--u) * 2.5) var(--glow);
  }
  .nursepill svg { font-size: calc(var(--u) * 3.2); }
  .conn { justify-self: end; display: flex; align-items: center; gap: calc(var(--u) * .8); font-size: calc(var(--u) * 1.8); color: var(--hint); }
  .conn::before { content: ""; width: calc(var(--u) * 1.1); height: calc(var(--u) * 1.1); border-radius: 50%; background: var(--ok); box-shadow: 0 0 calc(var(--u) * 1.2) var(--ok); }
  .conn.off::before { background: #f59e0b; box-shadow: 0 0 calc(var(--u) * 1.2) #f59e0b; }

  /* ---------- stage ---------- */
  main { display: flex; align-items: center; justify-content: center; min-height: 0; }
  /* slide only, never fade: content is visible from the very first frame even if animations stall */
  .enter { animation: enter .28s ease-out; }
  @keyframes enter { from { transform: translateY(calc(var(--u) * 1.5)) scale(.985); } }
  .panel {
    width: min(100%, calc(var(--u) * 150)); text-align: center;
    padding: calc(var(--u) * 3.4) calc(var(--u) * 5) calc(var(--u) * 3.8);
    border: 2px solid var(--edge); border-radius: calc(var(--u) * 3.2);
    background: linear-gradient(180deg, rgba(55, 125, 245, .22), rgba(20, 60, 120, .30));
    box-shadow: 0 0 calc(var(--u) * 5) rgba(50, 130, 255, .28), inset 0 0 calc(var(--u) * 4) rgba(110, 175, 255, .10);
  }
  .badge-row { display: flex; align-items: center; justify-content: center; gap: calc(var(--u) * .8); margin-bottom: calc(var(--u) * 1.6); }
  .badge-row > svg { font-size: calc(var(--u) * 3.4); color: #e3f1ff; }
  .badge {
    display: inline-flex; align-items: center; gap: calc(var(--u) * .7);
    font-size: calc(var(--u) * 1.55); font-weight: 600; letter-spacing: .14em; text-transform: uppercase;
    padding: calc(var(--u) * .5) calc(var(--u) * 1.8); border: 1.5px solid var(--edge); border-radius: 99px;
    background: rgba(30, 80, 170, .45); color: #d9ebff;
  }
  .badge svg { font-size: calc(var(--u) * 2); }
  h1 { margin: 0; font-size: calc(var(--u) * 6.4); line-height: 1.12; font-weight: 700; letter-spacing: -.01em; text-wrap: balance; }
  .subtitle { margin: calc(var(--u) * 1.2) 0 0; font-size: calc(var(--u) * 3); color: var(--hint); }
  .illus { font-size: calc(var(--u) * 9); color: #d6eaff; margin-top: calc(var(--u) * 1); filter: drop-shadow(0 0 calc(var(--u) * 1.5) var(--glow)); }
  .illus svg { stroke-width: 1.2; }

  /* idle */
  .idle { text-align: center; display: flex; flex-direction: column; align-items: center; }
  .moon { width: calc(var(--u) * 13); height: calc(var(--u) * 13); margin-bottom: calc(var(--u) * 1); filter: drop-shadow(0 0 calc(var(--u) * 2.5) rgba(110, 180, 255, .8)); }
  .idle h1 { font-size: calc(var(--u) * 9.5); }
  .talk {
    margin-top: calc(var(--u) * 4); min-width: calc(var(--u) * 52);
    padding: calc(var(--u) * 2) calc(var(--u) * 6); border-radius: calc(var(--u) * 7);
    border: 2.5px solid var(--edge-hi); background: linear-gradient(180deg, rgba(70, 145, 255, .38), rgba(25, 75, 170, .35));
    box-shadow: 0 0 calc(var(--u) * 3.5) var(--glow), inset 0 0 calc(var(--u) * 2) rgba(140, 200, 255, .25);
  }
  .talk b { display: flex; align-items: center; justify-content: center; gap: calc(var(--u) * 1.4); font-size: calc(var(--u) * 4.6); font-weight: 600; }
  .talk small { display: block; margin-top: calc(var(--u) * .4); font-size: calc(var(--u) * 1.9); color: var(--hint); }

  /* suggestion */
  .suggestion h1 { font-size: calc(var(--u) * 7.2); }
  .engage {
    display: inline-flex; align-items: center; gap: calc(var(--u) * 1.4); margin-top: calc(var(--u) * 3.4);
    padding: calc(var(--u) * 1.4) calc(var(--u) * 4); border-radius: 99px; font-size: calc(var(--u) * 2.4);
    border: 2px solid var(--edge-hi); background: var(--card-hi); box-shadow: 0 0 calc(var(--u) * 2) var(--glow);
  }
  .engage svg { font-size: calc(var(--u) * 3); }

  /* option cards */
  .opts { display: grid; grid-template-columns: 1fr 1fr; gap: calc(var(--u) * 3); margin-top: calc(var(--u) * 3.4); }
  .opts.one { grid-template-columns: minmax(0, calc(var(--u) * 70)); justify-content: center; }
  .opt {
    position: relative; overflow: hidden; display: flex; align-items: center; gap: calc(var(--u) * 2.4); text-align: left;
    min-height: calc(var(--u) * 15); padding: calc(var(--u) * 2) calc(var(--u) * 3);
    border: 2px solid var(--edge); border-radius: calc(var(--u) * 2.6); background: var(--card);
  }
  .opt .ic { font-size: calc(var(--u) * 6.5); color: #e0efff; }
  .opt b { display: block; font-size: calc(var(--u) * 4.6); font-weight: 600; line-height: 1.1; }
  .opt small, .center small { display: block; margin-top: calc(var(--u) * .6); font-size: calc(var(--u) * 1.8); color: var(--hint); }
  .opts.stack .opt { flex-direction: column; justify-content: center; text-align: center; gap: calc(var(--u) * 1.2); min-height: calc(var(--u) * 19); }
  .opts.stack .opt .ic { font-size: calc(var(--u) * 6); }
  .opts.stack .opt b { font-size: calc(var(--u) * 4.5); }

  .center {
    position: relative; overflow: hidden; display: flex; align-items: center; justify-content: center; gap: calc(var(--u) * 2);
    width: min(100%, calc(var(--u) * 72)); margin: calc(var(--u) * 2.6) auto 0;
    padding: calc(var(--u) * 1.4) calc(var(--u) * 3); text-align: left;
    border: 2px solid var(--edge); border-radius: calc(var(--u) * 2.2); background: rgba(20, 55, 115, .35);
  }
  .center .ic { font-size: calc(var(--u) * 4); color: #e0efff; }
  .center b { display: block; font-size: calc(var(--u) * 2.8); font-weight: 600; }

  .instr {
    position: relative; overflow: hidden; display: flex; align-items: center; gap: calc(var(--u) * 2.2); text-align: left;
    margin-top: calc(var(--u) * 2.8); padding: calc(var(--u) * 1.6) calc(var(--u) * 3);
    border: 2px solid var(--edge); border-radius: calc(var(--u) * 2.2); background: rgba(20, 55, 115, .35);
    font-size: calc(var(--u) * 2.1); color: #dcecff; line-height: 1.35;
  }
  .instr .ic { font-size: calc(var(--u) * 4.6); color: var(--bell); padding-right: calc(var(--u) * 2.2); border-right: 1.5px solid var(--edge); box-sizing: content-box; }
  .dots { display: flex; justify-content: center; gap: calc(var(--u) * 1.3); margin-top: calc(var(--u) * 2.4); }
  .dots i { width: calc(var(--u) * 1.4); height: calc(var(--u) * 1.4); border-radius: 50%; background: rgba(130, 180, 255, .45); transition: background .2s; }
  .dots i.on { background: #fff; box-shadow: 0 0 calc(var(--u) * 1.2) #9fd0ff; }

  /* ---------- live gaze feedback ---------- */
  [data-spot] { transition: border-color .2s, background .2s, box-shadow .2s, transform .2s; }
  [data-spot].dwell { border-color: var(--edge-hi); box-shadow: 0 0 calc(var(--u) * 3.5) rgba(110, 185, 255, .85), inset 0 0 calc(var(--u) * 2) rgba(150, 205, 255, .25); }
  [data-spot].asking { border-color: #fff; background: rgba(70, 145, 255, .55); box-shadow: 0 0 calc(var(--u) * 5) rgba(120, 190, 255, .95), inset 0 0 calc(var(--u) * 3) rgba(180, 220, 255, .35); transform: scale(1.025); }
  [data-spot].asking small { color: #fff; font-weight: 600; }
  [data-spot] .bar { position: absolute; left: 0; bottom: 0; height: calc(var(--u) * .7); width: 0; background: #fff; opacity: 0; border-radius: 0 99px 99px 0; }
  [data-spot].asking .bar { opacity: .9; animation: shrink 3s linear forwards; }
  @keyframes shrink { from { width: 100%; } to { width: 0; } }
  [data-spot].confirmed { animation: confirm .45s ease-out; }
  @keyframes confirm { 0% { background: rgba(255, 255, 255, .85); box-shadow: 0 0 calc(var(--u) * 7) #fff; } 100% { background: var(--card-hi); } }

  /* ---------- call nurse banner + overlay ---------- */
  footer {
    width: min(100%, calc(var(--u) * 150)); justify-self: center; display: flex; align-items: center; gap: calc(var(--u) * 2.4);
    padding: calc(var(--u) * 1.6) calc(var(--u) * 3.4); border: 2px solid var(--edge); border-radius: calc(var(--u) * 2.6);
    background: rgba(12, 40, 95, .55);
  }
  footer .ic { font-size: calc(var(--u) * 4.4); color: var(--bell); padding-right: calc(var(--u) * 2.4); border-right: 1.5px solid var(--edge); box-sizing: content-box; }
  footer b { font-size: calc(var(--u) * 3); font-weight: 600; }
  footer .tx { font-size: calc(var(--u) * 2.3); color: var(--hint); }
  footer .tx::before { content: "•"; margin: 0 calc(var(--u) * 1.4); color: #6f93c4; }
  .gone { display: none !important; }

  #callnurse {
    position: fixed; inset: 0; z-index: 5; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: calc(var(--u) * 2.4);
    background: radial-gradient(ellipse 70% 60% at 50% 48%, #b8483c, #6e1a24 58%, #24060d);
    text-align: center; opacity: 0; pointer-events: none; transition: opacity .3s;
  }
  #callnurse.show { opacity: 1; }
  #callnurse .ic { font-size: calc(var(--u) * 16); color: #ffe1d6; filter: drop-shadow(0 0 calc(var(--u) * 3) rgba(255, 170, 140, .9)); }
  #callnurse.show .ic { animation: ring 1.1s ease-in-out infinite; }
  #callnurse h1 { font-size: calc(var(--u) * 8.5); }
  #callnurse p { margin: 0; font-size: calc(var(--u) * 3.2); color: #ffd9cf; }
  @keyframes ring { 0%, 100% { transform: rotate(0); } 20% { transform: rotate(-14deg); } 40% { transform: rotate(12deg); } 60% { transform: rotate(-8deg); } 80% { transform: rotate(5deg); } }
  @media (prefers-reduced-motion: reduce) { *, *::before { animation-duration: .01s !important; animation-iteration-count: 1 !important; } }
</style>
</head>
<body>
<svg id="bg" viewBox="0 0 1600 900" preserveAspectRatio="none" aria-hidden="true">
  <defs>
    <filter id="soft"><feGaussianBlur stdDeviation="7"/></filter>
    <linearGradient id="sea" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2f74ff" stop-opacity=".20"/><stop offset="1" stop-color="#2f74ff" stop-opacity="0"/></linearGradient>
    <radialGradient id="halo" cx=".5" cy=".42" r=".45"><stop offset="0" stop-color="#3d8bff" stop-opacity=".16"/><stop offset="1" stop-color="#3d8bff" stop-opacity="0"/></radialGradient>
  </defs>
  <rect width="1600" height="900" fill="url(#halo)"/>
  <path d="M-50 770 C 380 610, 820 1010, 1650 650 L1650 950 L-50 950 Z" fill="url(#sea)"/>
  <g fill="none" stroke="#5aa9ff">
    <path d="M-50 770 C 380 610, 820 1010, 1650 650" stroke-width="7" opacity=".32" filter="url(#soft)"/>
    <path d="M-50 770 C 380 610, 820 1010, 1650 650" stroke-width="1.6" opacity=".75"/>
    <path d="M-50 840 C 460 730, 920 1010, 1650 780" stroke-width="1" opacity=".4"/>
    <path d="M-70 190 C 170 380, 150 660, 430 960" stroke-width="6" opacity=".22" filter="url(#soft)"/>
    <path d="M-70 190 C 170 380, 150 660, 430 960" stroke-width="1.1" opacity=".45"/>
    <path d="M1670 160 C 1460 320, 1510 610, 1260 960" stroke-width="1" opacity=".22"/>
  </g>
</svg>

<header id="head"></header>
<main id="stage"></main>
<footer id="bar"></footer>
<div id="callnurse"></div>

<script>
// ---------------- icons (inline line icons) ----------------
const ICONS = {
  eye: '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3.2"/><circle cx="12" cy="12" r="1.1" fill="currentColor"/>',
  spark: '<path d="M10 3.5l1.7 5.3 5.3 1.7-5.3 1.7L10 17.5l-1.7-5.3L3 10.5l5.3-1.7z" fill="currentColor" stroke="none"/><path d="M18.5 2.5l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z" fill="currentColor" stroke="none"/>',
  lamp: '<path d="M8.5 3h7l3 7.5h-13z"/><path d="M12 10.5V19M8 21h8M12 14.5h2.6"/>',
  chat: '<path d="M21 11.5a8.5 8 0 0 1-12.4 7.1L3.5 20l1.3-4.3A8.5 8 0 1 1 21 11.5z"/><circle cx="8.3" cy="11.5" r=".9" fill="currentColor"/><circle cx="12.3" cy="11.5" r=".9" fill="currentColor"/><circle cx="16.3" cy="11.5" r=".9" fill="currentColor"/>',
  bell: '<path d="M6.5 9.5a5.5 5.5 0 0 1 11 0c0 5.6 2.5 7.5 2.5 7.5H4s2.5-1.9 2.5-7.5z"/><path d="M10.2 20.5a2 2 0 0 0 3.6 0"/><path d="M2.4 8l1.9.7M21.6 8l-1.9.7M4.4 3.6l1.3 1.6M19.6 3.6l-1.3 1.6M12 1.4v1.5"/>',
  home: '<path d="M3 11.5L12 4l9 7.5"/><path d="M5.5 9.6V20h4.6v-5.6h3.8V20h4.6V9.6"/>',
  back: '<path d="M10 5l-6 7 6 7"/><path d="M4.5 12H20"/>',
  bulb: '<path d="M9 18.5h6M10 21.5h4"/><path d="M12 3a6 6 0 0 0-3.6 10.8c.7.6 1.1 1.4 1.1 2.3v.4h5v-.4c0-.9.4-1.7 1.1-2.3A6 6 0 0 0 12 3z"/>',
  bulbon: '<path d="M9 18.5h6M10 21.5h4"/><path d="M12 5.6a4.7 4.7 0 0 0-2.8 8.5c.6.5.9 1.1.9 1.9v.5h3.8V16c0-.8.3-1.4.9-1.9A4.7 4.7 0 0 0 12 5.6z"/><path d="M12 1.2v1.7M4.3 4.3l1.2 1.2M19.7 4.3l-1.2 1.2M1.6 11h1.7M20.7 11h1.7"/>',
  thermo: '<path d="M14 14.6V4.6a2 2 0 0 0-4 0v10a4 4 0 1 0 4 0z"/><path d="M12 9.5v6"/><circle cx="12" cy="17.6" r="1.3" fill="currentColor"/><path d="M17 5h2.5M17 8h1.6M17 11h2.5"/>',
  fan: '<circle cx="12" cy="11" r="1.6"/><path d="M12 9.4C11 6.4 11.5 3 13.6 3c2.4 0 1.9 4-1.6 6.4zM13.4 11.8c3.1.6 5.5 3 4.4 4.8-1.2 2-4.4-.4-4.4-4.8zM10.6 11.8c-2.1 2.3-5.4 3-6.1 1-.8-2.2 3-3.5 6.1-1z"/><path d="M12 12.6V21M8.5 21h7"/>',
  sun: '<circle cx="12" cy="12" r="4.2"/><path d="M12 2.2v2.2M12 19.6v2.2M2.2 12h2.2M19.6 12h2.2M5 5l1.6 1.6M17.4 17.4L19 19M5 19l1.6-1.6M17.4 6.6L19 5"/>',
  snow: '<path d="M12 2.5v19M3.8 7.2l16.4 9.6M3.8 16.8l16.4-9.6"/><path d="M9.6 4.2L12 5.8l2.4-1.6M9.6 19.8L12 18.2l2.4 1.6"/>',
  pillow: '<path d="M3.5 6.5c3.5 1.6 13.5 1.6 17 0-1.2 3.7-1.2 7.3 0 11-3.5-1.6-13.5-1.6-17 0 1.2-3.7 1.2-7.3 0-11z"/>',
  pillows: '<path d="M2 18.5V12h20v6.5M2 15.5h20"/><path d="M3.5 6.6c2 .9 5.5.9 7.5 0-.5 1.9-.5 3.6 0 5.4-2-.9-5.5-.9-7.5 0 .5-1.8.5-3.5 0-5.4zM13 6.6c2 .9 5.5.9 7.5 0-.5 1.9-.5 3.6 0 5.4-2-.9-5.5-.9-7.5 0 .5-1.8.5-3.5 0-5.4z"/>',
  move: '<path d="M2.5 20V10M2.5 17h19v3M21.5 17v-2a2.5 2.5 0 0 0-2.5-2.5h-7.5V17"/><circle cx="6.8" cy="14.1" r="1.8"/><path d="M8 4.5h9M8 4.5l2.2-2.2M8 4.5l2.2 2.2M17 4.5l-2.2-2.2M17 4.5l-2.2 2.2"/>',
  rest: '<path d="M2.5 20V10M2.5 17h19v3M21.5 17v-2a2.5 2.5 0 0 0-2.5-2.5h-7.5V17"/><circle cx="6.8" cy="14.1" r="1.8"/><path d="M14 3h3.5L14 7h3.5M18.6 6.4h2.6L18.6 9.6h2.6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.2 2"/>',
  grid: '<rect x="4" y="4" width="6.5" height="6.5" rx="1.5"/><rect x="13.5" y="4" width="6.5" height="6.5" rx="1.5"/><rect x="4" y="13.5" width="6.5" height="6.5" rx="1.5"/><rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.5"/>',
  water: '<path d="M12 3s6 6.4 6 11a6 6 0 0 1-12 0c0-4.6 6-11 6-11z"/><path d="M9 14.5a3 3 0 0 0 3 3"/>',
  check: '<circle cx="12" cy="12" r="9.3"/><path d="M7.6 12.3l3 3 5.8-6.2"/>',
  x: '<circle cx="12" cy="12" r="9.3"/><path d="M8.9 8.9l6.2 6.2M15.1 8.9l-6.2 6.2"/>',
  dots: '<circle cx="5.5" cy="12" r="1.5" fill="currentColor"/><circle cx="12" cy="12" r="1.5" fill="currentColor"/><circle cx="18.5" cy="12" r="1.5" fill="currentColor"/>',
  nurse: '<circle cx="12" cy="8.2" r="3.8"/><path d="M8.4 6h7.2"/><path d="M12 4.1v2"/><path d="M4.5 21c.4-4 3.6-6.2 7.5-6.2s7.1 2.2 7.5 6.2"/><path d="M10.5 18h3M12 16.5v3"/>',
  target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3.8" fill="currentColor"/>',
  brows: '<path d="M3 9.5c1.8-2.2 4.8-2.6 7-1M14 8.5c2.2-1.6 5.2-1.2 7 1"/><path d="M4.5 14.5c1.6 1.4 4 1.4 5.5 0M14 14.5c1.6 1.4 4 1.4 5.5 0"/><path d="M6.5 5.2L6 3.2M12 5V2.8M17.5 5.2L18 3.2"/>',
};
const MOON = '<svg class="moon" viewBox="0 0 24 24" aria-hidden="true"><defs><linearGradient id="mg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#f2f8ff"/><stop offset="1" stop-color="#6aaeff"/></linearGradient></defs>'
  + '<mask id="bite"><rect width="24" height="24" fill="#fff"/><circle cx="15.2" cy="8.6" r="6.6" fill="#000"/></mask>'
  + '<circle cx="10.6" cy="13" r="8" fill="url(#mg)" mask="url(#bite)"/>'
  + '<path d="M18.2 2.4l.75 2.25 2.25.75-2.25.75-.75 2.25-.75-2.25-2.25-.75 2.25-.75z" fill="#eaf5ff"/>'
  + '<path d="M21.3 8.6l.4 1.15 1.15.4-1.15.4-.4 1.15-.4-1.15-1.15-.4 1.15-.4z" fill="#eaf5ff"/></svg>';
const icon = (name) => `<svg class="i" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ICONS.spark}</svg>`;

const RULES = [
  [/return|main menu|^home/, "home"], [/^back$/, "back"],
  [/lamp on/, "bulbon"], [/lamp off|light/, "bulb"], [/temperat/, "thermo"], [/cooler|fan/, "fan"], [/warmer/, "sun"],
  [/cold/, "snow"], [/warm|hot/, "thermo"], [/pillow/, "pillows"], [/position|\bturn\b|roll/, "move"],
  [/comfort|rest|sleep|tired/, "rest"], [/quiet|hour|later|wait/, "clock"], [/more/, "grid"], [/water|drink|thirst/, "water"],
  [/^no\b|^no,|not now|^nope/, "x"], [/^yes\b|^ok|fine|better/, "check"], [/something/, "dots"],
  [/room/, "lamp"], [/talk/, "chat"], [/nurse|help|pain/, "bell"],
];
function iconFor(text) {
  const t = (text || "").toLowerCase();
  for (const [re, name] of RULES) if (re.test(t)) return name;
  return null;
}

// ---------------- building blocks ----------------
const HINT = { left: "Look left + raise eyebrows", center: "Look center + raise eyebrows", right: "Look right + raise eyebrows" };
const esc = (t) => String(t ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const hintOf = (o, spot) => o.hint || HINT[spot];
const small = (o, spot) => `<small data-hint="${esc(hintOf(o, spot))}">${esc(hintOf(o, spot))}</small>`;

function badge(s) {
  if (!s.badge) return "";
  const room = /room/i.test(s.badge) ? icon("bell") : "";
  return `<div class="badge-row">${icon("spark")}<span class="badge">${room}${esc(s.badge)}</span></div>`;
}
function card(s, spot, layout, iconName) {
  const o = s[spot];
  if (!o) return "";
  return `<div class="opt" data-spot="${spot}"><span class="ic">${icon(iconName || iconFor(o.label) || "spark")}</span>`
    + `<span><b>${esc(o.label)}</b>${small(o, spot)}</span><i class="bar"></i></div>`;
}
function cards(s, layout, leftIcon) {
  const one = !(s.left && s.right);
  return `<div class="opts ${layout} ${one ? "one" : ""}">${card(s, "left", layout, leftIcon)}${card(s, "right", layout)}</div>`;
}
function centerCard(s) {
  const o = s.center;
  if (!o) return "";
  return `<div class="center" data-spot="center"><span class="ic">${icon(iconFor(o.label) || "home")}</span>`
    + `<span><b>${esc(o.label)}</b>${small(o, "center")}</span><i class="bar"></i></div>`;
}
function dots(s) {
  if (!(s.pages > 1)) return "";
  return `<div class="dots">${Array.from({ length: s.pages }, (_, i) => `<i class="${i === s.page ? "on" : ""}"></i>`).join("")}</div>`;
}

// ---------------- screens ----------------
function body(s) {
  switch (s.kind) {
    case "idle":
      return `<div class="idle">${MOON}<h1>${esc(s.title)}</h1><p class="subtitle">${esc(s.subtitle)}</p>
        ${s.center ? `<div class="talk" data-spot="center"><b>${icon("chat")}${esc(s.center.label)}</b>${small(s.center, "center")}<i class="bar"></i></div>` : ""}</div>`;
    case "suggestion":
      return `<div class="panel suggestion">${badge(s)}<h1>${esc(s.title)}</h1>
        ${s.center ? `<div class="engage" data-spot="center">${icon("target")}<span>${esc(s.center.label)}</span><i class="bar"></i></div>` : ""}</div>`;
    case "nurse": {
      const foot = s.footer || "Look left or right and raise your eyebrows to choose.";
      return `<div class="panel"><h1>${esc(s.title)}</h1>${cards(s, "row")}
        <div class="instr" ${s.center ? 'data-spot="center"' : ""}><span class="ic">${icon("brows")}</span><span>${esc(foot)}</span><i class="bar"></i></div>
        ${dots(s)}</div>`;
    }
    case "checkin": {
      const pic = s.icon || iconFor(s.title);
      return `<div class="panel">${badge(s)}<h1>${esc(s.title)}</h1>
        ${pic && s.pages <= 1 ? `<div class="illus">${icon(pic)}</div>` : ""}
        ${cards(s, "row", pic && /^yes/i.test(s.left?.label || "") ? pic : null)}${centerCard(s)}${dots(s)}</div>`;
    }
    default:   // assistant, room, lights, temperature
      return `<div class="panel">${badge(s)}<h1>${esc(s.title)}</h1>${cards(s, "stack")}${centerCard(s)}${dots(s)}</div>`;
  }
}

function head(s) {
  const brand = (cls) => `<div class="brand ${cls}">${icon("eye")}LookAt</div>`;
  if (s.kind === "nurse")
    return `${brand("start")}<div class="nursepill">${icon("nurse")}${esc(s.badge || "From your nurse")}</div><div class="conn" id="conn">Connected</div>`;
  const corner = (s.kind === "idle" || s.kind === "suggestion") && s.left
    ? `<div class="corner" data-spot="left"><span class="ic">${icon("lamp")}</span><span><b>${esc(s.left.label)}</b>${small(s.left, "left")}</span><i class="bar"></i></div>`
    : "<div></div>";
  return `${corner}<div></div>${brand("")}`;
}

// ---------------- render loop ----------------
const $ = (id) => document.getElementById(id);
let lastKey = "", prevAsking = null, holdUntil = 0, fails = 0;

$("bar").innerHTML = `<span class="ic">${icon("bell")}</span><b>Call Nurse</b><span class="tx">Long eyebrow raise anywhere</span>`;
$("callnurse").innerHTML = `<span class="ic">${icon("bell")}</span><h1>Your nurse has been called</h1><p>Help is on the way.</p>`;

function render(s) {
  const key = JSON.stringify(s);
  if (key === lastKey) return;
  lastKey = key;
  $("head").innerHTML = head(s);
  $("stage").innerHTML = `<div class="enter" style="width:100%;display:flex;justify-content:center">${body(s)}</div>`;
  $("bar").classList.toggle("gone", s.kind === "nurse");
}

function feedback(m) {
  const asking = m.state === "ASKING" ? m.asking : null;
  document.querySelectorAll("[data-spot]").forEach((el) => {
    const spot = el.dataset.spot;
    el.classList.toggle("dwell", m.dwell === spot && asking !== spot);
    el.classList.toggle("asking", asking === spot);
    const sm = el.querySelector("small[data-hint]");
    if (sm) sm.textContent = asking === spot ? "Raise your eyebrows" : sm.dataset.hint;
  });
}

function confirmFlash(spot) {
  document.querySelectorAll(`[data-spot="${spot}"]`).forEach((el) => {
    el.classList.remove("asking", "dwell"); void el.offsetWidth; el.classList.add("confirmed");
  });
}

function update(data) {
  const m = data.machine || {};
  const asking = m.state === "ASKING" ? m.asking : null;
  if (prevAsking && !asking && m.state === "COOLDOWN") {     // the eyebrow raise was accepted
    confirmFlash(prevAsking);
    holdUntil = performance.now() + 380;                      // let the flash show before the next screen
  }
  prevAsking = asking;
  if (performance.now() >= holdUntil) { render(data.screen); feedback(m); }
  $("callnurse").classList.toggle("show", !!data.call_nurse);
  const conn = $("conn");
  if (conn) { conn.classList.toggle("off", fails > 2); conn.textContent = fails > 2 ? "Reconnecting…" : "Connected"; }
}

async function poll() {
  const t0 = performance.now();
  try {
    const r = await fetch("/api/screen", { cache: "no-store" });
    update(await r.json());
    fails = 0;
  } catch (e) {
    fails++;
    const conn = $("conn");
    if (conn && fails > 2) { conn.classList.add("off"); conn.textContent = "Reconnecting…"; }
  }
  setTimeout(poll, Math.max(0, 150 - (performance.now() - t0)));
}
poll();
</script>
</body>
</html>
"""


# ----- self-test -------------------------------------------------------------
def _lan_ip() -> str:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))     # no packet is sent; just picks the LAN interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


if __name__ == "__main__":
    import uvicorn
    from fastapi import FastAPI

    from lookat import config

    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="cycle through every screen every 3 s")
    ap.add_argument("--frame", type=int, help="freeze on one demo frame (0-based)")
    args = ap.parse_args()

    frames = demo_frames()
    problems = check_frames(frames)
    for p in problems:
        print("  ", p)
    print("PASS" if not problems and "<html" in PAGE else "FAIL", f"({len(frames)} demo frames)")

    if args.demo or args.frame is not None:
        if args.frame is not None:
            set_source(lambda: frames[args.frame % len(frames)])
        else:
            set_source(lambda: frames[int(time.monotonic() / 3) % len(frames)])
    app = FastAPI()
    app.include_router(router)
    print(f"Patient screen: http://{_lan_ip()}:{config.PAGE_PORT}/patient  (press F11 for full screen)")
    uvicorn.run(app, host="0.0.0.0", port=config.PAGE_PORT, log_level="warning")
