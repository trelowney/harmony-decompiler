import { loadPyodide } from "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/pyodide.mjs";
import { Harmony525 } from "./hid525.js?v=e4361e7e64";
import { RemoteView } from "./remote.js?v=4bf22e8c8f";

const MODULES = ["_paths", "flipper_ir", "pronto", "irdb", "pic18dis", "hconfig", "analyze_525_ir", "check_525_mode_pages",
  "class5_ir_encoder", "verify_525_semantics", "render_525_screens", "h525_lcd_author",
  "clone_525_device", "compose525"];
const CORNERS = ["top left", "top right", "bottom left", "bottom right"];

const $ = (id) => document.getElementById(id);
let webapp = null;
let identity = null;
let history = [];          // [{bytes, label}], the last one is on show

const current = () => history[history.length - 1];
let commandList = [];      // [{device, label, group, command}]
let steps = [];            // the activity being built: [{group, command, text}]
let actKeys = {};          // its keys that go to another device: {key name: device mode}

function drawActKeys() {
  const names = Object.fromEntries([...$("act-key-device").options].map((o) => [o.value, o.textContent]));
  $("act-keys").replaceChildren(...Object.entries(actKeys).map(([key, mode]) => {
    const li = document.createElement("li");
    const x = document.createElement("button");
    x.type = "button";
    x.textContent = "×";
    x.addEventListener("click", () => { delete actKeys[key]; drawActKeys(); });
    li.append(`${key} goes to ${names[mode] ?? `mode ${mode}`} `, x);
    return li;
  }));
}

function fillStepCommands() {
  const dev = $("act-step-device").value;
  $("act-step-command").replaceChildren(...commandList.filter((c) => c.device === dev)
    .map((c) => option(`${c.group}:${c.command}`, c.label)));
}

function fillPowerCommands() {
  const sel = $("pw-device");
  const name = sel.options[sel.selectedIndex]?.textContent;
  const mine = commandList.filter((c) => c.device === name);
  const guess = mine.find((c) => /power|on.?off|pwr/i.test(c.label));
  $("pw-on").replaceChildren(...mine.map((c) => option(c.command, c.label)));
  $("pw-off").replaceChildren(option("", "the same (a toggle)"), ...mine.map((c) => option(c.command, c.label)));
  if (guess) $("pw-on").value = guess.command;
}

function drawSteps() {
  const ol = $("act-steps");
  ol.replaceChildren(...steps.map((s, i) => {
    const li = document.createElement("li");
    const x = document.createElement("button");
    x.type = "button";
    x.textContent = "×";
    x.addEventListener("click", () => { steps.splice(i, 1); drawSteps(); });
    li.append(`${s.text} `, x);
    return li;
  }));
}

// The add activity form, filled with an existing activity to edit it.
function loadActivityIntoForm() {
  const name = $("act-which").value;
  const editing = name !== "";
  $("act-submit").textContent = editing ? "Save the changes" : "Add";
  $("act-keep-row").hidden = true;
  $("act-unread").hidden = true;
  for (const box of $("act-on").querySelectorAll("input")) box.checked = false;
  if (!editing) {
    $("act-name").value = "";
    $("act-show").value = "";
    $("act-volume").value = "";
    steps = [];
    drawSteps();
    actKeys = {};
    drawActKeys();
    return;
  }
  const s = toJs(webapp.activity_settings(current().bytes, name));
  $("act-name").value = name;
  $("act-like").value = name;
  for (const box of $("act-on").querySelectorAll("input")) box.checked = s.on.includes(box.value);
  $("act-show").value = s.show ?? "";
  $("act-volume").value = s.volume ?? "";
  steps = s.steps;
  drawSteps();
  actKeys = { ...s.keys };
  drawActKeys();
  if (s.keeps.length) {
    $("act-keeps").textContent = s.keeps.join(", ");
    $("act-keep").checked = true;
    $("act-keep-row").hidden = false;
  }
  if (s.unread) {
    $("act-unread").textContent = `${s.unread} more thing${s.unread > 1 ? "s" : ""} this activity runs at ` +
      "its start cannot be shown here, and saving drops them.";
    $("act-unread").hidden = false;
  }
}

function status(text, bad = false) {
  $("status").textContent = text;
  $("status").className = bad ? "bad" : "";
}

// Python blocks the page while it works; let the status line paint first.
const repaint = () => new Promise((resolve) => setTimeout(resolve, 30));

async function startPython() {
  const py = await loadPyodide();
  for (const name of MODULES) {
    const source = await (await fetch(`../tools/${name}.py`, { cache: "no-cache" })).text();
    py.FS.writeFile(`${name}.py`, source);
  }
  // which pixels draw which character, per typeface (tools/build_arch9_glyphs.py)
  py.FS.writeFile("arch9_glyphs.json", await (await fetch("../tools/arch9_glyphs.json", { cache: "no-cache" })).text());
  // and glyphs for characters a configuration's font lacks (tools/build_arch9_glyphs.py)
  py.FS.writeFile("arch9_font_glyphs.json", await (await fetch("../tools/arch9_font_glyphs.json", { cache: "no-cache" })).text());
  py.FS.writeFile("webapp.py", await (await fetch("webapp.py", { cache: "no-cache" })).text());
  const sample = new Uint8Array(await (await fetch("../samples/harmony525/config.EZHex")).arrayBuffer());
  const module = py.pyimport("webapp");
  module.set_template(sample);
  return module;
}

const toJs = (value) => (value && value.toJs ? value.toJs({ dict_converter: Object.fromEntries }) : value);

function cell(tag, text) {
  const el = document.createElement(tag);
  el.textContent = text;
  return el;
}

function row(cells, header = false) {
  const tr = document.createElement("tr");
  for (const c of cells) tr.append(cell(header ? "th" : "td", c));
  return tr;
}

function option(value, text) {
  const o = document.createElement("option");
  o.value = value;
  o.textContent = text;
  return o;
}

// ---- screens and the drawn remote ----
let screenCache = new Map();   // "mode:page" -> pixels
let model = null;              // webapp.remote_model of what is on show
let remote = null;

function screenOf(mode, page) {
  const key = `${mode}:${page}`;
  if (!screenCache.has(key)) {
    try {
      for (const [m, p, pixels] of toJs(webapp.screens(current().bytes, [mode]))) {
        screenCache.set(`${m}:${p}`, pixels);
      }
    } catch { screenCache.set(key, null); }
  }
  return screenCache.get(key) ?? null;
}

function paint(canvas, pixels) {
  const css = getComputedStyle(document.documentElement);
  const g = canvas.getContext("2d");
  g.fillStyle = css.getPropertyValue("--lcd");
  g.fillRect(0, 0, 96, 64);
  g.fillStyle = css.getPropertyValue("--ink");
  if (pixels) for (let i = 0; i < pixels.length; i += 1) if (pixels[i]) g.fillRect(i % 96, Math.floor(i / 96), 1, 1);
}

const pagesOfMode = (mode) => mode === model.devices_mode ? model.devices_pages
  : mode === model.menu_mode ? model.menu_pages : model.modes[mode]?.pages ?? 1;

// the gallery, grouped the way the remote is: Devices, Activities, each device, each activity
function drawScreens() {
  const holder = $("screens");
  holder.replaceChildren();
  const groups = [["Devices", model.devices_mode]];
  if (model.menu_mode != null) groups.push(["Activities menu", model.menu_mode]);
  const byPlace = (x, y) => x.page - y.page || x.corner - y.corner;
  for (const d of [...model.devices].sort(byPlace)) groups.push([d.name, d.mode]);
  for (const a of [...model.activities].sort(byPlace)) groups.push([`${a.name} (activity)`, a.mode]);
  for (const [title, mode] of groups) {
    const box = document.createElement("div");
    box.append(cell("h3", title));
    const strip = document.createElement("div");
    strip.className = "screens";
    const n = pagesOfMode(mode);
    for (let page = 0; page < n; page += 1) {
      const canvas = document.createElement("canvas");
      canvas.width = 96;
      canvas.height = 64;
      paint(canvas, screenOf(mode, page));
      const fig = document.createElement("figure");
      fig.append(canvas, cell("figcaption", `page ${page + 1} of ${n} · mode ${mode}`));
      fig.addEventListener("click", () => remote.show(mode, page));
      strip.append(fig);
    }
    box.append(strip);
    holder.append(box);
  }
}

function flashRow(selector) {
  const tr = document.querySelector(selector);
  if (!tr) return;
  tr.classList.remove("flash");
  void tr.offsetWidth;
  tr.classList.add("flash");
}

// a press on the drawn remote lights the row it belongs to
function onRemote(event) {
  if (event.type === "device") flashRow(`#devices tr[data-mode="${event.mode}"]`);
  else if (event.type === "activity") flashRow(`#activities tr[data-name="${CSS.escape(event.name)}"]`);
  else if (event.type === "send") {
    for (const s of event.sends) {
      const d = model.devices.find((x) => x.name === s.device);
      if (d) flashRow(`#devices tr[data-mode="${d.mode}"]`);
    }
    if (event.kind === "activity") flashRow(`#activities tr[data-name="${CSS.escape(event.name)}"]`);
  }
}

const where = (x) => `page ${x.page + 1}, ${CORNERS[x.corner]}`;

function linkedRow(cells, kind, item) {
  const tr = document.createElement("tr");
  tr.className = "linked";
  for (const c of cells) {
    const td = document.createElement("td");
    if (c instanceof Node) td.append(c); else td.textContent = c;
    tr.append(td);
  }
  tr.addEventListener("mouseenter", () => remote.point(kind, item.page, item.corner));
  tr.addEventListener("mouseleave", () => remote.unpoint());
  return tr;
}

function show() {
  const { bytes } = current();
  const s = toJs(webapp.summary(bytes));
  screenCache = new Map();
  model = toJs(webapp.remote_model(bytes));
  const id = $("identity");
  id.replaceChildren();
  if (identity) {
    id.append(row(["firmware", identity.firmware]), row(["architecture", identity.architecture]),
      row(["skin", identity.skin]), row(["flash", identity.flash]));
  }
  id.append(row(["remote", s.model]), row(["source", history[0].label]), row(["sha256", s.sha256]),
    row(["config", `${s.blob_bytes} bytes`]),
    row(["checksum", s.trailer_ok ? "valid" : "INVALID"]),
    row(["decompiles and recompiles byte for byte", s.roundtrip_ok ? "yes" : "NO"]));

  const raw = $("devices-raw");
  raw.replaceChildren(row(["name", "mode", "IR group", "Devices page", "corner"], true));
  for (const d of s.devices) raw.append(row([d.name, d.mode, d.group ?? "-", d.page + 1, CORNERS[d.corner]]));

  const byPlace = (x, y) => x.page - y.page || x.corner - y.corner;
  const dv = $("devices");
  dv.replaceChildren(row(["name", "on the remote", "keys", "power state"], true));
  const kept = new Set(toJs(webapp.power_devices(bytes)));
  for (const d of [...s.devices].sort(byPlace)) {
    const soft = (model.modes[d.mode]?.soft ?? []).flat().filter(Boolean).length;
    const keys = Object.keys(model.modes[d.mode]?.keys ?? {}).length;
    const tr = linkedRow([d.name, where(d), `${soft} soft keys, ${keys} keys`,
      kept.has(d.name) ? "kept" : "not kept"], "device", d);
    tr.children[1].className = "where";
    tr.dataset.mode = d.mode;
    dv.append(tr);
  }
  const ac = $("activities");
  ac.replaceChildren(row(["name", "what it does", "on the remote"], true));
  for (const a of [...model.activities].sort(byPlace)) {
    const does = document.createElement("div");
    does.className = "does";
    for (const step of a.does) does.append(cell("span", step));
    const tr = linkedRow([a.name, does, where(a)], "activity", a);
    tr.children[2].className = "where";
    tr.dataset.name = a.name;
    ac.append(tr);
  }
  if (!model.activities.length) ac.append(row(["No activities."]));

  // the commands an activity can send, by device
  commandList = toJs(webapp.commands(bytes));
  const devs = [...new Set(commandList.map((c) => c.device))];
  $("act-step-device").replaceChildren(...devs.map((n) => option(n, n)));
  fillStepCommands();

  // the change forms follow what is on show
  const del = $("del-device");
  del.replaceChildren();
  for (const d of s.devices) del.append(option(d.mode, d.name));
  $("f-del-device").querySelector("button").disabled = !del.options.length;
  const pagesOf = (items) => Math.max(...items.map((x) => x.page)) + 1;
  const fillMove = (itemSelect, pageSelect, cornerSelect, items, value, label) => {
    itemSelect.replaceChildren(...items.map((x) => option(value(x), label(x))));
    const n = items.length ? pagesOf(items) : 1;
    pageSelect.replaceChildren(...[...Array(Math.min(n + 1, 6)).keys()].map((k) =>
      option(k, k < n ? `${k + 1}` : `${k + 1} (new)`)));
    cornerSelect.replaceChildren(...CORNERS.map((c, i) => option(i, c)));
  };
  fillMove($("mv-device"), $("mv-device-page"), $("mv-device-corner"), s.devices,
    (x) => x.mode, (x) => `${x.name} (${where(x)})`);
  fillMove($("mv-activity"), $("mv-activity-page"), $("mv-activity-corner"), s.activities,
    (x) => x.name, (x) => `${x.name} (${where(x)})`);
  const delA = $("del-activity");
  delA.replaceChildren(...s.activities.map((a) => option(a.name, a.name)));
  // devices the remote keeps no power state for, with their commands
  const powered = new Set(toJs(webapp.power_devices(bytes)));
  const unpowered = s.devices.filter((d) => !powered.has(d.name) && d.group != null);
  $("pw-device").replaceChildren(...unpowered.map((d) => option(d.mode, d.name)));
  $("f-power").querySelector("button").disabled = !unpowered.length;
  fillPowerCommands();

  const which = $("dev-which");
  const editingNow = which.value;
  which.replaceChildren(option("", "a new device"),
    ...s.devices.filter((d) => d.group != null).map((d) => option(d.mode, d.name)));
  which.value = [...which.options].some((o) => o.value === editingNow) ? editingNow : "";
  loadDeviceIntoForm();

  const like = $("act-like");
  like.replaceChildren(...(s.activities.length ? s.activities.map((a) => option(a.name, a.name))
    : [option("", "nothing, the first one")]));
  const byName = [...s.devices].sort((x, y) => x.name.localeCompare(y.name));
  $("act-show").replaceChildren(option("", "its own screen"),
    ...byName.map((d) => option(d.mode, `${d.name}'s screen`)));
  $("act-volume").replaceChildren(option("", "as on that screen"),
    ...byName.map((d) => option(d.mode, d.name)));
  $("act-key-device").replaceChildren(...byName.map((d) => option(d.mode, d.name)));
  if (!$("act-key-name").options.length) {
    $("act-key-name").replaceChildren(...toJs(webapp.key_names())
      .filter((k) => !["VolumeUp", "VolumeDown", "Mute"].includes(k)).map((k) => option(k, k)));
  }
  const on = $("act-on");
  on.replaceChildren();
  for (const name of toJs(webapp.power_devices(bytes))) {
    const l = document.createElement("label");
    l.className = "check";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.value = name;
    l.append(box, ` ${name}`);
    on.append(l);
  }
  const actWhich = $("act-which");
  const actNow = actWhich.value;
  actWhich.replaceChildren(option("", "a new activity"), ...s.activities.map((a) => option(a.name, a.name)));
  actWhich.value = [...actWhich.options].some((o) => o.value === actNow) ? actNow : "";
  if (actWhich.value !== "" || actNow !== "") loadActivityIntoForm();

  const n = history.length - 1;
  $("changes").textContent = n === 0 ? "" :
    `${n} change${n > 1 ? "s" : ""}: ${history.slice(1).map((h) => h.label).join("; ")}`;
  $("undo").disabled = n === 0;
  $("empty").hidden = true;
  $("result").hidden = false;
  $("save").disabled = false;
  remote.load(model);
  drawScreens();
}

async function change(label, run) {
  status(`${label}: building and checking…`);
  await repaint();
  try {
    const bytes = toJs(run(current().bytes));
    history.push({ bytes, label });
    show();
    status(`${label}: done. Save the file to keep it.`);
    return true;
  } catch (error) {
    const message = String(error.message ?? error).split("\n").filter(Boolean).pop();
    status(`${label}: refused. ${message}`, true);
    return false;
  }
}

// ---- the add device form ----
const REPEATING_KEYS = new Set(["Up", "Down", "Left", "Right", "VolumeUp", "VolumeDown",
  "ChannelUp", "ChannelDown", "Rewind", "FastForward"]);

// One row per thing the device answers: a 525 key, or a soft key with its label.
function learnRow(first, repeat) {
  const tr = document.createElement("tr");
  tr.innerHTML = `<td class="r-what"></td><td><input class="c-code" size="18" placeholder="—"></td>` +
    `<td><label class="check"><input type="checkbox" class="c-repeat"> repeats</label></td>` +
    `<td><button type="button" class="c-learn">Learn</button> <button type="button" class="c-del">×</button></td>`;
  tr.querySelector(".r-what").append(first);
  tr.querySelector(".c-repeat").checked = repeat;
  tr.querySelector(".c-code").addEventListener("input", () => {
    delete tr.dataset.learned;
    delete tr.dataset.current;
  });
  tr.querySelector(".c-del").addEventListener("click", () => {
    if (tr.dataset.current !== undefined) tr.dataset.cleared = "1";
    delete tr.dataset.learned;
    delete tr.dataset.current;
    tr.querySelector(".c-code").value = "";
  });
  tr.querySelector(".c-learn").addEventListener("click", () => learnInto(tr));
  return tr;
}

function rowName(tr) {
  return tr.dataset.key || tr.querySelector(".soft-label")?.value.trim() || "this key";
}

// The 525 as a receiver: point the device's own remote at the bottom end of
// the 525, below the keypad, press Learn and then the key, a short tap.
async function learnInto(tr) {
  const name = rowName(tr);
  let remote;
  try {
    remote = await Harmony525.connect();
    status(`Learning ${name}: point the device's remote at the bottom end of the 525 and tap the key…`);
    const capture = await remote.learnIR();
    if (!capture) throw new Error("nothing was received within five seconds");
    const decoded = toJs(webapp.decode_capture(capture.durations));
    if (decoded.nec) {
      const [a, a2, cmd] = decoded.nec;
      tr.dataset.learned = JSON.stringify({ address: a, address2: a2, code: cmd });
      tr.querySelector(".c-code").value = `NEC ${a.toString(16)} ${cmd.toString(16).padStart(2, "0")}`.toUpperCase();
      status(`Learned ${name}: NEC, address 0x${a.toString(16)} ${a2.toString(16)}, command 0x${cmd.toString(16)}.`);
    } else {
      tr.dataset.learned = JSON.stringify({ carrier: capture.carrier, durations: capture.durations });
      tr.querySelector(".c-code").value = "learned";
      status(`Learned ${name}: kept as it was received (${capture.durations.length} durations ` +
        `at ${capture.carrier} Hz).`);
    }
  } catch (error) {
    status(`Learning ${name} failed: ${error.message ?? error}`, true);
  } finally {
    if (remote) { try { await remote.close(); } catch { /* gone */ } }
  }
}

function addSoftPage() {
  const soft = $("dev-soft").querySelector("tbody");
  const page = soft.querySelectorAll("tr.soft-head").length;
  if (page >= 6) return;
  const head = document.createElement("tr");
  head.className = "soft-head";
  head.dataset.page = page;
  head.innerHTML = `<td colspan="4"><strong>page ${page + 1}</strong> ` +
    (page ? `<button type="button" class="soft-remove">remove this page</button>` : "") + `</td>`;
  head.querySelector(".soft-remove")?.addEventListener("click", () => removeSoftPage(Number(head.dataset.page)));
  soft.append(head);
  CORNERS.forEach((corner, i) => {
    const label = document.createElement("input");
    label.className = "soft-label";
    label.placeholder = `${corner} label`;
    label.maxLength = 20;
    const tr = learnRow(label, false);
    tr.dataset.corner = i;
    tr.dataset.page = page;
    soft.append(tr);
  });
  $("dev-soft-page").disabled = page + 1 >= 6;
}

// Take a page of soft keys out of the form; the pages after it move up one.
function removeSoftPage(page) {
  const soft = $("dev-soft").querySelector("tbody");
  for (const tr of [...soft.querySelectorAll("tr")]) {
    if (Number(tr.dataset.page) === page) tr.remove();
  }
  for (const tr of soft.querySelectorAll("tr")) {
    const p = Number(tr.dataset.page);
    if (p > page) {
      tr.dataset.page = p - 1;
      const title = tr.querySelector("strong");
      if (title) title.textContent = `page ${p}`;
    }
  }
  $("dev-soft-page").disabled = soft.querySelectorAll("tr.soft-head").length >= 6;
}

// ---- the code databases: Flipper-IRDB (CC0) and irdb (Simon Peter and contributors) ----
const SOURCES = {
  flipper: { index: "irdb-index.json", tag: "Flipper" },
  irdb: { index: "irdb-probonopd-index.json", tag: "irdb" },
};
const indexes = {};

async function sourceIndex(source) {
  if (!indexes[source]) indexes[source] = await (await fetch(SOURCES[source].index)).json();
  return indexes[source];
}

const shownPath = (source, path) => (source === "irdb" ? path.replace(/^codes\//, "") : path)
  .replace(/\.(ir|csv)$/, "").replaceAll("/", " / ").replaceAll("_", " ");

async function searchDatabase() {
  const terms = $("db-search").value.toLowerCase().split(/\s+/).filter(Boolean);
  const list = $("db-results");
  if (!terms.length) { list.replaceChildren(); return; }
  const hits = [];
  for (const source of Object.keys(SOURCES)) {
    const index = await sourceIndex(source);
    for (const f of index.files) {
      const p = shownPath(source, f).toLowerCase();
      if (terms.every((t) => p.includes(t))) hits.push([source, f]);
    }
  }
  list.replaceChildren(...hits.slice(0, 60).map(([source, path]) => {
    const li = document.createElement("li");
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = `${shownPath(source, path)}  ·  ${SOURCES[source].tag}`;
    b.addEventListener("click", () => useDatabaseFile(source, path));
    li.append(b);
    return li;
  }));
  $("db-note").textContent = hits.length > 60 ? `${hits.length} found, the first 60 shown; add a word.`
    : hits.length ? "" : "Nothing found. Try the brand alone.";
}

function clearDeviceRows() {
  while ($("dev-soft").querySelectorAll("tr.soft-head").length > 1) {
    removeSoftPage($("dev-soft").querySelectorAll("tr.soft-head").length - 1);
  }
  for (const tr of document.querySelectorAll("#dev-keys tbody tr, #dev-soft tbody tr:not(.soft-head)")) {
    delete tr.dataset.learned;
    delete tr.dataset.current;
    delete tr.dataset.cleared;
    tr.querySelector(".c-code").value = "";
    tr.querySelector(".c-code").title = "";
    const label = tr.querySelector(".soft-label");
    if (label) label.value = "";
  }
}

function putSignal(tr, item) {
  tr.dataset.learned = JSON.stringify({ flipper: item.signal });
  const code = tr.querySelector(".c-code");
  // the button as the database names it; address and command on hover
  code.value = `${item.name} (${item.code.split(/[ ,]/)[0]})`;
  code.title = `${item.name}: ${item.code}`;
}

async function useDatabaseFile(source, path) {
  const index = await sourceIndex(source);
  const url = `https://raw.githubusercontent.com/${index.repo}/${index.commit}/` +
    path.split("/").map(encodeURIComponent).join("/");
  status(`Fetching ${path}…`);
  let plan;
  try {
    const text = await (await fetch(url)).text();
    plan = toJs(source === "irdb" ? webapp.irdb_device(text) : webapp.flipper_device(text));
  } catch (error) {
    status(`${path} could not be read: ${error.message ?? error}`, true);
    return;
  }
  clearDeviceRows();
  for (const tr of $("dev-keys").querySelectorAll("tbody tr")) {
    const item = plan.keys[tr.dataset.key];
    if (item) putSignal(tr, item);
  }
  const soft = plan.soft.slice(0, 24);
  soft.forEach((item, i) => {
    const page = Math.floor(i / 4), corner = i % 4;
    while ($("dev-soft").querySelectorAll("tr.soft-head").length <= page) addSoftPage();
    const tr = $("dev-soft").querySelector(`tr[data-page="${page}"][data-corner="${corner}"]`);
    tr.querySelector(".soft-label").value = item.label.slice(0, 20);
    putSignal(tr, item);
  });
  const parts = path.split("/");
  if (!$("dev-name").value.trim()) {
    $("dev-name").value = (source === "irdb" ? `${parts[1]} ${parts[2]}`
      : `${parts[1]} ${parts[0].replace(/s$/, "")}`).slice(0, 20);
  }
  const keys = Object.keys(plan.keys).length;
  const left = plan.soft.length - soft.length;
  $("db-note").textContent = `${path}: ${keys} buttons on 525 keys, ${soft.length} on soft keys` +
    (left ? `, ${left} more did not fit (24 soft keys at most)` : "") +
    (plan.skipped.length ? `; ${plan.skipped.length} cannot be sent: ${plan.skipped.join("; ")}` : "") +
    ". Change anything below before adding. Database codes are only known to work on your device " +
    "once written and tried; if some do not, try another file of the same brand.";
  status(`Loaded ${path}. Check the keys below, then Add the device.`);
}

// ---- editing a device the configuration already has ----
let softSnapshot = null;               // the soft keys as loaded, to tell whether they changed

function softState() {
  return JSON.stringify([...$("dev-soft").querySelectorAll("tbody tr:not(.soft-head)")].map((tr) => [
    tr.dataset.page, tr.dataset.corner, tr.querySelector(".soft-label").value.trim(),
    tr.dataset.current ?? null, tr.dataset.learned ?? null, tr.querySelector(".c-code").value.trim()]));
}

function setCurrent(tr, item) {
  tr.dataset.current = item.command;
  const code = tr.querySelector(".c-code");
  code.value = `now: ${item.label}`;
  code.title = `sends command ${item.command} of this device now; Learn or type to change it, × to clear`;
}

function loadDeviceIntoForm() {
  clearDeviceRows();
  const mode = $("dev-which").value;
  const editing = mode !== "";
  $("f-add-device").querySelector("button.primary").textContent = editing ? "Save the changes" : "Add the device";
  softSnapshot = null;
  if (!editing) {
    $("dev-name").value = "";
    return;
  }
  const layout = toJs(webapp.device_layout(current().bytes, Number(mode)));
  $("dev-name").value = layout.name;
  $("dev-name").dataset.was = layout.name;
  for (const tr of $("dev-keys").querySelectorAll("tbody tr")) {
    const item = layout.keys[tr.dataset.key];
    if (item) setCurrent(tr, item);
  }
  layout.soft.forEach((row, page) => {
    while ($("dev-soft").querySelectorAll("tr.soft-head").length <= page) addSoftPage();
    row.forEach((item, corner) => {
      if (!item) return;
      const tr = $("dev-soft").querySelector(`tr[data-page="${page}"][data-corner="${corner}"]`);
      tr.querySelector(".soft-label").value = item.label.slice(0, 20);
      setCurrent(tr, item);
    });
  });
  softSnapshot = softState();
}

function buildDeviceForm() {
  $("dev-which").addEventListener("change", loadDeviceIntoForm);
  let timer;
  $("db-search").addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => searchDatabase().catch((e) => status(`Search failed: ${e.message ?? e}`, true)), 200);
  });
  addSoftPage();
  $("dev-soft-page").addEventListener("click", addSoftPage);
  const keys = $("dev-keys").querySelector("tbody");
  for (const key of toJs(webapp.key_names())) {
    const tr = learnRow(key, REPEATING_KEYS.has(key));
    tr.dataset.key = key;
    keys.append(tr);
  }
}

// A row's command: learned, or typed as NEC on the address field.
function rowCommand(tr, name) {
  const repeat = tr.querySelector(".c-repeat").checked;
  if (tr.dataset.learned) return { name, repeat, ...JSON.parse(tr.dataset.learned) };
  if (tr.dataset.current !== undefined) {
    return { name: `cur:${tr.dataset.current}`, existing: Number(tr.dataset.current) };
  }
  const typed = tr.querySelector(".c-code").value.trim();
  if (!typed) return null;
  // a Pronto code pasted from a forum or an old database: groups of four hex digits
  if (/^([0-9a-f]{4}\s+){3,}[0-9a-f]{4}$/i.test(typed)) return { name, repeat, pronto: typed };
  return { name, repeat, code: parseNumber(typed, `the code for ${name}`) };
}

function parseNumber(text, what) {
  const value = Number(text.trim());
  if (!Number.isInteger(value) || value < 0 || value > 0xff) throw new Error(`${what} must be 0 to 255 (0x00 to 0xFF)`);
  return value;
}

async function main() {
  try {
    webapp = await startPython();
  } catch (error) {
    status(`The reader did not load: ${error}`, true);
    return;
  }
  buildDeviceForm();
  remote = new RemoteView($("remote"), { screen: screenOf, onEvent: onRemote });
  for (const b of $("tabs").querySelectorAll("button")) {
    b.addEventListener("click", () => {
      for (const o of $("tabs").querySelectorAll("button")) o.classList.toggle("on", o === b);
      for (const t of document.querySelectorAll(".tab")) t.hidden = t.id !== `tab-${b.dataset.tab}`;
    });
  }
  // pointing at a key row of the add device form lights that key on the drawn remote
  const glowRow = (event, on) => {
    const tr = event.target.closest?.("tr");
    if (!tr) return;
    if (tr.dataset.key) remote.glow(tr.dataset.key, on);
    else if (tr.dataset.corner != null) remote.glow(`soft${tr.dataset.corner}`, on);
  };
  for (const id of ["dev-keys", "dev-soft"]) {
    $(id).addEventListener("mouseover", (e) => glowRow(e, true));
    $(id).addEventListener("mouseout", (e) => glowRow(e, false));
  }
  status(Harmony525.supported()
    ? "Ready. Connect the remote by USB and press Read the remote, or open a file."
    : "This browser has no WebHID (use Chrome or Edge on a computer). Opening a file still works.");
  $("connect").disabled = !Harmony525.supported();

  $("connect").addEventListener("click", async () => {
    let remote;
    try {
      remote = await Harmony525.connect();
      status("Connected. Asking the remote what it is…");
      await remote.drain();
      identity = await remote.version();
      if (identity.architecture !== 9) {
        throw new Error(`this is an architecture ${identity.architecture} remote; only the 515, 525 and 555 (9) are read`);
      }
      $("progress").hidden = false;
      const blob = await remote.readConfig((done, total) => {
        $("progress").max = total;
        $("progress").value = done;
        status(`Reading the configuration: ${done} of ${total} bytes`);
      });
      history = [{ bytes: toJs(webapp.ezhex_from_blob(blob)), label: "read from the remote" }];
      status(`Read a ${toJs(webapp.model(history[0].bytes))}. Save a backup before changing anything on it.`);
      show();
    } catch (error) {
      status(`${error.message ?? error}`, true);
    } finally {
      $("progress").hidden = true;
      if (remote) await remote.close();
    }
  });

  $("file").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    try {
      identity = null;
      history = [{ bytes: new Uint8Array(await file.arrayBuffer()), label: file.name }];
      status(`Opened ${file.name}.`);
      show();
    } catch (error) {
      status(`${file.name} could not be read: ${error.message ?? error}`, true);
    }
  });

  $("save").addEventListener("click", () => {
    const url = URL.createObjectURL(new Blob([current().bytes], { type: "application/octet-stream" }));
    const a = document.createElement("a");
    a.href = url;
    const tag = history.length > 1 ? "edited" : "backup";
    a.download = `harmony525-${tag}-${new Date().toISOString().slice(0, 10)}.EZHex`;
    a.click();
    URL.revokeObjectURL(url);
  });

  $("undo").addEventListener("click", () => {
    if (history.length > 1) history.pop();
    show();
    status("Undone.");
  });

  $("f-del-device").addEventListener("submit", (event) => {
    event.preventDefault();
    const select = $("del-device");
    const name = select.options[select.selectedIndex]?.textContent;
    change(`delete ${name}`, (bytes) => webapp.op_delete_device(bytes, Number(select.value)));
  });

  $("f-move-device").addEventListener("submit", (event) => {
    event.preventDefault();
    const sel = $("mv-device");
    const name = sel.options[sel.selectedIndex]?.textContent.replace(/ \(page.*$/, "");
    const page = Number($("mv-device-page").value), corner = Number($("mv-device-corner").value);
    change(`move ${name} to page ${page + 1}, ${CORNERS[corner]}`,
      (bytes) => webapp.op_place_device(bytes, Number(sel.value), page, corner));
  });

  $("f-move-activity").addEventListener("submit", (event) => {
    event.preventDefault();
    const name = $("mv-activity").value;
    const page = Number($("mv-activity-page").value), corner = Number($("mv-activity-corner").value);
    change(`move ${name} to page ${page + 1}, ${CORNERS[corner]}`,
      (bytes) => webapp.op_place_activity(bytes, name, page, corner));
  });

  $("f-del-activity").addEventListener("submit", (event) => {
    event.preventDefault();
    const name = $("del-activity").value;
    change(`delete activity ${name}`, (bytes) => webapp.op_delete_activity(bytes, name));
  });

  $("f-add-activity").addEventListener("submit", (event) => {
    event.preventDefault();
    const name = $("act-name").value.trim();
    const like = $("act-like").value;
    const on = [...$("act-on").querySelectorAll("input:checked")].map((b) => b.value);
    const sendSteps = steps.map((s) => s.wait ? ["wait", s.group, s.wait] : [s.group, s.command]);
    const show = $("act-show").value === "" ? null : Number($("act-show").value);
    const volume = $("act-volume").value === "" ? null : Number($("act-volume").value);
    const routedKeys = { ...actKeys };
    const editing = $("act-which").value;
    if (editing !== "") {
      const keep = $("act-keep-row").hidden || $("act-keep").checked;
      change(editing === name ? `edit activity ${name}` : `edit activity ${editing} into ${name}`,
        (bytes) => webapp.op_edit_activity(bytes, editing, name, like, on, sendSteps, show, volume, keep, routedKeys))
        .then(() => {
          // renamed: follow it to its new name
          if (editing !== name && [...$("act-which").options].some((o) => o.value === name)) {
            $("act-which").value = name;
            loadActivityIntoForm();
          }
        });
      return;
    }
    change(`add activity ${name}`,
      (bytes) => webapp.op_add_activity(bytes, name, like, on, sendSteps, show, volume, routedKeys))
      .then((ok) => {
        if (!ok) return;            // a refused one keeps what was typed, to fix and try again
        steps = [];
        drawSteps();
        actKeys = {};
        drawActKeys();
      });
  });

  $("act-step-device").addEventListener("change", fillStepCommands);
  $("act-which").addEventListener("change", loadActivityIntoForm);
  $("act-key-add").addEventListener("click", () => {
    actKeys[$("act-key-name").value] = Number($("act-key-device").value);
    drawActKeys();
  });
  $("pw-device").addEventListener("change", fillPowerCommands);
  $("f-power").addEventListener("submit", (event) => {
    event.preventDefault();
    const sel = $("pw-device");
    const name = sel.options[sel.selectedIndex]?.textContent;
    const off = $("pw-off").value === "" ? null : Number($("pw-off").value);
    const tenths = Math.round(Number($("pw-delay").value) * 10);
    change(`keep ${name}'s power state`,
      (bytes) => webapp.op_add_power(bytes, Number(sel.value), Number($("pw-on").value), off, tenths));
  });
  $("act-wait-add").addEventListener("click", () => {
    const device = $("act-step-device").value;
    const group = commandList.find((c) => c.device === device)?.group;
    const tenths = Math.round(Number($("act-wait").value) * 10);
    if (group === undefined || !(tenths > 0 && tenths <= 450)) {
      status("A wait is 0.1 to 45 seconds, for a device with commands.", true);
      return;
    }
    steps.push({ group, wait: tenths, text: `wait ${tenths / 10} s before ${device}'s next command` });
    drawSteps();
  });
  $("act-step-add").addEventListener("click", () => {
    const sel = $("act-step-command");
    if (!sel.value) return;
    const [group, command] = sel.value.split(":").map(Number);
    steps.push({ group, command, text: `${$("act-step-device").value}: ${sel.options[sel.selectedIndex].textContent}` });
    drawSteps();
  });

  $("f-add-device").addEventListener("submit", (event) => {
    event.preventDefault();
    const commands = [], keys = {}, lcd = [];
    let typed = false;
    try {
      for (const tr of $("dev-keys").querySelectorAll("tbody tr")) {
        const c = rowCommand(tr, `key:${tr.dataset.key}`);
        if (!c) continue;
        typed ||= !tr.dataset.learned && c.existing === undefined && c.pronto === undefined;
        commands.push(c);
        keys[tr.dataset.key] = c.name;
      }
      for (const tr of $("dev-soft").querySelectorAll("tbody tr:not(.soft-head)")) {
        const page = Number(tr.dataset.page), corner = Number(tr.dataset.corner);
        const label = tr.querySelector(".soft-label").value.trim();
        const c = rowCommand(tr, `soft:${page}:${corner}`);
        if (!c && !label) continue;
        if (!c || !label) {
          throw new Error(`soft key ${CORNERS[corner]} on page ${page + 1} needs both a label and a command`);
        }
        typed ||= !tr.dataset.learned && c.existing === undefined && c.pronto === undefined;
        commands.push(c);
        while (lcd.length <= page) lcd.push([null, null, null, null]);
        lcd[page][corner] = [label, c.name];
      }
      if (!commands.length && $("dev-which").value === "") throw new Error("learn or type at least one command");
      if (!lcd.some((p) => p.some(Boolean))) throw new Error("give the device at least one soft key");
    } catch (error) {
      status(error.message, true);
      return;
    }
    let address = 0;
    if (typed) {
      try {
        address = parseNumber($("dev-address").value, "the NEC address for typed codes");
      } catch (error) {
        status(error.message, true);
        return;
      }
    }
    const name = $("dev-name").value.trim();
    const editing = $("dev-which").value;
    if (editing === "") {
      change(`add ${name}`, (bytes) => webapp.op_add_device(bytes, name, address, commands, keys, lcd));
      return;
    }
    // keys: a new command where one was learned or typed, nothing where one was
    // cleared, the rest left as they are; soft keys only when they changed
    const editKeys = {};
    for (const tr of $("dev-keys").querySelectorAll("tbody tr")) {
      if (tr.dataset.learned || (tr.dataset.current === undefined && tr.querySelector(".c-code").value.trim())) {
        editKeys[tr.dataset.key] = `key:${tr.dataset.key}`;
      } else if (tr.dataset.cleared) {
        editKeys[tr.dataset.key] = null;
      }
    }
    const newCommands = commands.filter((c) => c.existing !== undefined || !c.name.startsWith("key:") ||
      Object.prototype.hasOwnProperty.call(editKeys, c.name.slice(4)));
    for (const c of newCommands) if (c.existing === undefined && c.code !== undefined) c.address = address;
    for (const c of commands) if (c.code !== undefined && c.address === undefined) c.address = address;
    const softChanged = softState() !== softSnapshot;
    const was = $("dev-name").dataset.was;
    const rekey = softChanged || Object.keys(editKeys).length > 0 ||
      newCommands.some((c) => c.existing === undefined);
    if (!rekey && name === was) {
      status("Nothing changed.");
      return;
    }
    change(name === was ? `edit ${name}` : `edit ${was}, renamed ${name}`, (bytes) => {
      // the rename first: it keeps every mode's number, which the rekey goes by
      const out = name === was ? bytes : webapp.op_rename_device(bytes, Number(editing), name);
      return rekey ? webapp.op_rekey_device(out, Number(editing), newCommands, editKeys,
        softChanged ? lcd : null) : out;
    });
  });

  // ---- writing ----
  let backupBytes = null;              // what this page read off the remote
  // The last backup taken before a write stays in this browser too, so it can
  // be written back after the page was closed. A convenience: the file is the copy.
  const BACKUP_KEY = "harmony525-backup-before-write";
  let backupWhen = null;
  const showRestore = (when = backupWhen) => {
    backupWhen = when;
    $("restore").textContent = when ? `Write back what was on before (${when})` : "Write back what was on before";
    $("restore").hidden = !backupBytes;
    $("backup-save").hidden = !backupBytes;
  };
  try {
    const kept = JSON.parse(localStorage.getItem(BACKUP_KEY) ?? "null");
    if (kept) {
      backupBytes = Uint8Array.from(atob(kept.data), (ch) => ch.charCodeAt(0));
      showRestore(kept.when);
    }
  } catch { /* no storage here: the downloaded file is the backup */ }
  const keepBackup = (bytes) => {
    const now = new Date();         // local time, as the person reads a clock
    const two = (n) => String(n).padStart(2, "0");
    const when = `${now.getFullYear()}-${two(now.getMonth() + 1)}-${two(now.getDate())} ` +
      `${two(now.getHours())}:${two(now.getMinutes())}`;
    try {
      let text = "";
      for (let i = 0; i < bytes.length; i += 0x8000) text += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
      localStorage.setItem(BACKUP_KEY, JSON.stringify({ when, data: btoa(text) }));
    } catch { /* full or blocked */ }
    return when;
  };
  $("backup-save").addEventListener("click", () => {
    if (backupBytes) download(backupBytes, "harmony525-backup-before-write.EZHex");
  });
  const download = (bytes, name) => {
    const url = URL.createObjectURL(new Blob([bytes], { type: "application/octet-stream" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    URL.revokeObjectURL(url);
  };
  const refreshWrite = () => {
    $("write").disabled = !(identity && $("write-ok").checked && history.length);
  };
  $("write-ok").addEventListener("change", refreshWrite);
  const observer = new MutationObserver(refreshWrite);
  observer.observe($("changes"), { childList: true, characterData: true, subtree: true });

  async function writeBytes(bytes, label) {
    let remote;
    let blob;
    try {
      blob = toJs(webapp.blob_of(bytes));
    } catch (error) {
      status(`Not written: ${String(error.message ?? error).split("\n").filter(Boolean).pop()}`, true);
      return false;
    }
    $("write").disabled = true;
    $("progress").hidden = false;
    try {
      remote = await Harmony525.connect();
      await remote.writeConfig(blob, (stage, done, total) => {
        $("progress").max = total || 1;
        $("progress").value = done;
        status(`${label}: ${stage}…`);
      });
      remote = null;
      pending = { bytes, label };
      $("finish").hidden = false;
      status(`${label}: written and verified, and the remote is restarting. When its screen is ` +
        `back, press Reconnect and finish and choose the remote again: the page sets its clock and ` +
        `reads it back.`);
      return true;
    } catch (error) {
      status(`${label} failed: ${error.message ?? error}. The backup can be written back.`, true);
      showRestore();
      return false;
    } finally {
      $("progress").hidden = true;
      if (remote) { try { await remote.close(); } catch { /* gone */ } }
      refreshWrite();
    }
  }

  // After the reset: Chrome forgot the remote when it left the bus, so the
  // user picks it again; then the clock, then a full read back.
  let pending = null;
  $("finish").addEventListener("click", async () => {
    if (!pending) return;
    let remote;
    try {
      remote = await Harmony525.connect();
      await remote.drain();
      status(`${pending.label}: setting the clock…`);
      await remote.setClock();
      status(`${pending.label}: reading the remote back…`);
      const back = toJs(webapp.ezhex_from_blob(await remote.readConfig()));
      const same = back.length === pending.bytes.length && back.every((b, i) => b === pending.bytes[i]);
      if (!same) throw new Error("the remote does not read back as what was written");
      history = [{ bytes: back, label: `${pending.label}, read back` }];
      show();
      status(`${pending.label}: done. The remote reads back byte for byte and its clock is set. Try it.`);
      pending = null;
      $("finish").hidden = true;
    } catch (error) {
      status(`Finishing failed: ${error.message ?? error}`, true);
      showRestore();
    } finally {
      if (remote) { try { await remote.close(); } catch { /* gone */ } }
    }
  });

  $("write").addEventListener("click", async () => {
    if (!identity) return;
    backupBytes = history[0].bytes;
    const when = keepBackup(backupBytes);
    download(backupBytes, `harmony525-backup-before-write-${new Date().toISOString().slice(0, 19).replace(/:/g, "")}.EZHex`);
    $("write-ok").checked = false;
    const which = toJs(webapp.model(current().bytes));
    if (which !== "Harmony 525") {
      // the same firmware, and writing is Concordance's sequence, but only a 525 has had it
      status(`Writing to a ${which}: this page has only ever written to a Harmony 525.`);
      await repaint();
    }
    await writeBytes(current().bytes, "writing");
    showRestore(when);
  });

  $("restore").addEventListener("click", async () => {
    if (!backupBytes) return;
    if (!$("write-ok").checked) {
      status("Writing the backup back replaces what is on the remote too: tick the box above first.", true);
      return;
    }
    $("write-ok").checked = false;
    await writeBytes(backupBytes, "writing the backup back");
  });
}

main();
