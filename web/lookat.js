/* Shared code for every patient-screen design (web/patient.html, web/designs/*.html).
 *
 * A design only provides CSS and a render(data) function that fills the page.
 * This file does the rest the same way for every design:
 *   - polls GET /api/screen every 150 ms (docs/API.md, section 1)
 *   - calls render(data) only when the screen content changes
 *   - gaze focus: every element with data-spot="left|middle|right" gets the class
 *     dwell / asking / chosen; its <small data-hint> turns into "Raise your eyebrows" while asking,
 *     and it gets a .countdown (a light that drains around its border in ASK_S seconds, or a draining
 *     .tank of liquid; each design's CSS picks which one shows)
 *   - #overlay (call nurse), #facehint ("Move into view of the camera"), #demotag (DEMO TIME)
 *   - #browtag: "Eyebrows up" while the eyebrows are raised, with a bar filling to the long raise
 *     (= Call Nurse); the asking option's hint turns into "Raised - lower to choose"
 *   - opts.view(data) can turn the backend data into what the design draws (used for calibration)
 *   - voice: reads the backend's spoken lines (d.voice) aloud with the browser's best natural voice
 *     (Edge: "Ava/Emma/Jenny Online (Natural)"); #voicetag asks for one click first, because browsers
 *     only allow speech after the page was clicked
 *   - body[data-conn="lost"] after 3 failed polls
 * Never crashes on null or unknown fields.
 */
(function () {
  "use strict";

  // ---------------- icons: the keywords from docs/API.md + a few extras ----------------
  const ICONS = {
    lamp: '<path d="M8.5 3h7l3 7.5h-13z"/><path d="M12 10.5V19M8 21h8M12 14.5h2.6"/>',
    bulb: '<path d="M9 18.5h6M10 21.5h4"/><path d="M12 3a6 6 0 0 0-3.6 10.8c.7.6 1.1 1.4 1.1 2.3v.4h5v-.4c0-.9.4-1.7 1.1-2.3A6 6 0 0 0 12 3z"/>',
    thermometer: '<path d="M14 14.6V4.6a2 2 0 0 0-4 0v10a4 4 0 1 0 4 0z"/><path d="M12 9.5v6"/><circle cx="12" cy="17.6" r="1.3" fill="currentColor"/><path d="M17 5h2.5M17 8h1.6M17 11h2.5"/>',
    fan: '<circle cx="12" cy="11" r="1.6"/><path d="M12 9.4C11 6.4 11.5 3 13.6 3c2.4 0 1.9 4-1.6 6.4zM13.4 11.8c3.1.6 5.5 3 4.4 4.8-1.2 2-4.4-.4-4.4-4.8zM10.6 11.8c-2.1 2.3-5.4 3-6.1 1-.8-2.2 3-3.5 6.1-1z"/><path d="M12 12.6V21M8.5 21h7"/>',
    moon: '<path d="M20 14.2A8.3 8.3 0 1 1 9.8 4a6.6 6.6 0 0 0 10.2 10.2z"/>',
    chat: '<path d="M21 11.5a8.5 8 0 0 1-12.4 7.1L3.5 20l1.3-4.3A8.5 8 0 1 1 21 11.5z"/><circle cx="8.3" cy="11.5" r=".9" fill="currentColor"/><circle cx="12.3" cy="11.5" r=".9" fill="currentColor"/><circle cx="16.3" cy="11.5" r=".9" fill="currentColor"/>',
    sparkle: '<path d="M10 3.5l1.7 5.3 5.3 1.7-5.3 1.7L10 17.5l-1.7-5.3L3 10.5l5.3-1.7z" fill="currentColor" stroke="none"/><path d="M18.5 2.5l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z" fill="currentColor" stroke="none"/>',
    target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3.8" fill="currentColor"/>',
    home: '<path d="M3 11.5L12 4l9 7.5"/><path d="M5.5 9.6V20h4.6v-5.6h3.8V20h4.6V9.6"/>',
    back: '<path d="M10 5l-6 7 6 7"/><path d="M4.5 12H20"/>',
    check: '<circle cx="12" cy="12" r="9.3"/><path d="M7.6 12.3l3 3 5.8-6.2"/>',
    x: '<circle cx="12" cy="12" r="9.3"/><path d="M8.9 8.9l6.2 6.2M15.1 8.9l-6.2 6.2"/>',
    bed: '<path d="M2.5 19V7M2.5 15h19v4M21.5 15v-2.5a3 3 0 0 0-3-3H11V15"/><circle cx="6.8" cy="11.8" r="2"/>',
    pillow: '<path d="M2 18.5V12h20v6.5M2 15.5h20"/><path d="M3.5 6.6c2 .9 5.5.9 7.5 0-.5 1.9-.5 3.6 0 5.4-2-.9-5.5-.9-7.5 0 .5-1.8.5-3.5 0-5.4zM13 6.6c2 .9 5.5.9 7.5 0-.5 1.9-.5 3.6 0 5.4-2-.9-5.5-.9-7.5 0 .5-1.8.5-3.5 0-5.4z"/>',
    water: '<path d="M12 3s6 6.4 6 11a6 6 0 0 1-12 0c0-4.6 6-11 6-11z"/><path d="M9 14.5a3 3 0 0 0 3 3"/>',
    grid: '<rect x="4" y="4" width="6.5" height="6.5" rx="1.5"/><rect x="13.5" y="4" width="6.5" height="6.5" rx="1.5"/><rect x="4" y="13.5" width="6.5" height="6.5" rx="1.5"/><rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.5"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.2 2"/>',
    nurse: '<circle cx="12" cy="8.2" r="3.8"/><path d="M8.4 6h7.2M12 4.1v2"/><path d="M4.5 21c.4-4 3.6-6.2 7.5-6.2s7.1 2.2 7.5 6.2"/><path d="M10.5 18h3M12 16.5v3"/>',
    bell: '<path d="M6.5 9.5a5.5 5.5 0 0 1 11 0c0 5.6 2.5 7.5 2.5 7.5H4s2.5-1.9 2.5-7.5z"/><path d="M10.2 20.5a2 2 0 0 0 3.6 0"/><path d="M2.4 8l1.9.7M21.6 8l-1.9.7M4.4 3.6l1.3 1.6M19.6 3.6l-1.3 1.6M12 1.4v1.5"/>',
    more: '<rect x="3" y="7.5" width="18" height="9" rx="4.5"/><circle cx="8" cy="12" r="1.2" fill="currentColor"/><circle cx="12" cy="12" r="1.2" fill="currentColor"/><circle cx="16" cy="12" r="1.2" fill="currentColor"/>',
    snow: '<path d="M12 2.5v19M3.8 7.2l16.4 9.6M3.8 16.8l16.4-9.6"/><path d="M9.6 4.2L12 5.8l2.4-1.6M9.6 19.8L12 18.2l2.4 1.6"/>',
    sun: '<circle cx="12" cy="12" r="4.2"/><path d="M12 2.2v2.2M12 19.6v2.2M2.2 12h2.2M19.6 12h2.2M5 5l1.6 1.6M17.4 17.4L19 19M5 19l1.6-1.6M17.4 6.6L19 5"/>',
    quiet: '<path d="M17.5 14.5A7.5 7.5 0 1 1 9.5 4.5a6 6 0 0 0 8 10z"/><path d="M15 3h3.5L15 7h3.5"/>',
    question: '<circle cx="12" cy="12" r="9.3"/><path d="M9.3 9.4a2.8 2.8 0 0 1 5.4 1c0 1.9-2.7 2.4-2.7 4"/><circle cx="12" cy="17.4" r=".9" fill="currentColor"/>',
    // extras used by the designs
    eye: '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3.2"/><circle cx="12" cy="12" r="1.1" fill="currentColor"/>',
    brows: '<path d="M3 9.5c1.8-2.2 4.8-2.6 7-1M14 8.5c2.2-1.6 5.2-1.2 7 1"/><path d="M4.5 14.5c1.6 1.4 4 1.4 5.5 0M14 14.5c1.6 1.4 4 1.4 5.5 0"/><path d="M6.5 5.2L6 3.2M12 5V2.8M17.5 5.2L18 3.2"/>',
    position: '<path d="M2.5 20V10M2.5 17h19v3M21.5 17v-2a2.5 2.5 0 0 0-2.5-2.5h-7.5V17"/><circle cx="6.8" cy="14.1" r="1.8"/><path d="M8 4.5h9M8 4.5l2.2-2.2M8 4.5l2.2 2.2M17 4.5l-2.2-2.2M17 4.5l-2.2 2.2"/>',
    camera: '<path d="M3 8.5A1.5 1.5 0 0 1 4.5 7h2.3l1.6-2.2h7.2L17.2 7h2.3A1.5 1.5 0 0 1 21 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z"/><circle cx="12" cy="12.8" r="3.6"/>',
  };

  // When the backend sends no icon (e.g. a nurse's typed answer), pick one from the words.
  const RULES = [
    [/return|main menu|^home/, "home"], [/^back$/, "back"],
    [/lamp on/, "sun"], [/lamp off/, "moon"], [/light|lamp/, "bulb"], [/temperat/, "thermometer"],
    [/cooler|fan/, "snow"], [/warmer/, "sun"], [/cold/, "snow"], [/warm|hot/, "thermometer"],
    [/pillow/, "pillow"], [/position|\bturn\b|roll/, "position"], [/comfort|rest|sleep|tired/, "bed"],
    [/quiet/, "quiet"], [/hour|later|wait/, "clock"], [/more sugg/, "grid"], [/more/, "more"],
    [/water|drink|thirst/, "water"], [/^no\b|^no,|not now|^nope/, "x"], [/^yes\b|^ok|fine|better/, "check"],
    [/something|other/, "question"], [/room/, "lamp"], [/talk/, "chat"], [/nurse|help|pain/, "bell"],
  ];
  function iconFor(text) {
    const t = String(text || "").toLowerCase();
    for (const [re, name] of RULES) if (re.test(t)) return name;
    return null;
  }

  function icon(name, cls) {
    const p = name && ICONS[name];
    return p ? `<svg class="i ${cls || ""}" viewBox="0 0 24 24" aria-hidden="true">${p}</svg>` : "";
  }
  /** Icon name for an option: the backend's keyword if known, else a guess from the label. Unknown keyword -> none. */
  function optIcon(o) {
    if (!o) return null;
    if (o.icon) return ICONS[o.icon] ? o.icon : null;
    return iconFor(o.label);
  }

  const DEFAULT_HINT = { left: "Look left + raise eyebrows", middle: "Look center + raise eyebrows", right: "Look right + raise eyebrows" };
  const esc = (t) => String(t ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  const hint = (o, spot) => (o && o.hint) || DEFAULT_HINT[spot] || "";
  /** The hint line; turns into "Raise your eyebrows" while that spot is asking. */
  const hintTag = (o, spot) => `<small data-hint="${esc(hint(o, spot))}">${esc(hint(o, spot))}</small>`;
  // ---------------- countdown: a light that drains around the asking option's border ----------------
  const ASK_S = 4;   // default for config.ASK_TIMEOUT_S; the backend sends the real value as d.ask_s
  let askS = ASK_S;

  function roundedPath(w, h, r, i) {   // rounded rect starting at top-center, clockwise, inset by i
    const x = i, y = i, W = w - 2 * i, H = h - 2 * i;
    r = Math.max(0, Math.min(r - i, W / 2, H / 2));
    return `M${x + W / 2},${y} H${x + W - r} A${r},${r} 0 0 1 ${x + W},${y + r} V${y + H - r} A${r},${r} 0 0 1 ${x + W - r},${y + H}`
      + ` H${x + r} A${r},${r} 0 0 1 ${x},${y + H - r} V${y + r} A${r},${r} 0 0 1 ${x + r},${y} Z`;
  }
  function drawRing(el, c) {   // (re)fit the ring to the element's current size
    const cs = getComputedStyle(el);
    const bw = parseFloat(cs.borderTopWidth) || 0, r = parseFloat(cs.borderTopLeftRadius) || 0;
    const w = el.offsetWidth, h = el.offsetHeight;
    if (!w || !h) return;
    const sw = parseFloat(cs.getPropertyValue("--ring-w")) || 3;
    const d = roundedPath(w, h, r, sw / 2);
    c.style.inset = `${-bw}px`;
    const svg = c.querySelector(".ring");
    svg.setAttribute("width", w); svg.setAttribute("height", h); svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
    svg.querySelectorAll("path").forEach((p) => p.setAttribute("d", d));
  }
  function addCountdown(el) {
    if (el.querySelector(":scope > .countdown")) return;
    const c = document.createElement("span");
    c.className = "countdown";
    c.setAttribute("aria-hidden", "true");
    c.style.setProperty("--ask", askS + "s");
    c.innerHTML = '<svg class="ring"><path class="ring-track"/><path class="ring-left" pathLength="100"/></svg>'
      + '<span class="tank"><i class="liquid"><svg class="wave" viewBox="0 0 200 12" preserveAspectRatio="none"><path d="M0 6 Q25 0 50 6 T100 6 T150 6 T200 6 V12 H0 Z"/></svg></i></span>';
    el.appendChild(c);
    drawRing(el, c);
    if (window.ResizeObserver) {
      el._ringObs = new ResizeObserver(() => drawRing(el, c));
      el._ringObs.observe(el);
    }
  }
  function removeCountdown(el) {
    const c = el.querySelector(":scope > .countdown");
    if (c) c.remove();
    if (el._ringObs) { el._ringObs.disconnect(); el._ringObs = null; }
  }

  // ?noanim in the URL turns off all animation (screenshots, testing)
  if (/[?&]noanim\b/.test(location.search)) document.documentElement.classList.add("noanim");

  function dots(page) {
    if (!page || !(page.count > 1)) return "";
    let s = "";
    for (let i = 0; i < page.count; i++) s += `<i class="${i === page.index ? "on" : ""}"></i>`;
    return `<div class="dots">${s}</div>`;
  }

  // ---------------- live parts (same for every design) ----------------
  function ensure(id, html) {
    let el = document.getElementById(id);
    if (!el) { el = document.createElement("div"); el.id = id; el.innerHTML = html || ""; document.body.appendChild(el); }
    return el;
  }

  function applyFocus(f, brow) {
    const up = !!(brow && brow.up);
    document.querySelectorAll("[data-spot]").forEach((el) => {
      const state = f && f.spot === el.dataset.spot ? f.state : null;
      el.classList.toggle("dwell", state === "dwell");
      el.classList.toggle("asking", state === "asking");
      el.classList.toggle("chosen", state === "chosen");
      if (state === "asking") addCountdown(el); else removeCountdown(el);
      const sm = el.querySelector("small[data-hint]");
      if (sm) sm.textContent = state === "asking" ? (up ? "✓ Raised – lower to choose" : "Raise your eyebrows") : sm.dataset.hint;
      el.classList.toggle("raised", state === "asking" && up);
    });
  }

  function applyLive(d) {
    const ov = ensure("overlay");
    const show = !!(d.overlay && d.overlay.kind === "call_nurse");
    if (show && !ov.classList.contains("show"))
      ov.innerHTML = `${icon("bell", "ov-icon")}<h1>${esc(d.overlay.text || "Your nurse has been called")}</h1><p>Help is on the way.</p>`;
    ov.classList.toggle("show", show);

    const face = ensure("facehint", `${icon("camera")}<span>Move into view of the camera</span>`);
    face.classList.toggle("show", d.face === false);

    // eyebrows: show that a raise is seen; the bar fills up to the long raise (= Call Nurse)
    const b = d.brow || {};
    const bt = ensure("browtag", `${icon("brows")}<span class="bt-t"></span><i class="bt-bar"><i></i></i>`);
    const held = +b.held_s || 0, long = +b.long_s || 2;
    bt.classList.toggle("show", !!b.up && !(d.overlay && d.overlay.kind === "call_nurse"));
    // past half the long raise: say what is about to happen and how to stop it (never "keep holding")
    bt.querySelector(".bt-t").textContent = held >= long * 0.5
      ? `Nurse in ${Math.max(0, long - held).toFixed(1)} s – lower to cancel` : "Eyebrows up";
    bt.querySelector(".bt-bar i").style.width = (b.up ? Math.min(100, (held / long) * 100) : 0) + "%";

    const demo = ensure("demotag");
    const isDemo = !!(d.clock && d.clock.demo);
    demo.textContent = isDemo ? `DEMO TIME${d.clock.time ? " · " + d.clock.time : ""}` : "";
    demo.classList.toggle("show", isDemo);
  }

  // ---------------- voice ----------------
  // d.voice = [{id, text, cut}]: cut = stop what is being said first; otherwise it waits its turn.
  const VOICES = [/Ava.*Natural/i, /Emma.*Natural/i, /Jenny.*Natural/i, /Aria.*Natural/i, /Michelle.*Natural/i,
    /Natural/i, /Google US English/i, /Zira/i, /Samantha/i];
  const synth = window.speechSynthesis;
  let voice = null, voiceOn = false, lastVoiceId = null, onlineFailed = false;
  function pickVoice() {
    if (!synth) return null;
    const en = synth.getVoices().filter((v) => /^en/i.test(v.lang));
    const ok = onlineFailed ? en.filter((v) => v.localService) : en;
    for (const re of VOICES) { const v = ok.find((x) => re.test(x.name)); if (v) return v; }
    return ok[0] || null;
  }
  if (synth) synth.onvoiceschanged = () => { voice = pickVoice(); };
  function speakNow(text) {
    if (!text) return;
    const u = new SpeechSynthesisUtterance(text);
    voice = voice || pickVoice();
    if (voice) { u.voice = voice; u.lang = voice.lang; }
    u.onerror = (e) => {   // an online voice without internet: fall back to an installed one
      if ((e.error === "network" || e.error === "synthesis-failed" || e.error === "synthesis-unavailable")
          && voice && !voice.localService) { onlineFailed = true; voice = pickVoice(); speakNow(text); }
    };
    synth.speak(u);
  }
  function applyVoice(lines) {
    if (!Array.isArray(lines) || !synth) return;
    const fresh = lines.filter((l) => l && +l.id > (lastVoiceId ?? Infinity));
    const top = lines.reduce((m, l) => Math.max(m, +l.id || 0), lastVoiceId || 0);
    if (lastVoiceId === null) { lastVoiceId = top; return; }   // don't replay what was said before the page opened
    lastVoiceId = top;
    if (!voiceOn || !fresh.length) return;
    let from = 0;
    fresh.forEach((l, i) => { if (l.cut) from = i; });
    const batch = fresh.slice(from);
    if (batch[0].cut) {   // stop, then speak (a short gap: speaking right after cancel() can get lost)
      synth.cancel();
      setTimeout(() => batch.forEach((l) => speakNow(l.text)), 40);
    } else batch.forEach((l) => speakNow(l.text));
  }
  function voiceTag() {
    if (!synth) return;
    const tag = ensure("voicetag", `${icon("chat")}<span>Click anywhere to turn on the voice</span>`);
    tag.classList.add("show");
    const on = () => {
      voiceOn = true;
      tag.classList.remove("show");
      voice = pickVoice();
      synth.cancel();
      speakNow("Voice on.");
      document.removeEventListener("pointerdown", on);
      document.removeEventListener("keydown", on);
    };
    document.addEventListener("pointerdown", on);
    document.addEventListener("keydown", on);
  }

  /** Content that changes the layout (focus/overlay/clock/room/face are applied live instead). */
  function contentKey(d) {
    const { focus, overlay, clock, room, face, brow, ...rest } = d;
    return JSON.stringify(rest);
  }

  function start(render, opts) {
    const every = (opts && opts.every) || 150;
    const url = (opts && opts.url) || "/api/screen";
    let lastKey = "", fails = 0;
    async function poll() {
      const t0 = performance.now();
      try {
        const r = await fetch(url, { cache: "no-store" });
        const raw = await r.json();
        if (raw && raw.ask_s > 0) askS = raw.ask_s;
        if (raw && raw.voice) {
          if (!document.getElementById("voicetag")) voiceTag();
          try { applyVoice(raw.voice); } catch (e) { console.error(e); }
        }
        let d = raw;
        if (opts && opts.view) { try { d = opts.view(raw) || raw; } catch (e) { console.error(e); } }
        fails = 0;
        document.body.dataset.conn = "ok";
        if (opts && opts.onData) { try { opts.onData(d); } catch (e) { console.error(e); } }
        const key = contentKey(d);
        if (key !== lastKey) {
          lastKey = key;
          try { render(d); } catch (e) { console.error("render failed", e); }
        }
        applyFocus(d.focus, d.brow);
        applyLive(d);
      } catch (e) {
        if (++fails > 2) document.body.dataset.conn = "lost";
      }
      setTimeout(poll, Math.max(0, every - (performance.now() - t0)));
    }
    poll();
  }

  window.LookAt = { ICONS, ASK_S, icon, iconFor, optIcon, esc, hint, hintTag, dots, start };
})();
