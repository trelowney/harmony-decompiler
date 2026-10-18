"""Compose a Harmony 525 (arch 9) configuration: general operations.

Each operation takes a container (`.EZHex` bytes) and returns a new one. It works
on the decompiled, symbolised document and compiles it, so nothing is shifted by
hand, and it checks its own result before returning: the new structure reads
back the way it was asked for, nothing it did not touch changed, the pool reader
still accounts for every page, and the file round-trips.

Everything here was first done by hand on a real 525 on 2026-10-18 and is
written up in docs/FORMAT.md section 5af. The operations so far:

    add_device(raw, name, commands, keys, lcd)   a new device, with its own
        infrared group, mode and soft-key pages, placed in the next free corner
        of the shared Devices pages; a page is added when they are full
    nec_command(name, address, command, repeat)  infrared for one NEC command

What is not general yet, and refuses rather than guesses: a Devices screen
whose first pages differ between modes beyond page 1, more than six pages
anywhere (the page counter knows digits 1 to 6), and a device name or label
the remote's font cannot draw.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Sequence

import _paths  # noqa: F401
import analyze_525_ir as ir
import check_525_mode_pages as cmp
import class5_ir_encoder as enc
import hconfig
import render_525_screens as renderer
import verify_525_semantics as sem
from clone_525_device import (class5_source, fresh_region, mode_fields, opaque,
                              region_at, render_map, symbol_for_offset)
from h525_lcd_author import alphabet_map, encode_text

BASE = hconfig.CONFIG_BASE

# The 525's keys by name, as event tags (press = 0x80 | scan code). Named from
# what the Panasonic and Xbox device modes of the public sample send on them,
# FORMAT 5af; "Hash" is by elimination and has not been pressed.
KEYS = {
    "Up": 0xAD, "Down": 0xB1, "Left": 0xB5, "Right": 0xAC, "OK": 0xB4,
    "Exit": 0x9D, "Menu": 0x9C, "Guide": 0x95, "Info": 0x94,
    "VolumeUp": 0xB3, "VolumeDown": 0xB2, "Mute": 0xA5,
    "ChannelUp": 0xB9, "ChannelDown": 0xA9, "Prev": 0xA4,
    "Play": 0x84, "Pause": 0x8C, "Stop": 0x87, "Record": 0x8F,
    "Rewind": 0x8D, "FastForward": 0x8E, "SkipBack": 0x85, "SkipForward": 0x86,
    "Red": 0x9B, "Green": 0xA3, "Yellow": 0xA2, "Blue": 0xA1,
    "Star": 0x93, "Hash": 0x99,
    "0": 0x9A, "1": 0xAB, "2": 0x82, "3": 0x81, "4": 0x83, "5": 0x8A,
    "6": 0x89, "7": 0x8B, "8": 0x92, "9": 0x91,
}
# soft keys beside the screen, in reading order: top left, top right,
# bottom left, bottom right (FORMAT 5ae, both top-left and bottom-right pressed)
SOFT = (0xA7, 0xA6, 0x9F, 0x9E)
# font 4 glyph codes for the page counter, read off a six page device mode
COUNTER_DIGIT = {1: 0x37, 2: 0x1E, 3: 0x35, 4: 0x3E, 5: 0x40, 6: 0x36}
COUNTER_OF = bytes((3, 4))
HALF_WIDTH = 47              # a name or label gets half the 96 pixel screen
SYMBOL_LIMIT = enc.IR_SYMBOL_WORD_LIMIT


class ComposeError(ValueError):
    """The operation cannot be done on this file without guessing."""


# ---------------------------------------------------------------- infrared ---

@dataclass(frozen=True)
class Command:
    """One infrared command: duration words for the first, held and tail blocks."""
    name: str
    streams: tuple                  # (first, held or None, tail or None)
    period_ns: int = 26_315         # 38 kHz


# NEC timings as the X96's Logitech-learned records hold them; the 525 sent
# these and the receivers answered (FORMAT 5af).
_NEC_HEADER = (0x8000 | 8990, 4490)
_NEC_BIT = 0x8000 | 568
_NEC_ZERO, _NEC_ONE = 562, 1662
_NEC_FRAME_US = 67_500


def _spaces(us: int) -> tuple[int, ...]:
    out = []
    while us > 0x7FFF:
        out.append(0x7FFF)
        us -= 0x7FFF
    out.append(us)
    return tuple(out)


def nec_frame(address: int, command: int, address_high: int | None = None) -> tuple[int, ...]:
    """One NEC frame: header, 32 bits LSB first, stop mark. `address_high` is the
    second address byte (extended NEC); by default the address's complement."""
    second = (~address & 0xFF) if address_high is None else address_high
    words = list(_NEC_HEADER)
    for byte in (address, second, command, ~command & 0xFF):
        for i in range(8):
            words += [_NEC_BIT, _NEC_ONE if byte >> i & 1 else _NEC_ZERO]
    words.append(_NEC_BIT)
    return tuple(words)


def nec_command(name: str, address: int, command: int, *, repeat: bool = False,
                address_high: int | None = None, repeat_delay_ms: int = 450,
                gap_ms: int = 40) -> Command:
    """An NEC command. A one-shot one sends one frame after Logitech's 500 ms
    lead. A repeating one sends a frame after a 50 ms lead, waits
    `repeat_delay_ms` (so a normal press on the 525's keypad sends once), and
    repeats whole frames while held, which every NEC receiver here decodes."""
    frame = nec_frame(address, command, address_high)
    if repeat:
        first = _spaces(50_000) + frame + _spaces(repeat_delay_ms * 1000) + (1,)
        held = frame + _spaces(gap_ms * 1000) + (1,)
        return Command(name, (first, held, None))
    first = _spaces(500_000) + frame + _spaces(gap_ms * 1000) + (1,)
    return Command(name, (first, None, None))


def _encode(command: Command, base: int) -> tuple[bytes, int]:
    record = enc.encode_class5_record(period_ns=command.period_ns,
                                      pointer_streams=list(command.streams),
                                      base_address=base)
    return record.blob, record.record_address - base


# ------------------------------------------------------------ the document ---

class _Doc:
    """A decompiled container and what its sections say."""

    def __init__(self, raw: bytes):
        self.raw = raw
        self.blob = hconfig.split_container(raw)[2]
        self.S = sem.section_offsets(self.blob)
        self.doc = hconfig.symbolise(hconfig.decompile(raw, "input"))
        self.regions = self.doc["blob"]["regions"]
        self.at: dict[int, list[dict]] = {}
        for region in self.regions:
            self.at.setdefault(region["offset"], []).append(region)
        self.alphabet = alphabet_map()
        self.fonts = renderer.fonts(self.blob, self.S)
        self.actions = ir.action_addresses(self.blob, self.S)
        self.n_modes = ir.u24(self.blob, self.S[sem.MODE_SLOT])
        self.n_actions = len(self.actions)

    def ins(self, index: int):
        return sem.instructions(self.blob, self.actions[index])

    def mode(self, index: int) -> dict:
        return mode_fields(self.blob, self.S, index)

    def region(self, offset: int, kind: str) -> dict:
        hits = [r for r in self.at.get(offset, []) if r["kind"] == kind]
        if len(hits) != 1:
            raise ComposeError(f"expected one {kind} at 0x{offset:X}, found {len(hits)}")
        return hits[0]

    def index_of(self, region: dict) -> int:
        return next(i for i, r in enumerate(self.regions) if r is region)

    def text(self, value: str, font: int = 0, limit: int | None = HALF_WIDTH) -> bytes:
        try:
            codes = encode_text(value, self.alphabet)
        except ValueError as error:
            raise ComposeError(f"{value!r}: {error}") from None
        if limit is not None:
            codes = self._fit(codes, font, limit)
        return codes

    def width(self, codes: bytes, font: int = 0) -> int:
        try:
            return sum(len(self.fonts[font].glyph(c)[0]) for c in codes)
        except ValueError as error:
            # the container's alphabet spans its fonts; a font may lack a glyph
            raise ComposeError(f"font {font} cannot draw this text: {error}") from None

    def _fit(self, codes: bytes, font: int, limit: int) -> bytes:
        if self.width(codes, font) <= limit:
            return codes
        dots = encode_text("..", self.alphabet)
        while codes and self.width(codes + dots, font) > limit:
            codes = codes[:-1]
        return codes + dots

    def selection_target(self, list_index: int) -> int | None:
        """The device mode a Devices soft key enters, or None."""
        ins = self.ins(list_index)
        if len(ins) >= 2 and ins[0][0] == 0x7E and ins[1] == (0x92, 1):
            return ins[0][1]
        return None

    def devices_modes(self) -> list[int]:
        """The reachable Devices screens: what 0xAF enters in section 9 (5ae)."""
        out = []
        for k in range(self.blob[self.S[9]]):
            at = ir.u24(self.blob, self.S[9] + 1 + 3 * k) - BASE
            for tag, op, value in cmp.tagged(self.blob, at):
                if tag != 0xAF:
                    continue
                if op == 0x7E:
                    out.append(value)
                elif op == 0x7F:
                    out += [v for o, v in self.ins(value) if o == 0x7E]
        modes = sorted(set(out))
        for m in modes:
            first = self.mode(m)["pages"][0]["list"]
            if not any(op == 0x7F and self.selection_target(v) is not None
                       for _, op, v in cmp.tagged(self.blob, first)):
                raise ComposeError(f"mode {m} is entered by 0xAF but is no device list")
        if not modes:
            raise ComposeError("no Devices screen found")
        return modes

    def pool_copy(self, page_list_offset: int) -> dict:
        """The pool copy of a page list: same tags, same opcodes, and 0x7F
        operands that name lists doing the same thing (FORMAT 5ad)."""
        original = self.region(page_list_offset, "tagged_list")
        def shape(entries):
            return [(e["tag"], e["opcode"],
                     tuple(self.ins(e["operand"])) if e["opcode"] == 0x7F else e["operand"])
                    for e in entries]
        hits = [r for r in self.regions if r.get("role") == "page_binding_copy"
                and shape(r["entries"]) == shape(original["entries"])]
        if not hits:
            raise ComposeError(f"no pool copy of the page list at 0x{page_list_offset:X}")
        return hits[0]

    def band_ends(self, root: int) -> dict[int, int]:
        """Offset of each band's closing 0x17 in a screen program."""
        band, ends = -1, {}
        for item in sem.screen_program_path(self.blob, root):
            if item["opcode"] == 0x16:
                band = item["operands"][0]
            elif item["opcode"] == 0x17:
                ends[band] = item["offset"]
        if sorted(ends) != list(range(8)):
            raise ComposeError(f"screen program at 0x{root:X} is not eight bands")
        return ends

    def insert_before(self, offset: int, new: list[dict]) -> None:
        anchor = next(r for r in self.at[offset] if r["kind"] == "screen_instruction")
        i = self.index_of(anchor)
        self.regions[i:i] = new

    def compile(self) -> bytes:
        return hconfig.compile_config(self.doc)


def _bands(y: int, height: int = 11) -> range:
    return range(y // 8, (y + height) // 8 + 1)


def _draw(x: int, y: int, codes: bytes, rid: str) -> dict:
    return opaque(rid, bytes((5, x, y)) + codes + b"\0")


def _counter(d: _Doc, root: int) -> list[tuple[dict, int]]:
    """The counter draws already in a program: (glyph_string region, x)."""
    out = []
    for item in sem.screen_program_path(d.blob, root):
        if item["opcode"] == 5 and item["operands"][1] == 56:
            glyphs = [r for r in d.at.get(item["offset"] + 3, []) if r["kind"] == "glyph_string"]
            if glyphs:
                out.append((glyphs[0], item["operands"][0]))
    return out


def _set_counters(d: _Doc, programs: dict[int, tuple[int, int]], prefix: str) -> None:
    """Make each program show `cur OF total`, adding the draws where missing."""
    for root, (cur, total) in programs.items():
        if total > 6:
            raise ComposeError("the page counter knows digits 1 to 6")
        existing = _counter(d, root)
        if total == 1:
            if existing:
                raise ComposeError("removing a page counter is not written yet")
            continue
        if existing:
            for glyphs, x in existing:
                if x == 65:
                    glyphs["codes"] = [COUNTER_DIGIT[total]]
                elif x in (35, 38):
                    if glyphs["codes"] != [COUNTER_DIGIT[cur]]:
                        raise ComposeError(f"page counter at 0x{root:X} disagrees with its page")
            continue
        x = 38 if cur == 1 else 35
        d.insert_before(d.band_ends(root)[7], [
            opaque(f"{prefix}_{root:x}_font", b"\x10\x04"),
            _draw(x, 56, bytes((COUNTER_DIGIT[cur],)), f"{prefix}_{root:x}_cur"),
            _draw(47, 56, COUNTER_OF, f"{prefix}_{root:x}_of"),
            _draw(65, 56, bytes((COUNTER_DIGIT[total],)), f"{prefix}_{root:x}_total"),
        ])


def _name_lines(d: _Doc, name: str) -> list[bytes]:
    words = name.split(" ", 1)
    if len(words) == 2 and d.width(d.text(name, limit=None)) > HALF_WIDTH:
        return [d.text(words[0]), d.text(words[1])]
    return [d.text(name)]


def _corner_draws(d: _Doc, corner: int, name: str, rid: str) -> dict[int, list[dict]]:
    """Draws per band for a device name in a Devices corner (rows at y 13 and
    24, 35 and 46, as Logitech's Devices pages put them)."""
    lines = _name_lines(d, name)
    top = corner in (0, 1)
    right = corner in (1, 3)
    ys = (13, 24) if top else (35, 46)
    per_band: dict[int, list[dict]] = {}
    for n, (codes, y) in enumerate(zip(lines, ys)):
        x = 96 - d.width(codes) if right else 0
        for band in _bands(y):
            per_band.setdefault(band, []).append(_draw(x, y, codes, f"{rid}_l{n}_b{band}"))
    return per_band


def _blank_devices_page(d: _Doc, template_root: int, rid: str,
                        title: bytes | None = None) -> list[dict]:
    """A page program with the background of `template_root`, none of its
    strings but the title, and the title replaced by `title` when given."""
    out = []
    title_y = 0
    for seq, item in enumerate(sem.screen_program_path(d.blob, template_root)):
        op, ops = item["opcode"], item["operands"]
        name = f"{rid}_{seq:02d}"
        if op == 3:
            out.append(fresh_region("screen_picture", name, coordinates=list(ops[:6]),
                                    targets=[symbol_for_offset(d.doc, ir.u24(ops, 6) - BASE)]))
        elif op in (4, 5):
            if ops[1] != title_y:
                continue
            if title is not None:
                out.append(_draw((96 - d.width(title)) // 2, 0, title, name))
            elif op == 4:
                out.append(fresh_region("screen_reference", name, opcode="0x04", x=ops[0], y=ops[1],
                                        targets=[symbol_for_offset(d.doc, ir.u24(ops, 2) - BASE)]))
            else:
                out.append(opaque(name, bytes((op,)) + ops))
        elif op == 0x10 and bytes(ops) == b"\x03" and title is not None:
            # font 3 has its own glyph numbering; a new title is drawn in font 0,
            # as the August device pages were and the remote showed them
            out.append(opaque(name, b"\x10\x00"))
        else:
            out.append(opaque(name, bytes((op,)) + ops))
    return out


def _insert_in_program(program: list[dict], per_band: dict[int, list[dict]]) -> None:
    """Insert draws before each band's 0x17 in a program held as regions."""
    band = -1
    out = []
    for region in program:
        data = bytes.fromhex("".join(region.get("data", []))) if region["kind"] == "opaque" else b""
        if data[:1] == b"\x16":
            band = data[1]
        if data == b"\x17":
            out += per_band.get(band, [])
        out.append(region)
    program[:] = out


# --------------------------------------------------------------- add_device ---

@dataclass
class Placement:
    """Where add_device put things, for the caller and for its own checks."""
    group: int
    mode: int
    devices_page: int
    corner: int
    actions: dict = field(default_factory=dict)


def add_device(raw: bytes, name: str, commands: Sequence[Command],
               keys: dict, lcd: Sequence[Sequence], *, on_enter: str | None = None,
               ) -> tuple[bytes, Placement]:
    """Add a device.

    `commands`: the device's infrared commands, in order; their position is the
    command number inside the new group. `keys`: 525 key name (or tag) to
    command name. `lcd`: the device's soft-key pages, each four (label, command
    name) or None in reading order. `on_enter`: a command sent when the device
    is opened from Devices (the PC's input mode, FORMAT 5af); None sends none.
    """
    d = _Doc(raw)
    blob, S = d.blob, d.S
    by_name = {c.name: i for i, c in enumerate(commands)}
    if len(by_name) != len(commands):
        raise ComposeError("command names must be unique")
    for c in commands:
        for stream in c.streams:
            if stream is None:
                continue
            for chunk in enc._chunks(tuple(stream)):
                if len(chunk) > SYMBOL_LIMIT:
                    raise ComposeError(f"{c.name}: a {len(chunk)} word symbol, above {SYMBOL_LIMIT}")
    key_tags = {}
    for key, cname in keys.items():
        tag = KEYS[key] if isinstance(key, str) else key
        if cname not in by_name:
            raise ComposeError(f"key {key} names no command {cname!r}")
        key_tags[tag] = cname
    if not lcd or len(lcd) > 6 or any(len(page) != 4 for page in lcd):
        raise ComposeError("lcd is one to six pages of four soft keys")
    for page in lcd:
        for item in page:
            if item is not None and item[1] not in by_name:
                raise ComposeError(f"soft key {item[0]!r} names no command {item[1]!r}")

    groups = ir.ir_groups(blob, S)
    group = len(groups)
    if group > 15:
        raise ComposeError("a sixteenth infrared group would not fit a 0x7D operand byte")
    mode = d.n_modes
    devices = d.devices_modes()
    pages = {m: d.mode(m)["pages"] for m in devices}

    # where the name goes: the first free corner of the shared pages, else a new page
    shared = []
    for k in range(1, max(len(p) for p in pages.values())):
        addrs = {pages[m][k]["address"] if k < len(pages[m]) else None for m in devices}
        if len(addrs) != 1 or None in addrs:
            raise ComposeError("the Devices screens' later pages are not shared; not written yet")
        shared.append(pages[devices[0]][k])
    corner, target_page = None, None
    if shared:
        last = shared[-1]
        used = {t for t, _, _ in cmp.tagged(blob, last["list"])}
        free = [i for i, tag in enumerate(SOFT) if tag not in used]
        if free:
            corner, target_page = free[0], last
    new_page = target_page is None
    if new_page:
        corner = 0
    n_pages = len(pages[devices[0]]) + (1 if new_page else 0)
    if n_pages > 6:
        raise ComposeError("Devices would get a seventh page")

    # ---- infrared ----
    seeds = []
    for i, c in enumerate(commands):
        seed, delta = _encode(c, 0x060000)
        seeds.append((c, seed, delta, opaque(f"dev{group}_class5_{i:02d}", seed)))
    group_region = fresh_region("ir_group", f"dev{group}_ir_group",
                                targets=[{"to": r["id"], "delta": dl} for _, _, dl, r in seeds])

    # ---- actions: one per used command, then the selection ----
    used = sorted({*key_tags.values(), *(i[1] for p in lcd for i in p if i),
                   *([on_enter] if on_enter else [])}, key=lambda n: by_name[n])
    act = {n: d.n_actions + i for i, n in enumerate(used)}
    actions = [fresh_region("action_list", f"dev{group}_action_{act[n]}",
                            instructions=[{"operand": group << 8 | by_name[n], "opcode": "0x7D"},
                                          {"operand": group << 8 | 1, "opcode": "0x7C"}])
               for n in used]
    select_index = d.n_actions + len(used)
    select_ins = [{"operand": mode, "opcode": "0x7E"}, {"operand": 1, "opcode": "0x92"}]
    if on_enter:
        select_ins.append({"operand": act[on_enter], "opcode": "0x7F"})
    selection = fresh_region("action_list", f"dev{group}_action_{select_index}", instructions=select_ins)

    # ---- the device mode: physical keys from the widest existing device list ----
    device_modes = sorted({t for m in devices for p in pages[m]
                           for _, op, v in cmp.tagged(blob, p["list"])
                           if op == 0x7F and (t := d.selection_target(v)) is not None})
    template = max(device_modes, key=lambda m: len(cmp.tagged(blob, d.mode(m)["start"])))
    tpl_list = d.region(d.mode(template)["start"], "tagged_list")
    entries = []
    for e in copy.deepcopy(tpl_list["entries"]):
        cname = key_tags.get(e["tag"])
        e["operand"], e["opcode"] = (act[cname], 0x7F) if cname else (0, 0)
        entries.append(e)
    missing = set(key_tags) - {e["tag"] for e in entries}
    for tag in sorted(missing):
        entries.append({"tag": tag, "operand": act[key_tags[tag]], "opcode": 0x7F})
    physical = fresh_region("tagged_list", f"dev{group}_mode_list", wide=tpl_list.get("wide", False),
                            entries=entries)

    # ---- the device's own pages, from a blanked device page ----
    donor = d.mode(template)["pages"][0]["program"]
    title = d.text(name, limit=96)
    mode_pages, mode_programs, page_lists, copies = [], [], [], []
    for k, page in enumerate(lcd):
        program = _blank_devices_page(d, donor, f"dev{group}_p{k}_screen", title=title)
        per_band: dict[int, list[dict]] = {}
        for c, item in enumerate(page):
            if item is None:
                continue
            codes = d.text(item[0])
            y = 16 if c < 2 else 35
            x = 0 if c in (0, 2) else 96 - d.width(codes)
            for band in _bands(y):
                per_band.setdefault(band, []).append(_draw(x, y, codes, f"dev{group}_p{k}_k{c}_b{band}"))
        if len(lcd) > 1:
            per_band.setdefault(7, []).extend([
                opaque(f"dev{group}_p{k}_cf", b"\x10\x04"),
                _draw(38 if k == 0 else 35, 56, bytes((COUNTER_DIGIT[k + 1],)), f"dev{group}_p{k}_cc"),
                _draw(47, 56, COUNTER_OF, f"dev{group}_p{k}_co"),
                _draw(65, 56, bytes((COUNTER_DIGIT[len(lcd)],)), f"dev{group}_p{k}_ct")])
        _insert_in_program(program, per_band)
        page_entries = [{"tag": SOFT[c], "operand": act[item[1]], "opcode": 0x7F}
                        for c, item in enumerate(page) if item is not None]
        plist = fresh_region("tagged_list", f"dev{group}_p{k}_list", section=8,
                             wide=not page_entries, entries=page_entries)
        record = fresh_region("mode_page", f"dev{group}_p{k}",
                              targets=[{"to": plist["id"]}, {"to": program[0]["id"]}])
        mode_programs += [*program, record]
        page_lists.append(plist)
        copies.append(fresh_region("tagged_list", f"dev{group}_p{k}_copy", wide=not page_entries,
                                   role="page_binding_copy", entries=copy.deepcopy(page_entries)))
        mode_pages.append(record)
    entry = fresh_region("record_header", f"dev{group}_mode_entry",
                         back_reference={"to": physical["id"]},
                         targets=[{"to": p["id"]} for p in mode_pages])

    # ---- the Devices corner ----
    select_entry = {"tag": SOFT[corner], "operand": select_index, "opcode": 0x7F}
    draws = _corner_draws(d, corner, name, f"dev{group}_on_devices")
    new_devices_regions = []
    if new_page:
        template_root = pages[devices[0]][0]["program"]
        program = _blank_devices_page(d, template_root, f"dev{group}_devices_screen")
        _insert_in_program(program, draws)
        dlist = fresh_region("tagged_list", f"dev{group}_devices_list", section=8, wide=False,
                             entries=[select_entry])
        drecord = fresh_region("mode_page", f"dev{group}_devices_page",
                               targets=[{"to": dlist["id"]}, {"to": program[0]["id"]}])
        new_devices_regions = [*program, drecord]
        page_lists.append(dlist)
        for m in devices:
            d.region(d.mode(m)["entry"], "record_header")["targets"].append({"to": drecord["id"]})
        # its pool copy after the last page copy of the first Devices mode
        last_list = pages[devices[0]][-1]["list"]
        after = d.pool_copy(last_list)
        d.regions.insert(d.index_of(after) + 1,
                         fresh_region("tagged_list", f"dev{group}_devices_copy", wide=False,
                                      role="page_binding_copy", entries=[dict(select_entry)]))
    else:
        twin = d.pool_copy(target_page["list"])
        d.region(target_page["list"], "tagged_list")["entries"].append(dict(select_entry))
        twin["entries"].append(dict(select_entry))
        ends = d.band_ends(target_page["program"])
        for band in sorted(draws, reverse=True):
            d.insert_before(ends[band], draws[band])

    # ---- counters on every Devices page ----
    counters = {}
    for m in devices:
        for k, p in enumerate(pages[m]):
            counters[p["program"]] = (k + 1, n_pages)
    _set_counters(d, counters, f"dev{group}_counter")
    if new_page:
        body = new_devices_regions[:-1]
        _insert_in_program(body, {7: [
            opaque(f"dev{group}_newpage_cf", b"\x10\x04"),
            _draw(35, 56, bytes((COUNTER_DIGIT[n_pages],)), f"dev{group}_newpage_cc"),
            _draw(47, 56, COUNTER_OF, f"dev{group}_newpage_co"),
            _draw(65, 56, bytes((COUNTER_DIGIT[n_pages],)), f"dev{group}_newpage_ct")]})
        new_devices_regions[:-1] = body

    # ---- tables and placement ----
    d.region(S[5], "pointer_table")["targets"].append({"to": group_region["id"]})
    d.region(S[sem.MODE_SLOT], "pointer_table")["targets"].append({"to": entry["id"]})
    d.region(S[sem.ACTION_SLOT], "pointer_table")["targets"].extend(
        {"to": a["id"]} for a in [*actions, selection])
    s9_first = ir.u24(blob, S[9] + 1 + 3 * 4) - BASE
    i4 = d.index_of(d.region(s9_first, "tagged_list"))
    d.regions[i4:i4] = copies
    s9 = next(i for i, r in enumerate(d.regions) if r.get("section") == 9)
    d.regions[s9:s9] = page_lists
    s0 = next(i for i, r in enumerate(d.regions) if r.get("section") == 0)
    d.regions[s0:s0] = [*(r for *_, r in seeds), *actions, selection, group_region, physical,
                        *mode_programs, entry, *new_devices_regions]

    resolve, _ = hconfig._resolver(d.regions)
    for c, seed, delta, region in seeds:
        base = BASE + resolve({"to": region["id"]})
        data, delta2 = _encode(c, base)
        if len(data) != len(seed) or delta2 != delta:
            raise AssertionError("record changed length on relocation")
        region["data"] = hconfig._hex_lines(data)

    out = d.compile()
    placement = Placement(group=group, mode=mode, devices_page=n_pages - 1, corner=corner,
                          actions=act)
    _check_add_device(raw, out, name, commands, key_tags, lcd, placement, devices, on_enter)
    return out, placement


def _check_add_device(raw, out, name, commands, key_tags, lcd, placement, devices, on_enter):
    before, after = _Doc(raw), _Doc(out)
    b, a = before.blob, after.blob
    g0, g1 = ir.ir_groups(b, before.S), ir.ir_groups(a, after.S)
    if [len(g) for g in g1] != [len(g) for g in g0] + [len(commands)]:
        raise AssertionError("group population")
    if [[class5_source(b, x) for x in g] for g in g0] != [[class5_source(a, x) for x in g] for g in g1[:-1]]:
        raise AssertionError("an existing infrared record changed")
    for c, address in zip(commands, g1[-1]):
        decoded = enc.decode_class5_record(bytes(a), base_address=BASE, record_address=address)
        if decoded.pointer_streams != tuple(None if s is None else tuple(s) for s in c.streams):
            raise AssertionError(f"{c.name} does not decode to what was asked")
    if not all(before.ins(i) == after.ins(i) for i in range(before.n_actions)):
        raise AssertionError("an existing action list changed")
    by_name = {c.name: i for i, c in enumerate(commands)}
    binds = {}
    for item in ir.mode_bindings(a, after.S, placement.mode):
        binds.setdefault(item["source"], {})[item["tag"]] = item
    for tag, cname in key_tags.items():
        item = binds.get("physical", {}).get(tag)
        if not item or item["group"] != placement.group or item["command"] != by_name[cname]:
            raise AssertionError(f"key 0x{tag:X} does not send {cname}")
    mode_pages = after.mode(placement.mode)["pages"]
    if len(mode_pages) != len(lcd):
        raise AssertionError("device page count")
    for page, record in zip(lcd, mode_pages):
        got = {t: v for t, op, v in cmp.tagged(a, record["list"])}
        for c, item in enumerate(page):
            if item is not None and after.ins(got[SOFT[c]])[0] != (0x7D, placement.group << 8 | by_name[item[1]]):
                raise AssertionError(f"soft key {item[0]!r}")
    for m in devices:
        pages = after.mode(m)["pages"]
        if len(pages) != placement.devices_page + 1:
            raise AssertionError(f"Devices mode {m} has {len(pages)} pages")
        got = {t: v for t, op, v in cmp.tagged(a, pages[-1]["list"])}
        sel = got.get(SOFT[placement.corner])
        if sel is None or after.selection_target(sel) != placement.mode:
            raise AssertionError(f"Devices mode {m} does not open the new device")
        if on_enter and after.ins(sel)[2:] != [(0x7F, placement.actions[on_enter])]:
            raise AssertionError("on_enter")
    r0, r1 = render_map(b), render_map(a)
    devices_keys = {(m, k) for m in devices for k in range(placement.devices_page + 1)}
    changed = [k for k in r0 if r0[k][0] != r1[k][0] and k not in devices_keys]
    if changed:
        raise AssertionError(f"screens changed that should not have: {changed}")
    pointers = [None if p is None else p + BASE for p in cmp.section_offsets(a)]
    pooled = hconfig.arch9_record_body_regions(a, pointers)
    page_regions = hconfig.arch9_mode_page_regions(a, pointers)
    copies = [r for r in pooled if r.get("role") == "page_binding_copy"]
    if not pooled or len(copies) != len(page_regions):
        raise AssertionError(f"the pool reader: {len(copies)} copies for {len(page_regions)} pages")
    if hconfig.compile_config(hconfig.decompile(out, "check")) != out:
        raise AssertionError("round trip")
    if int.from_bytes(a[-6:-4], "little") != hconfig.trailer_checksum(a):
        raise AssertionError("trailer checksum")
