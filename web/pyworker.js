// The page's Python, in a worker: building and checking a configuration takes
// seconds, and on the page's own thread it froze the page (Chrome asked whether
// to wait or close it). The page sends {id, fn, args}, a function of webapp.py
// by name; the answer is {id, ok, value} or {id, ok: false, error}. Arguments
// and answers are plain data: bytes as Uint8Array, dicts as objects.
import { loadPyodide } from "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/pyodide.mjs";

const MODULES = ["_paths", "flipper_ir", "pronto", "irdb", "pic18dis", "hconfig", "analyze_525_ir", "check_525_mode_pages",
  "class5_ir_encoder", "verify_525_semantics", "render_525_screens", "h525_lcd_author",
  "clone_525_device", "compose525"];

const text = async (url) => (await fetch(url, { cache: "no-cache" })).text();

async function startPython() {
  const py = await loadPyodide();
  for (const name of MODULES) py.FS.writeFile(`${name}.py`, await text(`../tools/${name}.py`));
  // which pixels draw which character, per typeface (tools/build_arch9_glyphs.py)
  py.FS.writeFile("arch9_glyphs.json", await text("../tools/arch9_glyphs.json"));
  // and glyphs for characters a configuration's font lacks (tools/build_arch9_glyphs.py)
  py.FS.writeFile("arch9_font_glyphs.json", await text("../tools/arch9_font_glyphs.json"));
  py.FS.writeFile("webapp.py", await text("webapp.py"));
  const sample = new Uint8Array(await (await fetch("../samples/harmony525/config.EZHex")).arrayBuffer());
  const module = py.pyimport("webapp");
  module.set_template(sample);
  return module;
}

const ready = startPython();

// A Python answer as plain data, and the proxy let go of.
function plain(value) {
  if (!value || !value.toJs) return value;
  const out = value.toJs({ dict_converter: Object.fromEntries });
  value.destroy?.();
  return out;
}

// One call at a time, in the order they came: an edit is built on the bytes
// the one before it gave back.
let queue = Promise.resolve();

self.onmessage = ({ data }) => {
  const { id, fn, args } = data;
  queue = queue.then(async () => {
    try {
      const webapp = await ready;
      if (fn === "ready") {
        self.postMessage({ id, ok: true, value: true });
        return;
      }
      if (typeof webapp[fn] !== "function") throw new Error(`webapp has no ${fn}`);
      self.postMessage({ id, ok: true, value: plain(webapp[fn](...args)) });
    } catch (error) {
      self.postMessage({ id, ok: false, error: String(error?.message ?? error) });
    }
  });
};
