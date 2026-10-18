// A drawn Harmony 525 that behaves like the configuration on show: the screen
// is the config's own screen, rendered by the decompiler; Activities and
// Devices open their menus, the soft keys enter devices and activities, the
// arrows under the screen turn pages, and every key says what it would send.
// The layout follows the European 525 (colour keys below the number pad); it
// is a drawing of our own, not a picture of the product.

const CORNERS = ["top left", "top right", "bottom left", "bottom right"];

// Rows top to bottom. A key is [name, label, class, caption]; names are
// compose525.KEYS plus the remote's own: activities, devices, help,
// pagePrev, pageNext.
const LAYOUT = [
  { cls: "r-act", keys: [["off", "⏻", "corner-key off"], ["activities", "Activities", "act"]] },
  { cls: "r-two r-small", keys: [["devices", "Devices", "pill"], ["help", "Help", "pill"]] },
  { screen: true },
  { cls: "r-pages", keys: [["pagePrev", "←", "page"], ["pageNext", "→", "page"]] },
  { cls: "r-transport", keys: [["Stop", "■", "round", "Stop"], ["SkipBack", "⏮", "round", "Replay"],
    ["SkipForward", "⏭", "round", "Skip"], ["Play", "▶", "round", "Play"],
    ["Record", "●", "round rec", "Rec"], ["Rewind", "◀◀", "round", "Rew"],
    ["FastForward", "▶▶", "round", "Fwd"], ["Pause", "❚❚", "round", "Pause"]] },
  { cls: "r-two", keys: [["Guide", "Guide", "pill"], ["Info", "Info", "pill"]] },
  { cls: "r-two", keys: [["Exit", "Exit", "pill"], ["Menu", "Menu", "pill"]] },
  { cls: "r-two", keys: [["Mute", "Mute", "pill"], ["Prev", "Prev", "pill"]] },
  { wheel: true },
  { cls: "r-digits", keys: [["1", "1"], ["2", "2"], ["3", "3"], ["4", "4"], ["5", "5"], ["6", "6"],
    ["7", "7"], ["8", "8"], ["9", "9"], ["Star", "*"], ["0", "0"], ["Hash", "#"]] },
  { cls: "r-colours", keys: [["Red", "", "c-red"], ["Green", "", "c-green"],
    ["Yellow", "", "c-yellow"], ["Blue", "", "c-blue"]] },
];
const OWN = ["activities", "devices", "help", "off", "glow", "pagePrev", "pageNext"];

const PRETTY = { VolumeUp: "Vol +", VolumeDown: "Vol −", ChannelUp: "Ch +", ChannelDown: "Ch −",
  FastForward: "Fwd", Rewind: "Rew", SkipBack: "Replay", SkipForward: "Skip", Record: "Rec",
  Star: "*", Hash: "#" };

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

const sendText = (sends) => sends.map((s) => `${s.device}: ${s.label}`).join(", then ");

export class RemoteView {
  // screen(mode, page) -> Uint8Array of 96 x 64 (1 = ink), or null
  // onEvent({type: "device"|"activity"|"send"|"none", ...}) for the page to react
  constructor(root, { screen, onEvent }) {
    this.screen = screen;
    this.onEvent = onEvent;
    this.model = null;
    this.state = null;
    this.saved = null;
    this.keys = new Map();
    this.build(root);
  }

  build(root) {
    root.replaceChildren();
    const body = el("div", "remote");
    this.body = body;
    const top = el("div", "r-black"), bottom = el("div", "r-silver");
    for (const row of LAYOUT) {
      const into = row.wheel || row.cls === "r-digits" || row.cls === "r-colours" ? bottom : top;
      if (row.screen) into.append(this.buildScreen());
      else if (row.wheel) bottom.append(this.buildWheel());
      else {
        const r = el("div", `r-row ${row.cls}`);
        for (const k of row.keys) r.append(this.key(...k));
        into.append(r);
      }
    }
    body.append(top, bottom);
    this.where = el("div", "r-where");
    this.log = el("div", "r-log", "Press a key.");
    // scaled as one piece so the whole remote fits the height of its column
    this.fitBox = el("div", "r-fit");
    this.fitBox.append(body, this.where, this.log);
    root.append(this.fitBox);
    // the column it is fitted to: the page's own, or the element it was given
    this.root = root.closest(".remote-col") ?? root;
    const watch = new ResizeObserver(() => this.fit());
    watch.observe(this.root);
    watch.observe(this.fitBox);
  }

  buildScreen() {
    const wrap = el("div", "r-screenrow");
    const left = el("div", "r-softcol"), right = el("div", "r-softcol");
    this.soft = [];
    const frame = el("div", "r-lcd");
    this.canvas = el("canvas");
    this.canvas.width = 96;
    this.canvas.height = 64;
    frame.append(this.canvas);
    this.quads = CORNERS.map((c, i) => {
      const q = el("div", `r-quad q${i}`);
      frame.append(q);
      return q;
    });
    for (let i = 0; i < 4; i += 1) {
      const b = el("button", "rk soft");
      b.type = "button";
      b.setAttribute("aria-label", `soft key ${CORNERS[i]}`);
      b.addEventListener("click", () => this.press(`soft${i}`));
      b.addEventListener("mouseenter", () => this.quads[i].classList.add("lit"));
      b.addEventListener("mouseleave", () => this.quads[i].classList.remove("lit"));
      this.soft.push(b);
      this.keys.set(`soft${i}`, b);
      (i % 2 ? right : left).append(b);
    }
    wrap.append(left, frame, right);
    return wrap;
  }

  // the silver ring: volume on the left, channel on the right, the dark
  // navigation disc with OK in the middle
  buildWheel() {
    const ring = el("div", "r-wheel");
    const side = (cls, up, down, upText, downText) => {
      const s = el("div", `r-rocker ${cls}`);
      s.append(this.key(up, upText, "rock"), this.key(down, downText, "rock"));
      return s;
    };
    const disc = el("div", "r-disc");
    for (const [name, label, cls] of [["Up", "▲", "n-up"], ["Left", "◀", "n-left"], ["OK", "OK", "n-ok"],
      ["Right", "▶", "n-right"], ["Down", "▼", "n-down"]]) disc.append(this.key(name, label, `nav ${cls}`));
    ring.append(side("r-vol", "VolumeUp", "VolumeDown", "+", "−"), disc,
      side("r-ch", "ChannelUp", "ChannelDown", "+", "−"));
    // Glow sits under the navigation disc, between the Vol and Ch captions
    const cap = el("div", "r-wheelcap");
    cap.append(el("span", "", "Vol"), this.key("glow", "Glow", "glow-key"), el("span", "", "Ch"));
    const wrap = el("div", "r-wheelwrap");
    wrap.append(ring, cap);
    return wrap;
  }

  key(name, label, cls = "", caption = null) {
    const b = el("button", `rk ${cls}`, label);
    b.type = "button";
    b.dataset.key = name;
    b.addEventListener("click", () => this.press(name));
    this.keys.set(name, b);
    if (!caption) return b;
    const box = el("div", "r-cap");
    box.append(b, el("small", "", caption));
    return box;
  }

  // ---- state ----

  load(model) {
    this.model = model;
    this.go({ mode: model.devices_mode, page: 0 });
    requestAnimationFrame(() => this.fit());
    this.say("Press a key.");
  }

  pages(state = this.state) {
    const m = this.model;
    if (state.kind === "devices") return m.devices_pages;
    if (state.kind === "menu") return m.menu_pages;
    return m.modes[state.mode]?.pages ?? 1;
  }

  // what a mode is, from the model: {kind, name}
  classify(mode) {
    const m = this.model;
    if (mode === m.devices_mode) return { kind: "devices", name: "Devices" };
    if (mode === m.menu_mode) return { kind: "menu", name: "Activities" };
    const d = m.devices.find((x) => x.mode === mode);
    if (d) return { kind: "device", name: d.name };
    const a = m.activities.find((x) => x.mode === mode);
    if (a) return { kind: "activity", name: a.name };
    return { kind: "other", name: `mode ${mode}` };
  }

  go(state) {
    this.state = { mode: state.mode, page: state.page, ...this.classify(state.mode) };
    this.draw();
  }

  // jump from outside (a screen in the gallery)
  show(mode, page) {
    this.go({ mode, page });
    this.say(`${this.state.name}, page ${page + 1}.`);
  }

  draw() {
    const { mode, page, name } = this.state;
    const pixels = this.screen(mode, page);
    const g = this.canvas.getContext("2d");
    const css = getComputedStyle(this.canvas);
    g.fillStyle = css.getPropertyValue("--r-lcd").trim() || "#5cc3ee";
    g.fillRect(0, 0, 96, 64);
    if (pixels) {
      g.fillStyle = css.getPropertyValue("--r-ink").trim() || "#082536";
      for (let i = 0; i < pixels.length; i += 1) if (pixels[i]) g.fillRect(i % 96, Math.floor(i / 96), 1, 1);
    }
    const n = this.pages();
    this.where.textContent = `${name}${n > 1 ? ` · page ${page + 1} of ${n}` : ""}`;
    this.keys.get("pagePrev").disabled = page <= 0;
    this.keys.get("pageNext").disabled = page >= n - 1;
    // keys that do something here are drawn live, the rest dimmed
    for (const [k, b] of this.keys) {
      if (OWN.includes(k)) continue;
      const what = this.meaning(k);
      b.classList.toggle("dead", !what);
      b.title = what ? `${PRETTY[k] ?? (k.startsWith("soft") ? CORNERS[+k[4]] : k)}: ${what.text}` : "";
    }
  }

  // what pressing a key does on the screen on show: {text, event, next} or null
  meaning(key) {
    const m = this.model, s = this.state;
    if (key.startsWith("soft")) {
      const corner = +key[4];
      if (s.kind === "devices") {
        const d = m.devices.find((x) => x.page === s.page && x.corner === corner);
        return d && { text: `opens ${d.name}`, event: { type: "device", mode: d.mode, name: d.name },
          next: { mode: d.mode, page: 0 } };
      }
      if (s.kind === "menu") {
        const a = m.activities.find((x) => x.page === s.page && x.corner === corner);
        return a && { text: `starts ${a.name}: ${a.does.join(", ")}`,
          event: { type: "activity", name: a.name }, next: { mode: a.mode, page: 0 } };
      }
      const sends = m.modes[s.mode]?.soft?.[s.page]?.[corner];
      return sends && { text: `sends ${sendText(sends)}`, event: { type: "send", sends, kind: s.kind, name: s.name } };
    }
    const sends = m.modes[s.mode]?.keys?.[key];
    return sends && { text: `sends ${sendText(sends)}`, event: { type: "send", sends, kind: s.kind, name: s.name } };
  }

  press(key) {
    if (!this.model) return;
    this.flashKey(key);
    const m = this.model, s = this.state;
    if (key === "activities") {
      if (m.menu_mode == null) { this.say("This configuration has no activities."); return; }
      this.go({ mode: m.menu_mode, page: 0 });
      this.say("Activities.");
      return;
    }
    if (key === "devices") {
      this.go({ mode: m.devices_mode, page: 0 });
      this.say("Devices.");
      return;
    }
    if (key === "glow") {
      this.light();
      this.say("Glow: the screen and keys light up for a few seconds.");
      return;
    }
    if (key === "off") {
      this.go({ mode: m.devices_mode, page: 0 });
      this.say("Off: the running activity's devices are switched off.");
      this.onEvent({ type: "none" });
      return;
    }
    if (key === "help") {
      this.say("Help asks, device by device, whether things are on; it is not drawn here.");
      return;
    }
    if (key === "pagePrev" || key === "pageNext") {
      const page = Math.max(0, Math.min(this.pages() - 1, s.page + (key === "pageNext" ? 1 : -1)));
      this.go({ mode: s.mode, page });
      return;
    }
    const what = this.meaning(key);
    if (!what) {
      this.say(`${PRETTY[key] ?? (key.startsWith("soft") ? "That soft key" : key)} does nothing here.`);
      this.onEvent({ type: "none" });
      return;
    }
    this.say(what.text.replace(/^./, (c) => c.toUpperCase()) + ".");
    if (what.next) this.go(what.next);
    this.onEvent(what.event);
  }

  fit() {
    const box = this.fitBox;
    if (matchMedia("(max-width: 1100px)").matches) {
      box.style.transform = box.style.marginBottom = box.style.width = this.root.style.width = "";
      return;
    }
    // as tall as the window allows, and the column exactly as wide as the
    // remote drawn at that size, so there is no empty band beside it
    const cs = getComputedStyle(this.root);
    const room = this.root.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom);
    const drawn = this.body.offsetWidth;
    box.style.width = `${drawn}px`;
    const need = box.offsetHeight;
    if (!need || !room || !drawn) return;
    const scale = Math.min(room / need, (innerWidth * 0.4) / drawn, 1.8);
    box.style.transform = `scale(${scale})`;
    box.style.marginBottom = `${need * (scale - 1)}px`;
    this.root.style.width = `${Math.ceil(drawn * scale + parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight))}px`;
  }

  // the backlight: on for six seconds, then the screen goes dim again
  light() {
    this.body.classList.add("lit");
    clearTimeout(this.lightTimer);
    this.lightTimer = setTimeout(() => this.body.classList.remove("lit"), 6000);
  }

  say(text) {
    this.log.textContent = text;
    this.log.classList.remove("pulse");
    void this.log.offsetWidth;
    this.log.classList.add("pulse");
  }

  flashKey(key) {
    const b = this.keys.get(key);
    if (!b) return;
    b.classList.remove("hit");
    void b.offsetWidth;
    b.classList.add("hit");
  }

  // ---- pointing from the page ----

  // show where a device or an activity sits, until unpoint()
  point(kind, page, corner) {
    if (!this.model) return;
    if (!this.saved) this.saved = { mode: this.state.mode, page: this.state.page };
    const mode = kind === "device" ? this.model.devices_mode : this.model.menu_mode;
    if (mode == null) return;
    this.go({ mode, page });
    this.glow(`soft${corner}`, true);
  }

  unpoint() {
    for (const q of this.quads) q.classList.remove("lit");
    for (const b of this.keys.values()) b.classList.remove("glow");
    if (this.saved) {
      this.go(this.saved);
      this.saved = null;
    }
  }

  glow(key, on) {
    const b = this.keys.get(key);
    if (b) b.classList.toggle("glow", on);
    if (key.startsWith("soft")) this.quads[+key[4]].classList.toggle("lit", on);
  }
}
