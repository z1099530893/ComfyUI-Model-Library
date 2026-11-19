/* 模型下载器 - M1
 * 下载任务已接入真实后端（Civitai 公开模型），模型库页面仍为示例数据。
 */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const state = {
  settings: {
    default_download_dir: "",
    recent_dirs: [],
    library_dirs: [],
    comfy_dirs: [],
    link_mode: "auto",
    auto_link: true,
    card_scale: 1,
    site_tokens: { civitai: "", huggingface: "" },
    proxy: "",
    blur_nsfw: true,
    scan_on_startup: true,
  },
  disk: { ok: false, total: 0, free: 0 },
  cache: null,
  tasks: [],
  pendingPlan: null,
  renameTaskId: null,
  viewType: "all",
  viewBase: "all",
  viewMode: "grid",
  scanning: false,
  searchTerm: "",
  detailModel: null,
  detailIndex: 0,
  detailRevealed: false,
  ctxModel: null,
  editModel: null,
  models: [],
  selected: new Set(),
  lastSelected: "",
  organizeItems: [],
  organizePaths: [],
  organizeTask: null,
  docSelection: "",
};

let booted = false;
let currentTab = "queue";

/* ---------- 与后端通信（网页预览时优雅降级） ---------- */
const hasBackend = () => Boolean(window.pywebview && window.pywebview.api);

async function call(method, ...args) {
  if (!hasBackend()) return null;
  try {
    return await window.pywebview.api[method](...args);
  } catch (err) {
    console.error(method, err);
    toast("操作失败：" + (err && err.message ? err.message : err));
    return null;
  }
}

/* ---------- 工具 ---------- */
function fmtBytes(n) {
  if (!n) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(Math.floor(Math.log(n) / Math.log(1024)), units.length - 1);
  const value = n / 1024 ** i;
  return `${value >= 100 ? value.toFixed(0) : value.toFixed(value >= 10 ? 1 : 2)} ${units[i]}`;
}

function fmtEta(seconds) {
  if (!seconds || !isFinite(seconds) || seconds <= 0) return "--";
  const total = Math.round(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h} 小时 ${m} 分`;
  if (m > 0) return `${m} 分 ${s} 秒`;
  return `${s} 秒`;
}

function esc(str) {
  return String(str ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function icon(name) {
  const paths = {
    down: '<path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M4 19h16"/>',
    pause: '<path d="M9 5v14"/><path d="M15 5v14"/>',
    play: '<path d="M7 4v16l13-8z"/>',
    x: '<path d="M6 6l12 12"/><path d="M18 6 6 18"/>',
    check: '<path d="m4 12 5 5L20 6"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    image: '<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/>',
    link: '<path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-2 2"/><path d="M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l2-2"/>',
    edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m14 6 4 4"/>',
  };
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">${paths[name] || ""}</svg>`;
}

let toastTimer = null;
function toast(msg) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 2400);
}

/* ---------- 渲染：磁盘 ---------- */
function renderDisk() {
  const { free, ok } = state.disk;
  const drive = state.settings.default_download_dir || "";
  const el = $("#statusDisk");
  el.textContent = ok ? `${fmtBytes(free)} 可用` : "磁盘 --";
  el.title = drive ? `目标目录：${drive}` : "尚未选择模型目录";
}

function renderCache() {
  const cache = state.cache || {};
  const count = Number(cache.count || 0);
  const size = Number(cache.size || 0);
  const el = $("#cacheSize");
  if (!el) return;
  el.textContent = count ? `${count} 个模型 · ${fmtBytes(size)}` : "空";
}

async function loadCacheInfo() {
  if (!hasBackend()) return;
  const res = await call("cache_info");
  if (res && res.cache) {
    state.cache = res.cache;
    renderCache();
  }
}

function renderStatus() {
  const running = state.tasks.filter((t) => t.status === "running");
  const failed = state.tasks.filter((t) => t.status === "error").length;
  const org = state.organizeTask;
  const orgActive = org && ["queued", "running", "paused"].includes(org.status);
  let text;
  if (state.scanning) text = "正在扫描模型库…";
  else if (orgActive) text = `${org.status === "paused" ? "补数据已暂停" : "正在补数据"} ${org.index || 0}/${org.total || 0}`;
  else if (running.length) text = `正在下载 ${running.length} 个任务`;
  else if (failed) text = `${failed} 个任务失败`;
  else text = `就绪 · ${state.models.length} 个模型`;
  $("#statusText").textContent = text;

  const speed = running.reduce((sum, t) => sum + (Number(t.speed) || 0), 0);
  $("#statusSpeed").textContent = running.length ? `${fmtBytes(speed)}/s` : "";
  $("#statusDot").hidden = !running.length;
}

/* ---------- 渲染：任务 ---------- */
function buildTaskMeta(t) {
  const dir = `<span class="path" title="${esc(t.dir)}">${esc(t.dir)}</span>`;
  if (t.status === "running") {
    const remain = t.speed > 0 && t.size ? (t.size - t.done) / t.speed : 0;
    return `<span>${fmtBytes(t.speed)}/s</span><span>剩余 ${fmtEta(remain)}</span><span>${esc(t.stage || "下载中")}</span>${dir}`;
  }
  if (t.status === "paused") return `<span>已暂停，临时文件已保留</span>${dir}`;
  if (t.status === "error") return `<span class="danger-text">${esc(t.error || "下载失败")}</span>${dir}`;
  if (t.status === "done") {
    return `<span>完成于 ${esc(t.finished_at || "")}</span><span>示例图 ${t.images_done || 0} 张</span>${dir}`;
  }
  return `<span>等待开始</span>${dir}`;
}

function taskActionsHTML(t) {
  const folder = `<button class="icon-btn" data-act="folder" title="打开所在文件夹">${icon("folder")}</button>`;
  if (t.status === "done") {
    return `${folder}<button class="icon-btn" data-act="remove" title="从记录中移除">${icon("x")}</button>`;
  }
  if (t.status === "error") {
    const auth = t.error_code === "auth" ? '<button class="mini" data-act="auth">去授权</button>' : "";
    return `<button class="icon-btn" data-act="resume" title="重试">${icon("play")}</button>${folder}
            <button class="icon-btn" data-act="rename" title="修改名称">${icon("edit")}</button>${auth}
            <button class="icon-btn" data-act="remove" title="从记录中移除">${icon("x")}</button>`;
  }
  if (t.status === "paused") {
    return `<button class="icon-btn" data-act="resume" title="继续">${icon("play")}</button>${folder}
            <button class="icon-btn" data-act="rename" title="修改名称">${icon("edit")}</button>
            <button class="icon-btn" data-act="cancel" title="取消并删除临时文件">${icon("x")}</button>`;
  }
  if (t.status === "running") {
    return `<button class="icon-btn" data-act="pause" title="暂停">${icon("pause")}</button>${folder}
            <button class="icon-btn" data-act="cancel" title="取消并删除临时文件">${icon("x")}</button>`;
  }
  return `${folder}<button class="icon-btn" data-act="rename" title="修改名称">${icon("edit")}</button>
          <button class="icon-btn" data-act="cancel" title="取消">${icon("x")}</button>`;
}

function renderTasks() {
  const list = state.tasks.filter((t) => (currentTab === "done" ? t.status === "done" : t.status !== "done"));

  $("#taskList").innerHTML = list.length
    ? list.map((t) => {
        const pct = t.size ? Math.min(100, (t.done / t.size) * 100) : 0;
        const running = t.status === "running";
        const done = t.status === "done";
        const failed = t.status === "error";
        const paused = t.status === "paused";
        const statusText = done
          ? `<span class="state-pill">${icon("check")}已归档</span>`
          : failed
            ? `<span class="state-pill danger">${icon("x")}失败</span>`
            : paused
              ? `<span class="state-pill put">${icon("pause")}已暂停</span>`
              : running
                ? `<b>${pct.toFixed(1)}%</b>`
                : `<span class="state-pill put">${icon("clock")}排队中</span>`;
        const sizeText = `${fmtBytes(t.done || 0)} / ${fmtBytes(t.size || 0)}`;
        return `
          <div class="task" data-id="${esc(t.id)}">
            <div class="task-icon ${running ? "running" : done ? "done" : failed ? "failed" : ""}">${icon(done ? "check" : running ? "down" : failed ? "x" : paused ? "pause" : "clock")}</div>
            <div class="task-main">
              <div class="task-title">
                <strong title="${esc(t.name)}">${esc(t.name)}</strong>
                <span class="src">${esc(t.site_name || t.site || "")}</span>
                <span class="src">${esc(t.base || "")}</span>
              </div>
              <div class="task-meta">${buildTaskMeta(t)}</div>
            </div>
            <div class="task-progress">
              <div class="progress-top">${statusText}<span>${sizeText}</span></div>
              <div class="progress-bar ${running || done ? "" : "idle"}"><i style="width:${done ? 100 : pct}%"></i></div>
            </div>
            <div class="task-actions">${taskActionsHTML(t)}</div>
          </div>`;
      }).join("")
    : `<div class="empty-row">${currentTab === "done" ? "还没有下载完成的记录。" : "队列是空的，粘贴模型地址开始下载。"}</div>`;

  const queue = state.tasks.filter((t) => t.status !== "done").length;
  const doneCount = state.tasks.filter((t) => t.status === "done").length;
  const runningCount = state.tasks.filter((t) => t.status === "running").length;
  $("#queueCount").textContent = String(queue);
  $("#doneCount").textContent = String(doneCount);
  $("#navTaskCount").textContent = String(runningCount);
  $("#navTaskCount").style.display = runningCount ? "grid" : "none";
  $("#btnClearDone").style.display = currentTab === "done" ? "" : "none";
  $("#btnPauseAll").style.display = currentTab === "done" ? "none" : "";

  renderStatus();
}

/* ---------- 渲染：模型库 ---------- */
const ART_POOL = [
  "linear-gradient(155deg,#cbd5e1,#64748b 55%,#1e293b)",
  "linear-gradient(155deg,#f8fafc,#cbd5e1 60%,#94a3b8)",
  "linear-gradient(155deg,#fde68a,#f59e0b 55%,#7c2d12)",
  "linear-gradient(155deg,#fbcfe8,#db2777 60%,#500724)",
  "linear-gradient(155deg,#a5f3fc,#0ea5e9 55%,#1e1b4b)",
  "linear-gradient(155deg,#ddd6fe,#8b5cf6 60%,#2e1065)",
  "linear-gradient(155deg,#bbf7d0,#10b981 60%,#064e3b)",
  "linear-gradient(155deg,#fed7aa,#f97316 60%,#431407)",
];

function artFor(name) {
  let hash = 0;
  for (const ch of String(name || "")) hash = (hash * 31 + ch.charCodeAt(0)) % 9973;
  return ART_POOL[hash % ART_POOL.length];
}

function modelCardHTML(m) {
  const blur = state.settings.blur_nsfw && m.nsfw;
  const cover = m.cover ? `<img class="cover" src="${esc(m.cover)}" alt="" onerror="this.remove()" />` : "";
  const label = m.cover ? "" : `<span class="art-label">${esc(m.type)}</span>`;
  const selected = state.selected.has(m.path);
  return `
    <article class="model-card ${selected ? "selected" : ""}" data-path="${esc(m.path)}">
      <button type="button" class="select-box ${selected ? "on" : ""}" data-act="select" title="选择">${selected ? "✓" : ""}</button>
      <div class="thumb ${blur ? "blur" : ""}">
        <div class="art" style="background:${artFor(m.name)}"></div>${cover}${label}
        <div class="thumb-badges">${m.cover ? `<span class="badge dark">${esc(m.type)}</span>` : ""}</div>
        ${m.images ? `<span class="thumb-count">${icon("image")}${m.images}</span>` : ""}
        ${m.nsfw ? '<button type="button" class="nsfw-badge" data-act="reveal" title="显示 / 隐藏原图">NSFW</button>' : ""}
      </div>
      <div class="model-info">
        <div class="model-name" title="${esc(m.name)}">${esc(m.name)}</div>
        <div class="model-meta">
          <span class="base">${esc(m.base)}</span>
          <span>${fmtBytes(m.size)}</span>
        </div>
      </div>
    </article>`;
}

function libraryItems() {
  const term = state.searchTerm.trim().toLowerCase();
  return state.models.filter((m) => {
    if (state.viewType !== "all" && m.type_key !== state.viewType) return false;
    if (state.viewBase !== "all" && m.base !== state.viewBase) return false;
    if (!term) return true;
    return [m.name, m.base, m.author, m.trigger, m.file].some((v) =>
      String(v || "").toLowerCase().includes(term)
    );
  });
}

const SORT_COLLATOR = new Intl.Collator("zh-CN", { numeric: true, sensitivity: "base" });

function sortModels(items) {
  const sort = state.settings.library_sort || "name";
  const desc = Boolean(state.settings.library_sort_desc);
  const getter =
    sort === "time"
      ? (m) => Number(m.mtime || 0)
      : sort === "size"
        ? (m) => Number(m.size || 0)
        : sort === "type"
          ? (m) => `${m.type_key || ""} ${m.name || ""}`
          : (m) => m.name || "";
  return [...items].sort((a, b) => {
    const va = getter(a);
    const vb = getter(b);
    const result =
      typeof va === "number" && typeof vb === "number"
        ? va - vb
        : SORT_COLLATOR.compare(String(va), String(vb));
    return desc ? -result : result;
  });
}

function visibleModels() {
  return sortModels(libraryItems());
}

function renderModels() {
  const items = visibleModels();
  if (!items.length) {
    $("#modelGrid").innerHTML = `<p class="hint" style="margin:16px 2px">${
      state.models.length
        ? "没有匹配的模型。"
        : "还没有读取到模型。请到「设置 → 模型库扫描范围」添加目录，然后点右上角刷新。"
    }</p>`;
    updateSelection();
    return;
  }
  $("#modelGrid").innerHTML = items.map(modelCardHTML).join("");
  updateSelection();
}

/* ---------- 多选 ---------- */
function updateSelection() {
  const count = state.selected.size;
  const bar = $("#selectionBar");
  if (bar) {
    bar.hidden = count === 0;
    if (count) $("#selectionCount").textContent = `已选 ${count} 项`;
  }
  $$("#modelGrid .model-card").forEach((card) => {
    const on = state.selected.has(card.dataset.path);
    card.classList.toggle("selected", on);
    const box = card.querySelector(".select-box");
    if (box) {
      box.classList.toggle("on", on);
      box.textContent = on ? "✓" : "";
    }
  });
}

function toggleSelect(path) {
  if (state.selected.has(path)) state.selected.delete(path);
  else state.selected.add(path);
  updateSelection();
}

function selectRange(fromPath, toPath) {
  const items = visibleModels();
  const start = items.findIndex((m) => m.path === fromPath);
  const end = items.findIndex((m) => m.path === toPath);
  if (start < 0 || end < 0) return toggleSelect(toPath);
  const [lo, hi] = start < end ? [start, end] : [end, start];
  // 目标项已选就整段取消，未选就整段选中
  const select = !state.selected.has(toPath);
  for (let i = lo; i <= hi; i += 1) {
    if (select) state.selected.add(items[i].path);
    else state.selected.delete(items[i].path);
  }
  updateSelection();
}

async function deleteModels(paths, models) {
  if (!paths.length) return;
  const names = models.slice(0, 3).map((m) => m.name).join("、");
  const more = models.length > 3 ? ` 等 ${models.length} 个` : "";
  const ok = await askConfirm({
    title: "移入回收站",
    text: `将删除 ${paths.length} 个模型，连同模型自己的资料夹（含说明与示例图）。\n${names}${more}\n删除后可从回收站恢复。`,
    okText: "移入回收站",
    danger: true,
  });
  if (!ok) return;
  const res = await call("delete_models", paths);
  if (!res) return;
  if (res.deleted && res.deleted.length) {
    toast(`已移入回收站 ${res.deleted.length} 个模型`);
    for (const path of paths) state.selected.delete(path);
    updateSelection();
    if (state.detailModel && paths.includes(state.detailModel.path)) closeDetail();
    await scanLibrary();
  }
  if (res.failed && res.failed.length) {
    toast(`有 ${res.failed.length} 个删除失败：${res.failed[0].message}`);
  }
}

function askConfirm({ title, text, okText = "确定", danger = false }) {
  return new Promise((resolve) => {
    const modal = $("#confirmModal");
    const ok = $("#confirmOk");
    const cancel = $("#confirmCancel");
    $("#confirmTitle").textContent = title;
    $("#confirmText").textContent = text;
    ok.textContent = okText;
    ok.classList.toggle("danger", danger);
    ok.classList.toggle("primary", !danger);
    modal.hidden = false;

    const finish = (value) => {
      modal.hidden = true;
      ok.removeEventListener("click", onOk);
      cancel.removeEventListener("click", onCancel);
      modal.removeEventListener("click", onMask);
      resolve(value);
    };
    const onOk = () => finish(true);
    const onCancel = () => finish(false);
    const onMask = (e) => {
      if (e.target === modal) finish(false);
    };
    ok.addEventListener("click", onOk);
    cancel.addEventListener("click", onCancel);
    modal.addEventListener("click", onMask);
  });
}

function setViewMode(mode, persist = true) {
  state.viewMode = mode === "list" ? "list" : "grid";
  $("#modelGrid").classList.toggle("list", state.viewMode === "list");
  $$("#viewToggle [data-view]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.view === state.viewMode);
  });
  if (persist) saveSettings({ library_view: state.viewMode }, null);
}

function applySortUI() {
  const select = $("#sortSelect");
  if (select) select.value = state.settings.library_sort || "name";
  const btn = $("#btnSortDir");
  if (btn) btn.classList.toggle("desc", Boolean(state.settings.library_sort_desc));
}

function renderTypeTabs() {
  const counts = { all: state.models.length, checkpoint: 0, lora: 0 };
  state.models.forEach((m) => {
    if (m.type_key === "checkpoint" || m.type_key === "lora") counts[m.type_key] += 1;
  });
  ["all", "checkpoint", "lora"].forEach((key) => {
    const btn = $(`#typeTabs [data-type="${key}"]`);
    if (!btn) return;
    btn.classList.toggle("active", state.viewType === key);
    const em = btn.querySelector("em");
    if (em) em.textContent = counts[key];
  });
}

function renderBaseChips() {
  const scope = state.models.filter((m) => state.viewType === "all" || m.type_key === state.viewType);
  if (state.viewBase !== "all" && !scope.some((m) => m.base === state.viewBase)) state.viewBase = "all";
  const counts = new Map();
  scope.forEach((m) => counts.set(m.base, (counts.get(m.base) || 0) + 1));
  const groups = [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "zh"));
  const html = [
    `<button class="chip ${state.viewBase === "all" ? "active" : ""}" data-base="all">全部 <em>${scope.length}</em></button>`,
    ...groups.map(
      ([base, count]) =>
        `<button class="chip ${state.viewBase === base ? "active" : ""}" data-base="${esc(base)}">${esc(base)} <em>${count}</em></button>`
    ),
  ];
  $("#baseChips").innerHTML = html.join("");
}

async function scanLibrary(showToast = false) {
  state.scanning = true;
  renderStatus();
  const res = await call("scan_library");
  state.scanning = false;
  if (!res) {
    renderStatus();
    return;
  }
  if (!res.ok) {
    renderStatus();
    toast(res.message || "扫描失败");
    return;
  }
  state.models = res.models || [];
  state.viewBase = "all";
  const alive = new Set(state.models.map((m) => m.path));
  state.selected = new Set([...state.selected].filter((path) => alive.has(path)));
  renderTypeTabs();
  renderBaseChips();
  renderModels();
  renderStatus();
  if (showToast) toast(`已读取 ${state.models.length} 个模型`);
}

/* ---------- 整理本地模型库（补数据） ---------- */
const ORGANIZE_ACTIVE = ["queued", "running", "paused"];

function organizeActive(task) {
  return Boolean(task && ORGANIZE_ACTIVE.includes(task.status));
}

function syncOrganizeSource() {
  const single = state.organizeItems.length === 1;
  $("#orgSource").hidden = !single;
  if (!single) $("#orgSourceUrl").value = "";
}

async function openOrganize(paths) {
  $("#organizeModal").hidden = false;
  const task = state.organizeTask;
  if (organizeActive(task)) {
    $("#organizeTitle").textContent = "补数据进行中";
    $("#orgProgress").hidden = false;
    $("#orgSource").hidden = true;
    $("#orgList").innerHTML = "";
    renderOrganizeTask(task);
    return;
  }

  state.organizePaths = Array.isArray(paths) ? paths : [];
  state.organizeItems = [];
  $("#orgSourceUrl").value = "";
  $("#organizeTitle").textContent = state.organizePaths.length
    ? `补数据 · 已选 ${state.organizePaths.length} 个模型`
    : "整理本地模型库";
  $("#orgProgress").hidden = true;
  $("#orgLog").innerHTML = "";
  $("#orgStatus").textContent = "";
  $("#orgBar").style.width = "0%";
  $("#orgStart").hidden = false;
  $("#orgStart").textContent = "开始补数据";
  $("#orgStart").disabled = true;
  $("#orgPause").hidden = true;
  $("#orgCancel").hidden = true;
  $("#orgList").innerHTML = '<p class="hint" style="margin:12px">正在读取模型…</p>';

  const res = await call("organize_preview", state.organizePaths.length ? state.organizePaths : null);
  if (!res) {
    $("#orgList").innerHTML = '<p class="hint" style="margin:12px">读取失败，请在桌面窗口中重试。</p>';
    return;
  }
  if (!res.ok) {
    $("#orgList").innerHTML = `<p class="hint" style="margin:12px">${esc(res.message || "读取失败")}</p>`;
    return;
  }
  state.organizeItems = res.items || [];
  if (!state.organizeItems.length) {
    $("#orgSource").hidden = true;
    $("#orgList").innerHTML =
      '<p class="hint" style="margin:12px">没有可整理的模型。请先在设置 → 模型库扫描范围里添加目录。</p>';
    return;
  }
  $("#orgStart").disabled = false;
  syncOrganizeSource();
  renderOrganizeList();
}

function organizePlanText(item) {
  if (!item.folder_mode) return { text: "跳过 · 该类型不生成资料夹", skip: true };
  if (item.has_meta && !$("#orgOverwrite").checked) return { text: "跳过 · 已有资料", skip: true };
  const parts = ["写入说明 + model.json"];
  if ($("#orgImages").checked) parts.push("补示例图");
  return { text: parts.join(" + "), skip: false };
}

function organizeMatchText(item) {
  if (item.match) {
    return `${item.match.title}${item.match.version_name ? " · " + item.match.version_name : ""}`;
  }
  if (item.checked) return "未匹配（已缓存）";
  return "运行时查询";
}

function renderOrganizeList() {
  if (!state.organizeItems.length) return;
  const head = `
    <div class="organize-row organize-head">
      <span>模型文件</span><span>类型</span><span>资料</span><span>Civitai 匹配</span><span>计划动作</span>
    </div>`;
  const rows = state.organizeItems.map((item) => {
    const plan = organizePlanText(item);
    const docs = item.folder_mode ? (item.has_meta ? `已有 ${item.images} 图` : "缺资料") : "不建资料夹";
    return `
      <div class="organize-row">
        <div class="organize-name">
          <strong title="${esc(item.path)}">${esc(item.name)}</strong>
          <span class="path">${esc(item.dir)}</span>
        </div>
        <span class="organize-type">${esc(item.type)}</span>
        <span>${esc(docs)}</span>
        <span title="${esc(item.match ? item.match.title : "")}">${esc(organizeMatchText(item))}</span>
        <span class="organize-plan ${plan.skip ? "skip" : ""}">${esc(plan.text)}</span>
      </div>`;
  }).join("");
  $("#orgList").innerHTML = head + rows;
}

function renderOrganizeTask(task) {
  if (!task) return;
  $("#organizeModal").hidden = false;
  $("#orgProgress").hidden = false;

  const total = Number(task.total) || 0;
  const done = Number(task.index) || 0;
  let frac = total ? done / total : 0;
  if (task.status === "running" && Number(task.item_size) > 0) {
    const part = Math.min(1, Number(task.item_done || 0) / Number(task.item_size));
    frac = total ? Math.min(1, (done + part) / total) : 0;
  }
  if (task.status === "done") frac = 1;
  $("#orgBar").style.width = `${Math.max(0, Math.min(100, frac * 100)).toFixed(1)}%`;

  const labels = {
    queued: "等待开始",
    running: "进行中",
    paused: "已暂停",
    done: "已完成",
    cancelled: "已取消",
    error: "出错",
  };
  const stage = task.stage ? ` · ${task.stage}` : "";
  const current = task.name ? ` · ${task.name}` : "";
  $("#orgStatus").innerHTML =
    `<span>${esc(labels[task.status] || task.status)} ${done}/${total}${esc(current)}${esc(stage)}</span>` +
    `<span>写入 ${task.written || 0} · 未匹配 ${task.no_match || 0} · 跳过 ${task.skipped || 0} · 失败 ${task.failed || 0}</span>`;

  const log = (task.log || []).slice(-20);
  $("#orgLog").innerHTML = log
    .map((item) => {
      const cls = item.state === "done" ? "ok" : item.state === "fail" ? "fail" : item.state;
      return `<div><span>${esc(item.name)}</span><em class="${esc(cls)}">${esc(item.message)}</em></div>`;
    })
    .join("");

  $("#orgStart").hidden = organizeActive(task);
  $("#orgStart").textContent = task.status === "done" ? "再次补数据" : "开始补数据";
  $("#orgPause").hidden = !organizeActive(task);
  $("#orgPause").textContent = task.status === "paused" ? "继续" : "暂停";
  $("#orgCancel").hidden = !organizeActive(task);
}

async function startOrganize() {
  if (!state.organizeItems.length) return toast("没有可整理的模型");
  const btn = $("#orgStart");
  btn.disabled = true;
  const res = await call(
    "organize_start",
    state.organizePaths.length ? state.organizePaths : null,
    {
      download_images: $("#orgImages").checked,
      overwrite: $("#orgOverwrite").checked,
      source_url: $("#orgSource").hidden ? "" : $("#orgSourceUrl").value.trim(),
    }
  );
  btn.disabled = false;
  if (!res) return;
  if (!res.ok) return toast(res.message || "无法开始整理");
  state.organizeTask = res.task;
  renderOrganizeTask(res.task);
  toast("开始补数据");
}

async function toggleOrganizePause() {
  const task = state.organizeTask;
  if (!task) return;
  const res = await call("organize_action", task.status === "paused" ? "resume" : "pause");
  if (res && !res.ok) toast(res.message || "操作失败");
  if (res && res.task) {
    state.organizeTask = res.task;
    renderOrganizeTask(res.task);
  }
}

async function refreshOrganize() {
  const res = await call("get_organize");
  if (!res) return;
  const prev = state.organizeTask;
  const task = res.task || null;
  state.organizeTask = task;
  const wasActive = organizeActive(prev);
  const modalOpen = !$("#organizeModal").hidden;

  if (organizeActive(task)) {
    if (modalOpen) renderOrganizeTask(task);
    renderStatus();
    return;
  }
  if (wasActive && task && (task.status === "done" || task.status === "cancelled")) {
    if (modalOpen) renderOrganizeTask(task);
    renderStatus();
    if (task.status === "done") {
      toast(
        `补数据完成：写入 ${task.written || 0} 个，未匹配 ${task.no_match || 0} 个，跳过 ${task.skipped || 0} 个`
      );
      await scanLibrary();
    } else {
      toast("已取消补数据");
    }
    return;
  }
  renderStatus();
}


function applyCardScale() {
  const scale = Number(state.settings.card_scale) || 1;
  document.documentElement.style.setProperty("--card-min", `${Math.round(178 * scale)}px`);
  document.documentElement.style.setProperty("--list-thumb", `${Math.round(68 * scale)}px`);
  $$("#cardScale button").forEach((b) => b.classList.toggle("active", Number(b.dataset.scale) === scale));
}

/* ---------- 模型详情弹窗 ---------- */
function openDetail(m) {
  state.detailModel = m;
  state.detailIndex = 0;
  state.detailRevealed = false;
  renderDetail();
  $("#detailModal").hidden = false;
}

function closeDetail() {
  $("#detailModal").hidden = true;
  hideDocContextMenu();
  state.detailModel = null;
}

/* ---------- 编辑模型 ---------- */
function openEditModel(m) {
  state.editModel = m;
  $("#editPathHint").textContent = m.file;
  $("#editTitle").value = m.name || "";
  $("#editVersion").value = m.version || "";
  $("#editBase").value = m.base && m.base !== "未分类" ? m.base : "";
  $("#editTriggers").value = m.trigger || "";
  $("#editDesc").value = m.desc || "";
  $("#editNsfw").checked = Boolean(m.nsfw);

  const typeSel = $("#editType");
  if (MODEL_TYPES.some(([key]) => key === m.type_key)) {
    typeSel.innerHTML = MODEL_TYPES.map(([key, label]) => `<option value="${key}">${label}</option>`).join("");
    typeSel.value = m.type_key;
    typeSel.disabled = false;
  } else {
    typeSel.innerHTML = `<option>${esc(m.type)}</option>`;
    typeSel.disabled = true;
  }
  $("#editModelModal").hidden = false;
}

function closeEditModel() {
  $("#editModelModal").hidden = true;
  state.editModel = null;
}

async function saveEditModel() {
  const m = state.editModel;
  if (!m) return;
  const title = $("#editTitle").value.trim();
  if (!title) return toast("模型名称不能为空");
  const btn = $("#editSave");
  btn.disabled = true;
  const res = await call("update_model", m.path, {
    title,
    model_type: $("#editType").disabled ? "" : $("#editType").value,
    version_name: $("#editVersion").value.trim(),
    base_model: $("#editBase").value.trim(),
    trigger_words: $("#editTriggers").value.trim(),
    description: $("#editDesc").value,
    nsfw: $("#editNsfw").checked,
  });
  btn.disabled = false;
  if (!res) return;
  if (!res.ok) return toast(res.message || "保存失败");
  closeEditModel();
  toast("已保存");
  await scanLibrary();
}

function stepDetail(delta) {
  const images = (state.detailModel && state.detailModel.image_paths) || [];
  if (images.length < 2) return;
  state.detailIndex = (state.detailIndex + delta + images.length) % images.length;
  renderDetail();
}

function renderDetail() {
  const m = state.detailModel;
  if (!m) return;
  const images = m.image_paths || [];
  const blur = state.settings.blur_nsfw && m.nsfw && !state.detailRevealed;

  $("#detailTitle").textContent = m.name;
  const canReveal = Boolean(state.settings.blur_nsfw && m.nsfw);
  $("#detailTags").innerHTML = [
    `<span class="hv-tag">${esc(m.type)}</span>`,
    `<span class="hv-tag">基础模型 ${esc(m.base)}</span>`,
    m.nsfw ? '<span class="hv-tag nsfw">NSFW</span>' : "",
    canReveal
      ? `<button type="button" class="mini" id="detailRevealTag">${state.detailRevealed ? "隐藏原图" : "显示原图"}</button>`
      : "",
  ].join("");

  const meta = [
    ["作者", m.author || "未记录"],
    ["大小", fmtBytes(m.size)],
    ["示例图", `${m.images} 张`],
    ["文件名", m.file],
    ["触发词", m.trigger || "未记录"],
    ["来源", m.source || "未记录"],
  ];
  $("#detailMeta").innerHTML = meta
    .map(([k, v]) => `<div><dt>${esc(k)}</dt><dd title="${esc(v)}">${esc(v)}</dd></div>`)
    .join("");

  const imageBox = $("#detailImage");
  if (images.length) {
    const src = images[Math.min(state.detailIndex, images.length - 1)];
    imageBox.innerHTML = `<img src="${esc(src)}" alt="" onerror="this.remove()" />`;
  } else {
    imageBox.innerHTML = `<div class="ph">${esc(m.type)}</div>`;
  }
  imageBox.classList.toggle("blur", Boolean(blur));
  $("#galleryPrev").disabled = images.length < 2;
  $("#galleryNext").disabled = images.length < 2;

  $("#detailThumbs").innerHTML = images
    .map(
      (src, i) =>
        `<button data-index="${i}" class="${i === state.detailIndex ? "active" : ""}"><img src="${esc(src)}" alt="" onerror="this.parentNode.remove()" /></button>`
    )
    .join("");
  $("#detailThumbs").classList.toggle("blur", Boolean(blur));
  $("#detailThumbs").hidden = images.length === 0;

  $("#detailDoc").textContent = m.doc || m.desc || "这个模型还没有说明文档。";
  $("#detailOpenLink").disabled = !m.source;
}

/* ---------- 渲染：设置 ---------- */
function renderSettings() {
  const s = state.settings;
  $("#settingsDir").value = s.default_download_dir || "";
  $("#dirInput").value = s.default_download_dir || "";
  $("#tokenCivitai").value = (s.site_tokens && s.site_tokens.civitai) || "";
  $("#tokenHf").value = (s.site_tokens && s.site_tokens.huggingface) || "";
  $("#proxyInput").value = s.proxy || "";
  $("#nsfwSwitch").checked = Boolean(s.blur_nsfw);
  $("#scanSwitch").checked = Boolean(s.scan_on_startup);
  $("#autoLinkSwitch").checked = Boolean(s.auto_link);

  const recent = s.recent_dirs || [];
  $("#recentMenu").innerHTML = recent.length
    ? recent.map((p) => `
        <div class="recent-opt" data-path="${esc(p)}" title="${esc(p)}">
          <span>${esc(p)}</span>
          <button class="recent-del" data-del="${esc(p)}" type="button" title="从最近目录中移除">${icon("x")}</button>
        </div>`).join("")
    : '<p class="recent-empty">还没有最近目录</p>';

  $("#recentList").innerHTML = recent.length
    ? recent.map((p) => `
        <div class="recent-item">
          ${icon("folder")}
          <span title="${esc(p)}">${esc(p)}</span>
          <button class="mini" data-recent="${esc(p)}">使用</button>
        </div>`).join("")
    : '<p class="hint" style="margin:0">还没有使用记录，选择一次目录后会自动记住。</p>';

  const libs = s.library_dirs || [];
  $("#libraryList").innerHTML = libs.length
    ? libs.map((p) => `
        <div class="recent-item">
          ${icon("folder")}
          <span title="${esc(p)}">${esc(p)}</span>
          <button class="mini" data-remove-lib="${esc(p)}">移除</button>
        </div>`).join("")
    : '<p class="hint" style="margin:0">尚未添加。建议加入你现有的模型库目录。</p>';

  const comfies = s.comfy_dirs || [];
  $("#comfyList").innerHTML = comfies.length
    ? comfies.map((p) => `
        <div class="recent-item">
          ${icon("link")}
          <span title="${esc(p)}">${esc(p)}</span>
          <button class="mini" data-remove-comfy="${esc(p)}">移除</button>
        </div>`).join("")
    : '<p class="hint" style="margin:0">还没有添加 ComfyUI 目录。</p>';

  $$("#linkMode button").forEach((b) => b.classList.toggle("active", b.dataset.mode === (s.link_mode || "auto")));
  applyCardScale();
}

/* ---------- 解析与下载 ---------- */
function hideParsePreview() {
  state.pendingPlan = null;
  const el = $("#parsePreview");
  el.hidden = true;
  el.innerHTML = "";
}

function cleanName(value, fallback = "") {
  const text = String(value ?? "")
    .replace(/[<>:"/\\|?*\x00-\x1f]/g, "_")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/[. ]+$/, "");
  return text || fallback;
}

function fileExt(filename) {
  const match = String(filename || "").match(/\.[A-Za-z0-9]+$/);
  return match ? match[0] : ".safetensors";
}

/* 模型类型：唯一的落点依据，决定 ComfyUI 目录与是否建同名文件夹 */
const MODEL_TYPES = [
  ["checkpoint", "基础模型（checkpoints）"],
  ["diffusion", "扩散模型（diffusion_models）"],
  ["lora", "LoRA（loras）"],
  ["vae", "VAE（vae）"],
  ["clip", "CLIP 文本编码器（text_encoders）"],
  ["upscale", "放大模型（upscale_models）"],
];
const TYPE_DIRS = {
  checkpoint: "checkpoints",
  diffusion: "diffusion_models",
  lora: "loras",
  vae: "vae",
  clip: "text_encoders",
  upscale: "upscale_models",
};

function renderParsePreview(plan) {
  const el = $("#parsePreview");
  state.pendingPlan = plan;
  const cover = plan.images && plan.images.length ? plan.images[0].url : "";
  const blur = state.settings.blur_nsfw && plan.nsfw;
  const modelType = plan.model_type && TYPE_DIRS[plan.model_type] ? plan.model_type : "checkpoint";
  const baseTag = plan.base_model && plan.base_model !== "未知"
    ? `<span class="hv-tag">基础模型 ${esc(plan.base_model)}</span>`
    : "";
  el.innerHTML = `
    <div class="pp-cover ${blur ? "blur" : ""}">
      ${cover ? `<img src="${esc(cover)}" alt="" referrerpolicy="no-referrer" />` : "<span>无示例图</span>"}
      ${plan.nsfw ? '<i class="pp-nsfw">NSFW</i>' : ""}
    </div>
    <div class="pp-main">
      <div class="pp-title">${esc(plan.title)}${plan.version_name ? `<em>${esc(plan.version_name)}</em>` : ""}</div>
      <div class="pp-tags">
        ${baseTag}
        <span class="hv-tag">${esc(plan.author || "未知作者")}</span>
        <span class="hv-tag">${plan.images.length} 张示例图</span>
      </div>
      <div class="pp-edit pair">
        <label>模型类型
          <select id="ppType">${MODEL_TYPES.map(([key, label]) => `<option value="${key}" ${key === modelType ? "selected" : ""}>${label}</option>`).join("")}</select>
        </label>
        <label>模型名称<input id="ppTitle" type="text" maxlength="96" value="${esc(plan.title)}" /></label>
      </div>
      <div class="pp-meta">
        <div><span>大小</span><strong>${fmtBytes(plan.size)}</strong></div>
        <div><span>示例图</span><strong>${plan.images.length} 张</strong></div>
        <div><span>归档到</span><strong id="ppTarget" title=""></strong></div>
        ${plan.trigger_words && plan.trigger_words.length ? `<div><span>触发词</span><strong title="${esc(plan.trigger_words.join(", "))}">${esc(plan.trigger_words.join(", "))}</strong></div>` : ""}
      </div>
      <p class="pp-desc">${esc(plan.description || "作者没有提供文字说明。")}</p>
      ${plan.site === "civitai" && !plan.has_token ? '<div class="pp-warn">Civitai 现已要求所有下载都提供 API Key。现在点「开始下载」会失败，建议先到设置里粘贴 Key。 <button class="link-btn" id="ppGoAuth" type="button">去粘贴</button></div>' : ""}
    </div>
    <div class="pp-actions">
      <button class="btn primary" id="btnStartDownload">开始下载</button>
      <button class="btn ghost" id="btnCancelParse">取消</button>
    </div>`;
  el.hidden = false;

  const titleInput = $("#ppTitle");
  const typeSelect = $("#ppType");

  const syncTarget = () => {
    const name = cleanName(titleInput.value, plan.title);
    // 模型文件平铺在类型目录下，说明和示例图放进同名文件夹
    const text = `${TYPE_DIRS[typeSelect.value]}\\${name}${fileExt(plan.filename)}`;
    const targetEl = $("#ppTarget");
    targetEl.textContent = text;
    targetEl.title = text;
  };

  titleInput.addEventListener("input", syncTarget);
  typeSelect.addEventListener("change", syncTarget);

  syncTarget();
  const goAuth = $("#ppGoAuth");
  if (goAuth) {
    goAuth.addEventListener("click", () => {
      switchPage("settings");
      $("#tokenCivitai").focus();
    });
  }
  $("#btnStartDownload").addEventListener("click", () => startDownload(plan));
  $("#btnCancelParse").addEventListener("click", hideParsePreview);
}

async function parseModel() {
  const url = $("#urlInput").value.trim();
  const dir = $("#dirInput").value.trim();
  if (!url) return toast("请先粘贴模型网页地址");
  if (!/^https?:\/\//i.test(url)) return toast("地址需要以 http(s):// 开头");
  if (!dir) return toast("请先选择保存目录");

  const btn = $("#btnParse");
  const label = btn.innerHTML;
  btn.disabled = true;
  btn.textContent = "解析中…";
  try {
    hideParsePreview();
    const res = await call("parse_url", url, dir);
    if (!res) {
      toast("网页预览模式无法联网解析，请在桌面窗口中使用");
      return;
    }
    if (!res.ok) {
      if (res.code === "auth") {
        switchPage("settings");
        $("#tokenCivitai").focus();
      }
      toast(res.message || "解析失败");
      return;
    }
    renderParsePreview(res);
  } finally {
    btn.disabled = false;
    btn.innerHTML = label;
  }
}

async function startDownload(basePlan) {
  const dir = $("#dirInput").value.trim();
  if (!dir) return toast("请先选择保存目录");

  const plan = { ...basePlan };
  const titleInput = $("#ppTitle");
  if (titleInput) {
    const title = cleanName(titleInput.value, basePlan.title);
    plan.title = title;
    plan.model_type = $("#ppType").value;
    plan.folder_name = title;
    plan.filename = title + fileExt(basePlan.filename);
  }

  const res = await call("create_task", plan, dir);
  if (!res || !res.ok) return toast((res && res.message) || "创建任务失败");
  if (res.settings) state.settings = res.settings;
  hideParsePreview();
  $("#urlInput").value = "";
  renderSettings();
  currentTab = "queue";
  $$("#taskTabs .tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === "queue"));
  await refreshTasks();
  toast("已加入下载队列");
}

async function refreshTasks() {
  const res = await call("get_tasks");
  if (!res || !res.tasks) return;
  const before = new Map(state.tasks.map((t) => [t.id, t.status]));
  state.tasks = res.tasks;
  renderTasks();
  if (state.tasks.some((t) => t.status === "done" && before.get(t.id) !== "done")) {
    scanLibrary();
  }
}

function openRenameModal(task) {
  const plan = task.plan || {};
  const filename = plan.filename || String(task.path || "").split("\\").pop() || "";
  state.renameTaskId = task.id;
  $("#rnTitle").value = plan.title || task.name || "";
  $("#rnFolder").value = plan.folder_name || task.name || "";
  $("#rnFile").value = String(filename).replace(/\.[A-Za-z0-9]+$/, "");
  $("#rnExt").textContent = fileExt(filename);
  $("#renameModal").hidden = false;
  $("#rnTitle").focus();
}

async function saveRename() {
  const id = state.renameTaskId;
  if (!id) return;
  const title = cleanName($("#rnTitle").value, "未命名模型");
  const folder = cleanName($("#rnFolder").value, title);
  const name = cleanName($("#rnFile").value, title) + $("#rnExt").textContent;
  const res = await call("rename_task", id, title, folder, name);
  if (!res || !res.ok) return toast((res && res.message) || "改名失败");
  $("#renameModal").hidden = true;
  state.renameTaskId = null;
  await refreshTasks();
  toast("已更新，下载完成会按新名字归档");
}

/* ---------- 页面切换 ---------- */
const PAGE_META = {
  download: { title: "下载任务", subtitle: "粘贴模型地址，自动归档为可管理的模型库" },
  library: { title: "模型库", subtitle: "直接读取磁盘上的模型、示例图与说明文档" },
  settings: { title: "设置", subtitle: "目录、站点凭证、扫描范围与 ComfyUI 接入" },
};

function switchPage(page) {
  $$(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.page === page));
  $$(".page").forEach((p) => p.classList.toggle("active", p.id === `page-${page}`));
  $("#pageTitle").textContent = PAGE_META[page].title;
  $("#pageSubtitle").textContent = PAGE_META[page].subtitle;
  if (page === "settings") loadCacheInfo();
}

/* ---------- 目录选择 ---------- */
async function pickFolder(targetInput) {
  if (!hasBackend()) {
    toast("网页预览模式无法打开系统目录选择框，请在桌面窗口中操作");
    return;
  }
  const res = await call("pick_folder", targetInput.value || state.settings.default_download_dir);
  if (!res || !res.ok) return;
  targetInput.value = res.path;
  state.settings.recent_dirs = res.recent_dirs;
  state.settings.default_download_dir = res.path;
  state.disk = (await call("get_disk", res.path)) || state.disk;
  renderSettings();
  renderDisk();
  toast("已选择：" + res.path);
}

async function saveSettings(patch, okText = "设置已保存") {
  const res = await call("save_settings", patch);
  if (res && res.settings) state.settings = res.settings;
  Object.assign(state.settings, patch);
  if (state.settings.default_download_dir) {
    state.disk = (await call("get_disk", state.settings.default_download_dir)) || state.disk;
  }
  renderSettings();
  renderDisk();
  renderTasks();
  if (okText) toast(okText);
}

/* ---------- 事件绑定 ---------- */
function bindEvents() {
  $$(".nav-item").forEach((btn) => btn.addEventListener("click", () => switchPage(btn.dataset.page)));

  $("#btnPickDir").addEventListener("click", () => pickFolder($("#dirInput")));
  $("#btnSettingsPickDir").addEventListener("click", () => pickFolder($("#settingsDir")));

  $("#btnRecent").addEventListener("click", (e) => {
    e.stopPropagation();
    const menu = $("#recentMenu");
    menu.hidden = !menu.hidden;
    if (menu.hidden) return;
    // 与按钮左对齐；右侧空间不足时整体左移，避免被窗口裁掉
    menu.style.left = "0px";
    const btn = $("#btnRecent");
    const overflow = btn.getBoundingClientRect().left + menu.offsetWidth - document.documentElement.clientWidth + 16;
    if (overflow > 0) menu.style.left = `${-overflow}px`;
  });

  $("#recentMenu").addEventListener("click", async (e) => {
    e.stopPropagation();
    const del = e.target.closest("[data-del]");
    if (del) {
      const path = del.dataset.del;
      const keep = $("#dirInput").value;
      const rest = (state.settings.recent_dirs || []).filter((p) => p !== path);
      await saveSettings({ recent_dirs: rest }, "已从最近目录中移除");
      $("#dirInput").value = keep;
      return;
    }
    const opt = e.target.closest("[data-path]");
    if (opt) {
      $("#dirInput").value = opt.dataset.path;
      $("#recentMenu").hidden = true;
    }
  });

  document.addEventListener("click", () => {
    const menu = $("#recentMenu");
    if (menu) menu.hidden = true;
  });

  $("#btnSetDefault").addEventListener("click", () => {
    const dir = $("#dirInput").value.trim();
    if (!dir) return toast("请先选择目录");
    const recent = [dir, ...(state.settings.recent_dirs || []).filter((p) => p !== dir)].slice(0, 8);
    saveSettings({ default_download_dir: dir, recent_dirs: recent }, "已设为默认下载目录");
  });

  $("#btnParse").addEventListener("click", parseModel);

  $("#urlInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") $("#btnParse").click();
  });

  $("#globalSearch").addEventListener("input", (e) => {
    state.searchTerm = e.target.value;
    renderModels();
  });

  $("#typeTabs").addEventListener("click", (e) => {
    const btn = e.target.closest(".type-tab");
    if (!btn) return;
    state.viewType = btn.dataset.type;
    state.viewBase = "all";
    renderTypeTabs();
    renderBaseChips();
    renderModels();
  });

  $("#baseChips").addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip) return;
    state.viewBase = chip.dataset.base;
    renderBaseChips();
    renderModels();
  });

  $("#cardScale").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-scale]");
    if (!btn) return;
    state.settings.card_scale = Number(btn.dataset.scale);
    applyCardScale();
    call("save_settings", { card_scale: state.settings.card_scale });
    toast(`卡片大小 ${Math.round(state.settings.card_scale * 100)}%`);
  });

  $("#viewToggle").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-view]");
    if (btn) setViewMode(btn.dataset.view);
  });

  $("#content").addEventListener(
    "scroll",
    () => {
      hideContextMenu();
      hideDocContextMenu();
    },
    { passive: true }
  );

  // 任务标签页
  $("#taskTabs").addEventListener("click", (e) => {
    const tab = e.target.closest(".tab");
    if (!tab) return;
    $$(".tab", $("#taskTabs")).forEach((t) => t.classList.toggle("active", t === tab));
    currentTab = tab.dataset.tab;
    renderTasks();
  });

  $("#taskList").addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-act]");
    if (!btn) return;
    const card = e.target.closest(".task");
    const task = state.tasks.find((t) => t.id === card.dataset.id);
    if (!task) return;
    const act = btn.dataset.act;
    if (act === "folder") {
      const res = await call("open_path", task.dir);
      if (res && !res.ok) toast(res.message || "该目录还不存在");
      return;
    }
    if (act === "auth") {
      switchPage("settings");
      $("#tokenCivitai").focus();
      toast("粘贴 API Key 后回到队列点重试");
      return;
    }
    if (act === "rename") {
      openRenameModal(task);
      return;
    }
    const res = await call("task_action", task.id, act);
    if (res && !res.ok) toast(res.message || "操作失败");
    if (act === "remove") toast("已从记录移除");
    if (act === "cancel") toast("已取消，临时文件已清理");
    await refreshTasks();
  });

  $("#btnPauseAll").addEventListener("click", async () => {
    await call("task_action", "", "pause_all");
    await refreshTasks();
    toast("已全部暂停");
  });

  $("#btnClearDone").addEventListener("click", async () => {
    await call("clear_done");
    await refreshTasks();
    toast("已清除下载记录");
  });

  $$("[data-open-url]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const res = await call("open_url", btn.dataset.openUrl);
      if (!res) toast("网页预览模式无法打开浏览器");
      else if (!res.ok) toast(res.message || "打开失败");
    });
  });

  $("#rnCancel").addEventListener("click", () => {
    $("#renameModal").hidden = true;
    state.renameTaskId = null;
  });
  $("#rnSave").addEventListener("click", saveRename);
  $("#renameModal").addEventListener("click", (e) => {
    if (e.target === $("#renameModal")) {
      $("#renameModal").hidden = true;
      state.renameTaskId = null;
    }
  });
  $("#rnTitle").addEventListener("keydown", (e) => {
    if (e.key === "Enter") saveRename();
  });

  $("#btnAddLibrary").addEventListener("click", async () => {
    const res = await call("add_library_dir");
    if (!res || !res.ok) {
      if (!hasBackend()) toast("网页预览模式无法打开系统目录选择框");
      return;
    }
    state.settings.library_dirs = res.library_dirs;
    renderSettings();
    toast("已加入扫描范围，正在读取…");
    await scanLibrary(true);
  });

  $("#libraryList").addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-remove-lib]");
    if (!btn) return;
    const p = btn.dataset.removeLib;
    await saveSettings(
      { library_dirs: (state.settings.library_dirs || []).filter((x) => x !== p) },
      "已移出扫描范围"
    );
    await scanLibrary();
  });

  $("#btnAddComfy").addEventListener("click", async () => {
    const res = await call("add_comfy_dir");
    if (!res || !res.ok) {
      if (!hasBackend()) toast("网页预览模式无法打开系统目录选择框");
      return;
    }
    state.settings.comfy_dirs = res.comfy_dirs;
    renderSettings();
    toast("已添加 ComfyUI 目录");
  });

  $("#comfyList").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-remove-comfy]");
    if (!btn) return;
    const p = btn.dataset.removeComfy;
    saveSettings({ comfy_dirs: (state.settings.comfy_dirs || []).filter((x) => x !== p) }, "已移除");
  });

  $("#linkMode").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-mode]");
    if (!btn) return;
    state.settings.link_mode = btn.dataset.mode;
    $$("#linkMode button").forEach((b) => b.classList.toggle("active", b === btn));
    const hints = {
      auto: "自动：优先写入映射配置 → 同盘用硬链接 → 跨盘才提示软链接权限。",
      yaml: "映射配置：写入 ComfyUI 的 extra_model_paths.yaml，官方支持，不需要管理员权限。",
      hardlink: "硬链接：不占额外空间，对 ComfyUI 完全透明；只在同一块磁盘内有效。",
      symlink: "软链接：可跨磁盘，但 Windows 默认需要管理员权限或开启开发者模式。",
    };
    $("#linkModeHint").textContent = hints[btn.dataset.mode] || "";
    call("save_settings", { link_mode: btn.dataset.mode });
  });

  $("#autoLinkSwitch").addEventListener("change", () => {
    saveSettings({ auto_link: $("#autoLinkSwitch").checked }, "已更新自动接入设置");
  });

  $("#recentList").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-recent]");
    if (!btn) return;
    $("#settingsDir").value = btn.dataset.recent;
    saveSettings({ default_download_dir: btn.dataset.recent }, "已切换默认目录");
  });

  $("#btnSaveSettings").addEventListener("click", () => {
    const dir = $("#settingsDir").value.trim();
    const recent = dir
      ? [dir, ...(state.settings.recent_dirs || []).filter((p) => p !== dir)].slice(0, 8)
      : state.settings.recent_dirs;
    saveSettings({
      default_download_dir: dir,
      recent_dirs: recent,
      site_tokens: { civitai: $("#tokenCivitai").value.trim(), huggingface: $("#tokenHf").value.trim() },
      proxy: $("#proxyInput").value.trim(),
      blur_nsfw: $("#nsfwSwitch").checked,
      scan_on_startup: $("#scanSwitch").checked,
    });
  });

  $("#btnClearCache").addEventListener("click", async () => {
    const ok = await askConfirm({
      title: "清理缓存",
      text: "将删除补数据的模型信息缓存（SHA256 与 Civitai 匹配结果）。\n下次整理会重新计算，不会影响模型文件、说明和设置。",
      okText: "清理",
      danger: true,
    });
    if (!ok) return;
    const res = await call("clear_cache");
    if (!res) return;
    if (!res.ok) return toast(res.message || "清理失败");
    state.cache = res.cache;
    renderCache();
    toast("缓存已清理");
  });

  $("#nsfwSwitch").addEventListener("change", () => {
    saveSettings({ blur_nsfw: $("#nsfwSwitch").checked }, "显示设置已保存");
    renderModels();
  });

  $("#btnRefresh").addEventListener("click", async () => {
    await refreshTasks();
    await scanLibrary(true);
  });

  $("#modelGrid").addEventListener("click", (e) => {
    const card = e.target.closest(".model-card");
    if (!card) return;
    const path = card.dataset.path;
    const model = state.models.find((m) => m.path === path);
    if (!model) return;
    const reveal = e.target.closest('[data-act="reveal"]');
    if (reveal) {
      const thumb = card.querySelector(".thumb");
      const hiddenNow = thumb.classList.toggle("blur");
      reveal.textContent = hiddenNow ? "NSFW" : "隐藏原图";
      return;
    }
    if (e.shiftKey && state.lastSelected) {
      selectRange(state.lastSelected, path);
      return;
    }
    if (e.target.closest('[data-act="select"]') || e.ctrlKey || e.metaKey) {
      toggleSelect(path);
      state.lastSelected = path;
      return;
    }
    state.lastSelected = path;
    openDetail(model);
  });

  $("#btnSelectAll").addEventListener("click", () => {
    visibleModels().forEach((m) => state.selected.add(m.path));
    updateSelection();
  });
  $("#btnSelectNone").addEventListener("click", () => {
    state.selected.clear();
    updateSelection();
  });

  $("#btnBatchFill").addEventListener("click", () => openOrganize([...state.selected]));
  $("#btnOrganizeAll").addEventListener("click", () => openOrganize([]));
  $("#orgStart").addEventListener("click", startOrganize);
  $("#orgPause").addEventListener("click", toggleOrganizePause);
  $("#orgCancel").addEventListener("click", async () => {
    const ok = await askConfirm({
      title: "取消补数据",
      text: "已经写入的资料会保留，尚未处理的模型不受影响。",
      okText: "取消任务",
      danger: true,
    });
    if (!ok) return;
    const res = await call("organize_action", "cancel");
    if (res && res.task) {
      state.organizeTask = res.task;
      renderOrganizeTask(res.task);
    }
  });
  const closeOrganize = () => {
    $("#organizeModal").hidden = true;
    if (organizeActive(state.organizeTask)) {
      toast("任务在后台继续，可从「补数据」重新打开查看");
    }
  };
  $("#orgClose").addEventListener("click", closeOrganize);
  $("#organizeModal").addEventListener("click", (e) => {
    if (e.target === $("#organizeModal")) closeOrganize();
  });
  $("#orgImages").addEventListener("change", renderOrganizeList);
  $("#orgOverwrite").addEventListener("change", renderOrganizeList);
  $("#btnBatchDelete").addEventListener("click", async () => {
    const paths = [...state.selected];
    if (!paths.length) return;
    const picked = state.models.filter((m) => state.selected.has(m.path));
    await deleteModels(paths, picked);
  });

  $("#sortSelect").addEventListener("change", () => {
    state.settings.library_sort = $("#sortSelect").value;
    renderModels();
    saveSettings({ library_sort: state.settings.library_sort }, null);
  });
  $("#btnSortDir").addEventListener("click", () => {
    state.settings.library_sort_desc = !state.settings.library_sort_desc;
    applySortUI();
    renderModels();
    saveSettings({ library_sort_desc: state.settings.library_sort_desc }, null);
  });

  $("#modelGrid").addEventListener("contextmenu", (e) => {
    const card = e.target.closest(".model-card");
    if (!card) return;
    e.preventDefault();
    const model = state.models.find((m) => m.path === card.dataset.path);
    if (model) showContextMenu(e, model);
  });

  $("#ctxMenu").addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-ctx]");
    if (!btn || btn.disabled) return;
    const action = btn.dataset.ctx;
    const model = state.ctxModel;
    hideContextMenu();
    if (!model) return;
    if (action === "detail") openDetail(model);
    else if (action === "edit") openEditModel(model);
    else if (action === "open") {
      const res = await call("open_path", model.dir);
      if (res && !res.ok) toast(res.message || "打不开该文件夹");
    } else if (action === "trigger") {
      copyText(model.trigger, "触发词已复制");
    } else if (action === "delete") {
      await deleteModels([model.path], [model]);
    }
  });
  document.addEventListener("click", () => {
    hideContextMenu();
    hideDocContextMenu();
  });
  window.addEventListener("blur", () => {
    hideContextMenu();
    hideDocContextMenu();
  });

  $("#detailClose").addEventListener("click", closeDetail);
  $("#detailModal").addEventListener("click", (e) => {
    if (e.target === $("#detailModal")) closeDetail();
  });
  $("#galleryPrev").addEventListener("click", () => stepDetail(-1));
  $("#galleryNext").addEventListener("click", () => stepDetail(1));
  $("#detailThumbs").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-index]");
    if (!btn) return;
    state.detailIndex = Number(btn.dataset.index);
    renderDetail();
  });
  $("#detailTags").addEventListener("click", (e) => {
    if (!e.target.closest("#detailRevealTag")) return;
    state.detailRevealed = !state.detailRevealed;
    renderDetail();
  });
  $("#detailOpen").addEventListener("click", async () => {
    const m = state.detailModel;
    if (!m) return;
    const res = await call("open_path", m.dir);
    if (res && !res.ok) toast(res.message || "打不开该文件夹");
  });
  $("#detailCopyTrigger").addEventListener("click", () => copyText(state.detailModel && state.detailModel.trigger, "触发词已复制"));
  $("#detailOpenLink").addEventListener("click", async () => {
    const m = state.detailModel;
    if (!m || !m.source) return toast("这个模型没有记录网页链接");
    const res = await call("open_url", m.source);
    if (res && !res.ok) toast(res.message || "打不开链接");
  });
  $("#detailCopyDoc").addEventListener("click", () => {
    const m = state.detailModel;
    if (!m) return;
    copyText(m.doc || m.desc, "说明已复制");
  });

  // 详情里的说明文字：网页默认右键菜单被禁用了，这里补一个复制菜单
  $("#detailDoc").addEventListener("contextmenu", (e) => {
    e.preventDefault();
    showDocContextMenu(e);
  });
  $("#docCtxMenu").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-doc-ctx]");
    if (!btn || btn.disabled) return;
    const action = btn.dataset.docCtx;
    hideDocContextMenu();
    if (action === "copy") {
      const text = state.docSelection || (window.getSelection() ? window.getSelection().toString() : "");
      copyText(text.trim(), "已复制选中内容");
    } else if (action === "copy-all") {
      const m = state.detailModel;
      copyText(m ? m.doc || m.desc : "", "说明已复制");
    } else if (action === "select-all") {
      const pre = $("#detailDoc");
      const range = document.createRange();
      range.selectNodeContents(pre);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
    }
  });
  document.addEventListener("contextmenu", (e) => {
    if (!e.target.closest("#detailDoc")) hideDocContextMenu();
  });

  $("#editCancel").addEventListener("click", closeEditModel);
  $("#editSave").addEventListener("click", saveEditModel);
  $("#editModelModal").addEventListener("click", (e) => {
    if (e.target === $("#editModelModal")) closeEditModel();
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      hideContextMenu();
      hideDocContextMenu();
    }
    if (e.key === "Escape" && !$("#editModelModal").hidden) {
      closeEditModel();
      return;
    }
    if (e.key === "Escape" && !$("#confirmModal").hidden) {
      $("#confirmCancel").click();
      return;
    }
    if (e.key === "Escape" && !$("#organizeModal").hidden) {
      $("#orgClose").click();
      return;
    }
    if ($("#detailModal").hidden) return;
    if (e.key === "Escape") closeDetail();
    else if (e.key === "ArrowLeft") stepDetail(-1);
    else if (e.key === "ArrowRight") stepDetail(1);
  });
}

async function copyText(value, message) {
  if (!value) return toast("没有可复制的内容");
  try {
    await navigator.clipboard.writeText(value);
    toast(message);
  } catch {
    toast("复制失败，请手动选择");
  }
}

function showContextMenu(event, model) {
  state.ctxModel = model;
  const menu = $("#ctxMenu");
  const triggerBtn = menu.querySelector('[data-ctx="trigger"]');
  triggerBtn.disabled = !model.trigger;
  menu.hidden = false;
  const rect = menu.getBoundingClientRect();
  let left = event.clientX;
  let top = event.clientY;
  if (left + rect.width > window.innerWidth - 8) left = window.innerWidth - rect.width - 8;
  if (top + rect.height > window.innerHeight - 8) top = window.innerHeight - rect.height - 8;
  menu.style.left = `${Math.max(8, left)}px`;
  menu.style.top = `${Math.max(8, top)}px`;
}

function hideContextMenu() {
  $("#ctxMenu").hidden = true;
}

function showDocContextMenu(event) {
  const menu = $("#docCtxMenu");
  const selection = window.getSelection();
  const hasSelection = Boolean(
    selection && selection.toString().trim() && $("#detailDoc").contains(selection.anchorNode)
  );
  // 点击菜单时选区可能被清掉，先记住选中的文字
  state.docSelection = hasSelection ? selection.toString() : "";
  menu.querySelector('[data-doc-ctx="copy"]').disabled = !hasSelection;
  menu.hidden = false;
  const rect = menu.getBoundingClientRect();
  let left = event.clientX;
  let top = event.clientY;
  if (left + rect.width > window.innerWidth - 8) left = window.innerWidth - rect.width - 8;
  if (top + rect.height > window.innerHeight - 8) top = window.innerHeight - rect.height - 8;
  menu.style.left = `${Math.max(8, left)}px`;
  menu.style.top = `${Math.max(8, top)}px`;
}

function hideDocContextMenu() {
  $("#docCtxMenu").hidden = true;
}

/* ---------- 启动 ---------- */
async function boot() {
  if (booted) return;
  booted = true;

  bindEvents();
  renderTasks();
  renderModels();
  renderSettings();
  renderDisk();
  renderStatus();

  const info = await call("get_state");
  if (info) {
    state.settings = info.settings;
    state.disk = info.disk;
    state.cache = info.cache || null;
    $("#configPath").textContent = "配置：" + info.config_path;
    $("#statusVersion").textContent = "v" + info.app.version;
    renderSettings();
    renderDisk();
    renderCache();
    setViewMode(state.settings.library_view || "grid", false);
    applySortUI();
    await refreshTasks();
    await refreshOrganize();
    setInterval(() => {
      refreshTasks();
      refreshOrganize();
    }, 900);
    if ((state.settings.library_dirs || []).length) scanLibrary();
  } else {
    $("#configPath").textContent = "网页预览模式：设置不会写入本机";
  }
}

document.addEventListener("DOMContentLoaded", () => {
  window.addEventListener("pywebviewready", boot);
  setTimeout(() => { if (!booted) boot(); }, 400);
});
