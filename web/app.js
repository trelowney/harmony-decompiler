import { loadPyodide } from "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/pyodide.mjs";
import { Harmony525 } from "./hid525.js";

const MODULES = ["_paths", "pic18dis", "hconfig", "analyze_525_ir", "check_525_mode_pages",
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

function fillStepCommands() {
  const dev = $("act-step-device").value;
  $("act-step-command").replaceChildren(...commandList.filter((c) => c.device === dev)
    .map((c) => option(`${c.group}:${c.command}`, c.label)));
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

function drawScreens(bytes) {
  const holder = $("screens");
  holder.replaceChildren();
  const modes = toJs(webapp.screen_modes(bytes));
  const css = getComputedStyle(document.documentElement);
  for (const [mode, page, pixels] of toJs(webapp.screens(bytes, modes))) {
    const scale = 3;
    const canvas = document.createElement("canvas");
    canvas.width = 96 * scale;
    canvas.height = 64 * scale;
    const g = canvas.getContext("2d");
    g.fillStyle = css.getPropertyValue("--lcd");
    g.fillRect(0, 0, canvas.width, canvas.height);
    g.fillStyle = css.getPropertyValue("--ink");
    for (let i = 0; i < pixels.length; i += 1) {
      if (pixels[i]) g.fillRect((i % 96) * scale, Math.floor(i / 96) * scale, scale, scale);
    }
    const fig = document.createElement("figure");
    fig.append(canvas, cell("figcaption", `mode ${mode}, page ${page + 1}`));
    holder.append(fig);
  }
}

function show() {
  const { bytes, label } = current();
  const s = toJs(webapp.summary(bytes));
  const id = $("identity");
  id.replaceChildren();
  if (identity) {
    id.append(row(["firmware", identity.firmware]), row(["architecture", identity.architecture]),
      row(["skin", identity.skin]), row(["flash", identity.flash]));
  }
  id.append(row(["source", history[0].label]), row(["sha256", s.sha256]),
    row(["config", `${s.blob_bytes} bytes`]),
    row(["checksum", s.trailer_ok ? "valid" : "INVALID"]),
    row(["decompiles and recompiles byte for byte", s.roundtrip_ok ? "yes" : "NO"]));

  const dv = $("devices");
  dv.replaceChildren(row(["name", "mode", "IR group", "Devices page", "corner"], true));
  for (const d of s.devices) dv.append(row([d.name, d.mode, d.group ?? "-", d.page + 1, CORNERS[d.corner]]));
  const ac = $("activities");
  ac.replaceChildren(row(["name", "what it does", "menu page", "corner"], true));
  for (const a of s.activities) ac.append(row([a.name, a.does, a.page + 1, CORNERS[a.corner]]));

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
    (x) => x.mode, (x) => `${x.name} (page ${x.page + 1}, ${CORNERS[x.corner]})`);
  fillMove($("mv-activity"), $("mv-activity-page"), $("mv-activity-corner"), s.activities,
    (x) => x.name, (x) => `${x.name} (page ${x.page + 1}, ${CORNERS[x.corner]})`);
  const delA = $("del-activity");
  delA.replaceChildren(...s.activities.map((a) => option(a.name, a.name)));
  const like = $("act-like");
  like.replaceChildren(...s.activities.map((a) => option(a.name, a.name)));
  const on = $("act-on");
  on.replaceChildren();
  for (const name of toJs(webapp.power_devices(bytes))) {
    const l = document.createElement("label");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.value = name;
    l.append(box, ` ${name}`);
    on.append(l);
  }

  const n = history.length - 1;
  $("changes").textContent = n === 0 ? "No changes yet." :
    `${n} change${n > 1 ? "s" : ""}: ${history.slice(1).map((h) => h.label).join("; ")}`;
  $("undo").disabled = n === 0;
  drawScreens(bytes);
  $("result").hidden = false;
  $("save").disabled = false;
}

async function change(label, run) {
  status(`${label}: building and checking…`);
  await repaint();
  try {
    const bytes = toJs(run(current().bytes));
    history.push({ bytes, label });
    show();
    status(`${label}: done. Save the file to keep it.`);
  } catch (error) {
    const message = String(error.message ?? error).split("\n").filter(Boolean).pop();
    status(`${label}: refused. ${message}`, true);
  }
}

// ---- the add device form ----
const REPEATING_KEYS = new Set(["Up", "Down", "Left", "Right", "VolumeUp", "VolumeDown",
  "ChannelUp", "ChannelDown", "Rewind", "FastForward"]);

// One row per thing the device answers: a 525 key, or a soft key with its label.
function learnRow(first, repeat) {
  const tr = document.createElement("tr");
  tr.innerHTML = `<td class="r-what"></td><td><input class="c-code" size="9" placeholder="—"></td>` +
    `<td><label class="check"><input type="checkbox" class="c-repeat"> repeats</label></td>` +
    `<td><button type="button" class="c-learn">Learn</button> <button type="button" class="c-del">×</button></td>`;
  tr.querySelector(".r-what").append(first);
  tr.querySelector(".c-repeat").checked = repeat;
  tr.querySelector(".c-code").addEventListener("input", () => { delete tr.dataset.learned; });
  tr.querySelector(".c-del").addEventListener("click", () => {
    delete tr.dataset.learned;
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
  head.innerHTML = `<td colspan="4"><strong>page ${page + 1}</strong></td>`;
  soft.append(head);
  CORNERS.forEach((corner, i) => {
    const label = document.createElement("input");
    label.className = "soft-label";
    label.placeholder = `${corner} label`;
    label.maxLength = 10;
    const tr = learnRow(label, false);
    tr.dataset.corner = i;
    tr.dataset.page = page;
    soft.append(tr);
  });
  $("dev-soft-page").disabled = page + 1 >= 6;
}

function buildDeviceForm() {
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
  const typed = tr.querySelector(".c-code").value.trim();
  if (!typed) return null;
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
        throw new Error(`this is an architecture ${identity.architecture} remote; only the 525 (9) is read`);
      }
      $("progress").hidden = false;
      const blob = await remote.readConfig((done, total) => {
        $("progress").max = total;
        $("progress").value = done;
        status(`Reading the configuration: ${done} of ${total} bytes`);
      });
      history = [{ bytes: toJs(webapp.ezhex_from_blob(blob)), label: "read from the remote" }];
      status("Read. Save a backup before changing anything on this remote.");
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
    const sendSteps = steps.map((s) => [s.group, s.command]);
    change(`add activity ${name}`, (bytes) => webapp.op_add_activity(bytes, name, like, on, sendSteps));
    steps = [];
    drawSteps();
  });

  $("act-step-device").addEventListener("change", fillStepCommands);
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
        typed ||= !tr.dataset.learned;
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
        typed ||= !tr.dataset.learned;
        commands.push(c);
        while (lcd.length <= page) lcd.push([null, null, null, null]);
        lcd[page][corner] = [label, c.name];
      }
      if (!commands.length) throw new Error("learn or type at least one command");
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
    change(`add ${name}`, (bytes) => webapp.op_add_device(bytes, name, address, commands, keys, lcd));
  });

  // ---- writing ----
  let backupBytes = null;              // what this page read off the remote
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
      $("restore").hidden = !backupBytes;
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
      $("restore").hidden = !backupBytes;
    } finally {
      if (remote) { try { await remote.close(); } catch { /* gone */ } }
    }
  });

  $("write").addEventListener("click", async () => {
    if (!identity) return;
    backupBytes = history[0].bytes;
    download(backupBytes, `harmony525-backup-before-write-${new Date().toISOString().slice(0, 19).replace(/:/g, "")}.EZHex`);
    $("write-ok").checked = false;
    await writeBytes(current().bytes, "writing");
  });

  $("restore").addEventListener("click", async () => {
    if (!backupBytes) return;
    if (await writeBytes(backupBytes, "writing the backup back")) $("restore").hidden = true;
  });
}

main();
