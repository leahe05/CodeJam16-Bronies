"use strict";

const nursePath = "/api/nurse";
const savedAgentsKey = "lookat.nurse.agents";
const $ = id => document.getElementById(id);
const list = value => Array.isArray(value) ? value.filter(item => item != null) : [];
const priorityLevels = { urgent: { label: "Urgent", rank: 2 }, soon: { label: "Soon", rank: 1 },
  routine: { label: "Routine", rank: 0 }, unrated: { label: "Unranked", rank: -1 } };
const priorityLevel = alert => {
  if (alert.kind === "call" || alert.urgency === "urgent") return "urgent";
  return Object.hasOwn(priorityLevels, alert.priority?.level) ? alert.priority.level : "routine";
};
const urgent = alert => priorityLevel(alert) === "urgent";
const alertKinds = { call: "Nurse call", page: "Intervention requested", flag: "Patient preference", info: "Update" };
const agents = new Map();
let selected = "";
let activeTab = "patients";
let audio = null;
let soundEnabled = false;
let feedbackTimer;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function button(text, className, onClick) {
  const node = element("button", className, text);
  node.type = "button";
  node.addEventListener("click", onClick);
  return node;
}

function updateText(node, text) {
  if (node.textContent !== text) node.textContent = text;
}

// Avoid replacing focused controls on every poll when their inputs are unchanged.
function updateRegion(node, value, build) {
  const signature = JSON.stringify(value);
  if (node.renderSignature === signature) return;
  const focusId = node.contains(document.activeElement) ? document.activeElement.id : "";
  node.replaceChildren(...build());
  node.renderSignature = signature;
  if (focusId) document.getElementById(focusId)?.focus({ preventScroll: true });
}

function agentOrigin(value) {
  const url = new URL(value);
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password ||
      url.pathname !== "/" || url.search || url.hash) {
    throw new Error("Enter the LookAt server address, including its port if needed.");
  }
  if (location.protocol === "https:" && url.protocol !== "https:") {
    throw new Error("Use an HTTPS agent address from this page.");
  }
  return url.origin === location.origin ? "" : url.origin;
}

function storeAgents() {
  try {
    localStorage.setItem(savedAgentsKey, JSON.stringify([...agents.values()]
      .filter(agent => agent.origin && !agent.demo).map(agent => agent.origin)));
  } catch {
    // The dashboard can still monitor agents when browser storage is unavailable.
  }
}

async function request(origin, path = "", body) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(),
    ["/suggest", "/proposal/refine"].includes(path) ? 12000 : 5000);
  try {
    const response = await fetch(origin + nursePath + path, {
      cache: "no-store",
      signal: controller.signal,
      ...(body === undefined ? {} : {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      })
    });
    if (!response.ok) throw new Error("The LookAt server could not complete the request.");
    return await response.json();
  } catch (error) {
    if (error.name === "AbortError") throw new Error("The LookAt server took too long to respond.");
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

function agentName(agent) {
  return agent.data?.patient?.name || (agent.demo ? "Simulated patient" :
    agent.origin ? new URL(agent.origin).host : "This LookAt server");
}

function agentStatus(agent) {
  if (agent.state === "connecting") return "Connecting…";
  if (agent.state === "offline") return "Reconnecting";
  return agent.data.connected === false ? "Patient disconnected" : "Connected";
}

function notify(text) {
  const node = $("feedback");
  node.hidden = false;
  updateText(node, text);
  clearTimeout(feedbackTimer);
  feedbackTimer = setTimeout(() => { node.hidden = true; }, 6500);
}

function flashUrgentAlerts(agent, data) {
  const pending = list(data.alerts).filter(alert => !alert.done);
  const keyFor = alert => JSON.stringify([alert.id, alert.time, alert.text, urgent(alert)]);
  const incoming = pending.filter(alert => !agent.pendingAlerts.has(keyFor(alert)));
  agent.pendingAlerts = new Set(pending.map(keyFor));
  if (!incoming.length) return;
  const announcement = incoming.map(alert => agentName(agent) + ". " + (alert.text || "New alert")).join(". ");
  updateText($("alert-announcement"), announcement);
  if (!incoming.some(urgent)) return;
  navigator.vibrate?.([180, 120, 180]);
  if (soundEnabled) playAlertTone();
}

function playAlertTone() {
  if (audio?.state !== "running") return;
  const start = audio.currentTime;
  for (const offset of [0, 0.24]) {
    const oscillator = audio.createOscillator();
    const volume = audio.createGain();
    oscillator.frequency.value = 660;
    oscillator.connect(volume);
    volume.connect(audio.destination);
    volume.gain.setValueAtTime(0.18, start + offset);
    volume.gain.exponentialRampToValueAtTime(0.001, start + offset + 0.18);
    oscillator.start(start + offset);
    oscillator.stop(start + offset + 0.18);
  }
}

async function poll(agent) {
  const revision = agent.revision;
  if (!agent.busy) {
    try {
      const data = await request(agent.origin);
      if (data?.v !== 1 || !("alerts" in data)) throw new Error("Unexpected nurse API response.");
      if (agents.get(agent.origin) !== agent || revision !== agent.revision) return;
      if (!Array.isArray(data.history)) data.observedHistory = observeHistory(agent, data);
      agent.data = data;
      agent.state = "online";
      if (!agent.origin && data.simulation) {
        const port = data.demo_companion_port;
        if (Number.isInteger(port) && port > 0 && port <= 65535) {
          const companion = new URL(location.origin);
          companion.port = String(port);
          if (companion.origin !== location.origin) addAgent(companion.origin, true);
        }
      }
      if (!agent.origin && (data.simulation || data.tracked_demo)) {
        for (const path of list(data.demo_agents)) {
          if (typeof path === "string" && /^\/demo\/[a-z0-9-]+$/.test(path)) addAgent(path, true);
        }
      }
      flashUrgentAlerts(agent, data);
    } catch {
      if (agents.get(agent.origin) !== agent || revision !== agent.revision) return;
      agent.state = "offline";
    } finally {
      if (agents.get(agent.origin) === agent && revision === agent.revision) render();
    }
  }
  if (agents.get(agent.origin) === agent) {
    agent.timer = setTimeout(() => poll(agent), 1000);
  }
}

function observeHistory(agent, data) {
  const identity = JSON.stringify(data.patient);
  if (!agent.observedHistory || agent.historyIdentity !== identity) {
    agent.observedHistory = new Map();
    agent.historyIdentity = identity;
  }
  const events = [];
  const currentAnswers = new Set();
  const recent = list(data.recent);
  for (const q of list(data.session?.thread)) {
    const key = [q.qid, q.time, q.question];
    events.push({ key: ["question", ...key], time: q.time, actor: "Nurse", action: "Asked", text: q.question });
    if (q.answer != null) {
      const activity = `Answered "${q.answer}" to: ${q.question}`;
      currentAnswers.add(activity);
      const entry = recent.find(item => item.text === activity);
      events.push({ key: ["answer", ...key, q.answer], time: entry?.time, sortTime: entry?.time || q.time,
        actor: "Patient", action: "Chose", text: q.answer, activity });
    }
  }
  for (const entry of recent) {
    if (!entry.text?.startsWith('Answered "') || currentAnswers.has(entry.text)) continue;
    if ([...agent.observedHistory.values()].some(event => event.activity === entry.text)) continue;
    events.push({ key: ["activity", entry.time, entry.text], time: entry.time,
      actor: "Patient", action: "Answered", text: entry.text, activity: entry.text });
  }
  for (const alert of list(data.alerts)) {
    if (alert.kind !== "call" && alert.kind !== "page") continue;
    const call = alert.kind === "call";
    events.push({ key: ["alert", alert.id, alert.time, alert.text], time: alert.time,
      actor: call || alert.source === "patient" ? "Patient" : "Patient agent",
      action: call ? "Called nurse" : "Requested help", text: alert.request || alert.text });
  }
  events.sort((a, b) => (a.sortTime || a.time || "").localeCompare(b.sortTime || b.time || ""));
  for (const event of events) {
    const key = JSON.stringify(event.key);
    if (!agent.observedHistory.has(key)) agent.observedHistory.set(key, event);
  }
  while (agent.observedHistory.size > 100) agent.observedHistory.delete(agent.observedHistory.keys().next().value);
  return [...agent.observedHistory.values()];
}

function addAgent(origin, demo = false) {
  if (agents.has(origin)) return;
  const agent = { origin, demo, state: "connecting", data: null, pendingAlerts: new Set(), revision: 0, busy: false };
  agents.set(origin, agent);
  poll(agent);
}

async function act(agent, path, body, success, onSuccess) {
  if (agent.busy || agent.state !== "online") return;
  agent.busy = true;
  agent.revision += 1;
  render();
  try {
    const result = await request(agent.origin, path, body);
    if (!result?.ok) throw new Error(result?.error || "The action could not be completed.");
    if (agents.get(agent.origin) !== agent) return;
    if (path === "/alert") {
      const alert = list(agent.data.alerts).find(item => item.id === body.id);
      if (alert) alert.done = true;
    }
    onSuccess?.(result);
    notify(typeof success === "function" ? success(result) : success);
  } catch (error) {
    notify(error.message || "The action could not be completed.");
  } finally {
    agent.busy = false;
    if (agents.get(agent.origin) === agent) {
      clearTimeout(agent.timer);
      render();
      poll(agent);
    }
  }
}

function showTab(tab, moveFocus = false) {
  activeTab = tab;
  for (const [name, panel] of [["patients", "overview"], ["attention", "attention"]]) {
    const control = $(name + "-tab");
    const visible = tab === name;
    control.setAttribute("aria-selected", String(visible));
    control.tabIndex = visible ? 0 : -1;
    $(panel).hidden = !visible;
    if (visible && moveFocus) control.focus();
  }
}

function alertOrigin(alert) {
  if (alert.delivery === "check-in") return "Reported at check-in";
  if (alert.delivery === "proactive" && alert.kind !== "call") return "Proactive patient-agent request";
  if (alert.source === "patient") return "Patient requested";
  if (alert.source === "ai") return "AI requested";
  if (alert.kind === "call") return "Patient requested";
  if (alert.kind === "page" || alert.kind === "flag") return "AI requested";
  return "Source unreported";
}

function openPatient(origin, showProfile = false) {
  selected = origin;
  showTab("patients");
  $("profile-details").open = showProfile;
  render();
  $("details-heading").focus();
}

function alertCard({ agent, alert }, handled) {
  const card = element("article", "alert-card");
  const priority = priorityLevel(alert);
  card.dataset.priority = handled ? "handled" : priority;
  const meta = element("div", "alert-meta");
  if (!handled) meta.append(element("span", "priority-label", priorityLevels[priority].label));
  meta.append(element("span", "alert-kind", alertKinds[alert.kind] || "Alert"),
    element("span", "alert-origin", alertOrigin(alert)),
    button([agentName(agent), agent.data?.patient?.room].filter(Boolean).join(" · "), "patient-link", () =>
      openPatient(agent.origin)));
  if (alert.time) meta.append(element("span", "", alert.time));
  if (agent.state !== "online") meta.append(element("span", "", "Last received"));
  card.append(meta, element("h3", "", alert.request || alert.text || "Patient alert"));
  if (!alert.request && alert.kind === "call") card.append(element("p", "muted", "Patient did not specify what they need."));
  if (alert.reason) card.append(element("p", "muted", alert.reason));
  if (alert.recommendation) card.append(element("p", "request-recommendation", alert.recommendation));
  if (alert.ai_rationale) card.append(element("p", "muted", "Claude: " + alert.ai_rationale));
  if (alert.priority_source === "local_rules" && alert.priority_basis) {
    card.append(element("p", "muted", "Local rules: " + alert.priority_basis));
  }
  const review = {
    analyzing: "Claude is reviewing",
    needs_review: "Nurse review needed",
    question_queued: "Suggested question awaiting approval",
    waiting_answer: "Waiting for the patient’s answer",
    clarified: "Patient clarified the request",
  }[alert.review];
  if (review) card.append(element("p", "muted", review));
  const actions = handled ? [] : list(alert.actions).filter(action => action.id);
  if (actions.length && alert.id != null) {
    const row = element("div", "alert-actions");
    actions.forEach((action, index) => {
      const control = button(action.label || "Acknowledge", index === 0 ? "button primary" : "button", () => {
        act(agent, "/alert", { id: alert.id, action: action.id }, "Alert updated for " + agentName(agent) + ".");
      });
      control.id = `action-${encodeURIComponent(JSON.stringify([agent.origin, alert.id, action.id]))}`;
      control.disabled = agent.state !== "online" || agent.busy;
      row.append(control);
    });
    card.append(row);
  }
  return card;
}

function renderAlerts() {
  const entries = [...agents.values()].flatMap(agent => list(agent.data?.alerts).map(alert => ({ agent, alert })));
  const pending = entries.filter(entry => !entry.alert.done)
    .sort(({ alert: left }, { alert: right }) => {
      const priority = priorityLevels[priorityLevel(right)].rank - priorityLevels[priorityLevel(left)].rank;
      const time = (left.time || "").localeCompare(right.time || "");
      return priority || time || (left.priority?.order ?? 0) - (right.priority?.order ?? 0);
    });
  const handled = entries.filter(entry => entry.alert.done);
  const signature = ({ agent, alert }) => [agent.origin, agentName(agent), agent.data?.patient?.room, agent.state, agent.busy, alert];
  const complete = [...agents.values()].every(agent => agent.state === "online" && agent.data.connected !== false);
  const empty = complete ? "No open requests." : "Waiting for agent updates.";
  updateRegion($("alerts"), [empty, pending.map(signature)], () =>
    pending.length ? pending.map(entry => alertCard(entry, false)) : [element("p", "empty", empty)]);
  updateRegion($("handled"), handled.map(signature), () => handled.map(entry => alertCard(entry, true)));
  $("history").hidden = !handled.length;
  $("handled-empty").hidden = Boolean(handled.length);
  updateText($("history-label"), `Handled alerts (${handled.length})`);
  $("alert-count").hidden = !pending.length;
  updateText($("alert-count"), String(pending.length));
  const hasUrgent = pending.some(entry => urgent(entry.alert));
  $("alert-count").dataset.urgent = String(hasUrgent);
  $("queue-count").dataset.urgent = String(hasUrgent);
  updateText($("queue-count"), complete || pending.length ? `${pending.length} open` : "Checking for requests…");
}

function cameraLink(camera, id) {
  if (typeof camera?.url !== "string" || !camera.url.startsWith("https://")) return null;
  const link = element("a", "demo-camera", camera.name || "Open camera");
  link.id = id;
  link.href = camera.url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.append(element("small", "", camera.source === "demo" ? "Demo camera · Opens in new tab" : "Opens in new tab"));
  return link;
}

function renderShift(roster) {
  const patients = roster.filter(agent => agent.data?.shift);
  const first = patients[0];
  $("shift-controls").hidden = !first;
  $("shift-panel").hidden = !first;
  if (!first) return;
  const state = first.data.shift.state;
  updateText($("shift-status"), {
    ready: "Ready for the simulated shift",
    running: "Simulated shift in progress",
    complete: "Simulated shift complete"
  }[state]);
  $("start-shift").hidden = state !== "ready";
  $("start-shift").disabled = first.busy || first.state !== "online";
  $("end-shift").hidden = state !== "running";
  $("end-shift").disabled = first.busy || first.state !== "online";
  updateText($("shift-ai-status"), first.data.shift.ai_enabled ?
    "Claude reviews incoming requests. Local rules provide an initial priority." :
    "Local rules rank requests. Set a Claude API key to enable AI review.");
  updateRegion($("shift-feeds"), patients.map(agent => [
    agentName(agent), agent.data.patient.room, agent.data.shift.camera, agent.data.shift.feed
  ]),
    () => patients.map(agent => {
      const card = element("article", "shift-feed");
      card.append(element("h3", "", [agentName(agent), agent.data.patient.room].filter(Boolean).join(" · ")));
      const camera = cameraLink(agent.data.shift.camera, "demo-camera-" + encodeURIComponent(agent.origin));
      if (camera) card.append(camera);
      const updates = list(agent.data.shift.feed);
      if (!updates.length) {
        card.append(element("p", "muted", "Waiting for the first patient-agent update."));
        return card;
      }
      const entries = element("ul");
      entries.tabIndex = 0;
      entries.setAttribute("aria-label", agentName(agent) + " updates");
      for (const update of updates.reverse()) {
        const row = element("li");
        row.append(element("time", "", update.time), element("p", "", update.status),
          element("small", "shift-response", update.response));
        entries.append(row);
      }
      card.append(entries);
      return card;
    }));
}

function renderProposals(roster) {
  const pending = roster.filter(agent => agent.data?.proposal);
  $("supervisor-panel").hidden = pending.length === 0;
  updateText($("supervisor-mode"), roster.some(agent => agent.data?.shift) ? "Simulated shift" :
    roster.some(agent => agent.data?.tracked_demo) ? "Live patient input + simulated patient" : "Two-patient simulation");
  updateRegion($("proposals"), pending.map(agent => [
    agent.origin, agent.data.proposal, agent.state, agent.busy, agent.data.session?.active, agent.data.ai_drafts,
    agent.data.shift?.queued_questions
  ]), () => pending.map(agent => {
    const proposal = agent.data.proposal;
    const card = element("article", "alert-card proposal");
    card.append(element("h3", "", agentName(agent) + " · " + agent.data.patient.room),
      element("p", "muted", proposal.evidence),
      element("p", "", proposal.preference));
    if (agent.data.shift?.queued_questions > 1) {
      card.append(element("small", "muted", `${agent.data.shift.queued_questions - 1} more check-in queued`));
    }
    const branches = element("ol", "proposal-branches");
    for (const step of proposal.steps) {
      const item = element("li", "", step.question);
      item.append(element("small", "muted", "Yes · " + step.request));
      branches.append(item);
    }
    card.append(branches, element("p", "muted", "No advances to the next question or ends the check-in. Center calls the nurse."));
    const actions = element("div", "alert-actions");
    const unavailable = agent.busy || agent.state !== "online";
    const handle = (action, message, after) => act(agent, "/proposal/" + action,
      { id: proposal.id, source: proposal.source }, message, () => {
      agent.data.proposal = null;
      after?.();
    });
    const send = button("Approve and send", "button primary", () =>
      handle("approve", "Check-in sent to " + agentName(agent) + ".", () => openPatient(agent.origin)));
    const dismiss = button("Dismiss", "button", () =>
      handle("dismiss", "Check-in dismissed for " + agentName(agent) + "."));
    send.disabled = unavailable || agent.data.session?.active === true;
    dismiss.disabled = unavailable;
    actions.append(send, dismiss);
    if (proposal.source === "ai") {
      card.append(element("p", "muted", "Questions drafted with Claude."));
    } else if (agent.data.ai_drafts) {
      const refine = button("Draft with Claude", "button", () =>
        act(agent, "/proposal/refine", { id: proposal.id }, "Draft ready for review.", result => {
          agent.data.proposal = result.proposal;
        }));
      refine.disabled = unavailable;
      actions.append(refine);
    }
    card.append(actions);
    return card;
  }));
}

function awarenessLabel(awareness) {
  const state = { awake: "Awake", asleep: "Asleep" }[awareness?.state];
  if (!state) return "Awake status unknown";
  return awareness.source === "camera" ? `${state} · camera estimate` :
    awareness.source === "demo" ? `${state} · demo` : state;
}

function presenceLabel(present) {
  return present === true ? "Visitors present" : present === false ? "No visitors" : "Visitor status unknown";
}

function vitalLines(vitals) {
  return [
    Number.isFinite(vitals?.heart_rate_bpm) && `Heart rate ${vitals.heart_rate_bpm} bpm`,
    Number.isFinite(vitals?.spo2_pct) && `SpO₂ ${vitals.spo2_pct}%`,
    Number.isFinite(vitals?.respiratory_rate_bpm) && `Respiratory rate ${vitals.respiratory_rate_bpm}/min`
  ].filter(Boolean);
}

function summaryLabel(summary) {
  return summary?.source === "demo" ? "Demo AI summary" : "Patient AI summary";
}

function tileStatus(data) {
  const temperature = data.profile?.room?.temp_c ?? data.room?.temp_c;
  const openRequests = list(data.alerts).filter(alert => !alert.done).length;
  const metrics = element("span", "tile-metrics");
  for (const text of [
    Number.isFinite(temperature) ? `Room ${temperature} °C` : null,
    openRequests && `${openRequests} open ${openRequests === 1 ? "request" : "requests"}`
  ].filter(Boolean)) {
    metrics.append(element("span", "metric", text));
  }
  return metrics.childElementCount ? metrics : null;
}

function renderRoster() {
  const roster = [...agents.values()];
  updateRegion($("patients"), [selected, roster.map(agent =>
    [agent.origin, agentName(agent), agent.data?.patient?.room, agentStatus(agent), agent.busy,
      agent.data?.profile?.room?.temp_c ?? agent.data?.room?.temp_c,
      list(agent.data?.alerts).filter(alert => !alert.done).length])], () =>
    roster.map(agent => {
      const item = element("li", "patient-item");
      const tile = button("", "patient-tile", () => openPatient(agent.origin, true));
      tile.id = "patient-" + encodeURIComponent(agent.origin);
      tile.setAttribute("aria-pressed", String(agent.origin === selected));
      tile.setAttribute("aria-controls", "profile-details");
      tile.append(element("strong", "patient-name", agentName(agent)),
        element("span", "patient-meta", [agent.data?.patient?.room, agentStatus(agent)].filter(Boolean).join(" · ")));
      const status = agent.data && tileStatus(agent.data);
      if (status) tile.append(status);
      item.append(tile);
      if (agent.origin && !agent.demo) {
        const disconnect = button("Disconnect", "disconnect", () => {
          clearTimeout(agent.timer);
          agents.delete(agent.origin);
          if (selected === agent.origin) {
            selected = "";
            $("profile-details").open = false;
          }
          storeAgents();
          render();
          notify("Disconnected " + agentName(agent) + ".");
        });
        disconnect.id = "disconnect-" + encodeURIComponent(agent.origin);
        disconnect.disabled = agent.busy;
        disconnect.setAttribute("aria-label", "Disconnect " + agentName(agent));
        item.append(disconnect);
      }
      return item;
    }));
}

function note(title, lines, className = "") {
  const items = lines.filter(Boolean);
  if (!items.length) return null;
  const section = element("section", "note");
  const content = element("ul", className);
  for (const line of items) content.append(element("li", "", line));
  section.append(element("h3", "", title), content);
  return section;
}

function stateLabel(name, value) {
  return typeof value === "boolean" ? name + (value ? " on" : " off") : null;
}

function elapsedMeal(minutes) {
  if (!Number.isFinite(minutes) || minutes < 0) return null;
  const hours = Math.floor(minutes / 60);
  const remaining = minutes % 60;
  return [hours ? hours + " h" : null, remaining || !hours ? remaining + " min" : null].filter(Boolean).join(" ") + " ago";
}

function vitalsTable(vitals) {
  const samples = list(vitals?.samples).slice(-4);
  const last = vitals?.current || samples.at(-1);
  if (!last) return null;
  const readings = [
    ["Heart rate", "bpm", sample => sample.heart_rate_bpm],
    ["SpO₂", "%", sample => sample.spo2_pct],
    ["Respiratory rate", "/min", sample => sample.respiratory_rate_bpm],
    ["Blood pressure", "mmHg", sample =>
      Number.isFinite(sample.systolic_mmhg) && Number.isFinite(sample.diastolic_mmhg) ?
        `${sample.systolic_mmhg}/${sample.diastolic_mmhg}` : null]
  ];
  const table = element("table", "vitals-table");
  table.append(element("caption", "", [vitals.source === "simulated" ? "Simulated vitals" : "Vitals",
    last.time].filter(Boolean).join(" · ")));
  const headers = element("tr");
  for (const label of ["Reading", "Current", "Recent trend"]) {
    const cell = element("th", "", label);
    cell.scope = "col";
    headers.append(cell);
  }
  const head = element("thead");
  head.append(headers);
  const body = element("tbody");
  for (const [label, unit, read] of readings) {
    const values = samples.map(read);
    const current = read(last);
    if (current == null && values.every(value => value == null)) continue;
    const row = element("tr");
    const heading = element("th", "", label);
    heading.scope = "row";
    row.append(heading, element("td", "current-reading", current == null ? "Unavailable" : `${current} ${unit}`),
      element("td", "trend-reading", values.length ? values.map(value => value ?? "—").join(" → ") : "Unavailable"));
    body.append(row);
  }
  table.append(head, body);
  return table;
}

function patientProfile(profile) {
  const section = element("section", "note patient-profile");
  section.append(element("h3", "", "Patient agent profile"));
  if (profile.reported_at) section.append(element("p", "muted", "Reported at " + profile.reported_at));
  const room = profile.room || {};
  const conditions = [
    ["Room temperature", Number.isFinite(room.temp_c) ? room.temp_c + " °C" : null],
    ["Last meal", [elapsedMeal(profile.last_meal?.minutes_since), profile.last_meal?.name,
      profile.last_meal?.time].filter(Boolean).join(" · ")],
    ["Bedside", [awarenessLabel(profile.awareness), profile.posture, presenceLabel(profile.visitors_present)]
      .filter(Boolean).join(" · ")],
    ["Room comfort", [typeof room.window === "string" && "Window " + room.window.toLowerCase(),
      typeof room.blinds === "string" && "Blinds " + room.blinds.toLowerCase()]
      .filter(Boolean).join(" · ")]
  ].filter(([, value]) => value);
  const summary = element("dl", "profile-metrics");
  for (const [label, value] of conditions) {
    const item = element("div");
    item.append(element("dt", "", label), element("dd", "", value));
    summary.append(item);
  }
  section.append(summary);
  if (profile.status) section.append(element("p", "", profile.status));
  const vitals = vitalsTable(profile.vitals);
  if (vitals) section.append(vitals);
  const context = note("Preferences and habits", [...list(profile.preferences), ...list(profile.habits)]);
  if (context) section.append(context);
  const camera = cameraLink(profile.camera, "profile-camera");
  if (camera) section.append(camera);
  return section;
}

function patientNotes(data) {
  const room = data.room || {};
  const session = data.session || {};
  const bedside = data.bedside;
  const fan = stateLabel("Fan", room.fan_on);
  const checkIn = data.agent_checkin;
  return [
    data.profile ? patientProfile(data.profile) : bedside && note("Bedside status", [
      awarenessLabel(bedside.awareness),
      presenceLabel(bedside.visitors_present),
      bedside.vitals?.simulated ? "Simulated vitals" : null,
      ...vitalLines(bedside.vitals)
    ]),
    checkIn && note("Agent check-ins", [
      `Every ${checkIn.interval_s} seconds`,
      checkIn.time ? `Last reply ${checkIn.time}` : "Waiting for the patient agent’s reply",
      checkIn.time && (checkIn.needs_attention ? "Requested nurse attention" :
        checkIn.suggested_question ? "Suggested a question" : "Nothing needs attention")
    ]),
    note("For next visit", list(data.next_visit)),
    note("Nurse visits", [data.last_visit && "Last visit " + data.last_visit]),
    note("Conversation", [
      session.waiting ? "Waiting for a patient answer" : session.active ? "Session active" : null,
    ]),
    !data.profile && note("Patient preferences", list(data.facts).map(fact => fact.text)),
    note("Room status", [
      !data.profile && Number.isFinite(room.temp_c) ? `${room.temp_c} °C` : null,
      Number.isFinite(room.setpoint_c) ? `Setpoint ${room.setpoint_c} °C` : null,
      stateLabel("Lamp", room.lamp_on),
      fan && (room.fan_simulated ? fan + " (simulated)" : fan)
    ], "room"),
    note("Routines", list(data.routines).map(routine =>
      [routine.text, routine.status, routine.evidence].filter(Boolean).join(" · "))),
    note("Recent activity", list(data.recent).slice(0, 5).map(entry =>
      [entry.time, entry.text].filter(Boolean).join(" · "))),
    note(summaryLabel(bedside?.interaction_summary), [bedside?.interaction_summary?.text]),
    note("Shift summary", [data.summary])
  ].filter(Boolean);
}

function renderHistory(data) {
  $("history-panel").hidden = !data;
  const observed = data && !Array.isArray(data.history);
  $("history-source").hidden = !data?.simulation && !observed;
  updateText($("history-source"), data?.simulation ? "Simulated history" : "From session and alert data");
  const history = list(data?.history ?? data?.observedHistory);
  const container = $("chat-history");
  const previous = container.firstElementChild;
  const samePatient = container.historyPatient === selected;
  const atEnd = !previous || previous.scrollHeight - previous.scrollTop - previous.clientHeight < 24;
  const previousTop = previous?.scrollTop ?? 0;
  updateRegion(container, [selected, history], () => {
    if (!history.length) return [element("p", "empty", "No chat history yet.")];
    const timeline = element("ol", "chat-timeline");
    timeline.id = "chat-timeline";
    timeline.tabIndex = 0;
    timeline.setAttribute("aria-label", "Patient interaction timeline");
    for (const entry of history) {
      const item = element("li", "chat-entry");
      const time = element(entry.time ? "time" : "span", "chat-time", entry.time || "—");
      const detail = element("div");
      detail.append(element("strong", "", entry.actor),
        element("span", "chat-action", entry.action), element("p", "", entry.text));
      item.append(time, detail);
      timeline.append(item);
    }
    return [timeline];
  });
  const timeline = $("chat-timeline");
  if (timeline && timeline !== previous) {
    timeline.scrollTop = !samePatient || atEnd ? timeline.scrollHeight : previousTop;
  }
  container.historyPatient = selected;
}

function renderPatientActions(agent, data) {
  const disabled = !data || agent.state !== "online" || data.connected === false || agent.busy;
  $("visit").hidden = !data;
  $("visit").disabled = disabled;
  $("patient-actions").hidden = !data;
  $("profile-details").hidden = !data;
  $("patient-screen").hidden = !data;
  $("patient-screen").href = agent.origin + "/patient";
  $("presence-form").hidden = !data?.bedside;
  updateText($("options-label"), data?.simulation ? "Two answer choices, one per line" : "Answer choices, one per line");
  $("presence").disabled = $("save-presence").disabled = disabled;
  $("question").disabled = $("options").disabled = $("send-question").disabled = disabled || data?.session?.waiting === true;
  $("suggest-choices").hidden = !data;
  $("suggest-choices").disabled = $("send-question").disabled;
  $("end-session").disabled = disabled;
  $("end-session").hidden = !data?.session?.active;
  const visitor = data?.bedside?.visitors_present;
  const visitorValue = agent.visitorDraft ?? (visitor === true ? "present" : visitor === false ? "absent" : "unknown");
  if ($("presence").value !== visitorValue) $("presence").value = visitorValue;
  if ($("question").value !== (agent.question ?? "")) $("question").value = agent.question ?? "";
  if ($("options").value !== (agent.options ?? "Yes\nNo")) $("options").value = agent.options ?? "Yes\nNo";
}

function renderLatestReply(data) {
  const region = $("latest-reply");
  const waiting = data?.session?.waiting;
  const latest = list(data?.session?.thread).at(-1);
  region.hidden = !waiting && latest?.answer == null;
  updateRegion(region, [waiting, latest], () => {
    if (region.hidden) return [];
    const status = element("strong", "", waiting ? "Waiting for a patient answer" : "Latest answer");
    if (!latest || (waiting && latest.answer != null)) return [status];
    const question = element("p", "muted", latest.question);
    return waiting ? [status, question] : [status, element("p", "reply-answer", latest.answer), question];
  });
}

function renderSimulation(agent, data) {
  const panel = $("simulation");
  panel.hidden = !data?.simulation;
  const choices = list(data?.profile?.care_choices);
  updateRegion(panel, [agent.origin, data?.simulation, data?.agent_checkin?.interval_s, choices,
    agent.state, agent.busy], () => {
    if (panel.hidden) return [];
    const rows = [element("h3", "", "Simulate patient input")];
    if (choices.length) {
      const label = element("label", "", "Care topic");
      label.htmlFor = "demo-care-choice";
      const select = element("select");
      select.id = "demo-care-choice";
      for (const choice of choices) {
        const option = element("option", "", choice.label);
        option.value = choice.id;
        select.append(option);
      }
      select.value = agent.careChoice || choices[0].id;
      select.addEventListener("change", () => { agent.careChoice = select.value; });
      const controls = element("div", "form-row");
      const push = button("Simulate patient request", "button", () =>
        act(agent, "/simulate/agent-request", { choice: select.value }, "The patient agent sent a request."));
      const draft = button("Suggest check-in", "button", () =>
        act(agent, "/check-in/suggest", { choice: select.value }, "Check-in queued for nurse review."));
      push.disabled = draft.disabled = select.disabled = agent.busy || agent.state !== "online";
      controls.append(push, draft);
      rows.push(label, select, controls);
    } else if (data.agent_checkin) {
      const push = button("Simulate agent request", "button", () =>
        act(agent, "/simulate/agent-request", {}, "The patient agent sent a request to the nurse."));
      push.disabled = agent.busy || agent.state !== "online";
      rows.push(push);
    }
    return rows;
  });
}

function renderSimulatedReply(agent, data) {
  const panel = $("simulated-reply");
  const question = data?.simulation && data.session?.waiting ? list(data.session.thread).at(-1) : null;
  panel.hidden = !question || question.answer != null;
  updateRegion(panel, [agent.origin, question, agent.state, agent.busy], () => {
    if (panel.hidden) return [];
    const controls = element("div", "form-row");
    for (const [spot, label] of [["left", "Left · " + question.options[0]],
      ["middle", "Center · Call nurse"], ["right", "Right · " + question.options[1]]]) {
      const control = button(label, "button", () =>
        act(agent, "/simulate/choose", { spot, qid: question.qid }, "Patient input sent."));
      control.id = "simulated-reply-" + spot;
      control.disabled = agent.busy || agent.state !== "online";
      controls.append(control);
    }
    return [element("h3", "", "Simulate patient reply"), controls];
  });
}

function render() {
  const roster = [...agents.values()];
  const connected = roster.filter(agent => agent.state === "online" && agent.data.connected !== false).length;
  const waiting = roster.every(agent => agent.state === "connecting");
  const connection = $("connection-summary");
  connection.dataset.state = waiting ? "waiting" : connected === roster.length ? "ready" : "partial";
  updateText(connection, waiting ? "Connecting to LookAt…" : `${connected} of ${roster.length} agents connected`);
  renderAlerts();
  renderRoster();
  renderShift(roster);
  renderProposals(roster);

  const agent = agents.get(selected);
  const data = agent?.data;
  updateText($("details-heading"), data ? agentName(agent) : "Patient status");
  updateText($("patient-status"), [data?.patient?.room, agentStatus(agent),
    data && agent.state !== "online" ? "Showing last received status" : null].filter(Boolean).join(" · "));
  renderPatientActions(agent, data);
  renderLatestReply(data);
  renderSimulatedReply(agent, data);
  renderSimulation(agent, data);
  renderHistory(data);
  updateRegion($("notes"), [selected, data?.profile, data?.bedside, data?.agent_checkin, data?.next_visit, data?.session, data?.facts, data?.room, data?.last_visit,
    data?.routines, data?.recent, data?.summary], () => {
    const notes = data ? patientNotes(data) : [];
    return notes.length ? notes : [element("p", "empty note", data ? "No status notes yet." : "Waiting for patient data…")];
  });
}

$("start-shift").addEventListener("click", () => {
  const agent = [...agents.values()].find(item => item.data?.shift);
  if (agent) act(agent, "/shift/start", {}, "Shift started.", () => {
    agent.data.shift.state = "running";
  });
});

$("end-shift").addEventListener("click", () => {
  const agent = [...agents.values()].find(item => item.data?.shift);
  if (agent) act(agent, "/shift/end", {}, "Shift ended.", () => {
    agent.data.shift.state = "complete";
  });
});

for (const name of ["patients", "attention"]) {
  const control = $(name + "-tab");
  control.addEventListener("click", () => showTab(name));
  control.addEventListener("keydown", event => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === "Home" ? "patients" : event.key === "End" ? "attention" :
      activeTab === "patients" ? "attention" : "patients";
    showTab(next, true);
  });
}

$("presence").addEventListener("change", event => { agents.get(selected).visitorDraft = event.target.value; });
$("presence-form").addEventListener("submit", event => {
  event.preventDefault();
  const agent = agents.get(selected);
  const present = { present: true, absent: false, unknown: null }[$("presence").value];
  act(agent, "/presence", { present }, "Visitor status saved for " + agentName(agent) + ".", () => {
    agent.data.bedside = { ...agent.data.bedside, visitors_present: present };
    delete agent.visitorDraft;
  });
});

for (const field of ["question", "options"]) {
  $(field).addEventListener("input", event => { agents.get(selected)[field] = event.target.value; });
}
$("suggest-choices").addEventListener("click", () => {
  const agent = agents.get(selected);
  const question = $("question").value.trim();
  if (!question) {
    $("question").focus();
    notify("Enter a question first.");
    return;
  }
  act(agent, "/suggest", { question }, result => result.source === "ai" ?
    "AI answer choices ready for review." : "Suggested answer choices ready for review.", result => {
    const options = list(result.options);
    agent.options = (agent.data.simulation ? options.slice(0, 2) : options).join("\n");
  });
});
$("question-form").addEventListener("submit", event => {
  event.preventDefault();
  const agent = agents.get(selected);
  const question = $("question").value.trim();
  const options = $("options").value.split(/\r?\n/).map(option => option.trim()).filter(Boolean);
  if (agent.data?.simulation && (options.length !== 2 || new Set(options).size !== 2)) {
    notify("Enter exactly two distinct answer choices for the simulation.");
    return;
  }
  if (!question || new Set(options).size < 2) {
    notify("Enter a question and at least two distinct answer choices.");
    return;
  }
  if (agent.data?.session?.waiting) return;
  act(agent, "/send", { question, options }, "Question sent to " + agentName(agent) + ".", () => {
    delete agent.question;
    delete agent.options;
    agent.data.session = { ...agent.data.session, active: true, waiting: true };
  });
});
$("end-session").addEventListener("click", () => {
  const agent = agents.get(selected);
  act(agent, "/session/end", {}, "Conversation ended for " + agentName(agent) + ".", () => {
    agent.data.session = { active: false, waiting: false };
  });
});

$("visit").addEventListener("click", () => {
  const agent = agents.get(selected);
  act(agent, "/visit", {}, "Visit logged for " + agentName(agent) + ".");
});

$("sound").addEventListener("click", async () => {
  const control = $("sound");
  control.disabled = true;
  try {
    if (soundEnabled) {
      soundEnabled = false;
    } else {
      const Audio = window.AudioContext || window.webkitAudioContext;
      if (!Audio) throw new Error("Audio unavailable");
      audio ??= new Audio();
      await audio.resume();
      soundEnabled = true;
      playAlertTone();
    }
    control.setAttribute("aria-pressed", String(soundEnabled));
    updateText(control, soundEnabled ? "Mute alert sound" : "Enable alert sound");
  } catch {
    notify("Alert sound is unavailable in this browser.");
  } finally {
    control.disabled = false;
  }
});

$("connect-form").addEventListener("submit", event => {
  event.preventDefault();
  const input = $("agent-url");
  try {
    const origin = agentOrigin(input.value.trim());
    addAgent(origin);
    selected = origin;
    $("profile-details").open = false;
    storeAgents();
    input.value = "";
    const setup = event.currentTarget.closest("details");
    setup.open = false;
    setup.querySelector("summary").focus();
    render();
  } catch (error) {
    notify(error instanceof TypeError ? "Enter a complete LookAt server URL." : error.message);
  }
});

addAgent("");
try {
  const saved = localStorage.getItem(savedAgentsKey) ?? sessionStorage.getItem(savedAgentsKey);
  for (const origin of list(JSON.parse(saved))) addAgent(agentOrigin(origin));
  storeAgents();
} catch {
  // Start with the local agent if saved addresses are unavailable.
}
render();
