"use strict";

const ROADMAP_STATUSES = ["Active", "Planned", "Completed", "Aborted"];
const SPRINT_STATUSES = ["Planned", "Open", "Closed", "Aborted"];
const DATA_URL = new URL('api/data', new URL('.', location.href));

const state = {
  data: { schemaVersion: 1, roadmaps: [], sprints: [] },
  revision: null,
  editorRevision: null,
  evidenceBase: null,
  archiveVisible: false,
  noticeTimer: null,
};

const elements = {
  active: document.querySelector("#active-roadmaps"),
  planned: document.querySelector("#planned-roadmaps"),
  archived: document.querySelector("#archived-roadmaps"),
  activeCount: document.querySelector("#active-count"),
  plannedCount: document.querySelector("#planned-count"),
  openSprintCount: document.querySelector("#open-sprint-count"),
  archivedCount: document.querySelector("#archived-count"),
  notice: document.querySelector("#notice"),
  connectionDot: document.querySelector("#connection-dot"),
  connectionLabel: document.querySelector("#connection-label"),
  toggleArchive: document.querySelector("#toggle-archive"),
  dialog: document.querySelector("#editor-dialog"),
  form: document.querySelector("#editor-form"),
  recordType: document.querySelector("#record-type"),
  recordId: document.querySelector("#record-id"),
  recordName: document.querySelector("#record-name"),
  recordDescription: document.querySelector("#record-description"),
  recordStatus: document.querySelector("#record-status"),
  recordRoadmap: document.querySelector("#record-roadmap"),
  recordSequence: document.querySelector("#record-sequence"),
  dialogTitle: document.querySelector("#dialog-title"),
  dialogKicker: document.querySelector("#dialog-kicker"),
};

function cloneData() {
  return JSON.parse(JSON.stringify(state.data));
}

function showNotice(message, type = "success", persistent = false) {
  const editorNotice = document.querySelector('#editor-notice');
  if (elements.dialog.open && type === 'error') { editorNotice.textContent = message; editorNotice.hidden = false; }
  clearTimeout(state.noticeTimer);
  elements.notice.textContent = message;
  elements.notice.className = type === "error" ? "notice error" : "notice";
  elements.notice.hidden = false;
  if (!persistent) {
    state.noticeTimer = setTimeout(() => {
      elements.notice.hidden = true;
    }, 4200);
  }
}

function setConnection(online, label) {
  elements.connectionDot.className = `connection-dot ${online ? "online" : "offline"}`;
  elements.connectionLabel.textContent = label;
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, { cache: "no-store", ...options });
  let payload;
  try {
    payload = await response.json();
  } catch {
    payload = { error: `Unexpected server response (${response.status})` };
  }
  if (!response.ok) {
    const detail = Array.isArray(payload.details) ? ` ${payload.details.join("; ")}` : "";
    const error = new Error(`${payload.error || "Request failed"}${detail}`);
    error.status = response.status;
    throw error;
  }
  return payload;
}

async function loadData({ quiet = false } = {}) {
  try {
    const payload = await requestJson(DATA_URL);
    const changed = payload.revision !== state.revision;
    state.data = payload.data;
    state.revision = payload.revision;
    state.evidenceBase = payload.evidenceBase;
    document.querySelector('#scope-label').textContent = payload.scope.label;
    document.querySelector('#data-source').textContent = payload.scope.source;
    document.querySelector('#scope-notice').textContent = payload.scope.notice;
    const links = document.querySelector('#project-links'); links.replaceChildren();
    for (const item of payload.links || []) {
      const link = document.createElement('a'); link.textContent = item.label; link.href = item.href;
      links.append(link, document.createTextNode(' · '));
    }
    setConnection(true, "Data connected");
    if (changed) render();
    if (!quiet) showNotice("Tracker refreshed from roadmaps.json.");
  } catch (error) {
    setConnection(false, "Data needs attention");
    showNotice(error.message, "error", true);
  }
}

async function saveData(nextData, message, expectedRevision = state.revision) {
  try {
    const payload = await requestJson(DATA_URL, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ data: nextData, expectedRevision }),
    });
    state.data = nextData;
    state.revision = payload.revision;
    setConnection(true, "Data saved");
    render();
    showNotice(message);
    return true;
  } catch (error) {
    if (error.status === 409) {
      showNotice("The JSON file changed outside this window. Your edit was not applied. The latest data has been loaded; close and reopen the editor to review it. Your entered fields are still here.", "error", true);
      await loadData({ quiet: true });
    } else {
      showNotice(error.message, "error", true);
    }
    render();
    return false;
  }
}

function option(value, label = value) {
  const item = document.createElement("option");
  item.value = value;
  item.textContent = label;
  return item;
}

function statusSelect(statuses, current, accessibleLabel, onChange) {
  const select = document.createElement("select");
  select.className = "status-select";
  select.setAttribute("aria-label", accessibleLabel);
  statuses.forEach((status) => select.append(option(status)));
  select.value = current;
  select.dataset.status = current;
  select.addEventListener("change", async () => {
    select.disabled = true;
    await onChange(select.value);
  });
  return select;
}

function smallButton(label, className, handler) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `button button-small ${className}`;
  button.textContent = label;
  button.addEventListener("click", handler);
  return button;
}

function sortedRoadmaps(statuses) {
  return state.data.roadmaps
    .filter((roadmap) => statuses.includes(roadmap.status))
    .sort((left, right) => left.name.localeCompare(right.name, undefined, { sensitivity: "base" }));
}

function sprintsFor(roadmapId) {
  return state.data.sprints
    .filter((sprint) => sprint.roadmapId === roadmapId)
    .sort((left, right) => left.sequence - right.sequence || left.name.localeCompare(right.name));
}

function updateRoadmapStatus(roadmapId, status) {
  const next = cloneData();
  const roadmap = next.roadmaps.find((item) => item.id === roadmapId);
  roadmap.status = status;
  return saveData(next, `${roadmap.name} is now ${status}.`);
}

function updateSprintStatus(sprintId, status) {
  const next = cloneData();
  const sprint = next.sprints.find((item) => item.id === sprintId);
  sprint.status = status;
  return saveData(next, `${sprint.name} is now ${status}.`);
}

function trackingLine(record) {
  // Schema 2 tracking fields (owner, updated, next): shown when present, never required.
  const parts = [];
  if (record.owner) parts.push(`Owner: ${record.owner}`);
  if (record.updated) parts.push(`Updated: ${record.updated}`);
  if (record.next) parts.push(`Next: ${record.next}`);
  if (!parts.length) return null;
  const line = document.createElement("span");
  line.className = "tracking-line";
  line.textContent = parts.join(" · ");
  return line;
}

function createSprintRow(sprint) {
  const row = document.createElement("div");
  row.className = "sprint-row";

  const sequence = document.createElement("span");
  sequence.className = "sequence";
  sequence.textContent = sprint.sequence;

  const copy = document.createElement("span");
  copy.className = "sprint-copy";
  const name = document.createElement("strong");
  name.className = "sprint-name";
  name.textContent = sprint.name;
  const description = document.createElement("span");
  description.className = "sprint-description";
  description.textContent = sprint.description;
  copy.append(name, description);
  const sprintTracking = trackingLine(sprint);
  if (sprintTracking) copy.append(sprintTracking);
  const published = state.data.roadmaps.find(r => r.id === sprint.roadmapId);
  if (published?.publication && state.evidenceBase) {
    const link = document.createElement('a'); link.textContent = 'Current Sprint evidence';
    link.href = state.evidenceBase + '&roadmap_id=' + encodeURIComponent(published.id) + '&sprint_id=' + encodeURIComponent(sprint.id.slice(published.id.length + 1));
    copy.append(link);
  }

  const status = statusSelect(
    SPRINT_STATUSES,
    sprint.status,
    `Status for ${sprint.name}`,
    (value) => updateSprintStatus(sprint.id, value),
  );
  const edit = smallButton("Edit", "button-quiet", () => openSprintEditor(sprint.roadmapId, sprint.id));
  edit.hidden = Boolean(state.data.roadmaps.find(r => r.id === sprint.roadmapId)?.publication);
  row.append(sequence, copy, status, edit);
  return row;
}

function createRoadmapCard(roadmap) {
  const card = document.createElement("article");
  card.className = "roadmap-card";

  const main = document.createElement("div");
  main.className = "roadmap-main";
  const summary = document.createElement("div");
  summary.className = "roadmap-summary";
  const titleRow = document.createElement("div");
  titleRow.className = "roadmap-title-row";
  const title = document.createElement("h3");
  title.textContent = roadmap.name;
  const status = statusSelect(
    ROADMAP_STATUSES,
    roadmap.status,
    `Status for ${roadmap.name}`,
    (value) => updateRoadmapStatus(roadmap.id, value),
  );
  titleRow.append(title, status);

  const description = document.createElement("p");
  description.className = "roadmap-description";
  description.textContent = roadmap.description;
  const roadmapTracking = trackingLine(roadmap);
  if (roadmapTracking) description.append(document.createElement("br"), roadmapTracking);
  const actions = document.createElement("div");
  actions.className = "card-actions";
  actions.append(
    smallButton("Edit roadmap", "button-quiet", () => openRoadmapEditor(roadmap.id)),
    smallButton("Add sprint", "button-secondary", () => openSprintEditor(roadmap.id)),
  );
  if (roadmap.publication) {
    actions.replaceChildren();
    const p = roadmap.publication, a = p.approved_plan;
    const label = document.createElement('p');
    label.textContent = `Published approval snapshot · ${a.evidence_kind} · plan version ${a.candidate.version}. Tracking status does not establish current authority.`;
    const scope = document.createElement('p'); scope.textContent = 'Approval scope: ' + a.scope;
    const details = document.createElement('details'), heading = document.createElement('summary'), evidence = document.createElement('pre');
    heading.textContent = 'Reviewed proposal, findings and exact approval';
    evidence.textContent = JSON.stringify(p, null, 2); evidence.style.whiteSpace = 'pre-wrap'; evidence.style.overflowWrap = 'anywhere';
    details.append(heading, evidence); actions.append(label, scope, details);
    if (state.evidenceBase) {
      const link = document.createElement('a'); link.textContent = 'Current staffing and execution evidence';
      link.href = state.evidenceBase + '&roadmap_id=' + encodeURIComponent(roadmap.id); actions.append(link);
    } else {
      const note = document.createElement('p'); note.textContent = 'Current execution evidence is available through Mission Control. This standalone view retains the published approval snapshot.'; actions.append(note);
    }
  }
  if (roadmap.status !== "Aborted") {
    actions.append(smallButton("Abort", "button-danger", async () => {
      if (window.confirm(`Move “${roadmap.name}” to the archive as Aborted?`)) {
        await updateRoadmapStatus(roadmap.id, "Aborted");
      }
    }));
  }
  summary.append(titleRow, description, actions);

  const chainPanel = document.createElement("div");
  chainPanel.className = "chain-panel";
  const chainHeader = document.createElement("div");
  chainHeader.className = "chain-header";
  const chainLabel = document.createElement("span");
  chainLabel.className = "chain-label";
  const sprints = sprintsFor(roadmap.id);
  chainLabel.textContent = `Sprint chain · ${sprints.length}`;
  chainHeader.append(chainLabel);
  const chain = document.createElement("div");
  chain.className = "sprint-chain";
  if (sprints.length) {
    sprints.forEach((sprint) => chain.append(createSprintRow(sprint)));
  } else {
    const empty = document.createElement("div");
    empty.className = "empty-chain";
    empty.textContent = "No sprints in this chain yet.";
    chain.append(empty);
  }
  chainPanel.append(chainHeader, chain);
  main.append(summary, chainPanel);
  card.append(main);
  return card;
}

function renderGroup(container, roadmaps, emptyMessage) {
  container.replaceChildren();
  if (!roadmaps.length) {
    const empty = document.createElement("div");
    empty.className = "empty-group";
    empty.textContent = emptyMessage;
    container.append(empty);
    return;
  }
  roadmaps.forEach((roadmap) => container.append(createRoadmapCard(roadmap)));
}

function render() {
  const active = sortedRoadmaps(["Active"]);
  const planned = sortedRoadmaps(["Planned"]);
  const archived = sortedRoadmaps(["Completed", "Aborted"]);
  renderGroup(elements.active, active, "No active roadmaps.");
  renderGroup(elements.planned, planned, "No planned roadmaps.");
  renderGroup(elements.archived, archived, "The archive is empty.");
  elements.archived.hidden = !state.archiveVisible;
  elements.toggleArchive.textContent = state.archiveVisible ? "Hide archive" : "Show archive";
  elements.toggleArchive.setAttribute("aria-expanded", String(state.archiveVisible));
  elements.activeCount.textContent = active.length;
  elements.plannedCount.textContent = planned.length;
  elements.archivedCount.textContent = archived.length;
  elements.openSprintCount.textContent = state.data.sprints.filter((sprint) => sprint.status === "Open").length;
  document.querySelector("#add-sprint").disabled = !state.data.roadmaps.some(r => !r.publication);
}

function populateStatusOptions(statuses, selected) {
  elements.recordStatus.replaceChildren();
  statuses.forEach((status) => elements.recordStatus.append(option(status)));
  elements.recordStatus.value = selected;
}

function populateRoadmapOptions(selected) {
  elements.recordRoadmap.replaceChildren();
  [...state.data.roadmaps]
    .filter(roadmap => !roadmap.publication)
    .sort((left, right) => left.name.localeCompare(right.name))
    .forEach((roadmap) => elements.recordRoadmap.append(option(roadmap.id, roadmap.name)));
  elements.recordRoadmap.value = selected;
}

function setSprintFields(visible) {
  document.querySelectorAll(".sprint-field").forEach((field) => {
    field.hidden = !visible;
  });
  elements.recordRoadmap.required = visible;
  elements.recordSequence.required = visible;
}

function openRoadmapEditor(roadmapId = null) {
  document.querySelector('#editor-notice').hidden = true;
  state.editorRevision = state.revision;
  const roadmap = roadmapId ? state.data.roadmaps.find((item) => item.id === roadmapId) : null;
  elements.form.reset();
  elements.recordType.value = "roadmap";
  elements.recordId.value = roadmap?.id || "";
  elements.recordName.value = roadmap?.name || "";
  elements.recordDescription.value = roadmap?.description || "";
  populateStatusOptions(ROADMAP_STATUSES, roadmap?.status || "Planned");
  setSprintFields(false);
  elements.dialogKicker.textContent = "Roadmap record";
  elements.dialogTitle.textContent = roadmap ? "Edit roadmap" : "Create roadmap";
  elements.dialog.showModal();
  elements.recordName.focus();
}

function openSprintEditor(roadmapId = null, sprintId = null) {
  document.querySelector('#editor-notice').hidden = true;
  state.editorRevision = state.revision;
  if (!state.data.roadmaps.some(r => !r.publication)) {
    showNotice("Create a roadmap before adding its first sprint.", "error");
    return;
  }
  const sprint = sprintId ? state.data.sprints.find((item) => item.id === sprintId) : null;
  const selectedRoadmap = sprint?.roadmapId || roadmapId || state.data.roadmaps.find(r => !r.publication).id;
  const nextSequence = sprintsFor(selectedRoadmap).length + 1;
  elements.form.reset();
  elements.recordType.value = "sprint";
  elements.recordId.value = sprint?.id || "";
  elements.recordName.value = sprint?.name || "";
  elements.recordDescription.value = sprint?.description || "";
  populateStatusOptions(SPRINT_STATUSES, sprint?.status || "Planned");
  populateRoadmapOptions(selectedRoadmap);
  elements.recordSequence.value = sprint?.sequence || nextSequence;
  setSprintFields(true);
  elements.dialogKicker.textContent = "Sprint record";
  elements.dialogTitle.textContent = sprint ? "Edit sprint" : "Add to sprint chain";
  elements.dialog.showModal();
  elements.recordName.focus();
}

function uniqueId(prefix, name, records) {
  const slug = name.toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 42) || prefix;
  const used = new Set(records.map((record) => record.id));
  let candidate = `${prefix}-${slug}`;
  let number = 2;
  while (used.has(candidate)) {
    candidate = `${prefix}-${slug}-${number}`;
    number += 1;
  }
  return candidate;
}

function normalizeChain(data, roadmapId) {
  data.sprints
    .filter((sprint) => sprint.roadmapId === roadmapId)
    .sort((left, right) => left.sequence - right.sequence || left.name.localeCompare(right.name))
    .forEach((sprint, index) => { sprint.sequence = index + 1; });
}

function saveRoadmapForm() {
  const next = cloneData();
  const id = elements.recordId.value;
  const previous = id ? next.roadmaps.find((item) => item.id === id) : null;
  const record = {
    ...(previous || {}),
    id: id || uniqueId("roadmap", elements.recordName.value, next.roadmaps),
    name: elements.recordName.value.trim(),
    description: elements.recordDescription.value.trim(),
    status: elements.recordStatus.value,
  };
  if (id) {
    const index = next.roadmaps.findIndex((item) => item.id === id);
    next.roadmaps[index] = record;
  } else {
    next.roadmaps.push(record);
  }
  return { next, message: `${record.name} was ${id ? "updated" : "created"}.` };
}

function saveSprintForm() {
  const next = cloneData();
  const id = elements.recordId.value;
  const old = id ? next.sprints.find((item) => item.id === id) : null;
  if (old) {
    next.sprints = next.sprints.filter((item) => item.id !== id);
    normalizeChain(next, old.roadmapId);
  }
  const roadmapId = elements.recordRoadmap.value;
  const target = next.sprints
    .filter((sprint) => sprint.roadmapId === roadmapId)
    .sort((left, right) => left.sequence - right.sequence);
  const requested = Number.parseInt(elements.recordSequence.value, 10);
  const position = Math.max(1, Math.min(Number.isInteger(requested) ? requested : target.length + 1, target.length + 1));
  const record = {
    ...(old || {}),
    id: id || uniqueId("sprint", elements.recordName.value, next.sprints),
    roadmapId,
    name: elements.recordName.value.trim(),
    description: elements.recordDescription.value.trim(),
    status: elements.recordStatus.value,
    sequence: position,
  };
  target.splice(position - 1, 0, record);
  target.forEach((sprint, index) => { sprint.sequence = index + 1; });
  const targetIds = new Set(target.map((sprint) => sprint.id));
  next.sprints = next.sprints.filter((sprint) => !targetIds.has(sprint.id));
  next.sprints.push(...target);
  return { next, message: `${record.name} was ${id ? "updated" : "added to its roadmap"}.` };
}

elements.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!elements.form.reportValidity()) return;
  const result = elements.recordType.value === "roadmap" ? saveRoadmapForm() : saveSprintForm();
  const saved = await saveData(result.next, result.message, state.editorRevision);
  if (saved) elements.dialog.close();
});

document.querySelector("#add-roadmap").addEventListener("click", () => openRoadmapEditor());
document.querySelector("#add-sprint").addEventListener("click", () => openSprintEditor());
document.querySelector("#refresh-data").addEventListener("click", () => loadData());
document.querySelector("#close-dialog").addEventListener("click", () => elements.dialog.close());
document.querySelector("#cancel-dialog").addEventListener("click", () => elements.dialog.close());
elements.toggleArchive.addEventListener("click", () => {
  state.archiveVisible = !state.archiveVisible;
  render();
});

window.addEventListener("focus", () => {
  if (!elements.dialog.open) loadData({ quiet: true });
});
setInterval(() => {
  if (document.visibilityState === "visible" && !elements.dialog.open) loadData({ quiet: true });
}, 15000);

loadData({ quiet: true });
