const state = { runId: null, eventSource: null, artifacts: [], pickerPath: "." };
const $ = (id) => document.getElementById(id);

const el = {
  setupView: $("setup-view"),
  runView: $("run-view"),
  missionForm: $("mission-form"),
  directiveForm: $("directive-form"),
  openPicker: $("open-picker"),
  backBtn: $("back-to-setup"),
  pickerOverlay: $("picker-overlay"),
  pickerClose: $("picker-close"),
  pickerSelect: $("picker-select"),
  pickerList: $("picker-list"),
  pickerBreadcrumb: $("picker-breadcrumb"),
  pickerDrives: $("picker-drives"),
  pickerCurrentPath: $("picker-current-path"),
  timeline: $("timeline"),
  runStatus: $("run-status"),
  runScore: $("run-score"),
  activePhase: $("active-phase"),
  runIteration: $("run-iteration"),
  targetScoreDisplay: $("target-score-display"),
  bestScoreDisplay: $("best-score-display"),
  eventCountDisplay: $("event-count-display"),
  branchBoard: $("branch-board"),
  artifactList: $("artifact-list"),
  artifactViewer: $("artifact-viewer"),
  versionPill: $("version-pill"),
  wsInfo: $("workspace-info"),
  wsFiles: $("ws-files"),
  wsDirs: $("ws-dirs"),
  wsLangs: $("ws-langs"),
  wsData: $("ws-data"),
  wsDisplay: $("workspace-display"),
  wsPathDisplay: $("workspace-path-display"),
};

el.missionForm.addEventListener("submit", startRun);
el.directiveForm.addEventListener("submit", queueDirective);
el.openPicker.addEventListener("click", openPicker);
el.pickerClose.addEventListener("click", closePicker);
el.pickerSelect.addEventListener("click", selectFolder);
el.pickerOverlay.addEventListener("click", (e) => { if (e.target === el.pickerOverlay) closePicker(); });
el.backBtn.addEventListener("click", showSetup);
document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));

bootstrap();

async function bootstrap() {
  try {
    const r = await fetch("/api/runtime-defaults");
    if (r.ok) applyDefaults((await r.json()).defaults || {});
  } catch {}
  try {
    const r = await fetch("/api/health");
    if (r.ok) { const d = await r.json(); el.versionPill.textContent = `v${d.version}`; }
  } catch { el.versionPill.textContent = "offline"; }
}

function applyDefaults(d) {
  setVal("workspace-path", d.workspace_path);
  setVal("data-path", d.data_path);
  setVal("goal", d.goal);
  setVal("judge-brief", d.judge_brief);
  setVal("operator-brief", d.operator_brief);
  setVal("target-score", d.target_score);
  setVal("max-iterations", d.max_iterations);
  setVal("manager-descriptor", d.manager_descriptor);
  setVal("specialist-1-descriptor", d.specialist_1_descriptor);
  setVal("specialist-2-descriptor", d.specialist_2_descriptor);
  if (d.workspace_path) el.wsPathDisplay.textContent = d.workspace_path;
  const r = $("resume"); if (r && typeof d.resume === "boolean") r.checked = d.resume;
}

function showSetup() { el.setupView.classList.remove("hidden"); el.runView.classList.add("hidden"); }
function showRun() { el.setupView.classList.add("hidden"); el.runView.classList.remove("hidden"); }
function switchTab(name) {
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab-content").forEach((p) => p.classList.toggle("active", p.dataset.panel === name));
}

// ── Folder Picker ──
async function openPicker() {
  el.pickerOverlay.classList.remove("hidden");
  await browseTo($("workspace-path").value || ".");
}
function closePicker() { el.pickerOverlay.classList.add("hidden"); }
function selectFolder() {
  $("workspace-path").value = state.pickerPath;
  el.wsPathDisplay.textContent = state.pickerPath;
  closePicker();
  scanWorkspace(state.pickerPath);
}

async function browseTo(path) {
  const r = await fetch(`/api/browse?path=${encodeURIComponent(path)}`);
  if (!r.ok) return;
  const d = await r.json();
  state.pickerPath = d.current;
  el.pickerCurrentPath.textContent = d.current;

  if (d.drives?.length) {
    el.pickerDrives.classList.remove("hidden");
    el.pickerDrives.innerHTML = d.drives.map((x) => `<button class="drive-btn" data-d="${esc(x)}">${esc(x)}</button>`).join("");
    el.pickerDrives.querySelectorAll(".drive-btn").forEach((b) => b.addEventListener("click", () => browseTo(b.dataset.d)));
  }

  const parts = d.current.split("/").filter(Boolean);
  let html = "", acc = "";
  for (let i = 0; i < parts.length; i++) {
    acc += (i === 0 && !d.current.startsWith("/") ? "" : "/") + parts[i];
    if (i === 0 && parts[i].endsWith(":")) acc = parts[i] + "/";
    html += `<button class="crumb" data-p="${esc(acc)}">${esc(parts[i])}</button>`;
    if (i < parts.length - 1) html += `<span class="crumb-sep">/</span>`;
  }
  el.pickerBreadcrumb.innerHTML = html;
  el.pickerBreadcrumb.querySelectorAll(".crumb").forEach((b) => b.addEventListener("click", () => browseTo(b.dataset.p)));

  const dirs = d.entries.filter((e) => e.is_dir);
  const files = d.entries.filter((e) => !e.is_dir);
  let list = "";
  if (d.parent) list += `<button class="p-item" data-p="${esc(d.parent)}"><span class="p-icon">&#8593;</span><span>..</span></button>`;
  for (const x of dirs) list += `<button class="p-item" data-p="${esc(x.path)}"><span class="p-icon">&#128193;</span><span>${esc(x.name)}</span></button>`;
  for (const x of files) list += `<div class="p-item is-file"><span class="p-icon">&#128196;</span><span>${esc(x.name)}</span></div>`;
  if (!list) list = '<div class="empty">Empty folder</div>';
  el.pickerList.innerHTML = list;
  el.pickerList.querySelectorAll(".p-item[data-p]").forEach((b) => b.addEventListener("click", () => browseTo(b.dataset.p)));
  el.pickerList.scrollTop = 0;
}

async function scanWorkspace(path) {
  const r = await fetch("/api/workspace/analyze", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ workspace_path: path, data_path: $("data-path").value.trim() }) });
  if (!r.ok) return;
  const w = (await r.json()).workspace;
  el.wsInfo.classList.remove("hidden");
  el.wsFiles.textContent = `${w.relevant_files} files`;
  el.wsDirs.textContent = `${w.directories} folders`;
  el.wsLangs.textContent = w.languages.map((l) => l.name).join(", ") || "no code";
  el.wsData.textContent = w.data_exists ? "data found" : "no data";
}

// ── Run ──
async function startRun(e) {
  e.preventDefault();
  closeEvents();
  el.timeline.innerHTML = "";
  el.branchBoard.innerHTML = '<p class="empty">Waiting...</p>';
  el.artifactList.innerHTML = '<p class="empty">No files yet.</p>';
  el.artifactViewer.textContent = "Select a file to preview.";

  const payload = {
    goal: $("goal").value.trim(),
    judge_brief: $("judge-brief").value.trim(),
    operator_brief: $("operator-brief").value.trim(),
    workspace_path: $("workspace-path").value.trim() || ".",
    data_path: $("data-path").value.trim(),
    target_score: Number($("target-score").value),
    max_iterations: Number($("max-iterations").value),
    manager_descriptor: $("manager-descriptor").value.trim(),
    specialist_descriptors: [$("specialist-1-descriptor").value.trim(), $("specialist-2-descriptor").value.trim()].filter(Boolean),
    resume: $("resume").checked,
  };
  el.targetScoreDisplay.textContent = payload.target_score.toFixed(2);
  setStatus("Launching"); el.activePhase.textContent = "launching";
  showRun();

  const r = await fetch("/api/runs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  if (!r.ok) { setStatus("Failed"); addEvent({ phase: "error", actor: "system", message: await r.text(), score: null, timestamp: "", meta: {} }); return; }
  const result = await r.json();
  state.runId = result.run_id;
  setStatus(result.status); refreshRun(); connectEvents();
}

async function refreshRun() {
  if (!state.runId) return;
  const r = await fetch(`/api/runs/${state.runId}`);
  if (!r.ok) return;
  const d = await r.json();
  setStatus(d.status);
  el.runScore.textContent = (d.summary?.score ?? latestScore(d.events) ?? 0).toFixed(2);
  const m = d.metrics || {};
  el.bestScoreDisplay.textContent = Number(m.best_score || d.summary?.score || 0).toFixed(2);
  el.eventCountDisplay.textContent = String(m.event_count || 0);
  el.runIteration.textContent = String(m.current_iteration || 0);
  el.activePhase.textContent = m.active_phase || "idle";
  renderBranches(m.branch_scores || [], m.manager_scores || []);
  loadArtifacts();
}

function connectEvents() {
  if (!state.runId) return; closeEvents();
  state.eventSource = new EventSource(`/api/runs/${state.runId}/events`);
  state.eventSource.onmessage = async (msg) => {
    const ev = JSON.parse(msg.data); addEvent(ev);
    if (typeof ev.score === "number") el.runScore.textContent = ev.score.toFixed(2);
    if (ev.meta?.iteration !== undefined) el.runIteration.textContent = String(ev.meta.iteration);
    el.activePhase.textContent = ev.phase || "running";
    if (ev.phase === "run.finished" || ev.phase === "run.failed") { await refreshRun(); closeEvents(); return; }
    refreshRun();
  };
  state.eventSource.onerror = () => closeEvents();
}

async function queueDirective(e) {
  e.preventDefault(); if (!state.runId) return;
  const f = $("directive-message"), msg = f.value.trim(); if (!msg) return;
  const r = await fetch(`/api/runs/${state.runId}/directives`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: msg }) });
  if (r.ok) { f.value = ""; addEvent({ phase: "directive.sent", actor: "you", message: msg, score: null, timestamp: new Date().toISOString(), meta: {} }); }
}

async function loadArtifacts() {
  if (!state.runId) return;
  const r = await fetch(`/api/runs/${state.runId}/artifacts`); if (!r.ok) return;
  state.artifacts = (await r.json()).artifacts;
  if (!state.artifacts.length) { el.artifactList.innerHTML = '<p class="empty">No files yet.</p>'; return; }
  el.artifactList.innerHTML = "";
  for (const a of state.artifacts) {
    const b = document.createElement("button"); b.className = "file-btn";
    b.innerHTML = `<span>${esc(a.relative_path)}</span><span class="sz">${fmtBytes(a.size)}</span>`;
    b.addEventListener("click", () => openArtifact(a)); el.artifactList.appendChild(b);
  }
}

async function openArtifact(a) {
  el.artifactViewer.textContent = await (await fetch(a.download_url)).text();
  switchTab("files");
}

function renderBranches(br, mg) {
  const cards = [...br, ...mg].sort((a, b) => (b.iteration || 0) - (a.iteration || 0)).slice(0, 8);
  if (!cards.length) { el.branchBoard.innerHTML = '<p class="empty">Waiting for branches...</p>'; return; }
  el.branchBoard.innerHTML = cards.map((c) => `<div class="branch"><div class="branch-top"><span class="branch-role">${esc(c.role || "")}</span><span class="branch-score">${(c.score || 0).toFixed(2)}</span></div><div class="branch-actor">${esc(c.actor || "")}</div><div class="branch-round">Round ${c.iteration || 0}</div></div>`).join("");
}

function addEvent(ev) {
  if (!ev) return;
  const dot = getDot(ev);
  const score = typeof ev.score === "number" ? `<div class="event-score">${ev.score.toFixed(2)}</div>` : "";
  const iter = ev.meta?.iteration !== undefined ? ` r${ev.meta.iteration}` : "";
  const div = document.createElement("div"); div.className = "event";
  div.innerHTML = `<div class="event-dot ${dot}"></div><div class="event-body"><div class="event-phase">${esc(ev.phase || "")}${esc(iter)}</div><div class="event-message">${esc(ev.message || "")}</div></div><div class="event-right">${score}<div class="event-actor">${esc(ev.actor || "")}</div><div class="event-time">${fmtTime(ev.timestamp)}</div></div>`;
  el.timeline.appendChild(div); el.timeline.scrollTop = el.timeline.scrollHeight;
}

function getDot(ev) {
  const p = (ev.phase || ""), a = (ev.actor || "");
  if (p.includes("fail") || p.includes("error")) return "error";
  if (p.includes("evaluated")) return "judge";
  if (p.includes("specialist")) return "specialist";
  if (a.includes("manager") || p.includes("manager")) return "manager";
  return "system";
}

function setStatus(s) { el.runStatus.textContent = s || "Idle"; }
function closeEvents() { if (state.eventSource) { state.eventSource.close(); state.eventSource = null; } }
function latestScore(ev) { for (let i = (ev||[]).length - 1; i >= 0; i--) if (typeof ev[i].score === "number") return ev[i].score; return null; }
function setVal(id, v) { const f = $(id); if (f && v != null) f.value = String(v); }
function esc(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
function fmtBytes(b) { return b < 1024 ? b + "B" : b < 1048576 ? (b/1024).toFixed(1) + "K" : (b/1048576).toFixed(1) + "M"; }
function fmtTime(r) { if (!r) return ""; const d = new Date(r); return isNaN(d.getTime()) ? "" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }); }
