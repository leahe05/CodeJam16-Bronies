/* The patient screen's structure and behaviour, shared by every design (web/patient.html and
 * web/designs/*.html). A design only adds CSS (and its background); it never changes this file.
 *
 * Three layers in the middle of the screen:
 *   #home  the home screen ("You're all set.", Talk to AI). Always there; blurred behind a window.
 *   #menu  room menus (room, lights, temperature) replace the home screen in place.
 *   #win   pop-ups open as a WINDOW in the middle, over the blurred home screen:
 *            suggestion (small notification window), checkin, assistant, nurse (full window).
 *          Opening: #win.opening. Next screen while open: the window morphs to the new size and its
 *          content cross-fades (.win-in.swap). Leaving: #win.closing, then hidden.
 * body[data-mode] = "idle" | "menu" | "window" tells the CSS which layer is in front.
 * html[data-theme] = "day" (07:00-17:00 on the backend clock) | "night"; it cross-fades when it changes.
 * Calibration (overlay.kind "calibrate"): drawn as an EMPTY check-in window, so the 3 targets are exactly
 * where the real check-in cards are; the target card glows with a dot. body[data-cal] is set meanwhile.
 */
(function () {
  "use strict";
  const { icon, optIcon, esc, hintTag, dots } = LookAt;
  const $ = (id) => document.getElementById(id);

  const WINDOW = new Set(["suggestion", "checkin", "assistant", "nurse"]);
  const HOME_DEFAULT = {
    screen: "idle", title: "You're all set.", subtitle: "I'm here if you need anything.",
    middle: { id: "talk", label: "Talk to AI", hint: "Look center + short eyebrow raise", icon: "chat" },
  };
  let home = HOME_DEFAULT, closeTimer = 0, openTimer = 0;

  // The home screen shows a moon at night and a sun by day (both drawn; CSS shows the right one).
  const MOON = '<svg class="celestial moon" viewBox="0 0 24 24" aria-hidden="true"><defs><linearGradient id="mg" x1="0" y1="0" x2="1" y2="1">'
    + '<stop offset="0" style="stop-color:var(--moon-a,#f2f8ff)"/><stop offset="1" style="stop-color:var(--moon-b,#6aaeff)"/></linearGradient>'
    + '<mask id="bite"><rect width="24" height="24" fill="#fff"/><circle cx="15.2" cy="8.6" r="6.6" fill="#000"/></mask></defs>'
    + '<circle cx="10.6" cy="13" r="8" fill="url(#mg)" mask="url(#bite)"/>'
    + '<path class="star" d="M18.2 2.4l.75 2.25 2.25.75-2.25.75-.75 2.25-.75-2.25-2.25-.75 2.25-.75z"/>'
    + '<path class="star" d="M21.3 8.6l.4 1.15 1.15.4-1.15.4-.4 1.15-.4-1.15-1.15-.4 1.15-.4z"/></svg>';
  const SUN = '<svg class="celestial sun" viewBox="0 0 24 24" aria-hidden="true"><defs><radialGradient id="sg" cx=".38" cy=".34" r=".75">'
    + '<stop offset="0" style="stop-color:var(--sun-a,#fff8db)"/><stop offset="1" style="stop-color:var(--sun-b,#f5b82e)"/></radialGradient></defs>'
    + '<g class="rays"><path d="M19.6 12H21.9M17.37 17.37L19 19M12 19.6V21.9M6.63 17.37L5 19M4.4 12H2.1M6.63 6.63L5 5M12 4.4V2.1M17.37 6.63L19 5"/></g>'
    + '<circle cx="12" cy="12" r="5.4" fill="url(#sg)"/>'
    + '<path class="star" d="M21.2 .9l.5 1.5 1.5.5-1.5.5-.5 1.5-.5-1.5-1.5-.5 1.5-.5z"/></svg>';
  const CELESTIAL = MOON + SUN;

  // ---------------- day / night ----------------
  // Day look from 07:00 to 17:00, night look otherwise. Uses the backend clock (so the demo's
  // "Set 02:00" / "Set 07:30" switch it too); ?theme=day or ?theme=night in the URL forces one.
  const DAY_START = 7 * 60, NIGHT_START = 17 * 60;
  function minutesOf(clock) {
    const m = /^(\d{1,2}):(\d{2})/.exec((clock && clock.time) || "");
    if (m) return +m[1] * 60 + +m[2];
    const now = new Date();
    return now.getHours() * 60 + now.getMinutes();
  }
  function themeFor(d) {
    const forced = new URLSearchParams(location.search).get("theme");
    if (forced === "day" || forced === "night") return forced;
    const t = minutesOf(d && d.clock);
    return t >= DAY_START && t < NIGHT_START ? "day" : "night";
  }
  function applyTheme(theme) {
    const root = document.documentElement;
    if (root.dataset.theme === theme) return;
    const set = () => { root.dataset.theme = theme; };
    // slow cross-fade between the two looks (instant on first load or with ?noanim)
    if (!root.dataset.theme || root.classList.contains("noanim") || !document.startViewTransition) set();
    else document.startViewTransition(set);
  }

  // ---------------- calibration ----------------
  // The patient is calibrated on the card positions, so draw a real check-in screen with blank cards.
  // Same parts as a typical check-in (badge, title, picture, 2 cards, centre card) so nothing moves.
  function calibrationView(d) {
    const ov = d && d.overlay;
    if (!ov || ov.kind !== "calibrate") return d;
    const blank = (spot) => ({ id: "cal-" + spot, label: " ", hint: " ", icon: "target" });
    return {
      ...d, screen: "checkin", badge: "Setting up", title: ov.text || "", subtitle: null, icon: "target", page: null,
      left: blank("left"), middle: blank("middle"), right: blank("right"),
      focus: null, overlay: null, calibrate: ov.spot || "none",
    };
  }
  function markTarget(d) {
    document.body.dataset.cal = d.calibrate ? "on" : "";
    document.querySelectorAll("#win [data-spot]").forEach((el) => el.classList.toggle("cal-target", el.dataset.spot === d.calibrate));
  }

  // ---------------- pieces ----------------
  function chip(d) {
    if (!d.badge || d.screen === "nurse") return "";
    return `<div class="chip-row">${icon("sparkle", "spark")}<span class="chip">${/room/i.test(d.badge) ? icon("lamp") : ""}${esc(d.badge)}</span></div>`;
  }
  const sub = (d) => (d.subtitle ? `<p class="sub">${esc(d.subtitle)}</p>` : "");
  function card(d, spot) {
    const o = d[spot];
    if (!o) return "";
    return `<div class="opt" data-spot="${spot}">${icon(optIcon(o) || "sparkle", "opt-i")}<span class="opt-t"><b>${esc(o.label)}</b>${hintTag(o, spot)}</span></div>`;
  }
  const cards = (d, layout) => `<div class="opts ${layout} ${d.left && d.right ? "" : "one"}">${card(d, "left")}${card(d, "right")}</div>`;
  function center(d) {
    const o = d.middle;
    if (!o) return "";
    return `<div class="center" data-spot="middle">${icon(optIcon(o) || "home", "center-i")}<span><b>${esc(o.label)}</b>${hintTag(o, "middle")}</span></div>`;
  }

  // ---------------- layers ----------------
  function homeHTML(h, live) {
    const m = h.middle;
    const pill = m ? `<div class="talk" ${live ? 'data-spot="middle"' : ""}><b>${icon(optIcon(m) || "chat")}${esc(m.label)}</b>`
      + `${live ? hintTag(m, "middle") : `<small>${esc(m.hint || "")}</small>`}</div>` : "";
    return `<div class="home-in">${CELESTIAL}<h1 class="hero">${esc(h.title)}</h1>${sub(h)}${pill}</div>`;
  }

  function winHTML(d) {
    switch (d.screen) {
      case "suggestion":
        return `${chip(d)}<h1>${esc(d.title)}</h1>${sub(d)}`
          + (d.middle ? `<div class="engage" data-spot="middle">${icon(optIcon(d.middle) || "target")}<span>${esc(d.middle.label)}</span></div>` : "");
      case "nurse": {
        // no answers on screen = waiting for the nurse's question / after an answer: no "choose" instruction
        const hasChoice = d.left || d.right || d.middle;
        const instr = hasChoice
          ? `<div class="instr" ${d.middle ? 'data-spot="middle"' : ""}>${icon("brows", "instr-i")}<span>${esc(d.footer || "Look left or right and raise your eyebrows to choose.")}</span></div>`
          : "";
        return `<h1>${esc(d.title)}</h1>${sub(d)}${cards(d, "row")}${instr}${dots(d.page)}`;
      }
      case "checkin": {
        const pic = d.icon && d.icon !== "sparkle" && !d.page ? `<div class="illus">${icon(d.icon)}</div>` : "";
        return `${chip(d)}<h1>${esc(d.title)}</h1>${sub(d)}${pic}${cards(d, "row")}${center(d)}${dots(d.page)}`;
      }
      default:   // assistant
        return `${chip(d)}<h1>${esc(d.title)}</h1>${sub(d)}${cards(d, "stack")}${center(d)}${dots(d.page)}`;
    }
  }
  const menuHTML = (d) => `<div class="panel enter">${chip(d)}<h1>${esc(d.title)}</h1>${sub(d)}${cards(d, "stack")}${center(d)}${dots(d.page)}</div>`;

  function headHTML(d) {
    const brand = (cls) => `<div class="brand ${cls}">${icon("eye")}<span>LookAt</span></div>`;
    if (d.screen === "nurse")
      return `${brand("start")}<div class="nursepill">${icon("nurse")}${esc(d.badge || "From your nurse")}</div>`
        + `<div class="conn"><span class="ok">Connected</span><span class="lost">Reconnecting…</span></div>`;
    const corner = (d.screen === "idle" || d.screen === "suggestion") && d.left
      ? `<div class="corner" data-spot="left">${icon(optIcon(d.left) || "lamp", "corner-i")}<span><b>${esc(d.left.label)}</b>${hintTag(d.left, "left")}</span></div>`
      : "<div></div>";
    return `${corner}<div></div>${brand("")}`;
  }

  // ---------------- the window ----------------
  function openWindow(d) {
    const win = $("win");
    clearTimeout(closeTimer);
    const wasOpen = win.classList.contains("open") && !win.classList.contains("closing");
    const size = d.screen === "suggestion" ? "notif" : "full";
    win.className = `open ${size} kind-${d.screen}` + (wasOpen ? "" : " opening");
    win.innerHTML = `<div class="win-in${wasOpen ? " swap" : ""}">${winHTML(d)}</div>`;
    if (!wasOpen) {
      clearTimeout(openTimer);
      openTimer = setTimeout(() => win.classList.remove("opening"), 1200);
    }
  }
  function closeWindow() {
    const win = $("win");
    if (!win.classList.contains("open") || win.classList.contains("closing")) return;
    win.querySelectorAll("[data-spot]").forEach((el) => el.removeAttribute("data-spot"));   // no gaze on a closing window
    win.classList.remove("opening");
    win.classList.add("closing");
    closeTimer = setTimeout(() => { win.className = ""; win.innerHTML = ""; }, 380);
  }

  // ---------------- render ----------------
  function render(d) {
    const kind = d.screen;
    const mode = kind === "idle" ? "idle" : WINDOW.has(kind) ? "window" : "menu";
    if (kind === "idle") home = d;
    $("head").innerHTML = headHTML(d);
    $("home").innerHTML = homeHTML(home, mode === "idle");
    if (mode === "menu") $("menu").innerHTML = menuHTML(d);
    if (mode === "window") openWindow(d); else closeWindow();
    document.body.dataset.mode = mode;
    document.body.dataset.screen = kind;
    $("bar").classList.toggle("gone", d.show_call_nurse === false);
    markTarget(d);
  }

  function start() {
    document.body.insertAdjacentHTML("beforeend",
      '<header id="head"></header>'
      + '<main id="stage"><section id="home"></section><section id="menu"></section><section id="win"></section></main>'
      + `<footer id="bar">${icon("bell", "bar-i")}<b>Call Nurse</b><span class="tx">Long eyebrow raise anywhere</span></footer>`);
    applyTheme(themeFor(null));   // best guess before the first answer, so the page doesn't flash
    LookAt.start(render, { view: calibrationView, onData: (d) => applyTheme(themeFor(d)) });
  }

  window.PatientUI = { start };
})();
