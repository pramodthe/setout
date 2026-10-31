"use strict";
// One sheet at a time. The server holds the record: every action posts and redraws from
// what comes back, so what is on screen is what is on disk.

const IDLE_MS = 45000; // past this with no input the clock stops, so a coffee break costs nothing
const $ = (id) => document.getElementById(id);
const svg = $("sheet");
const SVGNS = "http://www.w3.org/2000/svg";

const state = {
  sheets: [], index: 0, sheet: null, classes: [], selected: null, merge: new Set(),
  cutting: null, picking: false, showAside: false, showLabels: true, view: null, optionAt: 0, options: [],
  active: 0, lastTick: performance.now(), lastInput: performance.now(),
  saving: 0, held: [], // keys that arrive during a save wait their turn, so none is lost or misapplied
};

// ---------------------------------------------------------------- the clock

setInterval(() => {
  const now = performance.now();
  if (!document.hidden && now - state.lastInput < IDLE_MS) state.active += now - state.lastTick;
  state.lastTick = now;
}, 250);
for (const kind of ["keydown", "pointerdown", "pointermove", "wheel"]) {
  window.addEventListener(kind, () => { state.lastInput = performance.now(); }, { passive: true, capture: true });
}
function takeSeconds() {
  const seconds = state.active / 1000;
  state.active = 0;
  return Math.round(seconds * 1000) / 1000;
}

// ---------------------------------------------------------------- talking to the server

async function get(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error((await response.json()).error || response.statusText);
  return response.json();
}

async function post(action, body = {}) {
  state.saving += 1;
  try {
    const response = await fetch(`/api/sheets/${state.sheet.sheet_id}/${action}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Setout-Token": TOKEN },
      body: JSON.stringify({ ...body, seconds: takeSeconds() }),
    });
    const data = await response.json();
    if (!response.ok) { say(data.error || response.statusText); return null; }
    state.sheet = data;
    const row = state.sheets[state.index];
    if (row) Object.assign(row, data.summary);
    return data;
  } finally {
    state.saving -= 1;
    setTimeout(releaseKeys, 0); // after the caller has moved the selection on
  }
}

function releaseKeys() {
  while (state.held.length && !state.saving) onKey(state.held.shift());
}

function say(text, sticky = false) {
  const banner = $("banner");
  banner.textContent = text;
  banner.hidden = !text;
  clearTimeout(say.timer);
  if (text && !sticky) say.timer = setTimeout(() => { banner.hidden = true; }, 3500);
}

// ---------------------------------------------------------------- reading a record

const regions = () => state.sheet ? state.sheet.regions : [];
const region = (id) => regions().find((r) => r.id === id);
const inList = (r) => state.showAside || !r.dropped;
const decided = (r) => r.rejected || !!r.dropped || r.proposal.state !== "proposed";
const nameOf = (id) => { const c = state.classes.find((k) => k.id === id); return c ? c.name : id; };

function hue(text) {
  let h = 0;
  for (const c of text) h = (h * 31 + c.charCodeAt(0)) % 360;
  return h;
}
function colour(r) {
  const id = r.proposal.class_id;
  return id === "unknown" ? "hsl(0 0% 45%)" : `hsl(${hue(id)} 72% 42%)`;
}
function toPixel(x, y) {
  const { extents, px_per_unit: k, margin_px: m } = state.sheet.render;
  return [m + (x - extents[0]) * k, m + (extents[3] - y) * k];
}
function toDrawing(px, py) {
  const { extents, px_per_unit: k, margin_px: m } = state.sheet.render;
  return [extents[0] + (px - m) / k, extents[3] - (py - m) / k];
}
function ringPath(ring) {
  return "M" + ring.map(([x, y]) => toPixel(x, y).map((v) => v.toFixed(2)).join(" ")).join("L") + "Z";
}
function boxOf(r) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const ring of r.rings) {
    for (const [x, y] of ring) {
      const [px, py] = toPixel(x, y);
      x0 = Math.min(x0, px); y0 = Math.min(y0, py); x1 = Math.max(x1, px); y1 = Math.max(y1, py);
    }
  }
  return [x0, y0, x1, y1];
}

// ---------------------------------------------------------------- drawing

function setView(x, y, w, h) {
  state.view = { x, y, w, h };
  svg.setAttribute("viewBox", `${x} ${y} ${w} ${h}`);
  // text keeps its size on screen, whatever the zoom
  const unit = Math.max(w / (svg.clientWidth || 1), h / (svg.clientHeight || 1));
  const labels = $("labels");
  labels.setAttribute("font-size", 12 * unit);
  labels.setAttribute("stroke-width", 3 * unit);
}
function fitSheet() {
  const { width, height } = state.sheet.render;
  setView(0, 0, width, height);
}
function fitRegion(r) {
  const [x0, y0, x1, y1] = boxOf(r);
  const pad = Math.max(60, 0.35 * Math.max(x1 - x0, y1 - y0));
  const box = svg.getBoundingClientRect();
  const w = Math.max(x1 - x0 + 2 * pad, 240), h = Math.max(y1 - y0 + 2 * pad, 240);
  const scale = Math.max(w / box.width, h / box.height);
  setView((x0 + x1) / 2 - (box.width * scale) / 2, (y0 + y1) / 2 - (box.height * scale) / 2, box.width * scale, box.height * scale);
}
function ensureVisible(r) {
  const [x0, y0, x1, y1] = boxOf(r);
  const v = state.view;
  if (x0 < v.x || y0 < v.y || x1 > v.x + v.w || y1 > v.y + v.h) fitSheet();
}

function drawRegions() {
  const group = $("regions");
  group.textContent = "";
  // large first, so a thin part stays clickable on top of its neighbour
  const order = regions().filter(inList).sort((a, b) => b.area - a.area);
  for (const r of order) {
    const path = order.length ? r.rings.map(ringPath).join("") : "";
    const fill = document.createElementNS(SVGNS, "path");
    const kinds = ["fill"];
    if (decided(r) && !r.rejected && !r.dropped) kinds.push("decided");
    if (r.proposal.class_id === "unknown") kinds.push("unknown");
    if (r.rejected || r.dropped) kinds.push("out");
    if (state.merge.has(r.id)) kinds.push("picked");
    if (r.id === state.selected) kinds.push("on");
    fill.setAttribute("class", kinds.join(" "));
    fill.setAttribute("d", path);
    fill.setAttribute("fill", colour(r));
    fill.setAttribute("stroke", colour(r));
    fill.dataset.region = r.id;
    const halo = document.createElementNS(SVGNS, "path");
    halo.setAttribute("class", "halo");
    halo.setAttribute("d", path);
    halo.setAttribute("stroke", "#000");
    halo.dataset.region = r.id;
    group.append(fill, halo);
  }
}

function drawLabels() {
  const group = $("labels");
  group.textContent = "";
  if (!state.showLabels) return;
  for (const r of regions().filter((k) => !k.dropped && !k.rejected)) {
    const text = document.createElementNS(SVGNS, "text");
    const [x, y] = toPixel(r.anchor[0], r.anchor[1]);
    const known = r.proposal.class_id !== "unknown";
    text.setAttribute("x", x);
    text.setAttribute("y", y);
    text.setAttribute("class", [known ? "" : "unknown", r.id === state.selected ? "on" : ""].join(" ").trim());
    text.textContent = known ? nameOf(r.proposal.class_id) : "?";
    group.append(text);
  }
}

function drawMarks() {
  const group = $("marks");
  group.textContent = "";
  if (!state.cutting) return;
  const scale = state.view.w / svg.getBoundingClientRect().width;
  for (const [x, y] of state.cutting.points) {
    const [px, py] = toPixel(x, y);
    const dot = document.createElementNS(SVGNS, "circle");
    dot.setAttribute("cx", px); dot.setAttribute("cy", py); dot.setAttribute("r", 4 * scale);
    group.append(dot);
  }
  if (state.cutting.points.length === 2) {
    const [a, b] = state.cutting.points.map(([x, y]) => toPixel(x, y));
    const line = document.createElementNS(SVGNS, "line");
    line.setAttribute("x1", a[0]); line.setAttribute("y1", a[1]); line.setAttribute("x2", b[0]); line.setAttribute("y2", b[1]);
    group.append(line);
  }
}

function drawCard() {
  const card = $("card");
  const r = region(state.selected);
  if (!r) {
    const done = state.sheet && state.sheet.summary.done;
    card.innerHTML = `<h2>${done ? "Sheet done" : "Nothing selected"}</h2><p class="why">${done ? "Every region has a decision. ] for the next sheet." : ""}</p>`;
    return;
  }
  const p = r.proposal;
  card.textContent = "";
  const title = document.createElement("h2");
  title.textContent = p.class_id === "unknown" ? "Unknown" : nameOf(p.class_id);
  const sub = document.createElement("div");
  sub.className = "sub";
  sub.textContent = `${r.id} · ${r.kind} · ${r.width_px.toFixed(1)} px wide${r.thin ? " · thin" : ""}`;
  const badges = document.createElement("div");
  const badge = (text, kind) => { const b = document.createElement("span"); b.className = `badge ${kind}`; b.textContent = text; badges.append(b); };
  if (r.dropped) badge("set aside", "no");
  else if (r.rejected) badge("not a part", "no");
  else if (p.state !== "proposed") badge(p.state, "done");
  else badge(p.source ? `from ${p.source}` : "no source", p.source || "");
  card.append(title, sub, badges);
  if (p.verbatim.length) {
    const said = document.createElement("p");
    said.className = "said";
    said.textContent = p.verbatim.map((t) => `“${t}”`).join("\n");
    card.append(said);
  }
  const why = document.createElement("p");
  why.className = "why";
  why.textContent = r.dropped || p.reason;
  card.append(why);
  const actions = document.createElement("div");
  actions.className = "actions";
  const button = (text, key, run, main) => {
    const b = document.createElement("button");
    b.textContent = text;
    const k = document.createElement("kbd"); k.textContent = key; b.append(k);
    if (main) b.className = "main";
    b.addEventListener("click", run);
    actions.append(b);
  };
  if (r.dropped || r.rejected) button("Bring back", "r", restore, true);
  else {
    if (p.class_id !== "unknown") button("Accept", "↵", accept, true);
    button("Class", "c", openPicker);
    (p.candidates || []).filter((c) => c !== p.class_id).slice(0, 5).forEach((c, i) => button(nameOf(c), String(i + 1), () => choose(c)));
    button("Not a part", "x", reject);
    button("Split", "s", startCut);
    button("Merge", "m", mergeKey);
  }
  card.append(actions);
}

function drawList() {
  const list = $("list");
  list.textContent = "";
  for (const r of regions().filter(inList)) {
    const row = document.createElement("li");
    if (r.id === state.selected) row.classList.add("on");
    if (state.merge.has(r.id)) row.classList.add("picked");
    if (decided(r)) row.classList.add("decided");
    if (r.rejected || r.dropped) row.classList.add("out");
    row.dataset.region = r.id;
    const dot = document.createElement("span");
    dot.className = "dot"; dot.style.background = colour(r);
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = r.proposal.class_id === "unknown" ? (r.proposal.verbatim[0] || "unknown") : nameOf(r.proposal.class_id);
    const tag = document.createElement("span");
    tag.className = "tag";
    tag.textContent = r.dropped ? "aside" : r.rejected ? "no" : r.proposal.state === "proposed" ? (r.thin ? "thin" : "") : "✓";
    row.append(dot, name, tag);
    row.addEventListener("click", (event) => pick(r.id, event.shiftKey));
    list.append(row);
  }
  const on = list.querySelector("li.on");
  if (on) on.scrollIntoView({ block: "nearest" });
  $("aside-count").textContent = regions().filter((r) => r.dropped).length;
}

function drawHeader() {
  const s = state.sheet.summary;
  $("progress").textContent = `${s.decided} of ${s.regions} decided` + (state.sheet.excluded_from_export ? " · left out of the dataset" : "");
  const live = state.sheets.filter((row) => row.done).length;
  $("cost").textContent = (s.seconds_per_part === null ? "" : `${s.seconds_per_part} s per part · `) + `${live} of ${state.sheets.length} sheets done`;
}

function draw() {
  drawRegions(); drawLabels(); drawMarks(); drawCard(); drawList(); drawHeader();
}

// ---------------------------------------------------------------- selection and decisions

function nextUndecided(from) {
  const list = regions().filter((r) => !r.dropped);
  const at = list.findIndex((r) => r.id === from);
  for (let k = 1; k <= list.length; k += 1) {
    const r = list[(at + k + list.length) % list.length];
    if (!decided(r)) return r.id;
  }
  return null;
}

function pick(id, add = false) {
  if (add) {
    if (!state.merge.size && state.selected) state.merge.add(state.selected); // click, then shift-click
    state.merge.has(id) && id !== state.selected ? state.merge.delete(id) : state.merge.add(id);
  }
  state.selected = id;
  const r = region(id);
  if (r) ensureVisible(r);
  draw();
}
function advance(from) {
  state.selected = nextUndecided(from);
  const r = region(state.selected);
  if (r) ensureVisible(r);
  draw();
}
function saidCarried(data) {
  const carried = data && data.result && data.result.carried;
  if (carried && carried.propagated + carried.hatch + carried.set_aside > 0) {
    say(`Carried to ${carried.details} other details: ${carried.propagated} near-identical, ${carried.hatch} by hatch, ${carried.set_aside} set aside`);
  }
}
async function acceptAll() {
  const id = state.selected;
  const data = await post("acceptall");
  if (data) { saidCarried(data); advance(id); }
}
async function accept() {
  const id = state.selected;
  if (!id) return;
  if (region(id).proposal.class_id === "unknown") return openPicker();
  const data = await post("accept", { region: id });
  if (data) { saidCarried(data); advance(id); }
}
async function reject() {
  const id = state.selected;
  const data = id && await post("reject", { region: id });
  if (data) { saidCarried(data); advance(id); }
}
async function restore() {
  const id = state.selected;
  if (id && await post("restore", { region: id })) draw();
}
async function choose(classId) {
  const id = state.selected;
  closePicker();
  const data = id && await post("correct", { region: id, class_id: classId });
  if (data) { saidCarried(data); advance(id); }
}
function mergeKey() {
  if (state.merge.size >= 2 && state.merge.has(state.selected)) return merge();
  if (state.selected) state.merge.add(state.selected);
  say(`${state.merge.size} picked: choose more, then m`);
  const next = nextUndecided(state.selected);
  if (next) state.selected = next;
  draw();
}
async function merge() {
  const ids = [...state.merge];
  const data = await post("merge", { regions: ids });
  state.merge.clear();
  if (data) state.selected = data.result.created[0];
  draw();
}
function startCut() {
  if (!state.selected) return;
  state.cutting = { points: [] };
  svg.classList.add("cutting");
  say("Click two points along the cut. Esc cancels.", true);
}
function endCut() {
  state.cutting = null;
  svg.classList.remove("cutting");
  say("");
  drawMarks();
}
async function addCutPoint(x, y) {
  state.cutting.points.push(toDrawing(x, y));
  drawMarks();
  if (state.cutting.points.length < 2) return;
  const [start, end] = state.cutting.points;
  const data = await post("split", { region: state.selected, start, end });
  endCut();
  if (data) state.selected = data.result.created[0];
  draw();
}
async function undo() {
  const data = await post("undo");
  if (!data) return;
  state.merge.clear();
  if (!region(state.selected)) state.selected = nextUndecided(null);
  draw();
}
async function flag() {
  const leaving = !state.sheet.excluded_from_export;
  const note = leaving ? window.prompt("Why leave this sheet out of the dataset?", "a part has no region") : "";
  if (leaving && note === null) return;
  if (await post("flag", { exclude: leaving, note })) draw();
}

// ---------------------------------------------------------------- class picker

function ranked(text) {
  const words = text.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return state.classes;
  const score = (c) => {
    const hay = [c.id, c.name.toLowerCase(), ...c.aliases.map((a) => a.toLowerCase())];
    if (!words.every((w) => hay.some((h) => h.includes(w)))) return -1;
    return hay.some((h) => h.startsWith(words[0])) ? 2 : 1;
  };
  return state.classes.map((c) => [score(c), c]).filter(([s]) => s > 0).sort((a, b) => b[0] - a[0]).map(([, c]) => c);
}
function drawOptions() {
  state.options = ranked($("filter").value);
  state.optionAt = Math.min(state.optionAt, Math.max(state.options.length - 1, 0));
  const list = $("options");
  list.textContent = "";
  state.options.forEach((c, k) => {
    const row = document.createElement("li");
    row.textContent = c.name + " ";
    const small = document.createElement("small"); small.textContent = c.id; row.append(small);
    if (k === state.optionAt) row.className = "on";
    row.addEventListener("mousedown", (event) => { event.preventDefault(); choose(c.id); });
    list.append(row);
  });
  const on = list.querySelector("li.on");
  if (on) on.scrollIntoView({ block: "nearest" });
}
function openPicker() {
  if (!state.selected) return;
  state.picking = true;
  state.optionAt = 0;
  $("picker").hidden = false;
  $("filter").value = "";
  drawOptions();
  $("filter").focus();
}
function closePicker() {
  state.picking = false;
  $("picker").hidden = true;
  $("stage").focus();
}
function pickerKey(key) {
  if (key === "ArrowDown") { state.optionAt = Math.min(state.optionAt + 1, state.options.length - 1); drawOptions(); }
  else if (key === "ArrowUp") { state.optionAt = Math.max(state.optionAt - 1, 0); drawOptions(); }
  else if (key === "Enter") { if (state.options[state.optionAt]) choose(state.options[state.optionAt].id); }
  else if (key === "Escape") closePicker();
  else return false;
  return true;
}
$("filter").addEventListener("input", () => { state.optionAt = 0; drawOptions(); });
$("filter").addEventListener("keydown", (event) => {
  if (pickerKey(event.key)) { event.preventDefault(); event.stopPropagation(); }
});

// ---------------------------------------------------------------- keys

function step(direction) {
  const list = regions().filter(inList);
  if (!list.length) return;
  const at = list.findIndex((r) => r.id === state.selected);
  pick(list[(at + direction + list.length) % list.length].id);
}

window.addEventListener("keydown", (event) => {
  if (event.metaKey || event.ctrlKey || event.altKey) return;
  if (state.saving || state.held.length) {
    state.held.push({ key: event.key, shiftKey: event.shiftKey });
    event.preventDefault();
    return;
  }
  if (state.picking) return; // the class box has the keys
  if (onKey(event)) event.preventDefault();
});

function onKey(event) {
  if (state.picking) { // a key held back during a save, now arriving at the class box
    if (!pickerKey(event.key) && event.key.length === 1) {
      $("filter").value += event.key;
      state.optionAt = 0;
      drawOptions();
    }
    return true;
  }
  const help = $("help");
  if (!help.hidden) { if (event.key === "Escape" || event.key === "?") help.hidden = true; return true; }
  const r = region(state.selected);
  const key = event.key;
  const run = {
    Enter: accept, a: accept, A: acceptAll, c: openPicker, "/": openPicker, x: reject, r: restore, u: undo, s: startCut, m: mergeKey, f: flag,
    n: () => advance(state.selected), p: () => step(-1), j: () => step(1), k: () => step(-1),
    Tab: () => (event.shiftKey ? step(-1) : advance(state.selected)),
    "?": () => { help.hidden = false; },
    z: () => { if (r) fitRegion(r); drawMarks(); }, 0: () => { fitSheet(); drawMarks(); },
    v: () => { $("show-aside").checked = !state.showAside; toggleAside(); },
    l: () => { state.showLabels = !state.showLabels; drawLabels(); },
    "[": () => openSheet(state.index - 1), "]": () => openSheet(state.index + 1),
    Escape: () => { if (state.cutting) endCut(); state.merge.clear(); draw(); },
  }[key];
  if (run) { run(); return true; }
  if (/^[1-9]$/.test(key) && r) {
    const options = (r.proposal.candidates || []).filter((c) => c !== r.proposal.class_id);
    const choice = options[Number(key) - 1];
    if (choice) { choose(choice); return true; }
  }
  return false;
}

// ---------------------------------------------------------------- pointer

function svgPoint(event) {
  const box = svg.getBoundingClientRect();
  const v = state.view;
  return [v.x + ((event.clientX - box.left) / box.width) * v.w, v.y + ((event.clientY - box.top) / box.height) * v.h];
}
svg.addEventListener("wheel", (event) => {
  event.preventDefault();
  const [px, py] = svgPoint(event);
  const v = state.view;
  const factor = Math.exp(event.deltaY * 0.0015);
  setView(px - (px - v.x) * factor, py - (py - v.y) * factor, v.w * factor, v.h * factor);
  drawMarks();
}, { passive: false });
let drag = null;
svg.addEventListener("pointerdown", (event) => {
  drag = { x: event.clientX, y: event.clientY, view: { ...state.view }, moved: false, target: event.target };
  svg.setPointerCapture(event.pointerId);
});
svg.addEventListener("pointermove", (event) => {
  if (!drag) return;
  const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
  if (Math.hypot(dx, dy) < 4 && !drag.moved) return;
  drag.moved = true;
  svg.classList.add("panning");
  const box = svg.getBoundingClientRect();
  setView(drag.view.x - (dx / box.width) * drag.view.w, drag.view.y - (dy / box.height) * drag.view.h, drag.view.w, drag.view.h);
});
svg.addEventListener("pointerup", (event) => {
  const was = drag;
  drag = null;
  svg.classList.remove("panning");
  if (!was || was.moved) return;
  if (state.cutting) { const [x, y] = svgPoint(event); addCutPoint(x, y); return; }
  const id = was.target && was.target.dataset ? was.target.dataset.region : null;
  if (id) pick(id, event.shiftKey);
});

// ---------------------------------------------------------------- sheets

function toggleAside() {
  state.showAside = $("show-aside").checked;
  draw();
}
$("show-aside").addEventListener("change", toggleAside);
$("prev-sheet").addEventListener("click", () => openSheet(state.index - 1));
$("next-sheet").addEventListener("click", () => openSheet(state.index + 1));
$("help-button").addEventListener("click", () => { $("help").hidden = false; });
$("sheet-pick").addEventListener("change", (event) => openSheet(Number(event.target.value)));

async function openSheet(index) {
  if (index < 0 || index >= state.sheets.length) return;
  state.index = index;
  state.sheet = await get(`/api/sheets/${state.sheets[index].sheet_id}`);
  state.merge.clear();
  state.cutting = null;
  $("sheet-pick").value = String(index);
  const image = $("sheet-image");
  image.setAttribute("href", `/api/sheets/${state.sheet.sheet_id}/image`);
  image.setAttribute("width", state.sheet.render.width);
  image.setAttribute("height", state.sheet.render.height);
  fitSheet();
  state.selected = nextUndecided(null);
  state.active = 0;
  draw();
}

async function start() {
  state.classes = await get("/api/classes");
  state.sheets = await get("/api/sheets");
  if (!state.sheets.length) {
    $("card").innerHTML = "<h2>No sheets</h2><p class='why'>Run propose first: python annotation/cli.py propose data/drawings</p>";
    return;
  }
  $("sheet-pick").textContent = "";
  state.sheets.forEach((row, k) => {
    const option = document.createElement("option");
    option.value = String(k);
    option.textContent = row.label || row.sheet_id;
    $("sheet-pick").append(option);
  });
  const first = state.sheets.findIndex((row) => !row.done);
  await openSheet(first >= 0 ? first : 0);
  $("stage").focus();
}
start().catch((error) => say(String(error), true));
