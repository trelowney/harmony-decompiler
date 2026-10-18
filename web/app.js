import { loadPyodide } from "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/pyodide.mjs";
import { Harmony525 } from "./hid525.js";

const MODULES = ["_paths", "pic18dis", "hconfig", "analyze_525_ir", "check_525_mode_pages",
  "class5_ir_encoder", "verify_525_semantics", "render_525_screens", "h525_lcd_author",
  "clone_525_device", "compose525"];

const $ = (id) => document.getElementById(id);
let py = null;
let current = null;        // the .EZHex bytes on show
let currentName = null;

function status(text, bad = false) {
  $("status").textContent = text;
  $("status").className = bad ? "bad" : "";
}

async function startPython() {
  py = await loadPyodide();
  for (const name of MODULES) {
    const source = await (await fetch(`../tools/${name}.py`)).text();
    py.FS.writeFile(`${name}.py`, source);
  }
  py.FS.writeFile("webapp.py", await (await fetch("webapp.py")).text());
  const sample = new Uint8Array(await (await fetch("../samples/harmony525/config.EZHex")).arrayBuffer());
  const webapp = py.pyimport("webapp");
  webapp.set_template(sample);
  return webapp;
}

function toJs(value) {
  return value.toJs({ dict_converter: Object.fromEntries });
}

function row(cells, header = false) {
  const tr = document.createElement("tr");
  for (const c of cells) {
    const td = document.createElement(header ? "th" : "td");
    td.textContent = c;
    tr.appendChild(td);
  }
  return tr;
}

const CORNERS = ["top left", "top right", "bottom left", "bottom right"];

function show(webapp, container, identity) {
  const s = toJs(webapp.summary(container));
  const id = $("identity");
  id.replaceChildren();
  if (identity) {
    id.append(row(["firmware", identity.firmware]), row(["architecture", identity.architecture]),
      row(["skin", identity.skin]), row(["flash", identity.flash]));
  }
  id.append(row(["source", currentName]), row(["sha256", s.sha256]),
    row(["config", `${s.blob_bytes} bytes`]),
    row(["checksum", s.trailer_ok ? "valid" : "INVALID"]),
    row(["decompiles and recompiles byte for byte", s.roundtrip_ok ? "yes" : "NO"]));

  const dv = $("devices");
  dv.replaceChildren(row(["name", "mode", "IR group", "Devices page", "corner"], true));
  for (const d of s.devices) {
    dv.append(row([d.name, d.mode, d.group ?? "-", d.page + 1, CORNERS[d.corner]]));
  }
  const ac = $("activities");
  ac.replaceChildren(row(["name", "value", "key set", "menu page", "corner"], true));
  for (const a of s.activities) {
    ac.append(row([a.name, a.value, a.key_set, a.page + 1, CORNERS[a.corner]]));
  }

  const holder = $("screens");
  holder.replaceChildren();
  const modes = toJs(webapp.screen_modes(container));
  for (const [mode, page, pixels] of toJs(webapp.screens(container, modes))) {
    const scale = 3;
    const canvas = document.createElement("canvas");
    canvas.width = 96 * scale;
    canvas.height = 64 * scale;
    const g = canvas.getContext("2d");
    const css = getComputedStyle(document.documentElement);
    g.fillStyle = css.getPropertyValue("--lcd");
    g.fillRect(0, 0, canvas.width, canvas.height);
    g.fillStyle = css.getPropertyValue("--ink");
    for (let i = 0; i < pixels.length; i += 1) {
      if (pixels[i]) g.fillRect((i % 96) * scale, Math.floor(i / 96) * scale, scale, scale);
    }
    const fig = document.createElement("figure");
    const cap = document.createElement("figcaption");
    cap.textContent = `mode ${mode}, page ${page + 1}`;
    fig.append(canvas, cap);
    holder.append(fig);
  }
  $("result").hidden = false;
  $("save").disabled = false;
}

async function main() {
  let webapp;
  try {
    webapp = await startPython();
  } catch (error) {
    status(`The reader did not load: ${error}`, true);
    return;
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
      const identity = await remote.version();
      if (identity.architecture !== 9) {
        throw new Error(`this is an architecture ${identity.architecture} remote; only the 525 (9) is read`);
      }
      $("progress").hidden = false;
      const blob = await remote.readConfig((done, total) => {
        $("progress").max = total;
        $("progress").value = done;
        status(`Reading the configuration: ${done} of ${total} bytes`);
      });
      current = toJs(webapp.ezhex_from_blob(blob));
      currentName = "read from the remote";
      status("Read. Save a backup before changing anything on this remote.");
      show(webapp, current, identity);
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
      current = new Uint8Array(await file.arrayBuffer());
      currentName = file.name;
      status(`Opened ${file.name}.`);
      show(webapp, current, null);
    } catch (error) {
      status(`${file.name} could not be read: ${error.message ?? error}`, true);
    }
  });

  $("save").addEventListener("click", () => {
    const url = URL.createObjectURL(new Blob([current], { type: "application/octet-stream" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `harmony525-${new Date().toISOString().slice(0, 10)}.EZHex`;
    a.click();
    URL.revokeObjectURL(url);
  });
}

main();
