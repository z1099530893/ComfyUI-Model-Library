/* ComfyUI 本地模型库 —— 侧边栏标签页
 *
 * 只读展示 ComfyUI 模型目录里的模型、示例图与说明；
 * 支持把 LoRA / 基础模型 / 扩散模型一键插入到画布中央。
 */

import { app } from "../../scripts/app.js";

const API = "/model_library";
const STORAGE_BLUR = "modelLibrary.nsfwBlur";
const STORAGE_REVEALED = "modelLibrary.revealed";
const STORAGE_VIEW = "modelLibrary.view";
const CSS_URL = new URL("./model_library.css", import.meta.url).href;

// 类型筛选顺序：前四个常驻，其余有模型才显示
const TYPE_CHIPS = [
  { type: "checkpoint", label: "Checkpoint 模型", primary: true },
  { type: "diffusion", label: "UNET 模型", primary: true },
  { type: "lora", label: "LoRA 模型", primary: true },
  { type: "vae", label: "VAE 模型" },
  { type: "clip", label: "CLIP 模型" },
  { type: "upscale", label: "Upscale 模型" },
];

const state = {
  el: null,
  loaded: false,
  loading: false,
  error: "",
  models: [],
  types: [],
  query: "",
  type: "all",
  base: "all",
  sort: "name",
  desc: false,
  view: localStorage.getItem(STORAGE_VIEW) === "list" ? "list" : "grid",
  nsfwBlur: localStorage.getItem(STORAGE_BLUR) !== "0",
  revealed: loadRevealed(),
  detail: null,
  detailData: null,
  detailIndex: 0,
  detailLoading: false,
};

let searchTimer = null;

/* ------------------------------------------------------------------ */
/* 工具                                                                */
/* ------------------------------------------------------------------ */
function $(selector, root = state.el || document) {
  return root?.querySelector(selector) || null;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function loadRevealed() {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_REVEALED) || "[]");
    return new Set(Array.isArray(raw) ? raw : []);
  } catch {
    return new Set();
  }
}

function saveRevealed() {
  try {
    const list = Array.from(state.revealed).slice(-800);
    localStorage.setItem(STORAGE_REVEALED, JSON.stringify(list));
  } catch {
    /* 忽略存储失败 */
  }
}

function ensureCss() {
  if (document.querySelector("link[data-model-library]")) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = CSS_URL;
  link.dataset.modelLibrary = "1";
  document.head.appendChild(link);
}

function humanSize(bytes) {
  const value = Number(bytes) || 0;
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  const digits = index === 0 ? 0 : index === 1 ? 0 : 1;
  return `${size.toFixed(digits)} ${units[index]}`;
}

function humanDate(seconds) {
  if (!seconds) return "";
  const date = new Date(seconds * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

// 列表缩略图只跟随右上角的全局开关
function coverHidden(model) {
  return Boolean(model?.nsfw && state.nsfwBlur);
}

// 详情页允许单独查看 / 重新模糊
function isHidden(model) {
  if (!model?.nsfw || !state.nsfwBlur) return false;
  return !state.revealed.has(model.id);
}

function toast(message, kind = "ok") {
  const el = $(".ml-toast");
  if (!el) return;
  el.textContent = message;
  el.dataset.kind = kind;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => {
    el.hidden = true;
  }, 2400);
}

async function copyText(text, label) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    try {
      document.execCommand("copy");
    } catch {
      /* 忽略 */
    }
    area.remove();
  }
  toast(`${label}已复制`);
}

async function openFolder(id) {
  if (!id) return;
  try {
    const response = await fetch(`${API}/reveal?id=${encodeURIComponent(id)}`, {
      method: "POST",
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || `HTTP ${response.status}`);
    }
    toast("已在文件夹中打开");
  } catch (error) {
    toast(`打开失败：${error?.message || error}`, "error");
  }
}

/* ------------------------------------------------------------------ */
/* 数据                                                                */
/* ------------------------------------------------------------------ */
async function loadList(force = false) {
  state.loading = true;
  state.error = "";
  renderRefresh();
  renderStatus();

  try {
    const response = await fetch(`${API}/list${force ? "?refresh=1" : ""}`, {
      cache: "no-store",
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    state.models = Array.isArray(data.models) ? data.models : [];
    state.types = Array.isArray(data.types) ? data.types : [];
    state.loaded = true;
    if (state.base !== "all" && !state.models.some((m) => m.base === state.base)) {
      state.base = "all";
    }
  } catch (error) {
    state.error = `读取失败：${error?.message || error}`;
  } finally {
    state.loading = false;
    renderRefresh();
    renderAll();
  }
}

async function loadDetail(id) {
  state.detailLoading = true;
  renderDetail();
  try {
    const response = await fetch(`${API}/detail?id=${encodeURIComponent(id)}`, {
      cache: "no-store",
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (state.detail?.id !== id) return;
    state.detailData = data.model || null;
  } catch {
    if (state.detail?.id === id) state.detailData = null;
  } finally {
    if (state.detail?.id === id) {
      state.detailLoading = false;
      renderDetail();
    }
  }
}

/* ------------------------------------------------------------------ */
/* 筛选与排序                                                          */
/* ------------------------------------------------------------------ */
function visibleModels() {
  let list = state.models;

  if (state.type !== "all") {
    list = list.filter((model) => model.type === state.type);
  }
  if (state.base !== "all") {
    list = list.filter((model) => model.base === state.base);
  }

  const query = state.query.trim().toLowerCase();
  if (query) {
    list = list.filter((model) =>
      [
        model.name,
        model.title,
        model.file,
        model.base,
        model.trigger,
        model.author,
        ...(model.tags || []),
      ]
        .join(" ")
        .toLowerCase()
        .includes(query)
    );
  }

  const compare = {
    name: (a, b) => a.name.localeCompare(b.name, "zh-Hans-CN"),
    time: (a, b) => a.mtime - b.mtime,
    size: (a, b) => a.size - b.size,
    base: (a, b) =>
      a.base.localeCompare(b.base, "zh-Hans-CN") ||
      a.name.localeCompare(b.name, "zh-Hans-CN"),
  };

  const direction = state.desc ? -1 : 1;
  return [...list].sort((a, b) => (compare[state.sort] || compare.name)(a, b) * direction);
}

/* ------------------------------------------------------------------ */
/* 渲染：标签、筛选、卡片                                              */
/* ------------------------------------------------------------------ */
function renderAll() {
  renderTypes();
  renderBases();
  renderGrid();
  renderStatus();
}

function renderRefresh() {
  const button = $(".ml-refresh");
  if (!button) return;
  button.classList.toggle("busy", state.loading);
}

function renderViewButton() {
  const button = $(".ml-view");
  if (!button) return;
  const isList = state.view === "list";
  button.innerHTML = `<i class="pi ${isList ? "pi-th-large" : "pi-list"}"></i>`;
  button.title = isList ? "切换到网格视图" : "切换到列表视图";
}

function renderTypes() {
  const box = $(".ml-types");
  if (!box) return;

  const total = state.models.length;
  const counts = new Map(state.types.map((item) => [item.type, item.count]));
  const chips = [{ type: "all", label: "全部", count: total }];
  for (const chip of TYPE_CHIPS) {
    const count = counts.get(chip.type) || 0;
    if (chip.primary || count > 0) chips.push({ ...chip, count });
  }
  if (!chips.some((chip) => chip.type === state.type)) state.type = "all";
  box.innerHTML = chips
    .map(
      (chip) => `
      <button class="ml-chip ${chip.type === state.type ? "active" : ""}" data-type="${escapeHtml(
        chip.type
      )}">
        ${escapeHtml(chip.label)} <em>${chip.count}</em>
      </button>`
    )
    .join("");
}

function baseOptions() {
  let list = state.models;
  if (state.type !== "all") list = list.filter((model) => model.type === state.type);
  const counts = new Map();
  for (const model of list) {
    counts.set(model.base, (counts.get(model.base) || 0) + 1);
  }
  return Array.from(counts.entries()).sort((a, b) => a[0].localeCompare(b[0], "zh-Hans-CN"));
}

function renderBases() {
  const select = $(".ml-base");
  if (!select) return;

  const options = baseOptions();
  select.innerHTML =
    `<option value="all">全部分类</option>` +
    options
      .map(
        ([base, count]) =>
          `<option value="${escapeHtml(base)}">${escapeHtml(base)} (${count})</option>`
      )
      .join("");
  select.value = options.some(([base]) => base === state.base) ? state.base : "all";
  if (select.value === "all") state.base = "all";

  const sort = $(".ml-sort");
  if (sort) sort.value = state.sort;

  const order = $(".ml-order");
  if (order) {
    order.classList.toggle("desc", state.desc);
    order.innerHTML = `<i class="pi pi-sort-amount-${
      state.desc ? "down" : "up"
    }"></i>`;
  }
}

function cardHtml(model) {
  const hidden = coverHidden(model);
  const cover = model.cover
    ? `<img src="${escapeHtml(model.cover)}" alt="" loading="lazy" decoding="async" draggable="false" onerror="this.remove()" class="${
        hidden ? "blurred" : ""
      }">`
    : "";

  return `
    <article class="ml-card" data-id="${escapeHtml(model.id)}" title="${escapeHtml(model.name)}${
      model.node ? "（可拖到画布）" : ""
    }" ${model.node ? 'draggable="true"' : ""}>
      <div class="ml-cover">
        <div class="ml-ph"><span>${escapeHtml(model.type_label)}</span></div>
        ${cover}
        ${hidden ? `<span class="ml-nsfw-mask">NSFW</span>` : ""}
        ${
          model.node
            ? `<button class="ml-insert" title="插入 ${escapeHtml(model.node)}"><i class="pi pi-plus"></i></button>`
            : ""
        }
        ${model.count > 1 ? `<span class="ml-count">${model.count}</span>` : ""}
      </div>
      <div class="ml-caption">
        <div class="ml-name" title="${escapeHtml(model.name)}">${escapeHtml(model.name)}</div>
        <div class="ml-sub">
          <span class="ml-type type-${escapeHtml(model.type)}">${escapeHtml(model.type_label)}</span>
          <span class="ml-base-name">${escapeHtml(model.base)}</span>
        </div>
        <div class="ml-extra">
          <span>${humanSize(model.size)}</span>
          <span>${humanDate(model.mtime)}</span>
        </div>
      </div>
    </article>`;
}

function renderGrid() {
  const grid = $(".ml-grid");
  if (!grid) return;
  grid.dataset.view = state.view;
  grid.innerHTML = visibleModels().map(cardHtml).join("");
}

function renderStatus() {
  const status = $(".ml-status");
  const grid = $(".ml-grid");
  if (!status || !grid) return;

  if (state.loading && !state.models.length) {
    status.innerHTML = `<div class="ml-spinner"></div><p>正在扫描模型目录…</p>`;
    status.hidden = false;
    grid.hidden = true;
    return;
  }

  if (state.error) {
    status.innerHTML = `<p class="ml-error">${escapeHtml(state.error)}</p>
      <button class="ml-btn ml-retry">重试</button>`;
    status.hidden = false;
    grid.hidden = true;
    return;
  }

  if (!state.models.length) {
    status.innerHTML = `<p>模型目录里还没有模型</p>
      <p class="ml-hint">把模型放进 ComfyUI 的 models 目录后，点击右上角刷新。</p>`;
    status.hidden = false;
    grid.hidden = true;
    return;
  }

  if (!visibleModels().length) {
    status.innerHTML = `<p>没有匹配的模型</p>`;
    status.hidden = false;
    grid.hidden = true;
    return;
  }

  status.hidden = true;
  grid.hidden = false;
}

/* ------------------------------------------------------------------ */
/* 详情                                                                */
/* ------------------------------------------------------------------ */
function openDetail(id) {
  const basic = state.models.find((model) => model.id === id);
  if (!basic) return;
  state.detail = basic;
  state.detailData = null;
  state.detailIndex = 0;
  state.detailLoading = true;
  renderDetail();
  loadDetail(id);
}

function closeDetail() {
  state.detail = null;
  state.detailData = null;
  state.detailIndex = 0;
  const panel = $(".ml-detail");
  if (panel) {
    panel.hidden = true;
    panel.innerHTML = "";
  }
  const root = $(".ml-root");
  root?.classList.remove("detail-open");
}

function detailModel() {
  return state.detailData || state.detail;
}

function detailImages() {
  return state.detailData?.images || [];
}

function detailHtml(model) {
  const images = detailImages();
  const count = images.length;
  const index = Math.min(Math.max(state.detailIndex, 0), Math.max(count - 1, 0));
  let current = count ? images[index] : null;
  // 详情还没返回时先用列表里的缩略图顶着，避免闪一下「没有示例图」
  if (!current && state.detailLoading && model.cover) {
    current = { url: model.cover };
  }
  const hidden = isHidden(model);
  const trigger = model.trigger || "";
  const canToggleNsfw = model.nsfw && state.nsfwBlur;
  const imageRole = canToggleNsfw ? ' data-role="toggle-nsfw"' : "";

  const media = current
    ? `<img src="${escapeHtml(current.url)}" alt="" class="${
        hidden ? "blurred" : ""
      }${canToggleNsfw ? " toggleable" : ""}"${imageRole}>
       ${hidden ? `<button class="ml-nsfw-mask big" data-role="toggle-nsfw">NSFW · 点击查看</button>` : ""}
       ${
         canToggleNsfw
           ? `<button class="ml-media-eye" data-role="toggle-nsfw" title="${
               hidden ? "显示 NSFW 图片" : "重新模糊"
             }"><i class="pi ${hidden ? "pi-eye" : "pi-eye-slash"}"></i></button>`
           : ""
       }
       ${
         count > 1
           ? `<button class="ml-media-nav prev" data-role="prev" title="上一张"><i class="pi pi-angle-left"></i></button>
              <button class="ml-media-nav next" data-role="next" title="下一张"><i class="pi pi-angle-right"></i></button>
              <span class="ml-media-index">${index + 1} / ${count}</span>`
           : ""
       }`
    : `<div class="ml-media-empty"><i class="pi pi-image"></i><span>没有示例图</span></div>`;

  const meta = [
    model.author ? `作者：${escapeHtml(model.author)}` : "",
    model.version ? `版本：${escapeHtml(model.version)}` : "",
    model.downloaded_at ? `下载：${escapeHtml(model.downloaded_at)}` : "",
    `大小：${humanSize(model.size)}`,
    `修改：${humanDate(model.mtime)}`,
  ]
    .filter(Boolean)
    .map((item) => `<span>${item}</span>`)
    .join("");

  const desc = state.detailData?.desc || model.desc || "";
  const doc = state.detailData?.doc || model.doc || "";

  return `
    <div class="ml-detail-head">
      <button class="ml-icon" data-role="close" title="返回"><i class="pi pi-arrow-left"></i></button>
      <div class="ml-detail-title" title="${escapeHtml(model.name)}">${escapeHtml(model.name)}</div>
      ${
        model.page_url
          ? `<button class="ml-icon" data-role="page" title="打开模型网页"><i class="pi pi-external-link"></i></button>`
          : ""
      }
    </div>

    <div class="ml-detail-scroll">
      <div class="ml-media">${media}</div>

      <div class="ml-dmeta-line">
        <span class="ml-type type-${escapeHtml(model.type)}">${escapeHtml(model.type_label)}</span>
        <span class="ml-base-name">${escapeHtml(model.base)}</span>
        ${model.nsfw ? `<span class="ml-nsfw-tag">NSFW</span>` : ""}
      </div>

      <div class="ml-dactions">
        ${
          model.node
            ? `<button class="ml-btn primary" data-role="insert"><i class="pi pi-plus"></i>插入节点</button>`
            : ""
        }
        <button class="ml-btn" data-role="copy-name">复制名称</button>
        <button class="ml-btn" data-role="open-folder"><i class="pi pi-folder-open"></i>打开文件夹</button>
        <button class="ml-btn" data-role="copy-trigger" ${trigger ? "" : "disabled"}>复制触发词</button>
      </div>

      ${
        model.node
          ? `<div class="ml-drag-tip"><i class="pi pi-arrows-alt"></i>也可以把模型卡片直接拖到画布上</div>`
          : ""
      }

      <div class="ml-info">${meta}</div>

      ${
        trigger
          ? `<section class="ml-field"><h4>触发词</h4><p class="ml-trigger">${escapeHtml(
              trigger
            )}</p></section>`
          : ""
      }

      ${
        state.detailLoading && !state.detailData
          ? `<div class="ml-detail-loading">正在读取说明…</div>`
          : ""
      }

      ${
        doc || desc
          ? `<section class="ml-field">
              <div class="ml-field-head">
                <h4>模型使用说明</h4>
                <button class="ml-mini" data-role="copy-doc">复制说明</button>
              </div>
              ${
                doc
                  ? `<pre class="ml-doc">${escapeHtml(doc)}</pre>`
                  : `<p class="ml-desc">${escapeHtml(desc)}</p>`
              }
            </section>`
          : ""
      }
    </div>`;
}

function renderDetail() {
  const panel = $(".ml-detail");
  if (!panel) return;

  const model = state.detail;
  if (!model) {
    panel.hidden = true;
    panel.innerHTML = "";
    $(".ml-root")?.classList.remove("detail-open");
    return;
  }

  panel.hidden = false;
  panel.innerHTML = detailHtml(model);
  $(".ml-root")?.classList.add("detail-open");

  const image = $('[data-role="detail-image"]', panel);
  if (image) {
    image.addEventListener("error", () => {
      image.classList.add("broken");
    });
  }
}

function stepDetail(delta) {
  const count = detailImages().length;
  if (count <= 1) return;
  state.detailIndex = (state.detailIndex + delta + count) % count;
  renderDetail();
}

/* ------------------------------------------------------------------ */
/* 插入节点                                                            */
/* ------------------------------------------------------------------ */
function insertNode(model, at = null) {
  if (!model?.node) {
    toast("该类型暂不支持一键插入");
    return;
  }

  const LiteGraph = window.LiteGraph;
  if (!LiteGraph?.createNode) {
    toast("画布还没准备好，请稍后再试", "warn");
    return;
  }

  let node = null;
  try {
    node = LiteGraph.createNode(model.node);
  } catch {
    node = null;
  }
  if (!node) {
    toast(`没有找到节点 ${model.node}`, "error");
    return;
  }

  try {
    app.graph.add(node);
  } catch (error) {
    toast(`插入失败：${error?.message || error}`, "error");
    return;
  }

  const widget = node.widgets?.find((item) => item.name === model.widget);
  if (widget) {
    widget.value = model.combo;
    try {
      node.onWidgetChanged?.(model.widget, model.combo, undefined, widget);
    } catch {
      /* 某些节点没有这个回调 */
    }
  }

  try {
    if (at) {
      // 拖拽落点：节点左上角贴着鼠标
      node.pos = [at[0], at[1]];
    } else {
      const canvasEl = app.canvas.canvas;
      const rect = canvasEl.getBoundingClientRect();
      const [x, y] = app.canvas.ds.convertCanvasToOffset([rect.width / 2, rect.height / 2]);
      const [width, height] = node.size || [220, 120];
      node.pos = [x - width / 2, y - height / 2];
    }
  } catch {
    /* 定位失败不影响插入 */
  }

  try {
    app.canvas.selectNode(node, false);
  } catch {
    try {
      app.canvas.selectNode(node);
    } catch {
      /* 忽略 */
    }
  }
  app.graph.setDirtyCanvas(true, true);

  toast(`已插入 ${model.node}`);
  if (state.detail) closeDetail();
}

/* ------------------------------------------------------------------ */
/* 拖拽到画布                                                          */
/* ------------------------------------------------------------------ */
const NODE_MIME = "application/x-model-library";
let dropBound = false;
let dragGuardUntil = 0;

function canvasRect() {
  const canvasEl = app.canvas?.canvas;
  if (!canvasEl) return null;
  const rect = canvasEl.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0 ? rect : null;
}

function hasModelDrag(event) {
  return Array.from(event.dataTransfer?.types || []).includes(NODE_MIME);
}

function inCanvas(event, rect) {
  return (
    event.clientX >= rect.left &&
    event.clientX <= rect.right &&
    event.clientY >= rect.top &&
    event.clientY <= rect.bottom
  );
}

function onCanvasDragOver(event) {
  const rect = canvasRect();
  if (!rect || !hasModelDrag(event) || !inCanvas(event, rect)) return;
  event.preventDefault();
  if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
}

function onCanvasDrop(event) {
  const rect = canvasRect();
  if (!rect || !hasModelDrag(event) || !inCanvas(event, rect)) return;
  event.preventDefault();
  event.stopPropagation();

  let model = null;
  try {
    model = JSON.parse(event.dataTransfer.getData(NODE_MIME));
  } catch {
    return;
  }
  if (!model?.node) return;

  let point = null;
  try {
    point = app.canvas.ds.convertCanvasToOffset([
      event.clientX - rect.left,
      event.clientY - rect.top,
    ]);
  } catch {
    point = null;
  }
  insertNode(model, point ? [point[0] - 10, point[1] - 10] : null);
}

function bindCanvasDrop() {
  if (dropBound) return;
  dropBound = true;
  window.addEventListener("dragover", onCanvasDragOver, true);
  window.addEventListener("drop", onCanvasDrop, true);
}

/* ------------------------------------------------------------------ */
/* 事件绑定                                                            */
/* ------------------------------------------------------------------ */
function bindEvents(el) {
  const search = $(".ml-search input");
  search?.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.query = search.value || "";
      renderGrid();
      renderStatus();
    }, 120);
  });

  $(".ml-nsfw")?.addEventListener("click", () => {
    state.nsfwBlur = !state.nsfwBlur;
    localStorage.setItem(STORAGE_BLUR, state.nsfwBlur ? "1" : "0");
    renderNsfwButton();
    renderGrid();
    if (state.detail) renderDetail();
  });

  $(".ml-refresh")?.addEventListener("click", () => {
    if (!state.loading) loadList(true);
  });

  $(".ml-types")?.addEventListener("click", (event) => {
    const chip = event.target.closest(".ml-chip");
    if (!chip) return;
    state.type = chip.dataset.type || "all";
    state.base = "all";
    renderTypes();
    renderBases();
    renderGrid();
    renderStatus();
  });

  $(".ml-base")?.addEventListener("change", (event) => {
    state.base = event.target.value || "all";
    renderGrid();
    renderStatus();
  });

  $(".ml-sort")?.addEventListener("change", (event) => {
    state.sort = event.target.value || "name";
    renderGrid();
    renderStatus();
  });

  $(".ml-order")?.addEventListener("click", () => {
    state.desc = !state.desc;
    renderBases();
    renderGrid();
    renderStatus();
  });

  $(".ml-view")?.addEventListener("click", () => {
    state.view = state.view === "list" ? "grid" : "list";
    localStorage.setItem(STORAGE_VIEW, state.view);
    renderViewButton();
    renderGrid();
  });

  $(".ml-grid")?.addEventListener("dragstart", (event) => {
    const card = event.target.closest?.(".ml-card");
    if (!card || card.getAttribute("draggable") !== "true") {
      event.preventDefault();
      return;
    }

    const model = state.models.find((item) => item.id === card.dataset.id);
    if (!model?.node) {
      event.preventDefault();
      return;
    }

    dragGuardUntil = Date.now() + 800;
    const payload = JSON.stringify({
      node: model.node,
      widget: model.widget,
      combo: model.combo,
      name: model.name,
    });
    event.dataTransfer.setData(NODE_MIME, payload);
    event.dataTransfer.effectAllowed = "copy";

    const image = card.querySelector(".ml-cover img");
    if (image) {
      try {
        event.dataTransfer.setDragImage(image, image.width / 2 || 32, image.height / 2 || 32);
      } catch {
        /* 某些浏览器不支持自定义拖拽图 */
      }
    }
  });

  $(".ml-grid")?.addEventListener("click", (event) => {
    if (Date.now() < dragGuardUntil) return;
    const insert = event.target.closest(".ml-insert");
    const card = event.target.closest(".ml-card");
    if (!card) return;

    if (insert) {
      event.stopPropagation();
      const model = state.models.find((item) => item.id === card.dataset.id);
      if (model) insertNode(model);
      return;
    }
    openDetail(card.dataset.id);
  });

  $(".ml-status")?.addEventListener("click", (event) => {
    if (event.target.closest(".ml-retry")) loadList(false);
  });

  $(".ml-detail")?.addEventListener("click", async (event) => {
    const target = event.target.closest("[data-role]");
    if (!target) return;
    const role = target.dataset.role;
    const model = detailModel();
    if (!model) return;

    switch (role) {
      case "close":
        closeDetail();
        break;
      case "page":
        if (model.page_url) window.open(model.page_url, "_blank", "noopener");
        break;
      case "toggle-nsfw":
        toggleReveal(model.id);
        break;
      case "prev":
        stepDetail(-1);
        break;
      case "next":
        stepDetail(1);
        break;
      case "insert":
        insertNode(model);
        break;
      case "copy-name":
        copyText(model.name, "名称");
        break;
      case "open-folder":
        await openFolder(model.id);
        break;
      case "copy-trigger":
        if (model.trigger) copyText(model.trigger, "触发词");
        break;
      case "copy-doc": {
        const doc = state.detailData?.doc || model.doc || "";
        const desc = state.detailData?.desc || model.desc || "";
        if (doc || desc) copyText(doc || desc, "说明");
        break;
      }
      default:
        break;
    }
  });

  // 图片加载失败时隐藏，露出占位图
  el.addEventListener(
    "error",
    (event) => {
      const image = event.target;
      if (image instanceof HTMLImageElement) {
        image.classList.add("broken");
        image.closest(".ml-cover, .ml-media")?.classList.add("no-image");
      }
    },
    true
  );
}

function toggleReveal(id) {
  if (!id) return;
  if (state.revealed.has(id)) {
    state.revealed.delete(id);
  } else {
    state.revealed.add(id);
  }
  saveRevealed();
  renderGrid();
  if (state.detail?.id === id) renderDetail();
}

function renderNsfwButton() {
  const button = $(".ml-nsfw");
  if (!button) return;
  button.classList.toggle("active", state.nsfwBlur);
  button.title = state.nsfwBlur ? "NSFW 图片已模糊（点击显示）" : "NSFW 图片未模糊（点击模糊）";
}

/* ------------------------------------------------------------------ */
/* 挂载                                                                */
/* ------------------------------------------------------------------ */
function mount(el) {
  state.el = el;
  el.classList.add("ml-root");
  el.innerHTML = `
    <div class="ml-top">
      <div class="ml-search">
        <i class="pi pi-search"></i>
        <input type="search" placeholder="搜索模型、触发词、作者" spellcheck="false" />
      </div>
      <button class="ml-icon ml-nsfw" title="NSFW 图片模糊"><span>NSFW</span></button>
      <button class="ml-icon ml-refresh" title="重新扫描模型目录"><i class="pi pi-refresh"></i></button>
    </div>
    <div class="ml-types"></div>
    <div class="ml-filters">
      <select class="ml-select ml-base" title="按分类筛选"></select>
      <select class="ml-select ml-sort" title="排序方式">
        <option value="name">名称</option>
        <option value="time">修改时间</option>
        <option value="size">文件大小</option>
        <option value="base">分类</option>
      </select>
      <button class="ml-icon ml-order" title="切换升序 / 降序"></button>
      <button class="ml-icon ml-view" title="切换列表视图"></button>
    </div>
    <div class="ml-body">
      <div class="ml-grid" hidden></div>
      <div class="ml-status" hidden></div>
    </div>
    <div class="ml-detail" hidden></div>
    <div class="ml-toast" hidden></div>`;

  const search = $(".ml-search input");
  if (search) search.value = state.query;

  bindEvents(el);
  renderNsfwButton();
  renderViewButton();
  renderAll();

  if (!state.loaded) loadList(false);
}

function register() {
  const manager = app.extensionManager;
  if (!manager?.registerSidebarTab) {
    console.warn("[ModelLibrary] 当前前端不支持 registerSidebarTab，模型库未注册");
    return;
  }

  manager.registerSidebarTab({
    id: "local-model-library",
    icon: "pi pi-database",
    title: "本地模型库",
    tooltip: "本地模型库（ComfyUI models 目录）",
    type: "custom",
    render: (el) => {
      ensureCss();
      mount(el);
    },
  });
}

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && state.detail) {
    closeDetail();
  }
});

app.registerExtension({
  name: "ModelLibrary.Local",
  async setup() {
    ensureCss();
    bindCanvasDrop();
    register();
  },
});
