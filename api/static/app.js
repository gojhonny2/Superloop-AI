const state = { runId: null, eventSource: null, artifacts: [], pickerPath: ".", scannedModels: null, seenEvents: new Set() };
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
  wsDisplay: $("workspace-display"),
  wsPathDisplay: $("workspace-path-display"),
  scanIndicator: $("scan-indicator"),
  managerSelect: $("manager-descriptor"),
  spec1Select: $("specialist-1-descriptor"),
  spec2Select: $("specialist-2-descriptor"),
  managerCustom: $("manager-descriptor-custom"),
  spec1Custom: $("specialist-1-descriptor-custom"),
  spec2Custom: $("specialist-2-descriptor-custom"),
  analysisSection: $("analysis-section"),
  analysisLoading: $("analysis-loading"),
  analysisResults: $("analysis-results"),
  missionSection: $("mission-section"),
  settingsSection: $("settings-section"),
  launchBtn: $("launch-btn"),
  suggestionsBlock: $("suggestions-block"),
  suggestionsList: $("suggestions-list"),
};

el.missionForm.addEventListener("submit", startRun);
el.directiveForm.addEventListener("submit", queueDirective);
el.openPicker.addEventListener("click", openPicker);
el.pickerClose.addEventListener("click", closePicker);
el.pickerSelect.addEventListener("click", selectFolder);
el.pickerOverlay.addEventListener("click", (e) => { if (e.target === el.pickerOverlay) closePicker(); });
el.backBtn.addEventListener("click", showSetup);
document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));

[el.managerSelect, el.spec1Select, el.spec2Select].forEach((sel) => {
  const customInput = $(sel.id + "-custom");
  sel.addEventListener("change", () => {
    if (sel.value === "__custom__") {
      customInput.classList.remove("hidden");
      customInput.classList.add("visible");
      customInput.focus();
    } else {
      customInput.classList.add("hidden");
      customInput.classList.remove("visible");
      customInput.value = "";
    }
  });
});

function fmtTokens(n) {
  const v = Number(n);
  if (v >= 1_000_000) return (v / 1_000_000).toFixed(v % 1_000_000 === 0 ? 0 : 2).replace(/\.?0+$/, "") + "M";
  if (v >= 1000) return (v / 1000).toFixed(v % 1000 === 0 ? 0 : 1).replace(/\.0$/, "") + "k";
  return String(v);
}
[
  ["manager-max-tokens", "manager-max-tokens-label"],
  ["specialist-1-max-tokens", "specialist-1-max-tokens-label"],
  ["specialist-2-max-tokens", "specialist-2-max-tokens-label"],
].forEach(([inputId, labelId]) => {
  const input = $(inputId), label = $(labelId);
  if (!input || !label) return;
  const sync = () => { label.textContent = fmtTokens(input.value); };
  input.addEventListener("input", sync);
  sync();
});

bootstrap();

async function bootstrap() {
  let defaults = {};
  try {
    const r = await fetch("/api/runtime-defaults");
    if (r.ok) { defaults = (await r.json()).defaults || {}; applyDefaults(defaults); }
  } catch {}
  try {
    const r = await fetch("/api/health");
    if (r.ok) { const d = await r.json(); el.versionPill.textContent = `v${d.version}`; }
  } catch { el.versionPill.textContent = "offline"; }
  await scanModels();
}

function applyDefaults(d) {
  setVal("workspace-path", d.workspace_path);
  setVal("data-path", d.data_path);
  setVal("goal", d.goal);
  setVal("judge-brief", d.judge_brief);
  setVal("operator-brief", d.operator_brief);
  setVal("target-score", d.target_score);
  setVal("max-iterations", d.max_iterations);
  if (d.workspace_path) el.wsPathDisplay.textContent = d.workspace_path;
  const r = $("resume"); if (r && typeof d.resume === "boolean") r.checked = d.resume;
  state.defaultManager = d.manager_descriptor || "";
  state.defaultSpec1 = d.specialist_1_descriptor || "";
  state.defaultSpec2 = d.specialist_2_descriptor || "";
}

function showSetup() { el.setupView.classList.remove("hidden"); el.runView.classList.add("hidden"); }
function showRun() { el.setupView.classList.add("hidden"); el.runView.classList.remove("hidden"); }
function switchTab(name) {
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab-content").forEach((p) => p.classList.toggle("active", p.dataset.panel === name));
}

// ── Model Scanning ──
async function scanModels() {
  el.scanIndicator.textContent = "Scanning providers...";
  el.scanIndicator.className = "scan-indicator";
  try {
    const r = await fetch("/api/models/scan");
    if (!r.ok) throw new Error("Scan failed");
    const data = await r.json();
    state.scannedModels = data.providers;
    const active = data.providers.filter((p) => p.status === "ok" && p.models.length);
    const count = active.reduce((s, p) => s + p.models.length, 0);
    el.scanIndicator.textContent = `${count} models from ${active.length} provider${active.length !== 1 ? "s" : ""}`;
    el.scanIndicator.className = "scan-indicator done";
    populateModelSelect(el.managerSelect, data.providers, state.defaultManager || "gemini:gemini-1.5-pro", "manager");
    populateModelSelect(el.spec1Select, data.providers, state.defaultSpec1 || "openai:gpt-4o", "specialist");
    populateModelSelect(el.spec2Select, data.providers, state.defaultSpec2 || "anthropic:claude-3-5-sonnet-20240620", "specialist");
  } catch {
    el.scanIndicator.textContent = "Could not scan — using defaults";
    el.scanIndicator.className = "scan-indicator error";
    populateFallback(el.managerSelect, state.defaultManager || "gemini:gemini-1.5-pro");
    populateFallback(el.spec1Select, state.defaultSpec1 || "openai:gpt-4o");
    populateFallback(el.spec2Select, state.defaultSpec2 || "anthropic:claude-3-5-sonnet-20240620");
  }
}

function populateModelSelect(selectEl, providers, defaultValue, role) {
  selectEl.innerHTML = "";
  const isManager = role === "manager";
  for (const p of providers) {
    if (p.status !== "ok" || !p.models.length) continue;
    const paidModels = [];
    const freeModels = [];
    for (const m of p.models) {
      if (/:free$/i.test(m) || /free$/i.test(m)) freeModels.push(m);
      else paidModels.push(m);
    }
    if (paidModels.length) {
      const group = document.createElement("optgroup");
      group.label = p.label;
      for (const m of paidModels) {
        const opt = document.createElement("option");
        const descriptor = `${p.provider}:${m}`;
        opt.value = descriptor;
        opt.textContent = m;
        if (descriptor === defaultValue) opt.selected = true;
        group.appendChild(opt);
      }
      selectEl.appendChild(group);
    }
    if (freeModels.length) {
      const group = document.createElement("optgroup");
      group.label = `${p.label} — free tier ${isManager ? "(not recommended for Manager)" : ""}`;
      for (const m of freeModels) {
        const opt = document.createElement("option");
        const descriptor = `${p.provider}:${m}`;
        opt.value = descriptor;
        opt.textContent = isManager ? `⚠ ${m}` : m;
        if (descriptor === defaultValue) opt.selected = true;
        group.appendChild(opt);
      }
      selectEl.appendChild(group);
    }
  }
  const customOpt = document.createElement("option");
  customOpt.value = "__custom__";
  customOpt.textContent = "Custom (type manually)";
  selectEl.appendChild(customOpt);
  if (defaultValue && !selectEl.querySelector(`option[value="${CSS.escape(defaultValue)}"]`)) {
    const modelName = defaultValue.split(":").slice(1).join(":") || defaultValue;
    const opt = document.createElement("option");
    opt.value = defaultValue;
    opt.textContent = `${modelName} (configured)`;
    opt.selected = true;
    selectEl.insertBefore(opt, customOpt);
  }
}

function populateFallback(selectEl, defaultValue) {
  selectEl.innerHTML = "";
  const opt = document.createElement("option");
  opt.value = defaultValue; opt.textContent = defaultValue; opt.selected = true;
  selectEl.appendChild(opt);
  const customOpt = document.createElement("option");
  customOpt.value = "__custom__"; customOpt.textContent = "Custom (type manually)";
  selectEl.appendChild(customOpt);
}

function getModelValue(selectEl) {
  const customInput = $(selectEl.id + "-custom");
  if (selectEl.value === "__custom__" && customInput) return customInput.value.trim();
  return selectEl.value;
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
  deepAnalyze(state.pickerPath);
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

// ── Deep Workspace Analysis ──
async function deepAnalyze(path) {
  // Show analysis section with loading
  el.analysisSection.classList.remove("hidden");
  el.analysisLoading.classList.remove("hidden");
  el.analysisResults.classList.add("hidden");
  el.missionSection.classList.add("hidden");
  el.settingsSection.classList.add("hidden");
  el.launchBtn.classList.add("hidden");

  try {
    const r = await fetch("/api/workspace/deep-analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ workspace_path: path }),
    });
    if (!r.ok) throw new Error("Analysis failed");
    const data = await r.json();
    const w = data.workspace;

    // Fill analysis results
    $("a-type").textContent = w.project_type.replace(/_/g, " ");
    $("a-files").textContent = w.code_files;
    $("a-dirs").textContent = w.total_dirs;
    $("a-lang").textContent = w.primary_language;

    $("a-entries").innerHTML = w.entry_points.length
      ? `<strong>Entry points:</strong> ${w.entry_points.map(esc).join(", ")}`
      : "";
    $("a-deps").innerHTML = w.dependencies.length
      ? `<strong>Dependencies:</strong> ${w.dependencies.slice(0, 12).map(esc).join(", ")}${w.dependencies.length > 12 ? ` +${w.dependencies.length - 12} more` : ""}`
      : "";
    $("a-data").innerHTML = w.data_files.length
      ? `<strong>Data files:</strong> ${w.data_files.slice(0, 6).map(esc).join(", ")}`
      : "";
    $("a-tests").innerHTML = w.test_files.length
      ? `<strong>Tests:</strong> ${w.test_files.slice(0, 6).map(esc).join(", ")}`
      : "";

    // Show suggested goals
    if (w.suggested_goals && w.suggested_goals.length) {
      el.suggestionsBlock.classList.remove("hidden");
      el.suggestionsList.innerHTML = "";
      for (const s of w.suggested_goals) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "suggestion-btn";
        btn.innerHTML = `<div class="suggestion-goal">${esc(s.goal)}</div><div class="suggestion-brief">${esc(s.judge_brief)}</div>`;
        btn.addEventListener("click", () => {
          $("goal").value = s.goal;
          $("judge-brief").value = s.judge_brief;
          // Scroll to mission section
          el.missionSection.scrollIntoView({ behavior: "smooth", block: "center" });
        });
        el.suggestionsList.appendChild(btn);
      }
    } else {
      el.suggestionsBlock.classList.add("hidden");
    }

    // Show results and unlock next steps
    el.analysisLoading.classList.add("hidden");
    el.analysisResults.classList.remove("hidden");
    el.missionSection.classList.remove("hidden");
    el.settingsSection.classList.remove("hidden");
    el.launchBtn.classList.remove("hidden");

  } catch (err) {
    el.analysisLoading.innerHTML = `<span class="scan-indicator error">Analysis failed: ${esc(err.message)}</span>`;
  }
}

// ── Run ──
async function startRun(e) {
  e.preventDefault();

  const managerDesc = getModelValue(el.managerSelect);
  if (/:free$|free$/i.test(managerDesc)) {
    const ok = confirm(
      "⚠ You picked a FREE model as Manager.\n\n" +
      "Free tier models (OpenRouter :free) often return empty content on large workspaces and hang the loop. " +
      "The Manager is the most critical role — it reads your whole project and writes the judge.\n\n" +
      "Recommended: Claude 3.5 Sonnet, GPT-4o, or Gemini 1.5 Pro.\n\n" +
      "Continue anyway?"
    );
    if (!ok) return;
  }

  closeEvents();
  el.timeline.innerHTML = "";
  state.seenEvents.clear();
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
    manager_descriptor: managerDesc,
    specialist_descriptors: [getModelValue(el.spec1Select), getModelValue(el.spec2Select)].filter(Boolean),
    manager_max_tokens: Number($("manager-max-tokens").value),
    specialist_max_tokens: [
      Number($("specialist-1-max-tokens").value),
      Number($("specialist-2-max-tokens").value),
    ],
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
  const key = `${ev.timestamp || ""}|${ev.phase || ""}|${ev.actor || ""}|${ev.message || ""}`;
  if (state.seenEvents.has(key)) return;
  state.seenEvents.add(key);
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
