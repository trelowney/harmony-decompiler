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
  ac.replaceChildren(row(["name", "value", "key set", "menu page", "corner"], true));
  for (const a of s.activities) ac.append(row([a.name, a.value, a.key_set, a.page + 1, CORNERS[a.corner]]));

  // the change forms follow what is on show
  const del = $("del-device");
  del.replaceChildren();
  for (const d of s.devices.filter((x) => x.page > 0)) del.append(option(d.mode, d.name));
  $("f-del-device").querySelector("button").disabled = !del.options.length;
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
function commandNames() {
  return [...$("dev-commands").querySelectorAll("tbody tr")]
    .map((tr) => tr.querySelector(".c-name").value.trim()).filter(Boolean);
}

function refreshCommandChoices() {
  const names = commandNames();
  for (const select of document.querySelectorAll(".cmd-choice")) {
    const keep = select.value;
    select.replaceChildren(option("", "—"), ...names.map((n) => option(n, n)));
    select.value = names.includes(keep) ? keep : "";
  }
}

function addCommandRow(name = "", code = "", repeat = false) {
  const tr = document.createElement("tr");
  tr.innerHTML = `<td><input class="c-name" size="12"></td><td><input class="c-code" size="6" placeholder="0x0A"></td>` +
    `<td><input type="checkbox" class="c-repeat"></td>` +
    `<td><button type="button" class="c-learn">Learn</button> <button type="button" class="c-del">×</button></td>`;
  tr.querySelector(".c-name").value = name;
  tr.querySelector(".c-code").value = code;
  tr.querySelector(".c-repeat").checked = repeat;
  tr.querySelector(".c-name").addEventListener("input", refreshCommandChoices);
  tr.querySelector(".c-del").addEventListener("click", () => { tr.remove(); refreshCommandChoices(); });
  tr.querySelector(".c-code").addEventListener("input", () => { delete tr.dataset.learned; });
  tr.querySelector(".c-learn").addEventListener("click", () => learnInto(tr));
  $("dev-commands").querySelector("tbody").append(tr);
  refreshCommandChoices();
}

// The 525 as a receiver: point the device's own remote at the bottom end of
// the 525, below the keypad, press Learn and then the key, a short tap.
async function learnInto(tr) {
  const name = tr.querySelector(".c-name").value.trim() || "this command";
  let remote;
  try {
    remote = await Harmony525.connect();
    status(`Learning ${name}: point the original remote at the bottom end of the 525 and tap the key…`);
    const capture = await remote.learnIR();
    if (!capture) throw new Error("nothing was received within five seconds");
    const decoded = toJs(webapp.decode_capture(capture.durations));
    if (decoded.nec) {
      const [a, a2, cmd] = decoded.nec;
      tr.dataset.learned = JSON.stringify({ address: a, address2: a2 });
      tr.querySelector(".c-code").value = `0x${cmd.toString(16).padStart(2, "0").toUpperCase()}`;
      if (!$("dev-address").value.trim()) $("dev-address").value = `0x${a.toString(16).toUpperCase()}`;
      status(`Learned ${name}: NEC, address 0x${a.toString(16)} ${a2.toString(16)}, command 0x${cmd.toString(16)}.`);
    } else {
      tr.dataset.learned = JSON.stringify({ carrier: capture.carrier, durations: capture.durations });
      tr.querySelector(".c-code").value = "learned";
      status(`Learned ${name}: not NEC, kept as it was received (${capture.durations.length} ` +
        `durations at ${capture.carrier} Hz).`);
    }
  } catch (error) {
    status(`Learning ${name} failed: ${error.message ?? error}`, true);
  } finally {
    if (remote) { try { await remote.close(); } catch { /* gone */ } }
  }
}

function buildDeviceForm() {
  const keys = $("dev-keys");
  for (const key of toJs(webapp.key_names())) {
    const l = document.createElement("label");
    const s = document.createElement("select");
    s.className = "cmd-choice";
    s.dataset.key = key;
    l.append(key, s);
    keys.append(l);
  }
  const soft = $("dev-soft");
  CORNERS.forEach((corner, i) => {
    const l = document.createElement("label");
    const label = document.createElement("input");
    label.className = "soft-label";
    label.placeholder = `${corner} label`;
    label.maxLength = 10;
    const s = document.createElement("select");
    s.className = "cmd-choice soft-cmd";
    s.dataset.corner = i;
    l.append(corner, label, s);
    soft.append(l);
  });
  for (const [n, c, r] of [["Up", "", true], ["Down", "", true], ["Left", "", true],
    ["Right", "", true], ["OK", "", false], ["Exit", "", false]]) addCommandRow(n, c, r);
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
    change(`add activity ${name}`, (bytes) => webapp.op_add_activity(bytes, name, like, on));
  });

  $("f-add-device").addEventListener("submit", (event) => {
    event.preventDefault();
    let address, commands;
    try {
      const rows = [...$("dev-commands").querySelectorAll("tbody tr")]
        .filter((tr) => tr.querySelector(".c-name").value.trim());
      const allLearned = rows.every((tr) => tr.dataset.learned);
      address = allLearned && !$("dev-address").value.trim() ? 0
        : parseNumber($("dev-address").value, "the NEC address");
      commands = rows.map((tr) => {
        const learned = tr.dataset.learned ? JSON.parse(tr.dataset.learned) : {};
        const command = { name: tr.querySelector(".c-name").value.trim(),
                          repeat: tr.querySelector(".c-repeat").checked, ...learned };
        if (!learned.durations) {
          command.code = parseNumber(tr.querySelector(".c-code").value, `the code of ${command.name}`);
        }
        return command;
      });
    } catch (error) {
      status(error.message, true);
      return;
    }
    const keys = {};
    for (const s of $("dev-keys").querySelectorAll("select")) if (s.value) keys[s.dataset.key] = s.value;
    const lcd = [...$("dev-soft").querySelectorAll("label")].map((l) => {
      const label = l.querySelector(".soft-label").value.trim();
      const cmd = l.querySelector(".soft-cmd").value;
      return label && cmd ? [label, cmd] : null;
    });
    const name = $("dev-name").value.trim();
    change(`add ${name}`, (bytes) => webapp.op_add_device(bytes, name, address, commands, keys, lcd));
  });

  $("dev-add-command").addEventListener("click", () => addCommandRow());

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
