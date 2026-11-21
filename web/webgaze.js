/* Gaze from the webcam IN THE BROWSER (WebGazer), for /patient?gaze=web.
 *
 *   1. One click (the operator) starts it: browsers only open the camera and speak after a click.
 *   2. Calibration without clicks (the patient can't click): a glowing dot visits 9 points; the voice
 *      says "Follow the dot"; while the patient looks at it we feed WebGazer that position
 *      (webgazer.recordScreenPosition). The mouse is never used for training.
 *   3. Live: WebGazer's x,y is smoothed (median of the last SMOOTH predictions) and turned into a ZONE:
 *      left third / centre third / right third of the screen, with a dead band so jitter at a border
 *      doesn't flip it. The zone is POSTed to /api/gaze ~10x a second; the backend's selection engine
 *      (dwell, eyebrow confirm, long raise = Call Nurse) does the rest, exactly as with the Python gaze.
 *   Keys (operator): R = recalibrate, D = show/hide the gaze dot.
 * WebGazer runs fully offline: its face model is in /web/vendor/webgazer/mediapipe/face_mesh.
 * WebGazer.js is GPLv3 (LGPLv3 for companies valued under $1M): web/vendor/webgazer/LICENSE.md.
 */
(function () {
  "use strict";
  const params = new URLSearchParams(location.search);
  if (params.get("gaze") !== "web") return;

  const SMOOTH = 12;            // predictions in the median (~0.4 s at 30 fps)
  const DEAD = 0.04;            // dead band around a zone border, share of the screen width
  const SEND_MS = 100;          // POST the zone this often
  const LOST_MS = 600;          // no prediction this long = no gaze (null)
  const POINTS = [[.1, .12], [.5, .12], [.9, .12], [.1, .5], [.5, .5], [.9, .5], [.1, .88], [.5, .88], [.9, .88]];
  const SETTLE_MS = 900, RECORD_MS = 1300, SAMPLE_MS = 50;

  let xs = [], ys = [], lastPred = 0, zone = null, running = false, calibrating = false;
  let showDot = params.get("dot") !== "0";

  // ---------------- UI ----------------
  document.documentElement.classList.add("gaze-web");
  const css = document.createElement("style");
  css.textContent = `
    #wg-start, #wg-cal { position: fixed; inset: 0; z-index: 20; display: flex; flex-direction: column; align-items: center;
      justify-content: center; gap: 2vh; text-align: center; color: var(--text, #fff); background: var(--bg, #050a22); cursor: pointer; }
    #wg-start h1 { font-size: 6vh; margin: 0; } #wg-start p, #wg-cal p { font-size: 2.6vh; opacity: .8; margin: 0; }
    #wg-cal { cursor: none; background: var(--bg, #050a22); }
    #wg-cal .msg { position: absolute; top: 44%; left: 0; right: 0; }
    #wg-cal .msg b { display: block; font-size: 4.5vh; margin-bottom: 1vh; }
    #wg-target { position: fixed; width: 5vh; height: 5vh; margin: -2.5vh 0 0 -2.5vh; border-radius: 50%; z-index: 21;
      background: radial-gradient(circle, #fff 0 22%, #67e8f9 38%, rgba(103,232,249,.25) 62%, transparent 70%);
      box-shadow: 0 0 4vh #22d3ee; transition: left .45s cubic-bezier(.2,.8,.2,1), top .45s cubic-bezier(.2,.8,.2,1); }
    #wg-target.rec { animation: wg-pulse .6s ease-in-out infinite alternate; }
    @keyframes wg-pulse { to { transform: scale(.7); } }
    #wg-dot { position: fixed; width: 7vh; height: 7vh; margin: -3.5vh 0 0 -3.5vh; border-radius: 50%; z-index: 9;
      pointer-events: none; border: 2px solid rgba(165, 243, 252, .55); background: rgba(103, 232, 249, .10);
      transition: left .12s linear, top .12s linear, opacity .3s; }
    #wg-zone { position: fixed; top: 0; bottom: 0; width: 33.34vw; z-index: 1; pointer-events: none;
      background: linear-gradient(180deg, transparent, rgba(103,232,249,.06) 40%, transparent); transition: left .25s ease, opacity .3s; }
    #webgazerVideoContainer { display: none !important; }`;
  document.head.appendChild(css);

  function el(id, html) {
    let e = document.getElementById(id);
    if (!e) { e = document.createElement("div"); e.id = id; document.body.appendChild(e); }
    if (html !== undefined) e.innerHTML = html;
    return e;
  }
  function say(text) {
    try {
      speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(text);
      u.rate = 1.05;
      speechSynthesis.speak(u);
    } catch (e) { /* no voice: the text is on screen */ }
  }
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));

  // ---------------- start (one click) ----------------
  function startScreen() {
    const s = el("wg-start", `<h1>Eye tracking on this screen</h1><p>Click once to start the camera, then follow the dot with your eyes.</p>`);
    s.onclick = () => { s.remove(); run(); };
  }

  async function run() {
    const st = el("wg-cal", `<div class="msg"><b>Starting the camera…</b><p>Loading eye tracking (a few seconds).</p></div>`);
    try {
      await begin();
      await calibrate();
    } catch (e) {
      console.error("WG", e);
      st.innerHTML = `<div class="msg"><b>Eye tracking could not start</b><p>${String(e && e.message || e)}</p>`
        + `<p>Allow the camera for this page. If another app is using it, start main.py with the other camera (--camera 0 / 1). Click to retry.</p></div>`;
      st.style.cursor = "pointer";
      st.onclick = () => location.reload();
    }
  }

  async function begin() {
    if (running) return;
    console.log("WG begin");
    const wg = window.webgazer;
    wg.params.faceMeshSolutionPath = "/web/vendor/webgazer/mediapipe/face_mesh";   // offline
    wg.params.showVideoPreview = false;
    wg.saveDataAcrossSessions(false);                // a fresh calibration every start
    wg.setRegression("ridge").applyKalmanFilter(true);
    wg.setGazeListener((data) => {
      if (!data || calibrating) return;
      xs.push(data.x); ys.push(data.y);
      if (xs.length > SMOOTH) { xs.shift(); ys.shift(); }
      lastPred = performance.now();
    });
    const started = wg.begin();
    const timeout = new Promise((_, no) => setTimeout(() => no(new Error("the camera or the face model did not start within 25 s")), 25000));
    await Promise.race([started, timeout]);
    console.log("WG started");
    wg.showVideoPreview(false).showPredictionPoints(false).showFaceOverlay(false).showFaceFeedbackBox(false);
    wg.removeMouseEventListeners();                  // never learn from the mouse: only from our dots
    running = true;
    setInterval(send, SEND_MS);
  }

  // ---------------- calibration: follow the dot ----------------
  async function calibrate() {
    const wg = window.webgazer;
    calibrating = true;
    wg.clearData();
    const cal = el("wg-cal", `<div class="msg"><b>Follow the dot with your eyes</b><p>Keep your head still.</p></div>`);
    const dot = el("wg-target");
    say("Follow the dot with your eyes. Keep your head still.");
    await wait(2200);
    cal.querySelector(".msg").style.opacity = ".25";
    for (const [px, py] of POINTS) {
      const x = px * innerWidth, y = py * innerHeight;
      dot.style.left = x + "px"; dot.style.top = y + "px";
      dot.classList.remove("rec");
      await wait(SETTLE_MS);                         // the eyes travel to the dot first
      dot.classList.add("rec");
      const end = performance.now() + RECORD_MS;
      while (performance.now() < end) {
        wg.recordScreenPosition(x, y, "click");
        await wait(SAMPLE_MS);
      }
    }
    dot.remove();
    cal.remove();
    say("Thank you.");
    xs = []; ys = [];
    calibrating = false;
  }

  // ---------------- live: zone -> backend ----------------
  const median = (a) => { const s = [...a].sort((p, q) => p - q); return s[s.length >> 1]; };
  function zoneOf(x) {
    const w = innerWidth, a = w / 3, b = 2 * w / 3, d = DEAD * w;
    if (zone === "left" && x < a + d) return "left";        // stay unless clearly past the border
    if (zone === "right" && x > b - d) return "right";
    if (zone === "middle" && x > a - d && x < b + d) return "middle";
    return x < a ? "left" : x > b ? "right" : "middle";
  }
  function send() {
    if (calibrating || !running) return;
    const fresh = performance.now() - lastPred < LOST_MS && xs.length >= 3;
    let x = null, y = null;
    if (fresh) {
      x = median(xs); y = median(ys);
      zone = zoneOf(x);
    } else {
      zone = null;
    }
    const dot = el("wg-dot"), band = el("wg-zone");
    dot.style.opacity = showDot && fresh ? "1" : "0";
    if (fresh) { dot.style.left = x + "px"; dot.style.top = y + "px"; }
    band.style.opacity = showDot && zone ? "1" : "0";
    band.style.left = zone === "left" ? "0" : zone === "middle" ? "33.33vw" : "66.66vw";
    fetch("/api/gaze", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ spot: zone, x: x && Math.round(x / innerWidth * 1000) / 1000,
                             y: y && Math.round(y / innerHeight * 1000) / 1000 }) }).catch(() => {});
  }

  document.addEventListener("keydown", (e) => {
    const k = e.key.toLowerCase();
    if (k === "r" && running && !calibrating) calibrate();
    if (k === "d") showDot = !showDot;
  });

  const s = document.createElement("script");
  s.src = "/web/vendor/webgazer/webgazer.js";
  s.onload = params.get("autostart") === "1" ? run : startScreen;   // autostart: testing only (no click)
  s.onerror = () => el("wg-start", "<h1>WebGazer did not load</h1><p>web/vendor/webgazer/webgazer.js is missing.</p>");
  document.head.appendChild(s);
})();
