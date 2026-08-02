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
let currentReport = null;
let reportFilterTimer = null;
let activeTreeFilter = { query: "", minimum: 0, visibility: null };
let totalReportNodes = 0;
let visibleReportNodes = 0;
let currentTreeRoots = [];
let nodeSearchIndex = new WeakMap();
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
  const historyPromise = loadHistory();
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
  await historyPromise;
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
    const report = await request(`/api/history/${encodeURIComponent(entry.id)}/report`);
    renderReport(report, entry);
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
  eventSource.addEventListener("complete", async event => {
    closeEvents();
    stopElapsedTimer();
    scanTerminal = true;
    try {
      const completion = JSON.parse(event.data);
      if (completion.history_saved) {
        const report = await request(`/api/history/${scanId}/report`);
        renderReport(report);
      } else {
        const state = await request(`/api/scans/${scanId}`);
        renderReport(state.report);
      }
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

async function launchScan({ workers, depth, roots }) {
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
  const startButton = byId("start-scan");
  startButton.disabled = true;
  try {
    await launchScan({
      workers: Number(byId("workers").value),
      depth: Number(byId("depth").value),
      roots
    });
  } catch (error) {
    setError(byId("setup-error"), error.message);
  } finally {
    startButton.disabled = false;
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

function sortedChildren(node) {
  return [...(node.children || [])].sort((a, b) => (Number(b.bytes) || 0) - (Number(a.bytes) || 0));
}

function calculateRemainder(node, children = node.children || []) {
  const childBytes = children.reduce((sum, child) => sum + (Number(child.bytes) || 0), 0);
  return Math.max(0, (Number(node.bytes) || 0) - childBytes);
}

function nodeVisibility(node) {
  return activeTreeFilter.visibility?.get(node) || { visible: true, visibleChildren: 0, looseVisible: true };
}

function openDrilldownDialog(path) {
  const workers = Number(currentReport?.settings?.workers) || Number(byId("workers").value) || 16;
  const depth = Math.min(8, Math.max(2, reportDepth + 1));
  byId("drilldown-path").value = path;
  byId("drilldown-workers").value = workers;
  byId("drilldown-workers-value").value = workers;
  byId("drilldown-depth").value = depth;
  byId("drilldown-depth-value").value = depth;
  setError(byId("drilldown-error"));
  byId("drilldown-dialog").showModal();
}

function renderLooseRow(remainder, bytes, level) {
  const label = level >= reportDepth ? "Files and deeper folders" : "Loose files in this folder";
  const looseElement = document.createElement("div");
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
  return looseElement;
}

function ensureChildrenRendered(binding) {
  if (binding.childrenRendered) return;
  binding.childrenRendered = true;
  const fragment = document.createDocumentFragment();
  for (const child of binding.childrenData) {
    if (!nodeVisibility(child).visible) continue;
    const childBinding = renderTreeNode(child, binding.bytes, binding.level + 1);
    binding.children.push(childBinding);
    fragment.append(childBinding.element);
  }
  const info = nodeVisibility(binding.node);
  if (binding.remainder > 0 && info.looseVisible) {
    binding.looseElement = renderLooseRow(binding.remainder, binding.bytes, binding.level);
    fragment.append(binding.looseElement);
  }
  if (!binding.children.length && !binding.looseElement) binding.details.classList.add("leaf");
  binding.container.append(fragment);
}

function renderTreeNode(node, parentBytes, level) {
  const details = document.createElement("details");
  details.className = "tree-node";
  const bytes = Number(node.bytes) || 0;
  const share = percentage(bytes, parentBytes);
  const visibility = nodeVisibility(node);
  details.open = level === 0 || Boolean(activeTreeFilter.query && visibility.visibleChildren);

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
    createText("span", "node-counts", `${formatCount(node.files)} files · ${formatCount(node.directories)} folders`),
    (() => {
      const button = createText("button", "drilldown-button", "Scan deeper");
      button.type = "button";
      button.title = `Scan ${node.path || node.name} with a deeper tree`;
      button.addEventListener("click", event => {
        event.preventDefault();
        event.stopPropagation();
        openDrilldownDialog(node.path || node.name);
      });
      return button;
    })()
  );
  const track = createText("span", "share-track", "");
  const fill = createText("span", "share-fill", "");
  fill.style.width = `${share}%`;
  track.append(fill);
  summary.append(track);
  details.append(summary);

  const container = document.createElement("div");
  container.className = "children";
  details.append(container);

  const binding = {
    node,
    element: details,
    details,
    container,
    bytes,
    childrenData: sortedChildren(node),
    children: [],
    childrenRendered: false,
    looseElement: null,
    remainder: calculateRemainder(node),
    level,
  };
  reportBindings.push(binding);
  details.addEventListener("toggle", () => {
    if (details.open) ensureChildrenRendered(binding);
  });
  if (details.open) ensureChildrenRendered(binding);
  return binding;
}

function renderDriveReport(drive, driveIndex) {
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
  const rootNode = currentTreeRoots[driveIndex];
  if (nodeVisibility(rootNode).visible) {
    const rootBinding = renderTreeNode(rootNode, null, 0);
    reportRoots.push(rootBinding);
    tree.append(rootBinding.element);
  } else {
    tree.append(createText("p", "empty-tree", "No folders match the current filters."));
  }
  section.append(header, tree);
  return section;
}

function metric(label, value) {
  const item = document.createElement("div");
  item.className = "metric";
  item.append(createText("span", "metric-label", label), createText("strong", "metric-value", value));
  return item;
}

function countTreeNodes(roots) {
  let count = 0;
  const stack = [...roots];
  while (stack.length) {
    const node = stack.pop();
    count += 1;
    stack.push(...(node.children || []));
  }
  return count;
}

function evaluateTreeFilter(node, query, minimum, visibility) {
  let visibleChildren = 0;
  let visibleCount = 0;
  for (const child of node.children || []) {
    const childResult = evaluateTreeFilter(child, query, minimum, visibility);
    if (childResult.visible) visibleChildren += 1;
    visibleCount += childResult.count;
  }
  let searchMatches = true;
  if (query) {
    let searchText = nodeSearchIndex.get(node);
    if (searchText === undefined) {
      searchText = `${node.name || ""} ${node.path || ""}`.toLowerCase();
      nodeSearchIndex.set(node, searchText);
    }
    searchMatches = searchText.includes(query);
  }
  const selfMatches = searchMatches && (Number(node.bytes) || 0) >= minimum;
  const looseVisible = calculateRemainder(node) >= minimum && searchMatches;
  const visible = selfMatches || visibleChildren > 0 || looseVisible;
  const result = {
    visible,
    count: visible ? visibleCount + 1 : 0,
    visibleChildren,
    looseVisible
  };
  visibility.set(node, result);
  return result;
}

function renderReportTrees() {
  reportBindings = [];
  reportRoots = [];
  const drives = byId("report-drives");
  drives.replaceChildren(...currentReport.drives.map(renderDriveReport));
  byId("result-count").textContent = `Showing ${formatCount(visibleReportNodes)} of ${formatCount(totalReportNodes)} directories. Open folders to load their children.`;
}

function renderReport(report, historyEntry = null) {
  currentReport = report;
  currentTreeRoots = report.drives.map(normalizeTree);
  nodeSearchIndex = new WeakMap();
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
  byId("report-search").value = "";
  byId("minimum-size").value = "0";
  totalReportNodes = countTreeNodes(currentTreeRoots);
  applyReportFilters();
}

function applyReportFilters() {
  if (!currentReport) return;
  const query = byId("report-search").value.trim().toLowerCase();
  const minimum = (Number(byId("minimum-size").value) || 0) * Number(byId("minimum-unit").value);
  if (!query && minimum === 0) {
    activeTreeFilter = { query, minimum, visibility: null };
    visibleReportNodes = totalReportNodes;
  } else {
    const visibility = new WeakMap();
    visibleReportNodes = 0;
    for (const rootNode of currentTreeRoots) {
      visibleReportNodes += evaluateTreeFilter(rootNode, query, minimum, visibility).count;
    }
    activeTreeFilter = { query, minimum, visibility };
  }
  renderReportTrees();
}

function scheduleReportFilters() {
  clearTimeout(reportFilterTimer);
  reportFilterTimer = setTimeout(applyReportFilters, 140);
}

async function expandAllReportNodes() {
  const button = byId("expand-report");
  const originalLabel = button.textContent;
  button.disabled = true;
  button.textContent = "Expanding…";
  const queue = [...reportRoots];
  let index = 0;
  while (index < queue.length) {
    const batchEnd = Math.min(index + 120, queue.length);
    for (; index < batchEnd; index += 1) {
      const binding = queue[index];
      binding.details.open = true;
      ensureChildrenRendered(binding);
      queue.push(...binding.children);
    }
    await new Promise(resolve => requestAnimationFrame(resolve));
  }
  button.disabled = false;
  button.textContent = originalLabel;
}

async function startDrilldown(event) {
  event.preventDefault();
  const button = byId("start-drilldown");
  button.disabled = true;
  setError(byId("drilldown-error"));
  try {
    await launchScan({
      roots: [byId("drilldown-path").value],
      workers: Number(byId("drilldown-workers").value),
      depth: Number(byId("drilldown-depth").value)
    });
    byId("drilldown-dialog").close();
  } catch (error) {
    setError(byId("drilldown-error"), error.message);
  } finally {
    button.disabled = false;
  }
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
byId("report-search").addEventListener("input", scheduleReportFilters);
byId("minimum-size").addEventListener("input", scheduleReportFilters);
byId("minimum-unit").addEventListener("change", applyReportFilters);
byId("expand-report").addEventListener("click", expandAllReportNodes);
byId("collapse-report").addEventListener("click", () => {
  for (const binding of reportBindings) binding.details.open = binding.level === 0;
});
byId("drilldown-workers").addEventListener("input", event => { byId("drilldown-workers-value").value = event.target.value; });
byId("drilldown-depth").addEventListener("input", event => { byId("drilldown-depth-value").value = event.target.value; });
byId("drilldown-form").addEventListener("submit", startDrilldown);
byId("cancel-drilldown").addEventListener("click", () => byId("drilldown-dialog").close());

loadConfiguration();
