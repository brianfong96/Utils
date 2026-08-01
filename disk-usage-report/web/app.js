"use strict";

const byId = id => document.getElementById(id);
const views = ["setup-view", "scan-view", "report-view"];
const customPaths = [];
const workerCards = new Map();
let activeScanId = null;
let eventSource = null;
let elapsedTimer = null;
let scanStartedAt = 0;
let scanTerminal = false;
let reportBindings = [];
let reportRoots = [];
let reportDepth = 3;
const scanPhaseOrder = ["chunking", "scanning", "generating"];

function showView(id) {
  for (const viewId of views) byId(viewId).classList.toggle("active", viewId === id);
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function formatBytes(value) {
  const units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"];
  let amount = Math.max(0, Number(value) || 0);
  let index = 0;
  while (amount >= 1024 && index < units.length - 1) {
    amount /= 1024;
    index += 1;
  }
  return index === 0 ? `${Math.round(amount)} B` : `${amount.toFixed(1)} ${units[index]}`;
}

function formatCount(value) {
  return new Intl.NumberFormat().format(Number(value) || 0);
}

function formatElapsed(seconds) {
  const whole = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(whole / 3600);
  const minutes = String(Math.floor((whole % 3600) / 60)).padStart(2, "0");
  const remainder = String(whole % 60).padStart(2, "0");
  return hours ? `${String(hours).padStart(2, "0")}:${minutes}:${remainder}` : `${minutes}:${remainder}`;
}

function formatScanDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Unknown date";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short"
  }).format(date);
}

function percentage(value, parent) {
  if (!parent) return 100;
  return Math.max(0, Math.min(100, (Number(value) / Number(parent)) * 100));
}

function createText(tag, className, text) {
  const element = document.createElement(tag);
  element.className = className;
  element.textContent = text;
  return element;
}

function setError(element, message = "") {
  element.textContent = message;
  element.hidden = !message;
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) }
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}

function renderDriveOptions(drives) {
  const container = byId("drive-list");
  container.replaceChildren();
  if (!drives.length) {
    container.append(createText("p", "field-help", "No mounted drives were discovered. Add a path below."));
    return;
  }
  drives.forEach((drive, index) => {
    const wrapper = document.createElement("div");
    wrapper.className = "drive-option";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.id = `drive-${index}`;
    input.value = drive.path;
    input.checked = true;
    input.className = "drive-checkbox";
    const label = document.createElement("label");
    label.htmlFor = input.id;
    label.append(
      createText("span", "drive-check", ""),
      (() => {
        const copy = document.createElement("span");
        copy.append(
          createText("span", "drive-name", drive.path),
          createText("span", "drive-detail", `${formatBytes(drive.used)} used of ${formatBytes(drive.total)}`)
        );
        copy.style.display = "grid";
        return copy;
      })(),
      createText("span", "drive-free", `${formatBytes(drive.free)} free`)
    );
    wrapper.append(input, label);
    container.append(wrapper);
  });
}

function renderPathList() {
  const container = byId("path-list");
  container.replaceChildren();
  customPaths.forEach((path, index) => {
    const chip = document.createElement("span");
    chip.className = "path-chip";
    chip.append(createText("span", "", path));
    const remove = createText("button", "", "×");
    remove.type = "button";
    remove.ariaLabel = `Remove ${path}`;
    remove.addEventListener("click", () => {
      customPaths.splice(index, 1);
      renderPathList();
    });
    chip.append(remove);
    container.append(chip);
  });
}

function addCustomPath() {
  const input = byId("path-input");
  const path = input.value.trim();
  if (!path) return;
  if (!customPaths.some(item => item.toLocaleLowerCase() === path.toLocaleLowerCase())) {
    customPaths.push(path);
    renderPathList();
  }
  input.value = "";
  input.focus();
}

async function loadConfiguration() {
  try {
    const config = await request("/api/config");
    byId("workers").value = config.defaults.workers;
    byId("workers-value").value = config.defaults.workers;
    byId("depth").value = config.defaults.depth;
    byId("depth-value").value = config.defaults.depth;
    renderDriveOptions(config.drives);
  } catch (error) {
    setError(byId("setup-error"), error.message);
    byId("drive-list").replaceChildren();
  }
  await loadHistory();
}

function historyRootLabel(roots) {
  if (!Array.isArray(roots) || !roots.length) return "No roots recorded";
  if (roots.length === 1) return roots[0];
  return `${roots[0]} + ${roots.length - 1} more`;
}

function renderHistory(entries) {
  const section = byId("history-section");
  const list = byId("history-list");
  list.replaceChildren();
  section.hidden = !entries.length;
  for (const entry of entries) {
    const row = document.createElement("article");
    row.className = "history-row";
    const main = document.createElement("div");
    main.className = "history-main";
    main.append(
      createText("strong", "history-date", formatScanDate(entry.created_at)),
      createText("span", "history-path", historyRootLabel(entry.roots))
    );
    const details = createText(
      "span",
      "history-details",
      `${formatBytes(entry.bytes)} indexed · ${formatElapsed(entry.duration_seconds)} · depth ${entry.depth} · ${entry.workers} workers`
    );
    const open = createText("button", "secondary-button history-open", "Open report");
    open.type = "button";
    open.addEventListener("click", () => openHistoryReport(entry, open));
    row.append(main, details, open);
    list.append(row);
  }
}

async function loadHistory() {
  try {
    const payload = await request("/api/history");
    renderHistory(payload.history || []);
  } catch (error) {
    byId("history-section").hidden = true;
  }
}

async function openHistoryReport(entry, button) {
  button.disabled = true;
  const original = button.textContent;
  button.textContent = "Opening…";
  try {
    const payload = await request(`/api/history/${encodeURIComponent(entry.id)}`);
    renderReport(payload.report, entry);
    showView("report-view");
  } catch (error) {
    setError(byId("setup-error"), error.message);
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

function initializeWorkers(count) {
  workerCards.clear();
  const grid = byId("worker-grid");
  grid.replaceChildren();
  for (let index = 0; index < count; index += 1) {
    const worker = `worker_${index}`;
    const card = document.createElement("article");
    card.className = "worker-card";
    card.dataset.worker = worker;
    const top = document.createElement("div");
    top.className = "worker-top";
    top.append(
      createText("span", "worker-name", `Worker ${index + 1}`),
      (() => {
        const state = document.createElement("span");
        state.className = "worker-state";
        state.append(createText("span", "worker-pulse", ""), createText("span", "worker-state-text", "Waiting"));
        return state;
      })()
    );
    card.append(
      top,
      createText("div", "worker-path", "Waiting for a chunk"),
      (() => {
        const stats = document.createElement("div");
        stats.className = "worker-stats";
        stats.append(createText("span", "worker-files", "0 files"), createText("span", "worker-bytes", "0 B"));
        return stats;
      })()
    );
    workerCards.set(worker, card);
    grid.append(card);
  }
  updateActiveWorkers();
}

function updateWorker(data) {
  const card = workerCards.get(data.worker);
  if (!card) return;
  const active = data.status === "scanning" || data.status === "preparing";
  card.classList.toggle("active", active);
  card.querySelector(".worker-state-text").textContent = data.status || "idle";
  if (data.path) card.querySelector(".worker-path").textContent = data.path;
  card.querySelector(".worker-files").textContent = `${formatCount(data.files)} files`;
  card.querySelector(".worker-bytes").textContent = formatBytes(data.bytes);
  updateActiveWorkers();
}

function updateActiveWorkers() {
  const active = [...workerCards.values()].filter(card => card.classList.contains("active")).length;
  byId("active-workers").textContent = `${active} active`;
}

function startElapsedTimer() {
  scanStartedAt = Date.now();
  clearInterval(elapsedTimer);
  elapsedTimer = setInterval(() => {
    byId("elapsed-time").textContent = formatElapsed((Date.now() - scanStartedAt) / 1000);
  }, 500);
}

function stopElapsedTimer() {
  clearInterval(elapsedTimer);
  elapsedTimer = null;
}

function updateScanPhase(stage) {
  const normalized = ["discovering", "preparing"].includes(stage) ? "chunking" : stage;
  const currentIndex = scanPhaseOrder.indexOf(normalized);
  if (currentIndex < 0) return;
  for (const phase of document.querySelectorAll(".scan-phase")) {
    const phaseIndex = scanPhaseOrder.indexOf(phase.dataset.phase);
    phase.classList.toggle("active", phaseIndex === currentIndex);
    phase.classList.toggle("complete", currentIndex >= 0 && phaseIndex < currentIndex);
  }
}

function handleStage(data) {
  updateScanPhase(data.stage);
  byId("scan-stage").textContent = data.message || data.stage;
  const fill = byId("progress-fill");
  const labels = {
    chunking: "Preparing work chunks",
    discovering: "Preparing work chunks",
    preparing: "Preparing work chunks",
    scanning: "Scanning storage",
    generating: "Generating results",
    cancelling: "Cancelling"
  };
  byId("progress-label").textContent = labels[data.stage] || "Working";
  fill.classList.toggle("indeterminate", data.stage !== "scanning");
  if (data.stage !== "scanning") fill.style.width = "34%";
}

function handleProgress(data) {
  updateScanPhase("scanning");
  const total = Number(data.total) || 0;
  const completed = Number(data.completed) || 0;
  const percent = total ? (completed / total) * 100 : 0;
  const fill = byId("progress-fill");
  fill.classList.toggle("indeterminate", total === 0);
  fill.style.width = total ? `${Math.max(1, percent)}%` : "34%";
  byId("progress-label").textContent = total ? `${completed.toLocaleString()} of ${total.toLocaleString()} chunks` : "Preparing chunks";
  byId("progress-stats").textContent = `${formatCount(data.files)} files · ${formatBytes(data.bytes)}`;
}

function closeEvents() {
  if (eventSource) eventSource.close();
  eventSource = null;
}

function listenForScan(scanId) {
  closeEvents();
  eventSource = new EventSource(`/api/scans/${scanId}/events`);
  eventSource.addEventListener("stage", event => handleStage(JSON.parse(event.data)));
  eventSource.addEventListener("worker", event => updateWorker(JSON.parse(event.data)));
  eventSource.addEventListener("progress", event => handleProgress(JSON.parse(event.data)));
  eventSource.addEventListener("complete", async () => {
    closeEvents();
    stopElapsedTimer();
    scanTerminal = true;
    try {
      const state = await request(`/api/scans/${scanId}`);
      renderReport(state.report);
      showView("report-view");
    } catch (error) {
      failScan(error.message);
    }
  });
  eventSource.addEventListener("cancelled", () => {
    closeEvents();
    stopElapsedTimer();
    scanTerminal = true;
    failScan("Scan cancelled.");
  });
  eventSource.addEventListener("error", event => {
    if (!event.data) return;
    closeEvents();
    stopElapsedTimer();
    scanTerminal = true;
    failScan(JSON.parse(event.data).message || "The scan failed.");
  });
}

function failScan(message) {
  setError(byId("scan-error"), message);
  byId("cancel-scan").textContent = "Back";
  byId("progress-fill").classList.remove("indeterminate");
}

async function startScan(event) {
  event.preventDefault();
  setError(byId("setup-error"));
  const selectedDrives = [...document.querySelectorAll(".drive-checkbox:checked")].map(input => input.value);
  const roots = [...selectedDrives, ...customPaths];
  if (!roots.length) {
    setError(byId("setup-error"), "Select at least one drive or add a path.");
    return;
  }
  const workers = Number(byId("workers").value);
  const depth = Number(byId("depth").value);
  byId("start-scan").disabled = true;
  try {
    const response = await request("/api/scans", {
      method: "POST",
      body: JSON.stringify({ workers, depth, roots })
    });
    activeScanId = response.id;
    scanTerminal = false;
    setError(byId("scan-error"));
    byId("cancel-scan").textContent = "Cancel";
    byId("scan-stage").textContent = "Starting workers…";
    byId("progress-label").textContent = "Preparing scan";
    byId("progress-stats").textContent = "Discovering paths";
    byId("progress-fill").style.width = "34%";
    byId("progress-fill").classList.add("indeterminate");
    updateScanPhase("chunking");
    initializeWorkers(workers);
    startElapsedTimer();
    showView("scan-view");
    listenForScan(response.id);
  } catch (error) {
    setError(byId("setup-error"), error.message);
  } finally {
    byId("start-scan").disabled = false;
  }
}

async function cancelOrReturn() {
  if (scanTerminal || !activeScanId) {
    showView("setup-view");
    return;
  }
  byId("cancel-scan").disabled = true;
  try {
    await request(`/api/scans/${activeScanId}/cancel`, { method: "POST", body: "{}" });
  } catch (error) {
    failScan(error.message);
  } finally {
    byId("cancel-scan").disabled = false;
  }
}

function pathName(path) {
  const pieces = String(path).split(/[\\/]/).filter(Boolean);
  return pieces.at(-1) || path;
}

function normalizeTree(drive) {
  if (drive.tree) return drive.tree;
  return {
    name: drive.path,
    path: drive.path,
    ...(drive.scan || {}),
    children: (drive.paths || []).map(item => ({
      name: pathName(item.path), path: item.path, ...item, skipped: 0, children: []
    }))
  };
}

function renderTreeNode(node, parentBytes, level) {
  const details = document.createElement("details");
  details.className = "tree-node";
  details.open = level < 2;
  const bytes = Number(node.bytes) || 0;
  const share = percentage(bytes, parentBytes);

  const summary = document.createElement("summary");
  summary.className = "node-row";
  summary.title = node.path || node.name;
  const name = document.createElement("span");
  name.className = "node-name";
  name.append(createText("span", "caret", ""), createText("span", "node-name-text", node.name || node.path));
  summary.append(
    name,
    createText("span", "node-size", formatBytes(bytes)),
    createText("span", "node-percent", `${share.toFixed(1)}%`),
    createText("span", "node-counts", `${formatCount(node.files)} files · ${formatCount(node.directories)} folders`)
  );
  const track = createText("span", "share-track", "");
  const fill = createText("span", "share-fill", "");
  fill.style.width = `${share}%`;
  track.append(fill);
  summary.append(track);
  details.append(summary);

  const container = document.createElement("div");
  container.className = "children";
  const children = [...(node.children || [])].sort((a, b) => b.bytes - a.bytes);
  const childBindings = [];
  let childBytes = 0;
  for (const child of children) {
    childBytes += Number(child.bytes) || 0;
    const rendered = renderTreeNode(child, bytes, level + 1);
    childBindings.push(rendered.binding);
    container.append(rendered.element);
  }

  const remainder = Math.max(0, bytes - childBytes);
  let looseElement = null;
  if (remainder > 0) {
    const label = level >= reportDepth ? "Files and deeper folders" : "Loose files in this folder";
    looseElement = document.createElement("div");
    looseElement.className = "loose-row";
    const looseName = document.createElement("span");
    looseName.className = "node-name";
    looseName.append(createText("span", "loose-dot", ""), createText("span", "node-name-text", label));
    const looseShare = percentage(remainder, bytes);
    looseElement.append(
      looseName,
      createText("span", "node-size", formatBytes(remainder)),
      createText("span", "node-percent", `${looseShare.toFixed(1)}%`),
      createText("span", "node-counts", "Remainder of parent total")
    );
    const looseTrack = createText("span", "share-track", "");
    const looseFill = createText("span", "share-fill", "");
    looseFill.style.width = `${looseShare}%`;
    looseTrack.append(looseFill);
    looseElement.append(looseTrack);
    container.append(looseElement);
  }
  if (!children.length && !looseElement) details.classList.add("leaf");
  details.append(container);

  const binding = {
    element: details,
    details,
    searchText: `${node.name || ""} ${node.path || ""}`.toLocaleLowerCase(),
    bytes,
    children: childBindings,
    looseElement,
    looseBytes: remainder,
    level
  };
  reportBindings.push(binding);
  return { element: details, binding };
}

function renderDriveReport(drive) {
  const section = document.createElement("section");
  section.className = "drive-report";
  const header = document.createElement("div");
  header.className = "drive-report-header";
  const title = document.createElement("div");
  title.className = "drive-title";
  const freePercent = percentage(drive.free, drive.total);
  title.append(createText("strong", "", drive.path), createText("span", "", `${formatBytes(drive.free)} free · ${freePercent.toFixed(1)}%`));
  const capacity = createText("div", "capacity-track", "");
  const capacityFill = createText("div", `capacity-fill${freePercent < 10 ? " critical" : ""}`, "");
  capacityFill.style.width = `${percentage(drive.used, drive.total)}%`;
  capacity.append(capacityFill);
  header.append(title, capacity);
  const tree = createText("div", "tree", "");
  const rendered = renderTreeNode(normalizeTree(drive), null, 0);
  reportRoots.push(rendered.binding);
  tree.append(rendered.element);
  section.append(header, tree);
  return section;
}

function metric(label, value) {
  const item = document.createElement("div");
  item.className = "metric";
  item.append(createText("span", "metric-label", label), createText("strong", "metric-value", value));
  return item;
}

function renderReport(report, historyEntry = null) {
  reportBindings = [];
  reportRoots = [];
  reportDepth = Number(report.settings?.depth) || 1;
  const totals = report.drives.reduce((sum, drive) => {
    sum.bytes += Number(drive.scan?.bytes) || 0;
    sum.files += Number(drive.scan?.files) || 0;
    sum.directories += Number(drive.scan?.directories) || 0;
    return sum;
  }, { bytes: 0, files: 0, directories: 0 });
  const savedAt = historyEntry ? `${formatScanDate(historyEntry.created_at)} · ` : "";
  byId("report-meta").textContent = `${savedAt}Completed in ${formatElapsed(report.duration_seconds)} · depth ${reportDepth} · ${report.settings.workers} workers`;
  const metrics = byId("report-metrics");
  metrics.replaceChildren(
    metric("Drives / roots", formatCount(report.drives.length)),
    metric("Indexed", formatBytes(totals.bytes)),
    metric("Files", formatCount(totals.files)),
    metric("Folders", formatCount(totals.directories)),
    metric("Skipped", formatCount(report.drives.reduce((sum, drive) => sum + (drive.scan?.skipped || 0), 0)))
  );
  const drives = byId("report-drives");
  drives.replaceChildren(...report.drives.map(renderDriveReport));
  byId("report-search").value = "";
  byId("minimum-size").value = "0";
  applyReportFilters();
}

function filterReportNode(binding, query, minimum, inheritedMatch = false) {
  const selfMatch = !query || binding.searchText.includes(query);
  const branchMatch = inheritedMatch || selfMatch;
  let visibleChildren = 0;
  for (const child of binding.children) {
    if (filterReportNode(child, query, minimum, branchMatch)) visibleChildren += 1;
  }
  const looseVisible = Boolean(binding.looseElement) && binding.looseBytes >= minimum && branchMatch;
  if (binding.looseElement) binding.looseElement.hidden = !looseVisible;
  const visible = (binding.bytes >= minimum && branchMatch) || visibleChildren > 0 || looseVisible;
  binding.element.hidden = !visible;
  if (query && (visibleChildren || looseVisible)) binding.details.open = true;
  return visible;
}

function applyReportFilters() {
  const query = byId("report-search").value.trim().toLocaleLowerCase();
  const minimum = (Number(byId("minimum-size").value) || 0) * Number(byId("minimum-unit").value);
  for (const root of reportRoots) filterReportNode(root, query, minimum);
  const visible = reportBindings.filter(binding => !binding.element.hidden).length;
  byId("result-count").textContent = `Showing ${formatCount(visible)} of ${formatCount(reportBindings.length)} directories. Bars are relative to each parent.`;
}

byId("workers").addEventListener("input", event => { byId("workers-value").value = event.target.value; });
byId("depth").addEventListener("input", event => { byId("depth-value").value = event.target.value; });
byId("add-path").addEventListener("click", addCustomPath);
byId("path-input").addEventListener("keydown", event => {
  if (event.key === "Enter") { event.preventDefault(); addCustomPath(); }
});
byId("scan-form").addEventListener("submit", startScan);
byId("cancel-scan").addEventListener("click", cancelOrReturn);
byId("new-scan").addEventListener("click", async () => {
  await loadHistory();
  showView("setup-view");
});
byId("report-search").addEventListener("input", applyReportFilters);
byId("minimum-size").addEventListener("input", applyReportFilters);
byId("minimum-unit").addEventListener("change", applyReportFilters);
byId("expand-report").addEventListener("click", () => {
  for (const binding of reportBindings) if (!binding.element.hidden) binding.details.open = true;
});
byId("collapse-report").addEventListener("click", () => {
  for (const binding of reportBindings) binding.details.open = binding.level === 0;
});

loadConfiguration();
