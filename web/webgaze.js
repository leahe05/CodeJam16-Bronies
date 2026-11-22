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
 *   4. Eyebrows, from the same face points (no Python camera): brow-to-eyelid distance / face height.
 *      Calibration right after the dot: "Relax" (rest), then "Raise your eyebrows" 3 times. A raise held
 *      >= CONFIRM_S and released = "confirm" (choose); held >= LONG_S = "emergency" (Call Nurse).
 *      Eyes open = eyelid gap / eye width (for the agent's "awake").
 *   Keys (operator): R = redo gaze, B = redo eyebrows, D = show/hide the gaze ring.
 * WebGazer runs fully offline: its face model is in /web/vendor/webgazer/mediapipe/face_mesh.
 * WebGazer.js is GPLv3 (LGPLv3 for companies valued under $1M): web/vendor/webgazer/LICENSE.md.
 */
(function () {
  "use strict";
  const params = new URLSearchParams(location.search);
  if (params.get("gaze") !== "web") return;

  const SMOOTH = 6;             // predictions in the median (~0.2 s at 30 fps; more = steadier but laggier)
  const DEAD = 0.04;            // dead band around a zone border, share of the screen width
  const SEND_MS = 50;           // POST the zone this often
  const LOST_MS = 600;          // no prediction this long = no gaze (null)
  // 9-point grid, then the far left/right edges and the middle row again: left/right is all that matters.
  const POINTS = [[.1, .12], [.5, .12], [.9, .12], [.1, .5], [.5, .5], [.9, .5], [.1, .88], [.5, .88], [.9, .88],
                  [.03, .5], [.97, .5], [.25, .5], [.75, .5], [.9, .3], [.1, .3], [.9, .7], [.1, .7]];
  const SETTLE_MS = 900, RECORD_MS = 1500, SAMPLE_MS = 50;
  // Check after the dot: look at the left / centre / right column; where the tracker THINKS you look there
  // sets the zone borders (half-way between those), so a tracker that reads everything a bit to the left
  // still gets "right" right. If the three come out of order, plain thirds (R redoes it).
  const CHECK = [["left", 1 / 6], ["middle", .5], ["right", 5 / 6]];
  const CHECK_SETTLE_MS = 1200, CHECK_MS = 2200, MIN_SPREAD = 0.02;   // in order, a little apart
  const CONFIRM_S = 0.3, LONG_S = 2.0;      // config.CONFIRM_S / config.EMERGENCY_S
  const EYES_OPEN = 0.16;                  // eyelid gap / eye width above this = open

  let borders = [1 / 3, 2 / 3];   // zone borders, share of the screen width (set by the check)
  let probe = null;               // predictions collected during the check
  let dotX = null, dotY = null;   // the ring's position (moved every animation frame, not every POST)
  let xs = [], ys = [], lastPred = 0, zone = null, running = false, calibrating = false;
  let showDot = params.get("dot") !== "0";
  // eyebrows: value = brow-to-eyelid distance / face height (smoothed); thr/rel from calibration
  const brow = { value: 0, thr: null, rel: null, start: null, since: null, longSent: false, events: [],
                  face: false, eyesOpen: null, frozenUntil: 0 };
  // Raising the eyebrows changes how the eyes look, so WebGazer's estimate jumps. From the moment the
  // brows START to rise until FREEZE_AFTER_MS after they come down, the gaze is frozen on the option the
  // patient was already looking at, and those predictions are thrown away.
  const FREEZE_AFTER_MS = 700;
  const frozen = () => brow.start != null && (brow.value >= brow.start || performance.now() < brow.frozenUntil);

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
    #wg-dot { position: fixed; left: 0; top: 0; width: 9vh; height: 9vh; margin: -4.5vh 0 0 -4.5vh; border-radius: 50%; z-index: 9;
      pointer-events: none; border: .6vh solid #22d3ee; background: rgba(34, 211, 238, .22); will-change: transform;
      box-shadow: 0 0 0 .4vh rgba(0, 10, 40, .45), 0 0 3.5vh rgba(34, 211, 238, .9), inset 0 0 2vh rgba(34, 211, 238, .5);
      transition: opacity .3s; }
    #wg-dot::after { content: ""; position: absolute; left: 50%; top: 50%; width: 1.6vh; height: 1.6vh; margin: -.8vh 0 0 -.8vh;
      border-radius: 50%; background: #fff; box-shadow: 0 0 0 .3vh #0891b2; }
    .wg-col { position: fixed; top: 12vh; bottom: 12vh; width: 28vw; margin-left: -14vw; border-radius: 3vh; z-index: 21;
      border: 2px dashed var(--card-border, rgba(165,243,252,.35)); display: flex; align-items: center; justify-content: center;
      font-size: 4vh; font-weight: 600; color: var(--hint, #a9c6ff); opacity: .6; transition: all .3s; }
    .wg-col.on { opacity: 1; border: 3px solid var(--ask-border, #a5f3fc); background: var(--ask-bg, rgba(103,232,249,.16));
      color: var(--text, #fff); box-shadow: var(--ask-glow, 0 0 5vh rgba(34,211,238,.5)); }
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
      await calibrateGaze();
      await calibrateBrows();
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
      if (data && probe) { probe.push(data.x / innerWidth); return; }
      if (!data || calibrating || frozen()) return;    // eyebrows up: ignore the jumpy predictions
      xs.push(data.x); ys.push(data.y);
      dotX = dotX == null ? data.x : dotX + 0.55 * (data.x - dotX);   // the ring follows each prediction
      dotY = dotY == null ? data.y : dotY + 0.55 * (data.y - dotY);
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
    setInterval(browTick, 33);
    setInterval(send, SEND_MS);
    const ring = el("wg-dot");
    (function frame() {
      if (dotX != null) ring.style.transform = `translate(${dotX}px, ${dotY}px)`;
      requestAnimationFrame(frame);
    })();
  }

  // ---------------- face points -> eyebrows + eyes open ----------------
  // MediaPipe face mesh indices: brows 105 (left) / 334 (right), upper eyelids 159 / 386, lower 145 / 374,
  // eye corners 33-133 / 362-263, forehead 10 and chin 152 (face height).
  function facePoints() {
    try {
      const p = window.webgazer.getTracker().getPositions();
      return p && p.length > 400 ? p : null;
    } catch (e) { return null; }
  }
  const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
  function measure() {
    const p = facePoints();
    if (!p) return null;
    const face = dist(p[10], p[152]) || 1;
    const browGap = ((p[159][1] - p[105][1]) + (p[386][1] - p[334][1])) / 2 / face;
    const ear = (dist(p[159], p[145]) / dist(p[33], p[133]) + dist(p[386], p[374]) / dist(p[362], p[263])) / 2;
    return { brow: browGap, eyesOpen: ear > EYES_OPEN };
  }
  function browTick() {                   // ~30x a second
    const m = measure();
    brow.face = !!m;
    if (!m) { brow.since = null; brow.longSent = false; return; }
    brow.eyesOpen = m.eyesOpen;
    brow.value = brow.value ? 0.6 * brow.value + 0.4 * m.brow : m.brow;
    if (brow.thr == null || calibrating) return;
    const now = performance.now();
    if (brow.value >= brow.start) brow.frozenUntil = now + FREEZE_AFTER_MS;
    if (brow.since == null) {
      if (brow.value >= brow.thr) { brow.since = now; brow.longSent = false; }
    } else if (brow.value < brow.rel) {   // released
      const held = (now - brow.since) / 1000;
      if (held >= CONFIRM_S && !brow.longSent) brow.events.push("confirm");
      brow.since = null;
    } else if (!brow.longSent && (now - brow.since) / 1000 >= LONG_S) {
      brow.events.push("emergency");      // Call Nurse at once, and the release won't also choose
      brow.longSent = true;
    }
  }
  const held = () => (brow.since == null ? 0 : (performance.now() - brow.since) / 1000);

  async function calibrateBrows() {
    calibrating = true;
    const cal = el("wg-cal", `<div class="msg"><b>Relax your face</b><p>Look at the screen.</p></div>`);
    const msg = (t, sub) => { cal.innerHTML = `<div class="msg"><b>${t}</b><p>${sub || ""}</p></div>`; };
    async function record(ms, pick) {
      const vals = [], end = performance.now() + ms;
      while (performance.now() < end) { const m = measure(); if (m) vals.push(m.brow); await wait(40); }
      return vals.length ? pick(vals) : null;
    }
    // ONE round of 3 raises, never repeated. Any raise the camera can tell apart from rest counts
    // (was > 0.012 + a second round, which a weaker webcam failed again and again); B redoes it.
    for (let attempt = 0; attempt < 1; attempt++) {
      msg("Relax your face", "Look at the screen.");
      say("Relax your face.");
      await wait(1500);
      const rest = await record(2000, median);
      const peaks = [];
      for (let i = 0; i < 3; i++) {
        msg("Raise your eyebrows", `${i + 1} of 3`);
        say("Raise your eyebrows.");
        await wait(500);
        peaks.push(await record(1500, (v) => Math.max(...v)));
        msg("Relax", "");
        say("Relax.");
        await wait(1300);
      }
      const peak = median(peaks.filter((v) => v != null));
      console.log("WG brows measured", { rest, peak });
      if (rest != null && peak != null && peak - rest > 0.004) {
        brow.thr = rest + 0.5 * (peak - rest);
        brow.rel = brow.thr - 0.3 * (brow.thr - rest);
        brow.start = rest + 0.2 * (peak - rest);           // starting to rise: freeze the gaze from here
        console.log("WG brows", { rest, peak, thr: brow.thr });
        break;
      }
    }
    if (brow.thr == null) msg("Eyebrows not detected", "Move closer to the camera, light your face, then press B.");
    else cal.remove();
    say(brow.thr == null ? "Eyebrows not detected." : "All set.");
    brow.since = null; brow.events = [];
    calibrating = false;
    if (brow.thr == null) { await wait(4000); cal.remove(); }
  }

  // ---------------- calibration: follow the dot ----------------
  async function calibrate() {
    const wg = window.webgazer;
    calibrating = true;
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
    xs = []; ys = [];
    calibrating = false;
  }

  // ---------------- check: where does the tracker put each column? ----------------
  async function checkZones() {
    calibrating = true;
    const cal = el("wg-cal", `<div class="msg"><b>Now look at each box</b><p>Keep looking until it moves on.</p></div>`);
    say("Now look at each box until it moves on.");
    await wait(2000);
    cal.innerHTML = CHECK.map(([z, cx]) => `<div class="wg-col" data-z="${z}" style="left:${cx * 100}vw">`
      + `${{ left: "Left", middle: "Centre", right: "Right" }[z]}</div>`).join("");
    const got = {};
    for (const [z] of CHECK) {
      const box = cal.querySelector(`[data-z="${z}"]`);
      box.classList.add("on");
      say({ left: "Look left.", middle: "Look at the centre.", right: "Look right." }[z]);
      await wait(CHECK_SETTLE_MS);
      probe = [];
      await wait(CHECK_MS);
      got[z] = probe.length >= 5 ? median(probe) : null;
      probe = null;
      box.classList.remove("on");
    }
    calibrating = false;
    const { left: l, middle: m, right: r } = got;
    console.log("WG check", got);
    if (l == null || m == null || r == null || !(m - l > MIN_SPREAD && r - m > MIN_SPREAD)) return false;
    borders = [(l + m) / 2, (m + r) / 2];
    return true;
  }

  // ONE dot, then the check, never repeated (a repeat made weaker webcams do the 45 s dot two or three
  // times). Check out of order: plain thirds. The operator can still press R to redo it.
  async function calibrateGaze() {
    window.webgazer.clearData();
    await calibrate();
    if (!(await checkZones())) borders = [1 / 3, 2 / 3];
    const c = document.getElementById("wg-cal");
    if (c) c.remove();
  }

  // ---------------- live: zone -> backend ----------------
  const median = (a) => { const s = [...a].sort((p, q) => p - q); return s[s.length >> 1]; };
  function zoneOf(x) {
    const w = innerWidth, a = borders[0] * w, b = borders[1] * w, d = DEAD * w;
    if (zone === "left" && x < a + d) return "left";        // stay unless clearly past the border
    if (zone === "right" && x > b - d) return "right";
    if (zone === "middle" && x > a - d && x < b + d) return "middle";
    return x < a ? "left" : x > b ? "right" : "middle";
  }
  // The option the patient sees in that third of the screen on the current layer (the big layout puts
  // e.g. Talk to AI, which is the backend's "middle" spot, in the right column on the home screen).
  function spotFor(z) {
    if (!z) return null;
    const layer = { idle: "#home", menu: "#menu", window: "#win" }[document.body.dataset.mode] || "#home";
    const col = document.querySelector(`${layer} [data-zone="${z}"]`);
    const opt = col && col.querySelector("[data-spot]");
    return opt ? opt.dataset.spot : (col ? null : z);
  }
  function send() {
    if (calibrating || !running) return;
    const hold = frozen() && zone;                    // eyebrows up: keep the option they were looking at
    const fresh = hold || (performance.now() - lastPred < LOST_MS && xs.length >= 3);
    let x = null, y = null;
    if (hold) {
      x = median(xs); y = median(ys);
    } else if (fresh) {
      x = median(xs); y = median(ys);
      zone = zoneOf(x);
    } else {
      zone = null;
    }
    const dot = el("wg-dot"), band = el("wg-zone");
    dot.style.opacity = showDot && fresh ? "1" : "0";
    band.style.opacity = showDot && zone ? "1" : "0";
    band.style.left = zone === "left" ? "0" : zone === "middle" ? "33.33vw" : "66.66vw";
    fetch("/api/gaze", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ spot: spotFor(zone), x: x && Math.round(x / innerWidth * 1000) / 1000,
                             y: y && Math.round(y / innerHeight * 1000) / 1000, face: brow.face,
                             eyes_open: brow.eyesOpen, brow: { up: brow.since != null, held_s: held() },
                             events: brow.events.splice(0) }) }).catch(() => {});
  }

  document.addEventListener("keydown", (e) => {
    const k = e.key.toLowerCase();
    if (k === "r" && running && !calibrating) calibrateGaze();
    if (k === "b" && running && !calibrating) calibrateBrows();
    if (k === "d") showDot = !showDot;
  });

  const s = document.createElement("script");
  s.src = "/web/vendor/webgazer/webgazer.js";
  s.onload = params.get("autostart") === "1" ? run : startScreen;   // autostart: testing only (no click)
  s.onerror = () => el("wg-start", "<h1>WebGazer did not load</h1><p>web/vendor/webgazer/webgazer.js is missing.</p>");
  document.head.appendChild(s);
})();
