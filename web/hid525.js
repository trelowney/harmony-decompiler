// Harmony 525 over WebHID. Read only, and enforced here rather than promised.
//
// The protocol is @dannybloe's reading in harmony-explorations, packages/usb (MIT,
// Copyright (c) Danny Bloemendaal): 64 byte reports, the command in the high
// nibble of the first byte and the payload length in the low one, READ_FLASH
// answered by 0x6A chunks of 62 data bytes behind a sequence byte that steps by
// 0x11, then 0xF0 0x50. This file is a small re-implementation of the read path
// for the browser, not a copy of his code.
//
// Safety: outside a write, the only reports this file can send are GET_VERSION
// (0x10, no payload) and READ_FLASH (0x55). writeConfig() opens a write and,
// for its length only, allows exactly what Concordance sends to update a 525's
// configuration (libconcord _update_configuration_hid, update_configuration):
// invalidate (0xA1 0x02), erase of 64 KiB sectors from 0x820000 up (0xD3), write
// into 0x820000..0x880000 (0x35, data 0x41..0x4A, done 0xF1 0x30), reset (0xE1
// 0x02) and the clock (0xA5 0x01). Every report is checked in send(), the one
// place a report leaves the page: the firmware's own block at 0x810000 cannot be
// erased or written from here, and nothing else can be sent at all.

export const VENDOR_ID = 0x046d;
export const PRODUCT_ID_525 = 0xc111;
export const CONFIG_FLASH_BASE = 0x820000;   // arch 9 (Harmony 525), Danny's rails.ts
export const CONFIG_BASE = 0x020000;         // the address the blob's own pointers use
const REPORT_SIZE = 64;

export const CONFIG_FLASH_END = 0x880000;    // 25F040: 512 KiB from 0x800000
const SECTOR = 0x10000;

function readOnly(report) {
  const first = report[0];
  return first === 0x10 || first === 0x55;
}

// A report a configuration update may send, given what the write is doing.
function updateReport(report, session) {
  const r = report;
  const addr = (r[1] << 16) | (r[2] << 8) | r[3];
  switch (r[0]) {
    case 0xa1: return r[1] === 0x02;                                  // invalidate
    case 0xd3: return addr >= CONFIG_FLASH_BASE && addr < CONFIG_FLASH_END && addr % SECTOR === 0;
    case 0x35: {                                                      // write setup
      const len = (r[4] << 8) | r[5];
      const ok = addr >= CONFIG_FLASH_BASE && addr + len <= CONFIG_FLASH_END && len > 0;
      if (ok) session.open = true;
      return ok;
    }
    case 0xf1: {                                                      // end of a write chunk
      const ok = session.open && r[1] === 0x30;
      session.open = false;
      return ok;
    }
    case 0xe1: return r[1] === 0x01 || r[1] === 0x02;                 // reset USB / the device
    case 0xa5: return r[1] === 0x01 && r[2] === 0x00 && r[3] <= 6;   // clock, STATE words 0..6
    default:
      return session.open && r[0] >= 0x41 && r[0] <= 0x4a;            // write data
  }
}

export class Harmony525 {
  static supported() {
    return typeof navigator !== "undefined" && "hid" in navigator;
  }

  static async connect() {
    // a remote this page was already allowed needs no prompt
    const known = (await navigator.hid.getDevices()).filter(
      (d) => d.vendorId === VENDOR_ID && d.productId === PRODUCT_ID_525);
    const devices = known.length ? known : await navigator.hid.requestDevice({
      filters: [{ vendorId: VENDOR_ID, productId: PRODUCT_ID_525 }],
    });
    if (!devices.length) throw new Error("no remote was chosen");
    const device = devices[0];
    if (!device.opened) await device.open();
    return new Harmony525(device);
  }

  constructor(device) {
    this.device = device;
    this.queue = [];
    this.waiting = null;
    this.onReport = (event) => {
      const data = new Uint8Array(event.data.buffer, event.data.byteOffset, event.data.byteLength);
      const report = new Uint8Array(REPORT_SIZE);
      report.set(data.subarray(0, REPORT_SIZE));
      if (this.waiting) {
        const resolve = this.waiting;
        this.waiting = null;
        resolve(report);
      } else {
        this.queue.push(report);
      }
    };
    device.addEventListener("inputreport", this.onReport);
  }

  async close() {
    this.device.removeEventListener("inputreport", this.onReport);
    if (this.device.opened) await this.device.close();
  }

  async send(bytes) {
    const report = new Uint8Array(REPORT_SIZE);
    report.set(bytes);
    const ok = readOnly(report) || (this.session && updateReport(report, this.session));
    if (!ok) {
      throw new Error(`refusing to send 0x${report[0].toString(16)}` +
        (this.session ? ": not part of a configuration update" : ": this page only reads"));
    }
    await this.device.sendReport(0, report);
  }

  async expectDone(command, timeoutMs = 1000) {
    const report = await this.read(timeoutMs);
    if (report === undefined) throw new Error(`no answer to 0x${command.toString(16)}`);
    if ((report[0] & 0xf0) !== 0xf0 || (report[1] & 0xf0) !== (command & 0xf0)) {
      throw new Error(`0x${command.toString(16)} answered 0x${report[0].toString(16)} 0x${report[1].toString(16)}`);
    }
  }

  read(timeoutMs = 500) {
    if (this.queue.length) return Promise.resolve(this.queue.shift());
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        this.waiting = null;
        resolve(undefined);
      }, timeoutMs);
      this.waiting = (report) => {
        clearTimeout(timer);
        resolve(report);
      };
    });
  }

  // Whatever an earlier session left in the pipe, before asking anything.
  async drain() {
    for (let quiet = 0; quiet < 2; ) {
      const report = await this.read(150);
      if (report === undefined) quiet += 1;
      else quiet = 0;
    }
  }

  // GET_VERSION. On arch 9 the reply is 0x27 and seven fields: firmware,
  // hardware, flash device and manufacturer, architecture << 4 | software
  // type, skin, platform (Danny's protocol.ts, encodeVersionBlock).
  async version() {
    await this.send([0x10]);
    for (let i = 0; i < 5; i += 1) {
      const report = await this.read(1000);
      if (report === undefined) break;
      if ((report[0] & 0xf0) === 0x20) {
        const count = report[0] & 0x0f;
        const f = report.subarray(1, 1 + (count >= 8 ? 12 : count));
        return {
          raw: Array.from(f),
          firmware: `${f[0] >> 4}.${f[0] & 0x0f}`,
          hardware: f[1],
          flash: `0x${f[3].toString(16).toUpperCase()}:0x${f[2].toString(16).toUpperCase()}`,
          architecture: f[4] >> 4,
          softwareType: f[4] & 0x0f,
          skin: f[5],
        };
      }
    }
    throw new Error("the remote did not answer GET_VERSION");
  }

  // READ_FLASH: 24 bit address and 16 bit count, most significant byte first.
  async readFlash(address, count) {
    if (count <= 0 || count > 0xffff || count % 2) throw new Error(`bad read count ${count}`);
    if (address >> 16 < 0x80 || address >> 16 > 0x87) {
      throw new Error(`0x${address.toString(16)} is not external flash; only the config is read`);
    }
    await this.send([0x55, (address >> 16) & 0xff, (address >> 8) & 0xff, address & 0xff,
                     (count >> 8) & 0xff, count & 0xff]);
    const out = new Uint8Array(count);
    let filled = 0;
    let sequence = null;
    let quiet = 0;
    while (quiet < 6) {
      const report = await this.read(500);
      if (report === undefined) {
        quiet += 1;
        continue;
      }
      quiet = 0;
      const code = report[0] & 0xf0;
      if (code === 0xf0 && report[1] === 0x50) {
        if (filled !== count) throw new Error(`flash read returned ${filled} of ${count} bytes`);
        return out;
      }
      if (code !== 0x60) {
        await this.drain();
        throw new Error(`a flash read answered with 0x${report[0].toString(16)}`);
      }
      const nibble = report[0] & 0x0f;
      const length = nibble === 0x8 ? 15 : nibble === 0x9 ? 31 : nibble === 0xa ? 63 : nibble;
      const expected = sequence === null ? 0x01 : (sequence + 0x11) & 0xff;
      if (report[1] !== expected) {
        await this.drain();
        throw new Error(`flash chunk out of sequence after ${filled} bytes`);
      }
      sequence = report[1];
      const data = report.subarray(2, 1 + length);
      if (data.length > count - filled) {
        await this.drain();
        throw new Error("a flash chunk carries more than was asked for");
      }
      out.set(data, filled);
      filled += data.length;
    }
    throw new Error(`the remote went quiet after ${filled} of ${count} bytes`);
  }

  // A configuration update as libconcord does it on arch 9: invalidate, erase
  // the sectors the new config covers, write it in 3150 byte chunks of up to 63
  // byte data reports, read it all back and compare, reset. This object is dead
  // after the reset; the clock is set on the reconnected remote (setClock).
  async writeConfig(blob, progress = () => {}) {
    blob = new Uint8Array(blob);
    const end = CONFIG_FLASH_BASE + blob.length;
    const magic = String.fromCharCode(...blob.subarray(0, 4));
    if (magic !== "AHCM") throw new Error("this is not an arch 9 configuration");
    if (end > CONFIG_FLASH_END) throw new Error("the configuration does not fit the flash");
    const identity = await this.version();
    if (identity.architecture !== 9) throw new Error("this is not a Harmony 525");

    this.session = { open: false };
    try {
      progress("invalidating the old configuration", 0, 1);
      await this.send([0xa1, 0x02]);
      await this.expectDone(0xa1);

      const sectors = [];
      for (let s = CONFIG_FLASH_BASE; s < end; s += SECTOR) sectors.push(s);
      for (const [i, s] of sectors.entries()) {
        progress("erasing", i, sectors.length);
        await this.send([0xd3, (s >> 16) & 0xff, (s >> 8) & 0xff, s & 0xff]);
        if ((await this.read(5000)) === undefined) throw new Error(`no answer to erasing 0x${s.toString(16)}`);
      }

      const sizes = [[0x0a, 63], [0x09, 31], [0x08, 15], [0x07, 7], [0x06, 6], [0x05, 5], [0x04, 4],
                     [0x03, 3], [0x02, 2], [0x01, 1]];
      for (let at = 0; at < blob.length; ) {
        const chunk = Math.min(3150, blob.length - at);
        const addr = CONFIG_FLASH_BASE + at;
        await this.send([0x35, (addr >> 16) & 0xff, (addr >> 8) & 0xff, addr & 0xff,
                         (chunk >> 8) & 0xff, chunk & 0xff]);
        let left = chunk;
        while (left) {
          const [nibble, size] = sizes.find(([, n]) => n <= left);
          await this.send([0x40 | nibble, ...blob.subarray(at, at + size)]);
          at += size;
          left -= size;
        }
        await this.send([0xf1, 0x30]);
        if ((await this.read(5000)) === undefined) throw new Error(`no answer after writing at 0x${addr.toString(16)}`);
        progress("writing", at, blob.length);
      }

      for (let at = 0; at < blob.length; at += 0x1000) {
        const n = Math.min(0x1000, blob.length - at);
        const back = await this.readFlash(CONFIG_FLASH_BASE + at, n + (n % 2));
        for (let i = 0; i < n; i += 1) {
          if (back[i] !== blob[at + i]) throw new Error(`verify failed at byte ${at + i}`);
        }
        progress("verifying", at + n, blob.length);
      }

      progress("restarting the remote", 0, 1);
      await this.send([0xe1, 0x02]);
    } finally {
      this.session = null;
    }
    try { await this.close(); } catch { /* it has already left the bus */ }
    // The remote has left the bus. Chrome forgets a device without a USB serial
    // number when it goes, so getting it back needs the user's click and a new
    // prompt: the caller connects again and calls setClock().
  }

  // libconcord's SetTime for arch 8 and up: seven STATE words, then the seconds.
  async setClock(date = new Date()) {
    this.session = { open: false };
    try {
      // libconcord's init sends "reset USB" before anything else ("otherwise,
      // the first communication attempt fails"), then asks for the identity;
      // straight after a reboot the 525 did not answer a clock write without it
      await this.send([0xe1, 0x01]);
      await new Promise((r) => setTimeout(r, 300));
      await this.drain();
      await this.version();
      const t = date;
      const words = [0, t.getMinutes(), t.getHours(), t.getDate() - 1, t.getDay(), t.getMonth(),
                     t.getFullYear() - 2000];
      for (const [i, w] of words.entries()) {
        await this.send([0xa5, 0x01, 0x00, i, (w >> 8) & 0xff, w & 0xff]);
        await this.expectDone(0xa5);
      }
      await this.send([0xa5, 0x01, 0x00, 0x00, 0x00, t.getSeconds()]);
      await this.expectDone(0xa5);
      await this.read(300);
    } finally {
      this.session = null;
    }
  }

  // The configuration blob: its header says where it ends.
  async readConfig(progress = () => {}) {
    const head = await this.readFlash(CONFIG_FLASH_BASE, 16);
    const magic = String.fromCharCode(...head.subarray(0, 4));
    if (magic !== "AHCM") throw new Error(`no arch 9 configuration at 0x820000 (found ${magic})`);
    const end = head[4] | (head[5] << 8) | (head[6] << 16) | (head[7] << 24);
    const length = end - CONFIG_BASE + 4;
    if (length < 0x100 || length > 0x5ffff) throw new Error(`implausible config length ${length}`);
    const blob = new Uint8Array(length);
    const step = 0x1000;
    for (let at = 0; at < length; at += step) {
      let n = Math.min(step, length - at);
      const odd = n % 2;
      const chunk = await this.readFlash(CONFIG_FLASH_BASE + at, n + odd);
      blob.set(chunk.subarray(0, n), at);
      progress(Math.min(at + n, length), length);
    }
    return blob;
  }
}
