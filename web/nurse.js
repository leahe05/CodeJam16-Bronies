"use strict";

const nursePath = "/api/nurse";
const savedAgentsKey = "lookat.nurse.agents";
const $ = id => document.getElementById(id);
const list = value => Array.isArray(value) ? value.filter(item => item != null) : [];
const urgent = alert => alert.kind === "call" || alert.urgency === "urgent";
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
  const keyFor = alert => JSON.stringify([alert.id, alert.time, alert.text]);
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

function openPatient(origin) {
  selected = origin;
  showTab("patients");
  render();
  $("details-heading").focus();
}

function alertCard({ agent, alert }, handled) {
  const card = element("article", "alert-card");
  card.dataset.urgent = String(urgent(alert) && !handled);
  const meta = element("div", "alert-meta");
  meta.append(element("span", "alert-kind", alertKinds[alert.kind] || "Alert"),
    element("span", "alert-origin", alertOrigin(alert)),
    button([agentName(agent), agent.data?.patient?.room].filter(Boolean).join(" · "), "patient-link", () =>
      openPatient(agent.origin)));
  if (alert.time) meta.append(element("span", "", alert.time));
  if (agent.state !== "online") meta.append(element("span", "", "Last received"));
  card.append(meta, element("h3", "", alert.request || alert.text || "Patient alert"));
  if (!alert.request && alert.kind === "call") card.append(element("p", "muted", "Patient did not specify what they need."));
  if (alert.reason) card.append(element("p", "muted", alert.reason));
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
    .sort((left, right) => Number(urgent(right.alert)) - Number(urgent(left.alert)));
  const handled = entries.filter(entry => entry.alert.done);
  const signature = ({ agent, alert }) => [agent.origin, agentName(agent), agent.data?.patient?.room, agent.state, agent.busy, alert];
  const complete = [...agents.values()].every(agent => agent.state === "online" && agent.data.connected !== false);
  const empty = complete ? "No open alerts." : "Waiting for agent updates.";
  updateRegion($("alerts"), [empty, pending.map(signature)], () =>
    pending.length ? pending.map(entry => alertCard(entry, false)) : [element("p", "empty", empty)]);
  updateRegion($("handled"), handled.map(signature), () => handled.map(entry => alertCard(entry, true)));
  $("history").hidden = !handled.length;
  updateText($("history-label"), `Handled alerts (${handled.length})`);
  $("alert-count").hidden = !pending.length;
  updateText($("alert-count"), String(pending.length));
  $("alert-count").dataset.urgent = String(pending.some(entry => urgent(entry.alert)));
}

function renderShift(roster) {
  const patients = roster.filter(agent => agent.data?.shift);
  const first = patients[0];
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
  updateRegion($("shift-feeds"), patients.map(agent => [agentName(agent), agent.data.patient.room, agent.data.shift.feed]),
    () => patients.map(agent => {
      const card = element("article", "shift-feed");
      card.append(element("h3", "", [agentName(agent), agent.data.patient.room].filter(Boolean).join(" · ")));
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
  const bedside = data?.bedside;
  const temperature = data?.room?.temp_c;
  const summary = bedside?.interaction_summary;
  const vitals = vitalLines(bedside?.vitals);
  const metrics = element("span", "tile-metrics");
  for (const text of [awarenessLabel(bedside?.awareness), presenceLabel(bedside?.visitors_present),
    Number.isFinite(temperature) ? `Room ${temperature} °C` : "Room temperature unavailable"]) {
    metrics.append(element("span", "metric", text));
  }
  const vitalsLine = element("span", "tile-vitals");
  vitalsLine.append(element("strong", "", bedside?.vitals?.simulated ? "Simulated vitals" : "Vitals"),
    element("span", "", vitals.join(" · ") || "Unavailable"));
  const summaryLine = element("span", "tile-summary");
  const summaryHeading = element("span", "summary-heading");
  summaryHeading.append(element("strong", "", summaryLabel(summary)));
  if (summary?.time) summaryHeading.append(element("time", "", summary.time));
  summaryLine.append(summaryHeading, element("span", "", summary?.text || "No summary from the patient AI yet."));
  return [metrics, vitalsLine, summaryLine];
}

function renderRoster() {
  const roster = [...agents.values()];
  updateRegion($("patients"), [selected, roster.map(agent =>
    [agent.origin, agentName(agent), agent.data?.patient?.room, agentStatus(agent), agent.busy,
      agent.data?.bedside, agent.data?.room?.temp_c])], () =>
    roster.map(agent => {
      const item = element("li", "patient-item");
      const tile = button("", "patient-tile", () => openPatient(agent.origin));
      tile.id = "patient-" + encodeURIComponent(agent.origin);
      tile.setAttribute("aria-pressed", String(agent.origin === selected));
      tile.append(element("strong", "patient-name", agentName(agent)),
        element("span", "patient-meta", [agent.data?.patient?.room, agentStatus(agent)].filter(Boolean).join(" · ")));
      if (agent.data) tile.append(...tileStatus(agent.data));
      item.append(tile);
      if (agent.origin && !agent.demo) {
        const disconnect = button("Disconnect", "disconnect", () => {
          clearTimeout(agent.timer);
          agents.delete(agent.origin);
          if (selected === agent.origin) selected = "";
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

function patientNotes(data) {
  const room = data.room || {};
  const session = data.session || {};
  const bedside = data.bedside;
  const fan = stateLabel("Fan", room.fan_on);
  const checkIn = data.agent_checkin;
  return [
    bedside && note("Bedside status", [
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
    note("Conversation", [
      session.waiting ? "Waiting for a patient answer" : session.active ? "Session active" : null,
    ]),
    note("Patient preferences", list(data.facts).map(fact => fact.text)),
    note("Room status", [
      Number.isFinite(room.temp_c) ? `${room.temp_c} °C` : null,
      Number.isFinite(room.setpoint_c) ? `Setpoint ${room.setpoint_c} °C` : null,
      stateLabel("Lamp", room.lamp_on),
      fan && (room.fan_simulated ? fan + " (simulated)" : fan)
    ], "room"),
    note("Routines", list(data.routines).map(routine =>
      [routine.text, routine.status, routine.evidence].filter(Boolean).join(" · "))),
    note("Recent activity", list(data.recent).slice(0, 5).map(entry =>
      [entry.time, entry.text].filter(Boolean).join(" · "))),
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

function renderSimulation(agent, data) {
  const panel = $("simulation");
  panel.hidden = !data;
  const q = data?.session?.waiting ? list(data.session.thread).at(-1) : null;
  updateRegion(panel, [agent.origin, data?.simulation, data?.tracked_demo, data?.agent_checkin?.interval_s,
    q, agent.state, agent.busy], () => {
    if (!data) return [];
    const link = element("a", "button", "Open patient screen");
    link.href = agent.origin + "/patient";
    link.target = "_blank";
    link.rel = "noopener";
    if (!data.simulation) return [link];
    const rows = [element("h3", "", "Simulate patient input"), link];
    if (data.agent_checkin) {
      const push = button("Simulate agent request", "button", () =>
        act(agent, "/simulate/agent-request", {}, "The patient agent sent a request to the nurse."));
      push.disabled = agent.busy || agent.state !== "online";
      rows.push(push);
    }
    if (q) {
      const controls = element("div", "form-row");
      for (const [spot, label] of [["left", "Left · " + q.options[0]], ["middle", "Center · Call nurse"], ["right", "Right · " + q.options[1]]]) {
        const control = button(label, "button", () =>
          act(agent, "/simulate/choose", { spot, qid: q.qid }, "Patient input sent."));
        control.disabled = agent.busy || agent.state !== "online";
        controls.append(control);
      }
      rows.push(controls);
    }
    return rows;
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
    data?.last_visit ? "Last visit " + data.last_visit : null,
    data && agent.state !== "online" ? "Showing last received status" : null].filter(Boolean).join(" · "));
  renderPatientActions(agent, data);
  renderSimulation(agent, data);
  renderHistory(data);
  updateRegion($("notes"), [selected, data?.bedside, data?.agent_checkin, data?.next_visit, data?.session, data?.facts, data?.room,
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
