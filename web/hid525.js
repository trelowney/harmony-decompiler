// Harmony 525 over WebHID. Read only, and enforced here rather than promised.
//
// The protocol is @dannybloe's reading in harmony-explorations, packages/usb (MIT,
// Copyright (c) Danny Bloemendaal): 64 byte reports, the command in the high
// nibble of the first byte and the payload length in the low one, READ_FLASH
// answered by 0x6A chunks of 62 data bytes behind a sequence byte that steps by
// 0x11, then 0xF0 0x50. This file is a small re-implementation of the read path
// for the browser, not a copy of his code.
//
// Safety: the only reports this file can send are GET_VERSION (0x10, no
// payload) and READ_FLASH (0x55, five payload bytes). Anything else is refused
// in send(), the one place a report leaves the page. Writing, erasing, the
// settings store, infrared learning and RAM reads are not reachable from here.

export const VENDOR_ID = 0x046d;
export const PRODUCT_ID_525 = 0xc111;
export const CONFIG_FLASH_BASE = 0x820000;   // arch 9 (Harmony 525), Danny's rails.ts
export const CONFIG_BASE = 0x020000;         // the address the blob's own pointers use
const REPORT_SIZE = 64;

function allowed(report) {
  const first = report[0];
  return first === 0x10 || first === 0x55;
}

export class Harmony525 {
  static supported() {
    return typeof navigator !== "undefined" && "hid" in navigator;
  }

  static async connect() {
    const devices = await navigator.hid.requestDevice({
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
    if (!allowed(report)) {
      throw new Error(`refusing to send 0x${report[0].toString(16)}: this page only reads`);
    }
    await this.device.sendReport(0, report);
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
