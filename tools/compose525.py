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
import functools
import marshal
import re
import unicodedata
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
from h525_lcd_author import alphabet_map, encode_text, install_character_glyph

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
# a device mode's own key list, in the order every device on the 525's and the
# 515's lists them: the 39 keys and the four soft keys, 0 where unused
DEVICE_KEY_ORDER = (0x89, 0x8B, 0x8A, 0x8D, 0x8C, 0x8F, 0x8E, 0x81, 0x83, 0x82, 0x85, 0x84, 0x87, 0x86,
                    0x99, 0x9A, 0x9B, 0x9C, 0x9D, 0x9E, 0x9F, 0x91, 0x92, 0x93, 0x94, 0x95, 0xAB, 0xA9,
                    0xAD, 0xAC, 0xA3, 0xA2, 0xA1, 0xA7, 0xA6, 0xA5, 0xA4, 0xB9, 0xB2, 0xB3, 0xB1, 0xB4,
                    0xB5)
# soft keys beside the screen, in reading order: top left, top right,
# bottom left, bottom right (FORMAT 5ae, both top-left and bottom-right pressed)
SOFT = (0xA7, 0xA6, 0x9F, 0x9E)
# font 4 glyph codes for the page counter, read off a six page device mode
# "n OF m" under a paged screen, in font set 4. The codes are the
# configuration's own: 0x37 is "1" on the 525's and ")" on a 515's.
COUNTER_FONT = 4


def _digit(d, n: int) -> int:
    return _counter_codes(d, str(n))[0]


def _of(d) -> bytes:
    return _counter_codes(d, "OF")


def _counter_codes(d, text: str) -> bytes:
    if len(d.fonts) <= COUNTER_FONT:
        raise NoCounterFont("this configuration has no page counter font")
    try:
        codes = encode_text(text, d.alphabet)
    except ValueError:
        raise NoCounterFont(f"the page counter's font has no {text!r} here") from None
    for code in codes:
        try:
            d.fonts[COUNTER_FONT].glyph(code)
        except ValueError:
            raise NoCounterFont(f"the page counter's font has no {text!r} here") from None
    return codes


# what a page counter draws: "n OF m" for one to six pages
COUNTER_TEXT = "123456OF"


def counter_font(raw: bytes) -> bytes:
    """Give a configuration the page counter's font set 4 with the digits 1
    to 6 and "OF", in the 525's 8 row counter typeface ("525-4"). An empty
    555 has only fonts 0 to 3, since nothing in it was ever paged; the 525
    and 515 have set 4 already, and a set 4 that lacks a character gets it."""
    d = _Doc(raw)
    glyphs = _font_glyphs().get("525-4", {})
    if any(ch not in glyphs for ch in COUNTER_TEXT):
        raise ComposeError("arch9_font_glyphs.json has no page counter typeface")
    if len(d.fonts) < COUNTER_FONT:
        raise ComposeError(f"{len(d.fonts)} font sets; the page counter's is number {COUNTER_FONT}")
    if len(d.fonts) == COUNTER_FONT:
        table = d.region(d.S[7], "pointer_table")
        sets = [r for r in d.regions if r["kind"] == "font_set"]
        new = {"kind": "font_set", "offset": -1, "length": 0, "height": len(glyphs["1"]),
               "first": sets[-1]["first"], "targets": [None] * len(sets[-1]["targets"]),
               "section": sets[-1].get("section"), "id": "counter_font_set"}
        d.regions.insert(d.index_of(sets[-1]) + 1, new)
        table["targets"].append({"to": new["id"]})
        d = _Doc(d.compile())
    d._ensure_glyphs(COUNTER_TEXT, COUNTER_FONT, glyphs)
    out = d.compile()

    # ---- checks ----
    after = _Doc(out)
    if len(after.fonts) != COUNTER_FONT + 1 or after.fonts[COUNTER_FONT].height != len(glyphs["1"]):
        raise AssertionError("the page counter's font set did not read back")
    for ch in COUNTER_TEXT:
        code = next(k for k, v in after.alphabet.items() if v == ch)
        rows = ["".join("#" if v == renderer.INK else "." for v in row)
                for row in after.fonts[COUNTER_FONT].glyph(code)]
        if rows != glyphs[ch]:
            raise AssertionError(f"the counter's {ch!r} reads back otherwise")
    r0, r1 = render_map(_Doc(raw).blob), render_map(after.blob)
    if {k: v[0] for k, v in r0.items()} != {k: v[0] for k, v in r1.items()}:
        raise AssertionError("a screen changed with the page counter's font added")
    _check_container(out)
    return out


def _needs_counter_font(op):
    """An operation that may draw a page counter: on a configuration with no
    counter font (an empty 555), add it (counter_font) and do it again."""
    @functools.wraps(op)
    def run(raw, *args, **kwargs):
        try:
            return op(raw, *args, **kwargs)
        except NoCounterFont:
            return op(counter_font(raw), *args, **kwargs)
    return run


HALF_WIDTH = 47              # a name or label gets half the 96 pixel screen
SYMBOL_LIMIT = enc.IR_SYMBOL_WORD_LIMIT


class ComposeError(ValueError):
    """The operation cannot be done on this file without guessing."""


class NoCounterFont(ComposeError):
    """A page counter is needed and the configuration has no font for it."""


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

# ------------------------------------------------------------- the alphabet ---
# A glyph code is assigned per configuration, in the order the generator met
# each character, so the 525's codes mean nothing in a 515's file. A skin's
# typeface is the same in every configuration of it, so a code is read by its
# pixels (@dannybloe's method, his section 112): tools/arch9_glyphs.json, built
# by build_arch9_glyphs.py from one configuration per typeface read by eye.

_GLYPHS: dict | None = None


def shape_key(shape) -> str:
    """A glyph's (height, rows of pixels) as a short stable key."""
    import hashlib
    height, rows = shape
    text = f"{height}:" + "/".join("".join("#" if v == renderer.INK else "." for v in row) for row in rows)
    return hashlib.sha1(text.encode()).hexdigest()[:16]


def _glyph_table() -> dict:
    global _GLYPHS
    if _GLYPHS is None:
        import json
        from pathlib import Path
        _GLYPHS = json.loads((Path(__file__).resolve().parent / "arch9_glyphs.json").read_text(encoding="utf-8"))
        # and the glyphs this module adds, so what it draws reads back
        import hashlib
        def learn(ch, rows):
            key = hashlib.sha1((f"{len(rows)}:" + "/".join(rows)).encode()).hexdigest()[:16]
            _GLYPHS[key] = "".join(sorted(set(_GLYPHS.get(key, "")) | {ch}))
        for glyphs in EXTRA_GLYPHS.values():
            for ch, rows in glyphs.items():
                learn(ch, rows)
        # and the letters with marks it draws from each typeface's own (_with_mark)
        for style, own in _font_glyphs().items():
            glyphs = {**own, **EXTRA_GLYPHS.get(style, {})}
            small = glyphs.get("n") or glyphs.get("a")
            x_top = next(r for r, row in enumerate(small) if "#" in row) if small else 4
            for ch in ACCENTED:
                base, mark = unicodedata.normalize("NFD", ch)
                rows = _with_mark(glyphs[base], MARKS[mark], base, x_top) if base in glyphs else None
                if rows:
                    learn(ch, rows)
    return _GLYPHS


def _glyph_shapes(blob: bytes, fonts) -> dict[int, set]:
    """Every code's pixels, (height, rows), in each font set that draws it."""
    out: dict[int, set] = {}
    for font in fonts:
        for code in range(font.first, font.first + font.count):
            try:
                rows = font.glyph(code)
            except ValueError:
                continue
            if any(v == renderer.INK for row in rows for v in row):
                out.setdefault(code, set()).add((font.height, tuple(map(tuple, rows))))
    return out


def _blank_codes(fonts) -> set[int]:
    out = set()
    for font in fonts:
        for code in range(font.first, font.first + font.count):
            try:
                rows = font.glyph(code)
            except ValueError:
                continue
            if not any(v == renderer.INK for row in rows for v in row):
                out.add(code)
    return out


def config_alphabet(blob: bytes, fonts) -> dict[int, str]:
    """This configuration's codes, by the characters their pixels draw. A shape
    two characters share (l and I on the 525) is settled by the rule that one
    code is one character; a code no known shape explains is left out and
    reads as '?'. The space draws nothing: it is the code that is drawn blank."""
    table = _glyph_table()
    out: dict[int, str] = {}
    open_: dict[int, set] = {}
    for code, shapes in _glyph_shapes(blob, fonts).items():
        candidates = None
        for shape in shapes:
            chars = set(table.get(shape_key(shape), ""))
            if chars:
                candidates = chars if candidates is None else candidates & chars
        if candidates and len(candidates) == 1:
            out[code] = next(iter(candidates))
        elif candidates:
            open_[code] = candidates
    changed = True
    while changed and open_:
        changed = False
        for code, chars in list(open_.items()):
            left = chars - set(out.values())
            if len(left) == 1:
                out[code] = left.pop()
                del open_[code]
                changed = True
    blank = _blank_codes(fonts) - set(out)
    if " " not in out.values() and len(blank) == 1:
        out[blank.pop()] = " "
    return out


# Characters a configuration may not carry. Logitech's compiler put in a font
# only the characters its own texts used: the 525's font 0 has no K, Q, J, j,
# 7, 8, 5 or ? among others, so a name with one could not be drawn. These are
# glyphs for the 11 row typeface of the 525's font 0: K, the comma and the
# double quote as the 555's fonts draw them, the rest drawn here in the same
# style (cap height rows 2 to 8, one pixel strokes, a blank right column).
# Keyed by typeface: "525-0" is the 525's (and 555's) font 0, narrow; "525-1"
# its fonts 1 to 3, wider, which Help questions and Yes/No are drawn in;
# "515-0" the 515's bold fonts. _style_of tells which a font set is.
EXTRA_GLYPHS = {
    "525-0": {
        "K": [".......", ".......", ".#...#.", ".#..#..", ".#.#...", ".##.#..", ".#..#..",
              ".#...#.", ".#...#.", ".......", "......."],
        ",": ["...", "...", "...", "...", "...", "...", "...", "...", ".#.", ".#.", ".#."],
        '"': [".....", ".....", ".#.#.", ".#.#.", ".#.#.", ".....", ".....", ".....", ".....",
              ".....", "....."],
        "J": ["......", "......", "....#.", "....#.", "....#.", "....#.", "....#.", "#...#.",
              ".###..", "......", "......"],
        "Q": ["........", "........", "..####..", ".#....#.", ".#....#.", ".#....#.", ".#..#.#.",
              ".#...#..", "..###.#.", "........", "........"],
        "j": ["....", "....", "..#.", "....", "..#.", "..#.", "..#.", "..#.", "..#.", "..#.", "##.."],
        "5": ["......", "......", "#####.", "#.....", "####..", "....#.", "....#.", "#...#.",
              ".###..", "......", "......"],
        "7": ["......", "......", "#####.", "....#.", "...#..", "...#..", "..#...", "..#...",
              "..#...", "......", "......"],
        "8": ["......", "......", ".###..", "#...#.", "#...#.", ".###..", "#...#.", "#...#.",
              ".###..", "......", "......"],
        "?": ["......", "......", ".###..", "#...#.", "....#.", "...#..", "..#...", "......",
              "..#...", "......", "......"],
        "!": ["...", "...", ".#.", ".#.", ".#.", ".#.", ".#.", "...", ".#.", "...", "..."],
        "'": ["...", "...", ".#.", ".#.", ".#.", "...", "...", "...", "...", "...", "..."],
        "(": ["....", "....", "..#.", ".#..", ".#..", ".#..", ".#..", ".#..", ".#..", "..#.", "...."],
        ")": ["....", "....", ".#..", "..#.", "..#.", "..#.", "..#.", "..#.", "..#.", ".#..", "...."],
        "&": [".......", ".......", ".##....", "#..#...", "#..#...", ".##....", "#..#.#.", "#...#..",
              ".###.#.", ".......", "......."],
    },
    # the 515's 12 row typeface: two pixel strokes, cap height rows 2 to 8,
    # ascenders and the question mark from row 1
    "515-0": {
        "K": [".......", ".......", "##..##.", "##.##..", "####...", "###....", "####...", "##.##..",
              "##..##.", ".......", ".......", "......."],
        "J": ["......", "......", "...##.", "...##.", "...##.", "...##.", "...##.", "##.##.", ".###..",
              "......", "......", "......"],
        "Q": [".......", ".......", ".####..", "##..##.", "##..##.", "##..##.", "##..##.", "##.###.",
              ".#####.", "....##.", ".......", "......."],
        "X": [".......", ".......", "##..##.", "##..##.", ".####..", "..##...", ".####..", "##..##.",
              "##..##.", ".......", ".......", "......."],
        "Z": [".......", ".......", "######.", "....##.", "...##..", "..##...", ".##....", "##.....",
              "######.", ".......", ".......", "......."],
        "j": ["....", ".##.", ".##.", "....", ".##.", ".##.", ".##.", ".##.", ".##.", ".##.", "##..", "...."],
        "3": ["......", "......", ".###..", "#..##.", "...##.", "..##..", "...##.", "#..##.", ".###..",
              "......", "......", "......"],
        "4": [".......", ".......", "...##..", "..###..", ".#.##..", "#..##..", "######.", "...##..",
              "...##..", ".......", ".......", "......."],
        "5": ["......", "......", "#####.", "##....", "####..", "...##.", "...##.", "#..##.", ".###..",
              "......", "......", "......"],
        "6": ["......", "......", "..##..", ".##...", "##....", "####..", "##.##.", "##.##.", ".###..",
              "......", "......", "......"],
        "7": ["......", "......", "#####.", "...##.", "...##.", "..##..", "..##..", ".##...", ".##...",
              "......", "......", "......"],
        "8": ["......", "......", ".###..", "##.##.", "##.##.", ".###..", "##.##.", "##.##.", ".###..",
              "......", "......", "......"],
        "9": ["......", "......", ".###..", "##.##.", "##.##.", ".####.", "...##.", "..##..", ".##...",
              "......", "......", "......"],
        "&": ["........", "........", ".###....", "##.##...", "##.##...", ".###....", "##.##.#.", "##..##..",
              ".###.##.", "........", "........", "........"],
        "'": ["..", "#.", "#.", "..", "..", "..", "..", "..", "..", "..", "..", ".."],
        "!": ["...", "##.", "##.", "##.", "##.", "##.", "##.", "...", "##.", "...", "...", "..."],
        "#": [".......", ".......", ".#..#..", ".#..#..", "######.", ".#..#..", "######.", ".#..#..",
              ".#..#..", ".......", ".......", "......."],
    },
    # the 525's wide typeface (fonts 1 to 3): capitals from column 1, five
    # pixels wide; lower case from column 0 on rows 4 to 8; digits six wide
    "525-1": {
        "j": ["....", "....", "..#.", "....", "..#.", "..#.", "..#.", "..#.", "..#.", "..#.", "##.."],
        "k": ["......", "......", "#.....", "#.....", "#..#..", "#.#...", "##....", "#.#...", "#..#..",
              "......", "......"],
        "q": ["......", "......", "......", "......", ".####.", "#...#.", "#...#.", "#...#.", ".####.",
              "....#.", "....#."],
        "z": ["......", "......", "......", "......", "#####.", "...#..", "..#...", ".#....", "#####.",
              "......", "......"],
        "F": [".......", ".......", ".#####.", ".#.....", ".#.....", ".####..", ".#.....", ".#.....",
              ".#.....", ".......", "......."],
        "J": ["......", "......", "....#.", "....#.", "....#.", "....#.", "....#.", "#...#.", ".###..",
              "......", "......"],
        "L": [".......", ".......", ".#.....", ".#.....", ".#.....", ".#.....", ".#.....", ".#.....",
              ".#####.", ".......", "......."],
        "M": [".........", ".........", ".#.....#.", ".##...##.", ".#.#.#.#.", ".#..#..#.", ".#.....#.",
              ".#.....#.", ".#.....#.", ".........", "........."],
        "Q": ["........", "........", "..####..", ".#....#.", ".#....#.", ".#....#.", ".#..#.#.", ".##..#..",
              "..###.#.", "........", "........"],
        "S": [".......", ".......", "..####.", ".#.....", ".#.....", "..###..", ".....#.", ".....#.",
              ".####..", ".......", "......."],
        "U": [".......", ".......", ".#...#.", ".#...#.", ".#...#.", ".#...#.", ".#...#.", ".#...#.",
              "..###..", ".......", "......."],
        "Z": [".......", ".......", ".#####.", ".....#.", "....#..", "...#...", "..#....", ".#.....",
              ".#####.", ".......", "......."],
        "4": ["......", "......", "...#..", "..##..", ".#.#..", "#..#..", "#####.", "...#..", "...#..",
              "......", "......"],
        "5": ["......", "......", "#####.", "#.....", "####..", "....#.", "....#.", "#...#.", ".###..",
              "......", "......"],
        "7": ["......", "......", "#####.", "....#.", "...#..", "...#..", "..#...", "..#...", "..#...",
              "......", "......"],
        "8": ["......", "......", ".###..", "#...#.", "#...#.", ".###..", "#...#.", "#...#.", ".###..",
              "......", "......"],
        "/": [".....", ".....", "....#", "...#.", "...#.", "..#..", ".#...", ".#...", "#....", ".....", "....."],
        "(": ["....", "....", "..#.", ".#..", ".#..", ".#..", ".#..", ".#..", ".#..", "..#.", "...."],
        ")": ["....", "....", ".#..", "..#.", "..#.", "..#.", "..#.", "..#.", "..#.", ".#..", "...."],
        "&": [".......", ".......", ".##....", "#..#...", "#..#...", ".##....", "#..#.#.", "#...#..",
              ".###.#.", ".......", "......."],
        "'": ["...", "...", ".#.", ".#.", ".#.", "...", "...", "...", "...", "...", "..."],
        "!": ["...", "...", ".#.", ".#.", ".#.", ".#.", ".#.", "...", ".#.", "...", "..."],
    },
}

# Letters with a mark (Czech, Slovak, German...) are drawn from the font's own
# base letter, the mark above it: for a small letter in the rows over it with
# one blank row between, for a capital or a tall letter (l, d) in the two rows
# its top leaves, touching it, as the 11 rows allow. A caron on d, t, l, L is
# the apostrophe beside the stem that Czech and Slovak print.
MARKS = {"́": "acute", "̌": "caron", "̊": "ring", "̈": "dots",
         "̂": "circumflex"}
# the ones read back by their pixels (_glyph_table): Czech, Slovak, German; not
# ĺ, drawn as an l is, like an I, so it would read as Í
ACCENTED = "áčďéěíňóřšťúůýžÁČĎÉĚÍŇÓŘŠŤÚŮÝŽäöüÄÖÜôľŕĽŔ"
MARK_PIXELS = {      # (row from the mark's top, column from the letter's middle)
    "acute": [(0, 1), (1, 0)],
    "caron": [(0, -1), (0, 1), (1, 0)],
    "circumflex": [(0, 0), (1, -1), (1, 1)],
    "dots": [(1, -1), (1, 1)],
    "ring": [(0, 0), (1, -1), (1, 1), (2, 0)],
}


def _with_mark(rows: list[str], mark: str, base: str, x_top: int) -> list[str]:
    """`rows` (a glyph, '#' ink) with `mark` above it (MARKS)."""
    grid = [list(r) for r in rows]
    height, width = len(grid), len(grid[0])
    ink = lambda r: [x for x, v in enumerate(grid[r]) if v == "#"]
    if base in "ij":
        # dotless first: whatever is drawn above the small letters' top goes
        for r in range(min(x_top, height)):
            grid[r] = ["."] * width
    top = next(r for r in range(height) if ink(r))
    cols = [x for r in range(top, min(top + 5, height)) for x in ink(r)]
    if mark == "caron" and base in "dtlL":
        # the apostrophe: right of the stem, at the top, one blank column after
        x = max(cols) + 2
        while width < x + 2:
            grid = [row + ["."] for row in grid]
            width += 1
        for r in (top, top + 1):
            grid[r][x] = "#"
        return ["".join(row) for row in grid]
    centre = round((min(cols) + max(cols)) / 2)
    pixels = MARK_PIXELS[mark]
    tall = top < x_top
    if tall and mark == "acute":
        # touching the letter, so a column right, clear of an A's apex
        pixels = [(r, c + 1) for r, c in pixels]
    rows_needed = 1 + max(r for r, _ in pixels)
    first = top - rows_needed if tall else top - rows_needed - 1
    if first < 0:
        # a three row ring over a capital: its top and sides only
        pixels = [(r, c) for r, c in pixels if r < rows_needed - 1] if tall else pixels
        first = max(0, first + 1) if tall else 0
    if first < 0 or not pixels or first + max(r for r, _ in pixels) >= top:
        return None           # no room above the letter (a font of 8 rows)
    left = min(centre + c for _, c in pixels)
    if left < 0:
        grid = [["."] * -left + row for row in grid]
        width -= left
        centre -= left
    right = max(centre + c for _, c in pixels)
    while width < right + 2:
        grid = [row + ["."] for row in grid]
        width += 1
    for r, c in pixels:
        if 0 <= first + r < height:
            grid[first + r][centre + c] = "#"
    return ["".join(row) for row in grid]


def _style_of(d, font: int):
    """Which typeface a font set draws: the style whose sample glyphs it
    matches most, by character, or None under five matches."""
    rev = {v: k for k, v in d.alphabet.items()}
    best, score = None, 0
    for style, glyphs in _font_glyphs().items():
        n = 0
        for ch, rows in glyphs.items():
            code = rev.get(ch)
            if code is not None and d._has_glyph(font, code) and (font, code) not in getattr(d, "added_glyphs", {}):
                if ["".join("#" if v == renderer.INK else "." for v in r) for r in d.fonts[font].glyph(code)] == rows:
                    n += 1
        if n > score:
            best, score = style, n
    return best if score >= 5 else None

_FONT_GLYPHS: dict | None = None


def _font_glyphs() -> dict:
    global _FONT_GLYPHS
    if _FONT_GLYPHS is None:
        import json
        from pathlib import Path
        path = Path(__file__).resolve().parent / "arch9_font_glyphs.json"
        _FONT_GLYPHS = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return _FONT_GLYPHS


def _same_style(d, a: int, b: int) -> bool:
    """Font sets a and b draw every code both have identically (on the 515,
    font 0 and 1 do in 41 of 41; on the 525, 0 and 3 differ in 29 of 39)."""
    if d.fonts[a].height != d.fonts[b].height:
        return False
    shared = 0
    for code in range(1, max(d.fonts[a].first + d.fonts[a].count, d.fonts[b].first + d.fonts[b].count)):
        try:
            ga, gb = d.fonts[a].glyph(code), d.fonts[b].glyph(code)
        except ValueError:
            continue
        if ga != gb:
            return False
        shared += 1
    return shared >= 5


# bytes -> (marshalled decompiled document, font sets, alphabet), the last few read
_DOCS: dict = {}


class _Doc:
    """A decompiled container and what its sections say."""

    def __init__(self, raw: bytes):
        self.raw = raw
        self.blob = hconfig.split_container(raw)[2]
        self.S = sem.section_offsets(self.blob)
        # an operation reads the same bytes several times (activities(),
        # devices(), its checks); the reading is kept and each _Doc gets its
        # own copy of it, since operations change their document
        key = bytes(raw)
        kept = _DOCS.pop(key, None)
        if kept is None:
            doc = hconfig.symbolise(hconfig.decompile(raw, "input"))
            fonts = renderer.fonts(self.blob, self.S)
            kept = (marshal.dumps(doc), fonts, config_alphabet(self.blob, fonts))
        _DOCS[key] = kept
        while len(_DOCS) > 8:
            _DOCS.pop(next(iter(_DOCS)))
        self.doc = marshal.loads(kept[0])
        self.regions = self.doc["blob"]["regions"]
        self.at: dict[int, list[dict]] = {}
        for region in self.regions:
            self.at.setdefault(region["offset"], []).append(region)
        self.fonts = kept[1]
        self.alphabet = dict(kept[2])
        self.actions = ir.action_addresses(self.blob, self.S)
        self.n_modes = ir.u24(self.blob, self.S[sem.MODE_SLOT])
        self.n_actions = len(self.actions)
        # Section 5 with no infrared group or one (a 555 before or after its
        # first device) is read as plain bytes, the decompiler taking a pointer
        # table from two entries up; held so, the group's address would not
        # follow when anything before it moves (a letter added to a font did
        # it, 2026-10-20). So it is held as the table it is, in every document.
        if self.S[5] is not None and not any(r["kind"] == "pointer_table" for r in self.at.get(self.S[5], [])):
            _ir_table(self)
        # Tables whose count is wider than a byte (the mode table's three, the
        # fonts', the action lists' and the screen streams' two) are read as a
        # one byte count and zero bytes after it while the count is under 256.
        # Held so, the 256th entry cannot be written (the 555's action lists,
        # 2026-10-20); held at their width, the bytes are the same.
        for slot, width in ((6, 3), (7, 2), (10, 2), (11, 2)):
            for r in self.at.get(self.S[slot] if slot < len(self.S) and self.S[slot] is not None else -1, []):
                if r["kind"] != "pointer_table" or r["count_width"] >= width:
                    continue
                header = bytes.fromhex(r.get("header", ""))
                more = width - r["count_width"]
                if header[:more] == bytes(more):
                    r["count_width"] = width
                    r["header"] = header[more:].hex()

    def ins(self, index: int):
        return sem.instructions(self.blob, self.actions[index])

    # Two state variables every config uses are numbered per config, after the
    # devices' own: CurrentActivityState is 15 on the 525's, 17 on a 515's, 16
    # on a 555's; the one a Devices soft key sets to 1 (and the activity menu to
    # 0) is 18 on the 525's and 21 on a 515's.

    @property
    def cas_var(self) -> int:
        """CurrentActivityState, found by its name in the name table."""
        if not hasattr(self, "_cas_var"):
            hits = [r["index"] for r in self.region(self.S[0], "name_table")["records"]
                    if r["parent"] == 1 and r["name"].startswith("CurrentActivityState")]
            if len(hits) != 1:
                raise ComposeError(f"{len(hits)} CurrentActivityState variables in the name table")
            self._cas_var = hits[0]
        return self._cas_var

    @property
    def cas_op(self) -> int:
        """The instruction that writes CurrentActivityState (0x8F on the 525)."""
        return 0x80 + self.cas_var

    @property
    def device_flag_op(self) -> int:
        """The state write a Devices soft key makes after entering its device's
        mode, `(op, 1)`; the activity menu writes `(op, 0)`. Read off the
        lists the Devices screens' soft keys run (0x92 on the 525)."""
        if not hasattr(self, "_device_flag_op"):
            seen: dict[int, int] = {}
            for k in range(self.blob[self.S[9]]):
                at = ir.u24(self.blob, self.S[9] + 1 + 3 * k) - BASE
                for tag, op, value in cmp.tagged(self.blob, at):
                    if tag != 0xAF:
                        continue
                    modes = [value] if op == 0x7E else [v for o, v in self.ins(value) if o == 0x7E]                         if op == 0x7F else []
                    for m in modes:
                        for p in self.mode(m)["pages"]:
                            for t, o, v in cmp.tagged(self.blob, p["list"]):
                                ins = self.ins(v) if o == 0x7F else []
                                if len(ins) >= 2 and ins[0][0] == 0x7E and 0x80 <= ins[1][0] < 0x100 \
                                        and ins[1][1] == 1:
                                    seen[ins[1][0]] = seen.get(ins[1][0], 0) + 1
            if not seen:
                # no device yet (an empty 555): the system list that clears it,
                # [flag := 0, 0x72 13], list 53 on the 525's and the 515's
                seen = {ins[0][0]: 1 for ins in map(self.ins, range(self.n_actions))
                        if len(ins) == 2 and 0x80 <= ins[0][0] < 0x100 and ins[0][1] == 0
                        and ins[1] == (0x72, 13)}
            if len(seen) < 1 or (len(seen) > 1 and len(set(seen.values())) == 1):
                raise ComposeError("no Devices soft key enters a device here, so the device flag is unknown")
            self._device_flag_op = max(seen, key=seen.get)
        return self._device_flag_op

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
        self._ensure_glyphs(value + "..", font)
        try:
            codes = encode_text(value, self.alphabet)
        except ValueError as error:
            raise ComposeError(f"{value!r}: {error}") from None
        if limit is not None:
            codes = self._fit(codes, font, limit)
        return codes

    def _has_glyph(self, font: int, code: int) -> bool:
        if (font, code) in getattr(self, "added_glyphs", {}):
            return True
        try:
            self.fonts[font].glyph(code)
            return True
        except ValueError:
            return False

    def _glyph_for(self, font: int, ch: str):
        """Rows for a character `font` lacks, nearest source first: another font
        set of this configuration that draws exactly like it, then the same
        typeface's sample (arch9_font_glyphs.json), then EXTRA_GLYPHS."""
        height = self.fonts[font].height
        code = next((k for k, v in self.alphabet.items() if v == ch), None)
        if code is not None:
            for i, other in enumerate(self.fonts):
                if i != font and other.height == height and self._has_glyph(i, code) and \
                        _same_style(self, font, i):
                    # one this operation added is not in the file's bytes yet
                    if (i, code) in getattr(self, "added_glyphs", {}):
                        return self.added_glyphs[(i, code)]
                    return ["".join("#" if v == renderer.INK else "." for v in row) for row in other.glyph(code)]
        style = _style_of(self, font)
        return _font_glyphs().get(style, {}).get(ch) or EXTRA_GLYPHS.get(style, {}).get(ch) \
            or self._accented(font, ch)

    def _rows_of(self, font: int, ch: str):
        """`ch` as `font` draws it, or as _glyph_for would add it."""
        code = next((k for k, v in self.alphabet.items() if v == ch), None)
        if code is not None and (font, code) in getattr(self, "added_glyphs", {}):
            return self.added_glyphs[(font, code)]
        if code is not None and self._has_glyph(font, code):
            return ["".join("#" if v == renderer.INK else "." for v in row) for row in self.fonts[font].glyph(code)]
        return self._glyph_for(font, ch)

    def _accented(self, font: int, ch: str):
        """A letter with a mark (č, é, ů, ä, ô...): the font's own base letter
        with the mark drawn above it (_with_mark)."""
        parts = unicodedata.normalize("NFD", ch)
        if len(parts) != 2 or parts[1] not in MARKS or parts[0] == ch:
            return None
        base = self._rows_of(font, parts[0])
        if base is None:
            return None
        # where small letters start: the top of an n (or an a)
        small = self._rows_of(font, "n") or self._rows_of(font, "a")
        x_top = next(r for r, row in enumerate(small) if "#" in row) if small else 4
        return _with_mark(base, MARKS[parts[1]], parts[0], x_top)

    def _ensure_glyphs(self, value: str, font: int, source: dict | None = None) -> None:
        """Give `font` a glyph for each character of `value` it cannot draw yet
        (_glyph_for, or `source`: character -> rows): at the character's own
        code when the configuration has one (the 525's font 0 lacks 5 and ?),
        else at a new code after the whole alphabet. The new glyphs go in front
        of section 0."""
        if not hasattr(self, "added_glyphs"):
            self.added_glyphs = {}
        for ch in dict.fromkeys(value):
            code = next((k for k, v in self.alphabet.items() if v == ch), None)
            if code is not None and self._has_glyph(font, code):
                continue
            rows = source.get(ch) if source is not None else self._glyph_for(font, ch)
            if rows is None or len(rows) != self.fonts[font].height:
                continue
            code, region = install_character_glyph(
                self.blob, self.doc, self.S, font_index=font, character=ch, rows=rows,
                alphabet=self.alphabet, region_id=f"glyph_f{font}_{ord(ch):x}")
            s0 = next(i for i, r in enumerate(self.regions) if r.get("section") == 0)
            self.regions.insert(s0, region)
            self.added_glyphs[(font, code)] = rows

    def width(self, codes: bytes, font: int = 0) -> int:
        added = getattr(self, "added_glyphs", {})
        try:
            return sum(len(added[(font, c)][0]) if (font, c) in added else len(self.fonts[font].glyph(c)[0])
                       for c in codes)
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
        if len(ins) >= 2 and ins[0][0] == 0x7E and ins[1] == (self.device_flag_op, 1):
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
            listed = cmp.tagged(self.blob, first)
            # an empty Devices screen (the 555's before setup) lists nothing at all
            if listed and not any(op == 0x7F and self.selection_target(v) is not None
                                  for _, op, v in listed):
                raise ComposeError(f"mode {m} is entered by 0xAF but is no device list")
        if not modes:
            raise ComposeError("no Devices screen found")
        return modes

    def idle_devices_mode(self) -> int:
        """The Devices screen the remote shows with no activity running: the key
        set section 8's leading list selects at start up (0x1F 0xFFnn, list 184
        on the sample), and what 0xAF enters there (FORMAT 5ae: mode 82)."""
        leading = [r for r in self.regions if r["kind"] == "action_list" and r.get("section") == 8]
        stack = [(int(i["opcode"], 16), i["operand"]) for r in leading for i in r["instructions"]]
        seen = set()
        while stack:
            op, operand = stack.pop(0)
            if op == 0x1F and operand >> 8 == 0xFF:
                at = ir.u24(self.blob, self.S[9] + 1 + 3 * (operand & 0xFF)) - BASE
                for tag, o, v in cmp.tagged(self.blob, at):
                    if tag == 0xAF:
                        if o == 0x7E:
                            return v
                        if o == 0x7F:
                            hits = [x for oo, x in self.ins(v) if oo == 0x7E]
                            if hits:
                                return hits[0]
            elif op == 0x7F and operand not in seen:
                seen.add(operand)
                stack[0:0] = self.ins(operand)
        return self.devices_modes()[0]

    def companion_modes(self, mode: int) -> list[int]:
        """Modes that belong to a device mode alone: reached from its keys,
        directly or through each other, and named by nothing else. Logitech's
        7.x compile of a 555 gives every device one or two (issue #46): keys
        0xA5 and 0xA4 go round the device and them, each a page with no soft
        keys."""
        def lists(m):
            g = self.mode(m)
            return {g["start"]} | {p["list"] for p in g["pages"]}
        # which mode enters which, by the keys and soft keys of its own lists;
        # followed both ways, since an edit of the device's keys can leave
        # them entering the device with nothing entering them any more
        enters = {m: {c for off in lists(m) for _, op, c in cmp.tagged(self.blob, off)
                      if op == 0x7E and 40 < c < self.n_modes}
                  for m in range(41, self.n_modes)}
        def component(allowed):
            found, todo = {mode}, [mode]
            while todo:
                m = todo.pop()
                for c in enters.get(m, set()) | {k for k, v in enters.items() if m in v}:
                    if c in allowed and c not in found:
                        found.add(c)
                        todo.append(c)
            return found
        group = component(set(enters))
        # what names a mode from outside the group keeps it out, and with it
        # what only it reached
        named = set()
        for r in self.regions:
            if r["kind"] == "action_list" and r.get("section") == 8:
                named |= {i["operand"] for i in r["instructions"] if int(i["opcode"], 16) == 0x7E}
            elif r["kind"] == "screen_instruction" and r.get("opcode") == "0x11":
                raw3 = bytes.fromhex(r["operands"])
                if len(raw3) == 3 and raw3[2] == 0x7E:
                    named.add(raw3[0] | raw3[1] << 8)
        named |= {v for i in range(self.n_actions) for o, v in self.ins(i) if o == 0x7E}
        named |= {v for _, op, v in _transition_sites(self.blob, self.S) if op == 0x7E}
        while True:
            inside = set().union(*map(lists, group))
            outside = named | {e["operand"] for r in self.regions
                               if r["kind"] == "tagged_list" and r["offset"] not in inside
                               and r.get("role") != "page_binding_copy"
                               for e in r["entries"] if e["opcode"] == 0x7E}
            out = {c for c in group - {mode} if c in outside}
            if not out:
                break
            # drop it, and keep what still joins the device without it
            group = component(group - out)
        return sorted(group - {mode})

    def pool_copy(self, page_list_offset: int) -> dict:
        """The pool copy of a page list. Copies sit in the pools in the order of
        the page records in the mode table, each record counted once, which is
        how arch9_record_body_regions pairs them (FORMAT 5ad); matching by shape
        is not enough, because a copied screen's list looks like its original's."""
        if not hasattr(self, "_copies"):
            seen, order = set(), []
            for m in range(self.n_modes):
                for p in self.mode(m)["pages"]:
                    if p["address"] not in seen:
                        seen.add(p["address"])
                        order.append(p["list"])
            copies = sorted((r for r in self.regions if r.get("role") == "page_binding_copy"),
                            key=lambda r: r["offset"])
            if len(copies) != len(order):
                raise ComposeError(f"{len(copies)} pool copies for {len(order)} pages")
            self._copies = dict(zip(order, copies))
        if page_list_offset not in self._copies:
            raise ComposeError(f"no pool copy of the page list at 0x{page_list_offset:X}")
        copy_ = self._copies[page_list_offset]
        original = self.region(page_list_offset, "tagged_list")
        if [(e["tag"], e["opcode"]) for e in copy_["entries"]] != \
                [(e["tag"], e["opcode"]) for e in original["entries"]]:
            raise ComposeError(f"the pool copy at 0x{copy_['offset']:X} does not mirror its page list")
        return copy_

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


# Where a corner's name is read from: the 525 draws its two lines at 13 and 24
# (35 and 46 below), a 515 at 13 and 25 (35 and 47), so a reader takes the band
# between the title and the page counter rather than exact rows.
TOP_ROWS = range(10, 34)
BOTTOM_ROWS = range(34, 56)


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
                    glyphs["codes"] = [_digit(d, total)]
                elif x in (35, 38):
                    if glyphs["codes"] != [_digit(d, cur)]:
                        raise ComposeError(f"page counter at 0x{root:X} disagrees with its page")
            continue
        x = 38 if cur == 1 else 35
        d.insert_before(d.band_ends(root)[7], [
            opaque(f"{prefix}_{root:x}_font", b"\x10\x04"),
            _draw(x, 56, bytes((_digit(d, cur),)), f"{prefix}_{root:x}_cur"),
            _draw(47, 56, _of(d), f"{prefix}_{root:x}_of"),
            _draw(65, 56, bytes((_digit(d, total),)), f"{prefix}_{root:x}_total"),
        ])


def _fonts_at_band_ends(d: _Doc, root: int) -> dict[int, int]:
    """The font in force at each band's closing 0x17."""
    font, band, out = 0, -1, {}
    for item in sem.screen_program_path(d.blob, root):
        if item["opcode"] == 0x10:
            font = item["operands"][0]
        elif item["opcode"] == 0x16:
            band = item["operands"][0]
        elif item["opcode"] == 0x17:
            out[band] = font
    return out


def _in_font_0(draws: list, font: int, rid: str) -> list:
    """Draws spelt for font 0, put where another font is in force (a page
    counter's, say): switch to font 0 and back around them."""
    if font == 0 or not draws:
        return draws
    return [opaque(f"{rid}_f0", b"\x10\x00"), *draws, opaque(f"{rid}_fb", bytes((0x10, font)))]


def _name_lines(d: _Doc, name: str) -> list[bytes]:
    # a line already cut short ("Panason..", as Logitech drew it) is kept as it is
    fit = lambda s: d.text(s, limit=None if s.endswith("..") else HALF_WIDTH)
    if " " not in name or d.width(d.text(name, limit=None)) <= HALF_WIDTH:
        return [fit(name)]
    # as many words as fit on the first line, the rest on the second
    words = name.split(" ")
    cut = 1
    for k in range(1, len(words)):
        if d.width(d.text(" ".join(words[:k]), limit=None)) <= HALF_WIDTH:
            cut = k
    return [fit(" ".join(words[:cut])), fit(" ".join(words[cut:]))]


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
        elif op == 0x10 and bytes(ops) == b"\x04":
            continue              # the page counter's font; a counter added brings its own
        elif op == 0x10 and title is not None:
            # the template's title font (3 on the 525's Devices screen, another
            # on the 555's) may lack the new title's letters; a new title is
            # drawn in font 0, as the August device pages were and the remote
            # showed them, and font 0 is the one missing letters are added to
            out.append(opaque(name, b"\x10\x00"))
        else:
            out.append(opaque(name, bytes((op,)) + ops))
    return out


def _program_fonts(program: list[dict]) -> dict[int, int]:
    """The font in force where each band of a program held as regions ends:
    0 on the 525's and 515's Devices pages, the title's font all the way down
    on the 555's."""
    font, band, out = 0, -1, {}
    for r in program:
        data = bytes.fromhex("".join(r.get("data", []))) if r["kind"] == "opaque" else b""
        if data[:1] == b"\x10" and len(data) == 2:
            font = data[1]
        elif data[:1] == b"\x16":
            band = data[1]
        elif data == b"\x17":
            out[band] = font
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


def _device_pages(d: _Doc, prefix: str, name: str, lcd, act: dict, donor_root: int):
    """A device's soft-key pages: (records, programs+records in layout order,
    section 8 lists, pool copies). `act` maps a command name to its action list."""
    title = d.text(name, limit=96)
    records, programs, lists, copies = [], [], [], []
    for k, page in enumerate(lcd):
        program = _blank_devices_page(d, donor_root, f"{prefix}_p{k}_screen", title=title)
        per_band: dict[int, list[dict]] = {}
        for c, item in enumerate(page):
            if item is None:
                continue
            # one line, or two where Logitech draws two ("Input" over "Hdmi1")
            lines = _name_lines(d, item[0])
            ys = ((16,) if c < 2 else (35,)) if len(lines) == 1 else ((13, 24) if c < 2 else (35, 46))
            for n, (codes, y) in enumerate(zip(lines, ys)):
                x = 0 if c in (0, 2) else 96 - d.width(codes)
                for band in _bands(y):
                    per_band.setdefault(band, []).append(
                        _draw(x, y, codes, f"{prefix}_p{k}_k{c}_l{n}_b{band}"))
        if len(lcd) > 1:
            per_band.setdefault(7, []).extend([
                opaque(f"{prefix}_p{k}_cf", b"\x10\x04"),
                _draw(38 if k == 0 else 35, 56, bytes((_digit(d, k + 1),)), f"{prefix}_p{k}_cc"),
                _draw(47, 56, _of(d), f"{prefix}_p{k}_co"),
                _draw(65, 56, bytes((_digit(d, len(lcd)),)), f"{prefix}_p{k}_ct")])
        _insert_in_program(program, per_band)
        entries = [{"tag": SOFT[c], "operand": act[item[1]], "opcode": 0x7F}
                   for c, item in enumerate(page) if item is not None]
        plist = fresh_region("tagged_list", f"{prefix}_p{k}_list", section=8,
                             wide=not entries, entries=entries)
        record = fresh_region("mode_page", f"{prefix}_p{k}",
                              targets=[{"to": plist["id"]}, {"to": program[0]["id"]}])
        programs += [*program, record]
        lists.append(plist)
        copies.append(fresh_region("tagged_list", f"{prefix}_p{k}_copy", wide=not entries,
                                   role="page_binding_copy", entries=copy.deepcopy(entries)))
        records.append(record)
    return records, programs, lists, copies


# --------------------------------------------------------------- add_device ---

@dataclass
class Placement:
    """Where add_device put things, for the caller and for its own checks."""
    group: int
    mode: int
    devices_page: int
    corner: int
    actions: dict = field(default_factory=dict)


def _ir_table(d: _Doc) -> dict:
    """Section 5, the infrared group table. The decompiler reads a pointer
    table only from two entries up, so with no group or one (a 555 with no
    device or its first) section 5 is plain bytes; it becomes the table it is,
    one byte of count."""
    S = d.S
    for r in list(d.at[S[5]]):
        if r["kind"] != "section" or not r.get("data"):
            continue
        raw = bytes.fromhex("".join(r["data"]))
        if raw[:1] == b"\x00":
            size, targets = 1, []
        elif raw[:1] == b"\x01" and len(raw) >= 4:
            size, targets = 4, [{"to": d.region(ir.u24(raw, 1) - BASE, "ir_group")["id"]}]
        else:
            continue
        rest = raw[size:]
        r.update(kind="pointer_table", count_width=1, header="", targets=targets, length=size)
        for key in ("data", "part"):
            r.pop(key, None)
        if rest:
            # what followed the table in the same span stays as it was
            tail = opaque(f"{r['id']}_after", rest)
            tail.update(offset=r["offset"] + size, length=len(rest))
            d.regions.insert(d.index_of(r) + 1, tail)
            d.at.setdefault(tail["offset"], []).append(tail)
    return d.region(S[5], "pointer_table")


@_needs_counter_font
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
    first_pages = []
    if not any(cmp.tagged(blob, pages[m][0]["list"]) for m in devices):
        # no device anywhere yet (an empty 555): the first one goes on the
        # first page, top left, of every Devices screen
        corner = 0
        seen_pages = set()
        for m in devices:
            if pages[m][0]["address"] not in seen_pages:
                seen_pages.add(pages[m][0]["address"])
                first_pages.append(pages[m][0])
        target_page = first_pages[0]
    elif shared:
        last = shared[-1]
        used = {t for t, _, _ in cmp.tagged(blob, last["list"])}
        free = [i for i, tag in enumerate(SOFT) if tag not in used]
        if free:
            corner, target_page = free[0], last
    else:
        # one page only (a 555 with its first device): a corner free on the
        # first page of every Devices screen, before a second page
        for m in devices:
            if pages[m][0]["address"] not in {p["address"] for p in first_pages}:
                first_pages.append(pages[m][0])
        used = {t for p in first_pages for t, _, _ in cmp.tagged(blob, p["list"])}
        free = [i for i, tag in enumerate(SOFT) if tag not in used]
        if free:
            corner, target_page = free[0], first_pages[0]
        else:
            first_pages = []
    new_page = target_page is None
    if new_page:
        corner = 0
    n_pages = len(pages[devices[0]]) + (1 if new_page else 0)
    if n_pages > 6:
        raise ComposeError("Devices would get a seventh page")
    # the Devices page it lands on: a new last one, the first (empty, or with a
    # free corner, on every Devices screen), or the last shared one
    on_page = n_pages - 1 if new_page else 0 if first_pages else \
        next(k for k, p in enumerate(pages[devices[0]]) if p["address"] == target_page["address"])

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
    select_ins = [{"operand": mode, "opcode": "0x7E"}, {"operand": 1, "opcode": f"0x{d.device_flag_op:02X}"}]
    if on_enter:
        select_ins.append({"operand": act[on_enter], "opcode": "0x7F"})
    selection = fresh_region("action_list", f"dev{group}_action_{select_index}", instructions=select_ins)

    # ---- the device mode: physical keys from the widest existing device list ----
    device_modes = sorted({t for m in devices for p in pages[m]
                           for _, op, v in cmp.tagged(blob, p["list"])
                           if op == 0x7F and (t := d.selection_target(v)) is not None})
    if device_modes:
        template = max(device_modes, key=lambda m: len(cmp.tagged(blob, d.mode(m)["start"])))
        tpl_list = d.region(d.mode(template)["start"], "tagged_list")
        donor_root = d.mode(template)["pages"][0]["program"]
    else:
        # the first device (an empty 555): every key, as Logitech lists them,
        # and the Devices screen's own frame of four corners for its pages
        tpl_list = {"wide": False, "entries": [{"tag": t, "operand": 0, "opcode": 0} for t in DEVICE_KEY_ORDER]}
        donor_root = pages[devices[0]][0]["program"]
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
    mode_pages, mode_programs, page_lists, copies = _device_pages(
        d, f"dev{group}", name, lcd, act, donor_root)
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
        fonts_at = _program_fonts(program)
        _insert_in_program(program, {band: _in_font_0(regs, fonts_at.get(band, 0), f"dev{group}_new_b{band}")
                                     for band, regs in draws.items()})
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
        for k, page in enumerate(first_pages or [target_page]):
            twin = d.pool_copy(page["list"])
            plist = d.region(page["list"], "tagged_list")
            if not plist["entries"]:
                plist["wide"] = twin["wide"] = False   # a list with entries is narrow here
            plist["entries"].append(dict(select_entry))
            twin["entries"].append(dict(select_entry))
            ends = d.band_ends(page["program"])
            fonts_at = _fonts_at_band_ends(d, page["program"])
            these = draws if k == 0 else _corner_draws(d, corner, name, f"dev{group}_on_devices{k}")
            for band in sorted(these, reverse=True):
                d.insert_before(ends[band], _in_font_0(these[band], fonts_at[band], f"dev{group}_b{band}_{k}"))

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
            _draw(35, 56, bytes((_digit(d, n_pages),)), f"dev{group}_newpage_cc"),
            _draw(47, 56, _of(d), f"dev{group}_newpage_co"),
            _draw(65, 56, bytes((_digit(d, n_pages),)), f"dev{group}_newpage_ct")]})
        new_devices_regions[:-1] = body

    # ---- tables and placement ----
    _ir_table(d)["targets"].append({"to": group_region["id"]})
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
    placement = Placement(group=group, mode=mode, devices_page=on_page, corner=corner,
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
        had = len(before.mode(m)["pages"])
        if len(pages) != max(had, placement.devices_page + 1):
            raise AssertionError(f"Devices mode {m} has {len(pages)} pages")
        got = {t: v for t, op, v in cmp.tagged(a, pages[placement.devices_page]["list"])}
        sel = got.get(SOFT[placement.corner])
        if sel is None or after.selection_target(sel) != placement.mode:
            raise AssertionError(f"Devices mode {m} does not open the new device")
        if on_enter and after.ins(sel)[2:] != [(0x7F, placement.actions[on_enter])]:
            raise AssertionError("on_enter")
    r0, r1 = render_map(b), render_map(a)
    # every Devices page: the one it lands on, and the counters of the others
    devices_keys = {(m, k) for m in devices for k in range(len(after.mode(m)["pages"]))}
    changed = [k for k in r0 if r0[k][0] != r1[k][0] and k not in devices_keys]
    if changed:
        raise AssertionError(f"screens changed that should not have: {changed}")
    _check_container(out)


def _check_container(out: bytes) -> None:
    """What every result must pass whatever the operation was."""
    a = hconfig.split_container(out)[2]
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
    # the infrared groups read, and every command sent is one its group has: a
    # one group table held as bytes once pointed elsewhere after a move
    # (2026-10-20), which every other check here let through
    S = sem.section_offsets(a)
    try:
        ir.ir_groups(a, S)
        sem.verify_action_closures(a, S, False)
    except AssertionError as error:
        raise AssertionError(f"the infrared groups or what is sent to them: {error}") from None
    # section 14 reads whole: every item's pointer lands in the file. One bad
    # record makes the reader give up on all of them, and then no pointer in
    # section 14 follows the next move
    items = sum(a[ir.u24(a, S[14] + 1 + 3 * k) - BASE + 1] for k in range(a[S[14]]))
    found = len(hconfig.arch9_value_map_references(a, pointers))
    if found < items:
        raise AssertionError(f"section 14 reads {found} pointers of {items} items")
    d = _Doc(out)
    clipped = _band_problems(d)
    if clipped:
        raise AssertionError(f"text drawn in one band and cut off in the next: {clipped[:3]}")
    # every action list the index names reads as one: bytes a pattern claimed
    # instead (a 0x16 "reference") are what the next relocation rewrites
    # (an action list after a mode record, 2026-10-20)
    listed = {r["offset"] for r in d.regions if r["kind"] == "action_list"}
    misread = [i for i, at in enumerate(d.actions) if at not in listed]
    if misread:
        raise AssertionError(f"action lists not read as action lists: {misread[:5]}")


def _band_problems(d: "_Doc") -> list:
    """Draws missing from a band their glyphs reach into. The panel takes a
    screen eight rows at a time (opcode 22 picks the band), so text crossing
    into the next band is drawn again there; on every page of the 525, 515 and
    555 samples it is, and a draw that is not shows only its top on the remote
    (the first "Starting" composed, 2026-10-19). Our renderer draws whole
    glyphs and cannot see it, so it is checked here."""
    out = []
    for m in range(d.n_modes):
        for k, p in enumerate(d.mode(m)["pages"]):
            band, font, draws = None, 0, []
            for it in sem.screen_program_path(d.blob, p["program"]):
                op, ops = it["opcode"], it["operands"]
                if op == 0x16:
                    band = ops[0]
                elif op == 0x10:
                    font = ops[0]
                elif op in (4, 5):
                    draws.append((band, ops[0], ops[1], renderer_string(d, it), d.fonts[font].height))
            have = {(b, x, y, s) for b, x, y, s, _ in draws}
            for b, x, y, s, h in draws:
                for need in range(y // 8, min((y + h - 1) // 8, 7) + 1):
                    if (need, x, y, s) not in have:
                        out.append((m, k, need, x, y))
    return out


# ------------------------------------------------------------ the inventory ---

@dataclass(frozen=True)
class Device:
    name: str           # as the Devices screen draws it
    mode: int
    group: int | None   # infrared group its keys send from, None if they send none
    page: int           # Devices page, 0 based
    corner: int         # index into SOFT


def devices(raw: bytes) -> list[Device]:
    """The devices as the remote lists them, read off the Devices screens."""
    d = _Doc(raw)
    names = d.alphabet             # glyph code -> character
    first = d.idle_devices_mode()      # what the remote shows with nothing running
    out = []
    for page_index, page in enumerate(d.mode(first)["pages"]):
        drawn = []
        for item in sem.screen_program_path(d.blob, page["program"]):
            if item["opcode"] in (4, 5):
                codes = renderer_string(d, item)
                drawn.append((item["operands"][0], item["operands"][1], codes))
        for tag, op, value in cmp.tagged(d.blob, page["list"]):
            if op != 0x7F or tag not in SOFT:
                continue
            mode = d.selection_target(value)
            if mode is None:
                continue
            corner = SOFT.index(tag)
            rows = TOP_ROWS if corner < 2 else BOTTOM_ROWS
            right = corner in (1, 3)
            lines = []
            for y in rows:
                hits = {codes for x, yy, codes in drawn if yy == y and (x >= 48) == right}
                lines += ["".join(names.get(c, "?") for c in codes) for codes in sorted(hits)]
            groups = [b["group"] for b in ir.mode_bindings(d.blob, d.S, mode)]
            group = max(set(groups), key=groups.count) if groups else None
            out.append(Device(" ".join(lines), mode, group, page_index, corner))
    return out


_devices_of = devices        # for functions with a local of that name


def renderer_string(d: _Doc, item: dict) -> bytes:
    ops = item["operands"]
    if item["opcode"] == 4:
        at = ir.u24(ops, 2) - BASE
        return bytes(d.blob[at:d.blob.index(0, at)])
    return bytes(ops[2:-1])


# ------------------------------------------------------------ rekey_device ---

@_needs_counter_font
def rekey_device(raw: bytes, mode: int, commands: dict, keys: dict,
                 lcd: Sequence[Sequence] | None = None) -> bytes:
    """Change what a device's keys send, add commands to it, and optionally
    replace its soft-key pages.

    `commands`: name -> Command (added to the device's infrared group) or int
    (a record the group already has). `keys`: 525 key name or tag -> command
    name, or None for a key that sends nothing; keys not named keep their
    binding. `lcd`: new soft-key pages as in add_device, or None to keep them.
    """
    d = _Doc(raw)
    blob, S = d.blob, d.S
    found = [x for x in devices(raw) if x.mode == mode]
    if len(found) != 1 or found[0].group is None:
        raise ComposeError(f"mode {mode} is not a device on the Devices screens")
    device = found[0]
    group = device.group
    records = ir.ir_groups(blob, S)[group]
    new = [n for n, v in commands.items() if isinstance(v, Command)]
    index = {}
    for n, v in commands.items():
        if isinstance(v, Command):
            index[n] = len(records) + new.index(n)
        elif isinstance(v, int) and 0 <= v < len(records):
            index[n] = v
        else:
            raise ComposeError(f"{n!r}: not a Command and not a record of group {group}")
    for n in new:
        for stream in commands[n].streams:
            if stream is not None and any(len(c) > SYMBOL_LIMIT for c in enc._chunks(tuple(stream))):
                raise ComposeError(f"{n}: a symbol above {SYMBOL_LIMIT} words")
    key_tags = {}
    for key, n in keys.items():
        tag = KEYS[key] if isinstance(key, str) else key
        if n is not None and n not in index:
            raise ComposeError(f"key {key} names no command {n!r}")
        key_tags[tag] = n
    if index and len(records) + len(new) > 0xFF:
        raise ComposeError("a group holds at most 255 commands")

    used = sorted({n for n in key_tags.values() if n} |
                  {i[1] for p in (lcd or []) for i in p if i}, key=lambda n: index[n])
    act = {n: d.n_actions + i for i, n in enumerate(used)}
    actions = [fresh_region("action_list", f"rekey{mode}_action_{act[n]}",
                            instructions=[{"operand": group << 8 | index[n], "opcode": "0x7D"},
                                          {"operand": group << 8 | 1, "opcode": "0x7C"}])
               for n in used]
    seeds = []
    for n in new:
        seed, delta = _encode(commands[n], 0x060000)
        seeds.append((commands[n], seed, delta, opaque(f"rekey{mode}_class5_{index[n]}", seed)))
    if seeds:
        group_region = d.region(ir.u24(blob, S[5] + 1 + 3 * group) - BASE, "ir_group")
        group_region["targets"] += [{"to": r["id"], "delta": dl} for _, _, dl, r in seeds]

    f = d.mode(mode)
    mode_list = d.region(f["start"], "tagged_list")
    have = {e["tag"] for e in mode_list["entries"]}
    for tag in key_tags:
        if tag not in have:
            mode_list["entries"].append({"tag": tag, "operand": 0, "opcode": 0})
    for e in mode_list["entries"]:
        if e["tag"] in key_tags:
            n = key_tags[e["tag"]]
            e["operand"], e["opcode"] = (act[n], 0x7F) if n else (0, 0)

    if lcd is not None:
        if not lcd or len(lcd) > 6 or any(len(p) != 4 for p in lcd):
            raise ComposeError("lcd is one to six pages of four soft keys")
        donor = f["pages"][0]["program"]
        records_, programs, lists, copies = _device_pages(d, f"rekey{mode}", device.name, lcd, act, donor)
        gone = set()
        for p in f["pages"]:
            gone.add(d.region(p["address"], "mode_page")["id"])
            plist = d.region(p["list"], "tagged_list")
            twin = d.pool_copy(p["list"])
            gone.update((plist["id"], twin["id"]))
            for item in sem.screen_program_path(blob, p["program"]):
                gone.update(r["id"] for r in d.at[item["offset"]] if r["kind"] != "section")
                if item["opcode"] == 5:
                    gone.update(r["id"] for r in d.at.get(item["offset"] + 3, [])
                                if r["kind"] == "glyph_string")
        first_copy = min(i for i, r in enumerate(d.regions) if r["id"] in gone
                         and r.get("role") == "page_binding_copy")
        d.regions[first_copy:first_copy] = copies
        d.region(f["entry"], "record_header")["targets"] = [{"to": r["id"]} for r in records_]
        d.regions[:] = [r for r in d.regions if r["id"] not in gone]
        dangling = [r["id"] for r in d.regions for t in (r.get("targets") or [])
                    if t and t["to"] in gone]
        if dangling:
            raise ComposeError(f"the old pages are still referenced from {dangling[:3]}")
        # the compiler's layout, which the pool reader checks: a mode's key list
        # right before its first page's screen program
        at_list = d.index_of(mode_list)
        d.regions[at_list + 1:at_list + 1] = programs
        s9 = next(i for i, r in enumerate(d.regions) if r.get("section") == 9)
        d.regions[s9:s9] = lists

    d.region(S[sem.ACTION_SLOT], "pointer_table")["targets"].extend({"to": a["id"]} for a in actions)
    s0 = next(i for i, r in enumerate(d.regions) if r.get("section") == 0)
    d.regions[s0:s0] = [*(r for *_, r in seeds), *actions]
    resolve, _ = hconfig._resolver(d.regions)
    for c, seed, delta, region in seeds:
        base = BASE + resolve({"to": region["id"]})
        data, delta2 = _encode(c, base)
        if len(data) != len(seed) or delta2 != delta:
            raise AssertionError("record changed length on relocation")
        region["data"] = hconfig._hex_lines(data)
    out = d.compile()

    # ---- checks ----
    after = _Doc(out)
    a = after.blob
    g0, g1 = ir.ir_groups(blob, S), ir.ir_groups(a, after.S)
    if [len(g) for g in g1] != [len(g) + (len(new) if i == group else 0) for i, g in enumerate(g0)]:
        raise AssertionError("group population")
    for i, g in enumerate(g0):
        if [class5_source(blob, x) for x in g] != [class5_source(a, x) for x in g1[i][:len(g)]]:
            raise AssertionError(f"an existing record of group {i} changed")
    for n in new:
        decoded = enc.decode_class5_record(bytes(a), base_address=BASE,
                                           record_address=g1[group][index[n]])
        if decoded.pointer_streams != tuple(None if s is None else tuple(s) for s in commands[n].streams):
            raise AssertionError(f"{n} does not decode to what was asked")
    if not all(d.ins(i) == after.ins(i) for i in range(d.n_actions)):
        raise AssertionError("an existing action list changed")
    before_binds = {b["tag"]: b for b in ir.mode_bindings(blob, S, mode) if b["source"] == "physical"}
    after_binds = {b["tag"]: b for b in ir.mode_bindings(a, after.S, mode) if b["source"] == "physical"}
    for tag in set(before_binds) | set(after_binds):
        if tag in key_tags:
            n = key_tags[tag]
            got = after_binds.get(tag)
            if n is None and got is not None or n is not None and (
                    got is None or got["group"] != group or got["command"] != index[n]):
                raise AssertionError(f"key 0x{tag:X}")
        elif (before_binds.get(tag) or {}).get("command") != (after_binds.get(tag) or {}).get("command"):
            raise AssertionError(f"key 0x{tag:X} changed without being asked")
    if lcd is not None:
        pages = after.mode(mode)["pages"]
        if len(pages) != len(lcd):
            raise AssertionError("page count")
        for page, rec in zip(lcd, pages):
            got = {t: v for t, op, v in cmp.tagged(a, rec["list"])}
            for c, item in enumerate(page):
                if item is not None and after.ins(got[SOFT[c]])[0] != (0x7D, group << 8 | index[item[1]]):
                    raise AssertionError(f"soft key {item[0]!r}")
    r0, r1 = render_map(blob), render_map(a)
    changed = [k for k in set(r0) | set(r1) if k[0] != mode and r0.get(k, (None,))[0] != r1.get(k, (None,))[0]]
    if changed:
        raise AssertionError(f"screens changed that should not have: {changed}")
    _check_container(out)
    return out


# ------------------------------------------------------------- references ---
# An action list, a mode and an infrared group are named by index. These are
# every place a 525 config names one (FORMAT 5af): action lists, the section 8
# leading list among them; tagged lists; section 11 streams; section 13
# transitions. 0x7F names an action list, 0x7E a mode, and the high byte of
# 0x7D and 0x7C an infrared group.

def _remap(op: int, operand: int, actions: dict, modes: dict, groups: dict,
           sets: dict | None = None, values: dict | None = None, cas_op: int = 0x8F):
    """The operand after renumbering, or None when it names something deleted.
    `sets` renumbers key sets (0x1F 0xFFnn and 0xE8nn), `values` the
    CurrentActivityState values written by `cas_op` (0x8F on the 525)."""
    if op == 0x1F and sets and operand >> 8 in (0xFF, 0xE8):
        k = operand & 0xFF
        if k in sets:
            return None if sets[k] is None else (operand & 0xFF00) | sets[k]
        return operand
    if op == cas_op and values:
        if operand in values:
            return values[operand]
        return operand
    if op == 0x7F:
        return actions.get(operand, operand) if operand not in actions or actions[operand] is not None else None
    if op == 0x7E:
        return modes.get(operand, operand) if operand not in modes or modes[operand] is not None else None
    if op in (0x7C, 0x7D):
        g = operand >> 8
        if g in groups and groups[g] is None:
            return None
        return (groups.get(g, g) << 8) | (operand & 0xFF)
    return operand


def _transition_sites(blob: bytes, S) -> list[tuple[int, int, int]]:
    """(blob offset of the operand, opcode, operand) of every section 13 transition."""
    s13 = S[13]
    out = []
    for v in range(ir.u16(blob, s13)):
        record = ir.u24(blob, s13 + 8 + 3 * v) - BASE
        for t in range(ir.u16(blob, record + 4)):
            at = record + 7 + 8 * t
            out.append((at + 5, blob[at + 7], ir.u16(blob, at + 5)))
    return out


def _patch_bytes(d: _Doc, at: int, value: bytes) -> None:
    """Write `value` over the bytes at `at`, in whichever data regions hold
    them: the decompiler may cut a section 14 key in two, when the pointer
    after it starts with 0x16 and reads as a reference (2026-10-20)."""
    for k, byte in enumerate(value):
        pos = at + k
        holder = [r for r in d.regions if r["offset"] <= pos < r["offset"] + r["length"] and "data" in r]
        if len(holder) != 1:
            raise ComposeError(f"no single data region holds 0x{pos:X}")
        data = bytearray(bytes.fromhex("".join(holder[0]["data"])))
        data[pos - holder[0]["offset"]] = byte
        holder[0]["data"] = hconfig._hex_lines(bytes(data))


def _drop_span(d: _Doc, at: int, n: int, gone: set) -> None:
    """Take bytes [at, at + n) out, however the regions divide them: a region
    wholly inside goes, a data region running across an end keeps the rest."""
    end = at + n
    for r in d.regions:
        start, stop = r["offset"], r["offset"] + r["length"]
        if r["length"] == 0 or stop <= at or start >= end or r["id"] in gone:
            continue
        if at <= start and stop <= end:
            gone.add(r["id"])
            continue
        if "data" not in r:
            raise ComposeError(f"a {r['kind']} at 0x{start:X} runs across 0x{at:X}-0x{end:X}")
        data = bytes.fromhex("".join(r["data"]))
        keep = data[:max(0, at - start)] + data[min(len(data), end - start):]
        r["data"] = hconfig._hex_lines(keep)
        r["length"] = len(keep)


def _split_at(d: _Doc, at: int) -> dict:
    """Make a region boundary at byte `at` and return the region that ends
    there. A data region running across it is cut in two, and what pointed
    into its second part points into the new one, at the same byte."""
    for r in d.regions:
        if r["offset"] < at < r["offset"] + r["length"]:
            break
    else:
        return next(r for r in d.regions if r["offset"] + r["length"] == at and r["length"] > 0)
    if "data" not in r:
        raise ComposeError(f"0x{at:X} falls inside a {r['kind']}, which cannot be cut")
    data = bytes.fromhex("".join(r["data"]))
    cut = at - r["offset"]
    tail = {k: v for k, v in r.items() if k not in ("data", "offset", "length", "id")}
    tail.update(id=f"{r['id']}_from_{at:x}", offset=at, length=len(data) - cut,
                data=hconfig._hex_lines(data[cut:]))
    r["data"] = hconfig._hex_lines(data[:cut])
    r["length"] = cut
    d.regions.insert(d.index_of(r) + 1, tail)
    d.at.setdefault(at, []).append(tail)
    for other in d.regions:
        refs = list(other.get("targets") or [])
        if other.get("back_reference"):
            refs.append(other["back_reference"])
        for t in refs:
            if isinstance(t, dict) and t.get("to") == r["id"] and t.get("delta", 0) >= cut:
                t["to"] = tail["id"]
                t["delta"] = t.get("delta", 0) - cut
                if not t["delta"]:
                    del t["delta"]
    return r


def _apply_maps(d: _Doc, actions: dict, modes: dict, groups: dict, keep,
                sets: dict | None = None, values: dict | None = None) -> None:
    """Renumber every reference in the kept regions. A reference to something
    deleted is an error: the caller decided what goes and must have removed it."""
    def fix(op, operand, where):
        new = _remap(op, operand, actions, modes, groups, sets, values, d.cas_op)
        if new is None:
            raise ComposeError(f"{where} still names something being deleted (0x{op:02X} {operand})")
        return new
    for r in d.regions:
        if not keep(r):
            continue
        if r["kind"] == "action_list":
            for i in r["instructions"]:
                op = int(i["opcode"], 16)
                i["operand"] = fix(op, i["operand"], r["id"])
        elif r["kind"] == "tagged_list":
            for e in r["entries"]:
                e["operand"] = fix(e["opcode"], e["operand"], r["id"])
        elif r["kind"] == "screen_instruction" and r.get("opcode") == "0x11":
            raw = bytes.fromhex(r["operands"])
            if len(raw) == 3:
                operand = fix(raw[2], raw[0] | raw[1] << 8, r["id"])
                r["operands"] = (operand.to_bytes(2, "little") + raw[2:]).hex()
    for at, op, operand in _transition_sites(d.blob, d.S):
        new = fix(op, operand, f"section 13 transition at 0x{at:X}")
        if new != operand:
            _patch_bytes(d, at, new.to_bytes(2, "little"))


# ------------------------------------------------------------ delete_device ---

def _keep_shared_strings(d: _Doc, gone: set) -> None:
    """A screen often draws a string another screen stores: opcode 0x04 points
    at the text of some program's inline 0x05 draw. Deleting that draw would
    leave the others pointing at nothing. So a string something that stays still
    points at survives: its draw stays where it is, moved below the 64 rows of
    the screen so it draws nothing, or, when its whole program goes, the string
    is kept by itself among the regions that stay."""
    referenced = {t["to"] for r in d.regions if r["id"] not in gone
                  for t in (r.get("targets") or []) if t}
    by_id = {r["id"]: r for r in d.regions}
    programs_gone = {r["id"] for r in d.regions if r["id"] in gone and r["kind"] == "mode_page"}
    for r in list(d.regions):
        if r["kind"] != "glyph_string" or r["id"] not in gone or r["id"] not in referenced:
            continue
        draw = next((x for x in d.at.get(r["offset"] - 3, []) if x["kind"] == "screen_instruction"
                     and x.get("opcode") == "0x05"), None)
        gone.discard(r["id"])
        if draw is None:
            continue
        # is the program this draw sits in staying? its band markers tell: a
        # program that goes has every one of its regions in `gone`
        neighbours = [x for x in d.regions if x["kind"] == "screen_instruction"
                      and abs(x["offset"] - r["offset"]) < 8 and x["id"] != draw["id"]]
        program_stays = any(x["id"] not in gone for x in neighbours)
        if program_stays:
            gone.discard(draw["id"])
            ops = bytearray(bytes.fromhex(draw["operands"]))
            ops[1] = 0x60          # y 96: below the screen, so the draw shows nothing
            draw["operands"] = ops.hex()
        else:
            # the string alone, moved to the regions that stay, before section 0
            d.regions.remove(r)
            s0 = next(i for i, x in enumerate(d.regions) if x.get("section") == 0)
            d.regions.insert(s0, r)


def _detach_device(raw: bytes, mode: int) -> tuple[bytes, dict]:
    """Take a device out of everything outside its own mode, so delete_device
    can remove it: activities, key sets, the all-off list, power and input
    transitions.

    The device's state variables (its power, its inputs, whatever ends up
    sending to its group) stay in the table, because renumbering variables is
    a change this project has not made on a remote; instead nothing writes them
    any more, so their transitions never run, and those transitions are pointed
    at nothing. Every instruction that sends to the device is removed, an action
    list left with nothing is removed with its callers' references to it, and a
    key or soft key whose list went is set to the null binding Logitech uses
    for a key that does nothing. Screens are not touched: a soft key label for
    the device stays drawn and does nothing.
    """
    d = _Doc(raw)
    blob, S = d.blob, d.S
    found = [x for x in devices(raw) if x.mode == mode]
    if len(found) != 1 or found[0].group is None:
        raise ComposeError(f"mode {mode} is not a device on the Devices screens")
    group = found[0].group
    n = d.n_actions
    ins = {i: list(d.ins(i)) for i in range(n)}
    transitions: dict[int, list] = {}
    s13 = S[13]
    for v in range(ir.u16(blob, s13)):
        record = ir.u24(blob, s13 + 8 + 3 * v) - BASE
        for t in range(ir.u16(blob, record + 4)):
            at = record + 7 + 8 * t
            transitions.setdefault(v, []).append((at, blob[at + 7], ir.u16(blob, at + 5)))

    # the device's variables: whose transitions end up sending to it, or
    # writing a variable that does (a TV's input request writes its input)
    def reaches(op, operand, dvars, seen):
        if op in (0x7C, 0x7D):
            return operand >> 8 == group
        if 0x80 <= op < 0x100:
            return op - 0x80 in dvars
        if op == 0x7F and operand not in seen:
            seen.add(operand)
            return any(reaches(o, x, dvars, seen) for o, x in ins[operand])
        return False
    dvars: set[int] = set()
    grew = True
    while grew:
        grew = False
        for v, ts in transitions.items():
            if v not in dvars and any(reaches(op, x, dvars, set()) for _, op, x in ts):
                dvars.add(v)
                grew = True

    # what the device's own mode, pages and Devices entries use is delete_device's
    # (and of the modes that belong to it alone, which go with it)
    own, own_twins = set(), set()
    for m in [mode] + d.companion_modes(mode):
        f = d.mode(m)
        own |= {f["start"]} | {p["list"] for p in f["pages"]}
        own_twins |= {d.pool_copy(p["list"])["offset"] for p in f["pages"]}
    own_targets = {v for off in own for _, op, v in cmp.tagged(blob, off) if op == 0x7F}
    selections = {i for i in range(n) if d.selection_target(i) == mode}
    keep_whole = own_targets | selections

    # strip, then let emptiness propagate
    removed = {}
    for i in range(n):
        if i in keep_whole:
            continue
        before = ins[i]
        after = [(o, x) for o, x in before
                 if not (o in (0x7C, 0x7D) and x >> 8 == group) and not (0x80 <= o < 0x100 and o - 0x80 in dvars)]
        if after != before:
            removed[i] = len(before) - len(after)
            ins[i] = after
    emptied = {i for i in range(n) if not ins[i] and i not in keep_whole}
    # a list the device's own keys use may be used by an activity's key too
    # (Logitech shares one list for the same command): it stays whole for the
    # device, and every reference to it from outside goes like an emptied one's
    shared_own = {i for i in keep_whole if reaches(0x7F, i, dvars, set())}
    dead = emptied | shared_own
    grew = True
    while grew:
        grew = False
        for i in range(n):
            if i in keep_whole or i in emptied:
                continue
            after = [(o, x) for o, x in ins[i] if not (o == 0x7F and x in dead)]
            if after != ins[i]:
                ins[i] = after
                if not after:
                    emptied.add(i)
                    dead.add(i)
                grew = True

    # write it into the document
    for r in d.regions:
        if r["kind"] == "action_list" and r.get("section") != 8:
            i = next((k for k, a in enumerate(d.actions) if a == r["offset"]), None)
            if i is None or ins[i] == d.ins(i):
                continue
            if not ins[i]:
                r["instructions"] = [{"operand": x, "opcode": f"0x{o:02X}"} for o, x in d.ins(i)]
                continue          # left as it was: nothing references it, delete_device removes it
            r["instructions"] = [{"operand": x, "opcode": f"0x{o:02X}"} for o, x in ins[i]]
        elif r["kind"] == "action_list" and r.get("section") == 8:
            kept = [i for i in r["instructions"] if not (
                int(i["opcode"], 16) == 0x7F and i["operand"] in dead) and not (
                0x80 <= int(i["opcode"], 16) < 0x100 and int(i["opcode"], 16) - 0x80 in dvars)]
            if len(kept) != len(r["instructions"]):
                if not kept:
                    raise ComposeError("section 8's leading list would be left empty")
                r["instructions"] = kept
        elif r["kind"] == "tagged_list" and r["offset"] not in own | own_twins:
            for e in r["entries"]:
                if e["opcode"] == 0x7F and e["operand"] in dead:
                    e["opcode"], e["operand"] = 0, 0
        elif r["kind"] == "screen_instruction" and r.get("opcode") == "0x11":
            raw3 = bytes.fromhex(r["operands"])
            if len(raw3) == 3 and raw3[2] == 0x7F and (raw3[0] | raw3[1] << 8) in dead:
                raise ComposeError("a section 14 stream runs a list that would be left empty")
    neutralised = 0
    for v in dvars:
        for at, op, x in transitions[v]:
            if op != 0 or x != 0:
                _patch_bytes(d, at + 5, b"\x00\x00\x00")
                neutralised += 1
    for v, ts in transitions.items():
        if v in dvars:
            continue
        for at, op, x in ts:
            if op == 0x7F and x in dead:
                raise ComposeError(f"variable {v}'s transition runs a list that would be left empty")
            if (op in (0x7C, 0x7D) and x >> 8 == group) or (0x80 <= op < 0x100 and op - 0x80 in dvars):
                raise ComposeError(f"variable {v}'s transition sends to the device directly")
    out = d.compile()

    # checks: nothing outside the device's own lists sends to it or writes its
    # variables, and no screen moved
    after = _Doc(out)
    for i in range(after.n_actions):
        if i in keep_whole or i in emptied:
            continue
        for o, x in after.ins(i):
            if o in (0x7C, 0x7D) and x >> 8 == group or 0x80 <= o < 0x100 and o - 0x80 in dvars:
                raise AssertionError(f"action list {i} still reaches the device")
        if after.ins(i) != ins[i]:
            raise AssertionError(f"action list {i} is not what was meant")
    r0, r1 = render_map(blob), render_map(after.blob)
    if {k: v[0] for k, v in r0.items()} != {k: v[0] for k, v in r1.items()}:
        raise AssertionError("detaching changed a screen")
    _check_container(out)
    return out, {"variables": sorted(dvars), "lists_changed": len(removed),
                 "emptied": sorted(emptied), "transitions_neutralised": neutralised}


@_needs_counter_font
def delete_device(raw: bytes, mode: int) -> bytes:
    """Delete a device: its entry and name on every Devices screen (each
    activity's first page too), its mode and pages, its action lists and its
    infrared group, renumbering everything after them.

    A device activities use is first taken out of them by _detach_device: what
    sent to it sends nothing, its state variables are no longer written. A
    Devices page left empty is removed when it is the last one."""
    if mode <= 40:
        raise ComposeError("modes up to 40 are the firmware's status screens")
    original = raw
    raw, detached = _detach_device(raw, mode)
    d = _Doc(raw)
    blob, S = d.blob, d.S
    found = [x for x in devices(raw) if x.mode == mode]
    if len(found) != 1:
        raise ComposeError(f"mode {mode} is not a device on the Devices screens")
    device = found[0]
    group = device.group
    devices_modes = d.devices_modes()
    f = d.mode(mode)
    # the device's mode and any mode that belongs to it alone (issue #46)
    removed_modes = [mode] + d.companion_modes(mode)

    # ---- what goes: the action lists only this device reaches ----
    referrers: dict[int, set] = {}
    def note(src, op, operand):
        if op == 0x7F:
            referrers.setdefault(operand, set()).add(src)
    for i in range(d.n_actions):
        for op, operand in d.ins(i):
            note(("list", i), op, operand)
    for r in d.regions:
        if r["kind"] == "tagged_list":
            for e in r["entries"]:
                note(("tagged", r["offset"]), e["opcode"], e["operand"])
        elif r["kind"] == "action_list" and r.get("section") == 8:
            for i in r["instructions"]:
                note(("leading", r["offset"]), int(i["opcode"], 16), i["operand"])
        elif r["kind"] == "screen_instruction" and r.get("opcode") == "0x11":
            raw3 = bytes.fromhex(r["operands"])
            if len(raw3) == 3:
                note(("stream", r["offset"]), raw3[2], raw3[0] | raw3[1] << 8)
    for at, op, operand in _transition_sites(blob, S):
        note(("transition", at), op, operand)

    own_lists, pool_twins = set(), set()
    for m in removed_modes:
        g = d.mode(m)
        own_lists |= {g["start"]} | {p["list"] for p in g["pages"]}
        pool_twins |= {d.pool_copy(p["list"])["offset"] for p in g["pages"]}
    seeds = set()
    for off in own_lists:
        seeds |= {v for _, op, v in cmp.tagged(blob, off) if op == 0x7F}
    # every list that opens the device, wherever it is listed: each Devices
    # screen's own first page, the shared later pages, their pool copies' twins
    selections = {i for i in range(d.n_actions) if d.selection_target(i) == mode}
    # what _detach_device left empty and nothing references any more
    emptied = set(detached["emptied"])
    doomed = set(seeds | selections | emptied)
    changed = True
    while changed:
        changed = False
        for i in list(doomed):
            for op, operand in d.ins(i):
                if op == 0x7F and operand not in doomed:
                    doomed.add(operand)
                    changed = True
    # keep any list something that stays still reaches
    def staying_source(src):
        kind, where = src
        if kind == "list":
            return where not in doomed
        if kind == "tagged":
            return where not in own_lists and where not in pool_twins
        return True
    changed = True
    while changed:
        changed = False
        for i in list(doomed):
            if i in selections:
                continue          # every entry naming one is removed below
            if any(staying_source(s) for s in referrers.get(i, ())):
                doomed.discard(i)
                changed = True
    for i in range(d.n_actions):
        if i in doomed:
            continue
        for op, operand in d.ins(i):
            if op in (0x7C, 0x7D) and operand >> 8 == group:
                raise ComposeError(f"action list {i}, which stays, sends to this device's group "
                                   f"{group}: an activity or a transition uses it")
            if op == 0x7E and operand in removed_modes:
                raise ComposeError(f"action list {i}, which stays, enters mode {operand}")
    for at, op, operand in _transition_sites(blob, S):
        if op in (0x7C, 0x7D) and operand >> 8 == group:
            raise ComposeError(f"a section 13 transition sends to group {group}")

    # ---- renumbering maps ----
    keep_actions = [i for i in range(d.n_actions) if i not in doomed]
    actions = {i: None for i in doomed}
    actions.update({old: new for new, old in enumerate(keep_actions) if old != new})
    modes = {m: None for m in removed_modes}
    modes.update({m: m - sum(r < m for r in removed_modes)
                  for m in range(min(removed_modes) + 1, d.n_modes) if m not in removed_modes})
    n_groups = len(ir.ir_groups(blob, S))
    groups = {group: None, **{g: g - 1 for g in range(group + 1, n_groups)}}

    # ---- regions that go ----
    gone = set()
    def drop_program(root):
        for item in sem.screen_program_path(blob, root):
            gone.update(r["id"] for r in d.at[item["offset"]] if r["kind"] != "section")
            if item["opcode"] == 5:
                gone.update(r["id"] for r in d.at.get(item["offset"] + 3, []) if r["kind"] == "glyph_string")
    for g in map(d.mode, removed_modes):
        gone.add(d.region(g["entry"], "record_header")["id"])
        gone.add(d.region(g["start"], "tagged_list")["id"])
        for p in g["pages"]:
            gone.add(d.region(p["address"], "mode_page")["id"])
            gone.add(d.region(p["list"], "tagged_list")["id"])
            gone.add(d.pool_copy(p["list"])["id"])
            drop_program(p["program"])
    # file offsets already: a configuration past 0x20000 bytes (a 555 of seven
    # devices after a few edits) has lists there too
    action_regions = {d.actions[i]: i for i in range(d.n_actions)}
    for r in d.regions:
        if r["kind"] == "action_list" and action_regions.get(r["offset"]) in doomed:
            gone.add(r["id"])
    group_region = d.region(ir.u24(blob, S[5] + 1 + 3 * group) - BASE, "ir_group")
    gone.add(group_region["id"])

    # ---- every page that lists the device: the entry, its pool copy's, the name ----
    touched_modes = set()
    done = set()
    for m in range(d.n_modes):
        if m == mode:
            continue
        for p in d.mode(m)["pages"]:
            tags = [t for t, op, v in cmp.tagged(blob, p["list"]) if op == 0x7F and v in selections]
            if not tags:
                continue
            touched_modes.add(m)
            if p["list"] in done:
                continue          # a shared page, already done for another mode
            done.add(p["list"])
            plist = d.region(p["list"], "tagged_list")
            twin = d.pool_copy(p["list"])
            plist["entries"] = [e for e in plist["entries"] if e["tag"] not in tags]
            twin["entries"] = [e for e in twin["entries"] if e["tag"] not in tags]
            if not plist["entries"]:
                # the last device off the page (a 555 again empty): an empty
                # list carries the wide header, as the empty 555's does
                plist["wide"] = twin["wide"] = True
            for tag in tags:
                corner = SOFT.index(tag)
                rows = TOP_ROWS if corner < 2 else BOTTOM_ROWS
                right = corner in (1, 3)
                for item in sem.screen_program_path(blob, p["program"]):
                    if item["opcode"] in (4, 5) and item["operands"][1] in rows and \
                            (item["operands"][0] >= 48) == right:
                        gone.update(r["id"] for r in d.at[item["offset"]])
                        if item["opcode"] == 5:
                            gone.update(r["id"] for r in d.at.get(item["offset"] + 3, [])
                                        if r["kind"] == "glyph_string")
    for r in d.regions:                 # any other list naming one, a stray copy
        if r["kind"] == "tagged_list":
            r["entries"] = [e for e in r["entries"] if not (e["opcode"] == 0x7F and e["operand"] in selections)]

    # ---- a last shared Devices page it leaves empty goes ----
    first = d.idle_devices_mode()
    pages0 = d.mode(first)["pages"]
    page = pages0[-1]
    plist = d.region(page["list"], "tagged_list")
    twin = d.pool_copy(page["list"])
    shared_last = len(pages0) > 1 and all(
        d.mode(m)["pages"][-1]["address"] == page["address"] for m in devices_modes)
    n_pages = len(pages0)
    page_removed = shared_last and not plist["entries"]
    if page_removed:
        gone.update((plist["id"], twin["id"], d.region(page["address"], "mode_page")["id"]))
        drop_program(page["program"])
        record_id = d.region(page["address"], "mode_page")["id"]
        for m in devices_modes:
            e = d.region(d.mode(m)["entry"], "record_header")
            e["targets"] = [t for t in e["targets"] if t["to"] != record_id]
        n_pages -= 1
        totals = {}
        for m in devices_modes:
            for k, p in enumerate(d.mode(m)["pages"][:n_pages]):
                totals[p["program"]] = (k + 1, n_pages)
        if n_pages == 1:
            for root in totals:
                for item in sem.screen_program_path(blob, root):
                    if item["opcode"] == 5 and item["operands"][1] == 56 or (
                            item["opcode"] == 0x10 and bytes(item["operands"]) == b"\x04"):
                        gone.update(r["id"] for r in d.at[item["offset"]])
                        gone.update(r["id"] for r in d.at.get(item["offset"] + 3, [])
                                    if r["kind"] == "glyph_string")
        else:
            _set_counters(d, totals, f"del{mode}_counter")

    _keep_shared_strings(d, gone)

    # ---- tables ----
    mt = d.region(S[sem.MODE_SLOT], "pointer_table")
    for m in sorted(removed_modes, reverse=True):
        del mt["targets"][m]
    at_ = d.region(S[sem.ACTION_SLOT], "pointer_table")
    at_["targets"] = [t for i, t in enumerate(at_["targets"]) if i not in doomed]
    gt = _ir_table(d)
    del gt["targets"][group]

    _apply_maps(d, actions, modes, groups, keep=lambda r: r["id"] not in gone)

    # the group's records, and whatever only they reached: infrared regions that
    # become unreferenced now (ones nothing referenced before are left alone)
    ir_kinds = {"ir_record_header", "ir_class5_body", "ir_symbol_table", "ir_symbol_block"}
    def referenced_ids(regions):
        # an infrared record header points back at itself (4h), which is no
        # reason to keep it
        ids = {t["to"] for r in regions for t in (r.get("targets") or []) if t and t["to"] != r["id"]}
        return ids | {r["back_reference"]["to"] for r in regions
                      if r.get("back_reference") and r["back_reference"]["to"] != r["id"]}
    loose_before = {r["id"] for r in d.regions if r["kind"] in ir_kinds} - referenced_ids(d.regions)
    d.regions[:] = [r for r in d.regions if r["id"] not in gone]
    while True:
        live = referenced_ids(d.regions)
        orphans = {r["id"] for r in d.regions if r["kind"] in ir_kinds
                   and r["id"] not in live and r["id"] not in loose_before}
        if not orphans:
            break
        gone |= orphans
        d.regions[:] = [r for r in d.regions if r["id"] not in orphans]
    dangling = [(r["id"], t["to"]) for r in d.regions for t in (r.get("targets") or [])
                if t and t["to"] in gone]
    if dangling:
        raise ComposeError(f"still referenced after deletion: {dangling[:3]}")
    out = d.compile()

    # ---- checks ----
    after = _Doc(out)
    a = after.blob
    g0, g1 = ir.ir_groups(blob, S), ir.ir_groups(a, after.S)
    if [[class5_source(blob, x) for x in g] for i, g in enumerate(g0) if i != group] != \
            [[class5_source(a, x) for x in g] for g in g1]:
        raise AssertionError("a remaining infrared group changed")
    if after.n_actions != len(keep_actions):
        raise AssertionError("action count")
    for new, old in enumerate(keep_actions):
        want = [(op, _remap(op, v, actions, modes, groups)) for op, v in d.ins(old)]
        if after.ins(new) != want:
            raise AssertionError(f"action list {old} -> {new} changed")
    if after.n_modes != d.n_modes - len(removed_modes):
        raise AssertionError("mode count")
    for old in range(d.n_modes):
        if old in removed_modes:
            continue
        new = modes.get(old, old)
        f0, f1 = d.mode(old), after.mode(new)
        t0 = [(t, o, _remap(o, v, actions, modes, groups)) for t, o, v in cmp.tagged(blob, f0["start"])]
        if t0 != cmp.tagged(a, f1["start"]):
            raise AssertionError(f"mode {old} -> {new}: key list changed")
    r0, r1 = render_map(blob), render_map(a)
    devices_set = set(devices_modes) | touched_modes
    for (m, k), value in r0.items():
        if m in removed_modes or m in devices_set:
            continue
        if r1.get((modes.get(m, m), k), (None,))[0] != value[0]:
            raise AssertionError(f"screen of mode {m} page {k} changed")
    before_names = sorted((x.name, x.page, x.corner) for x in devices(raw) if x.mode != mode)
    after_names = sorted((x.name, x.page, x.corner) for x in devices(out))
    if before_names != after_names:
        raise AssertionError(f"device list {after_names} != {before_names}")
    _check_container(out)
    return out


# ------------------------------------------------------------- activities ---

@dataclass(frozen=True)
class Activity:
    name: str           # as the activity menu draws it
    value: int          # CurrentActivityState
    key_set: int        # section 9 list (operand low byte of 0x1F 0xFFnn)
    start: int          # start list (key set tag 0x01)
    repick: int | None  # list for picking it while it runs (tag 0x05)
    start_mode: int     # "Starting ..." screen
    work_mode: int      # the activity's own screen
    page: int
    corner: int


def _name_value_count(d: _Doc, var: int, top: int) -> None:
    """A state variable's name node ends in its value count, the highest value
    plus one: CurrentActivityState_0_4 (@dannybloe, config-format.md, the
    rule of his section 86)."""
    for r in d.region(d.S[0], "name_table")["records"]:
        if r["parent"] == 1 and r["index"] == var:
            stem, _, count = r["name"].rpartition("_")
            if stem and count.isdigit():
                r["name"] = f"{stem}_{top + 1}"


def _menu_mode(d: _Doc) -> int:
    """The activity menu: the mode whose soft keys select key sets."""
    hits = []
    for m in range(d.n_modes):
        for p in d.mode(m)["pages"]:
            for tag, op, v in cmp.tagged(d.blob, p["list"]):
                if op == 0x7F and tag in SOFT:
                    ins = d.ins(v)
                    if len(ins) == 2 and ins[0][0] == 0x1F and ins[0][1] >> 8 == 0xFF and ins[1] == (d.device_flag_op, 0):
                        hits.append(m)
    if not hits:
        # no activity yet: the menu is the one mode whose only key is 0xB7 run
        # through section 14 (mode 47 on the 525's, 52 on the 515's, 45 on the
        # 555's, and what section 14's first record enters on all three)
        hits = [m for m in range(d.n_modes)
                if [(t, o) for t, o, _ in cmp.tagged(d.blob, d.mode(m)["start"])] == [(0xB7, 0x72)]]
        if len(hits) != 1:
            raise ComposeError("no activity menu found")
    if len(set(hits)) != 1:
        raise ComposeError(f"several activity menus: {sorted(set(hits))}")
    return hits[0]


def _records_keyed_by(d: _Doc, var: int) -> set[int]:
    """Section 14 records some 0x72 looks up by state variable `var`: its
    operand is the record's index over the variable (FORMAT 5al), so 0x72 271
    on the 525 is record 1 by CurrentActivityState, variable 15."""
    b, out = d.blob, set()
    def add(o, x):
        if o == 0x72 and x & 0xFF == var:
            out.add(x >> 8)
    for i in range(d.n_actions):
        for o, x in d.ins(i):
            add(o, x)
    for k in range(b[d.S[9]]):
        for _, o, x in cmp.tagged(b, ir.u24(b, d.S[9] + 1 + 3 * k) - BASE):
            add(o, x)
    for m in range(d.n_modes):
        f = d.mode(m)
        for at in [f["start"]] + [p["list"] for p in f["pages"]]:
            for _, o, x in cmp.tagged(b, at):
                add(o, x)
    s11 = d.S[11]
    for k in range(ir.u16(b, s11)):
        at = ir.u24(b, s11 + 2 + 3 * k) - BASE
        if b[at] == 0x11:
            add(b[at + 3], ir.u16(b, at + 1))
    for _, o, x in _transition_sites(b, d.S):
        add(o, x)
    return out


def _chain_end(d: _Doc, index: int) -> tuple[int, ...]:
    """The modes the last list of a 0x7F chain enters (start chains end with
    [0x71 .., 0x7E status screen, 0x7E activity screen])."""
    seen = set()
    while True:
        ins = d.ins(index)
        tail = [v for o, v in ins if o == 0x7F]
        if ins and ins[-1][0] == 0x7F and ins[-1][1] not in seen:
            seen.add(index)
            index = ins[-1][1]
            continue
        return tuple(v for o, v in ins if o == 0x7E)


def activities(raw: bytes) -> list[Activity]:
    """The activities as the menu lists them."""
    d = _Doc(raw)
    menu = _menu_mode(d)
    out = []
    for page_index, page in enumerate(d.mode(menu)["pages"]):
        drawn = [(it["operands"][0], it["operands"][1], renderer_string(d, it))
                 for it in sem.screen_program_path(d.blob, page["program"]) if it["opcode"] in (4, 5)]
        for tag, op, v in cmp.tagged(d.blob, page["list"]):
            if op != 0x7F or tag not in SOFT:
                continue
            ins = d.ins(v)
            if not (len(ins) == 2 and ins[0][0] == 0x1F and ins[0][1] >> 8 == 0xFF):
                continue
            key_set = ins[0][1] & 0xFF
            at = ir.u24(d.blob, d.S[9] + 1 + 3 * key_set) - BASE
            entries = {t: (o, x) for t, o, x in cmp.tagged(d.blob, at)}
            start = entries[1][1]
            repick = entries[5][1] if 5 in entries and entries[5][0] == 0x7F else None
            start_ins = d.ins(start)
            values = [x for o, x in start_ins if o == d.cas_op]
            if len(values) != 1 or start_ins[0][0] != 0x7E:
                raise ComposeError(f"key set {key_set}'s start list is not the shape this knows")
            corner = SOFT.index(tag)
            rows = TOP_ROWS if corner < 2 else BOTTOM_ROWS
            lines = []
            for y in rows:
                hits = sorted({c for x, yy, c in drawn if yy == y and (x >= 48) == (corner in (1, 3))})
                lines += ["".join(d.alphabet.get(ch, "?") for ch in c) for c in hits]
            out.append(Activity(" ".join(lines), values[0], key_set, start, repick,
                                start_ins[0][1], _chain_end(d, start)[-1], page_index, corner))
    return out


def power_variables(raw: bytes) -> dict[str, int]:
    """Device name -> its power variable.

    The power variables are the ones the all-off list sets to 0 (@dannybloe's
    reading; on the public sample 17, 16 and 19), and a variable belongs to the
    device its 1 -> 0 transition sends to. A variable whose transitions also
    send, like a TV's picture toggle, is not taken for power."""
    d = _Doc(raw)
    by_group = {x.group: x.name for x in devices(raw)}
    transitions: dict[int, dict] = {}
    s13 = d.S[13]
    for v in range(ir.u16(d.blob, s13)):
        record = ir.u24(d.blob, s13 + 8 + 3 * v) - BASE
        for t in range(ir.u16(d.blob, record + 4)):
            at = record + 7 + 8 * t
            frm = int.from_bytes(d.blob[at + 1:at + 3], "little", signed=True)
            to = int.from_bytes(d.blob[at + 3:at + 5], "little", signed=True)
            transitions.setdefault(v, {})[(frm, to)] = (d.blob[at + 7], ir.u16(d.blob, at + 5))

    def sends(op, operand):
        stack, groups = [(op, operand)], set()
        while stack:
            o, x = stack.pop()
            if o == 0x7D:
                groups.add(x >> 8)
            elif o == 0x7F:
                stack.extend(d.ins(x))
        return groups

    candidates = []
    for i in range(d.n_actions):
        ins = d.ins(i)
        vars_ = [o - 0x80 for o, x in ins if 0x80 <= o < 0x100 and x == 0]
        if len(ins) >= 2 and len(vars_) == len(ins) and all(
                (1, 0) in transitions.get(v, {}) for v in vars_):
            candidates.append(vars_)
    if not candidates:
        return {}
    all_off = max(candidates, key=len)
    out = {}
    for v in all_off:
        groups = sends(*transitions[v][(1, 0)]) & set(by_group)
        if len(groups) == 1:
            out[by_group[groups.pop()]] = v
    return out


def _clone_program(d: _Doc, root: int, rid: str, replace) -> list[dict]:
    """Regions copying a screen program. replace(x, y, text) returns None to keep
    a string, False to drop it, or (x, y, codes) to draw instead."""
    out = []
    font = 0
    for seq, item in enumerate(sem.screen_program_path(d.blob, root)):
        op, ops = item["opcode"], item["operands"]
        name = f"{rid}_{seq:02d}"
        if op == 0x10:
            font = ops[0]
        if op == 3:
            out.append(fresh_region("screen_picture", name, coordinates=list(ops[:6]),
                                    targets=[symbol_for_offset(d.doc, ir.u24(ops, 6) - BASE)]))
            continue
        if op in (4, 5):
            codes = renderer_string(d, item)
            text = "".join(d.alphabet.get(c, "?") for c in codes)
            r = replace(ops[0], ops[1], text)
            if r is False:
                continue
            if r is not None:
                # new text is spelt for font 0 unless replace names its font
                # (x, y, codes, font); another font may lack its glyphs
                want = r[3] if len(r) > 3 else 0
                if font != want:
                    out.append(opaque(f"{name}_f0", bytes((0x10, want))))
                out.append(_draw(r[0], r[1], r[2], name))
                if font != want:
                    out.append(opaque(f"{name}_fb", bytes((0x10, font))))
                continue
            if op == 4:
                out.append(fresh_region("screen_reference", name, opcode="0x04", x=ops[0], y=ops[1],
                                        targets=[symbol_for_offset(d.doc, ir.u24(ops, 2) - BASE)]))
                continue
        out.append(opaque(name, bytes((op,)) + ops))
    return out


def device_commands(raw: bytes) -> dict:
    """(group, command) -> (device name, label), for every command a device's
    keys or soft keys send: the soft key's label as the screen draws it, or the
    525 key's name. What a person would call the command."""
    d = _Doc(raw)
    keys = {v: k for k, v in KEYS.items()}
    out: dict = {}
    for dev in devices(raw):
        f = d.mode(dev.mode)
        soft = []
        for p in f["pages"]:
            drawn = [(it["operands"][0], it["operands"][1], renderer_string(d, it))
                     for it in sem.screen_program_path(d.blob, p["program"]) if it["opcode"] in (4, 5)]
            for tag, op, v in cmp.tagged(d.blob, p["list"]):
                if op != 0x7F or tag not in SOFT:
                    continue
                c = SOFT.index(tag)
                rows = TOP_ROWS if c < 2 else BOTTOM_ROWS
                # one line per y: Logitech draws a line again in every band it crosses
                lines = {}
                for x, y, codes in sorted(drawn, key=lambda t: t[1]):
                    if y in rows and (x >= 48) == (c in (1, 3)):
                        lines.setdefault(y, "".join(d.alphabet.get(ch, "?") for ch in codes))
                words = list(lines.values())
                soft.append((v, " ".join(words) or f"soft key {c}"))
        phys = [(v, keys.get(tag, f"key 0x{tag:X}")) for tag, op, v in cmp.tagged(d.blob, f["start"])
                if op == 0x7F]
        for v, label in soft + phys:
            sends = [(o, x) for o, x in d.ins(v) if o == 0x7D]
            if len(sends) == 1:
                g, cmd = sends[0][1] >> 8, sends[0][1] & 0xFF
                out.setdefault((g, cmd), (dev.name, label))
    return out


def input_variables(raw: bytes) -> list[dict]:
    """The state variables an activity can set that are not power: a TV's
    input, say. [{variable, device, values: [{value, label}]}], a value's label
    being the commands its transitions send, by their names; a variable whose
    transitions send nothing is left out (the activity state)."""
    d = _Doc(raw)
    power = set(power_variables(raw).values())
    labels = device_commands(raw)
    by_group = {x.group: device_title(d, x) for x in devices(raw)}
    s13 = d.S[13]
    transitions: dict[int, list] = {}
    for v in range(ir.u16(d.blob, s13)):
        record = ir.u24(d.blob, s13 + 8 + 3 * v) - BASE
        for t in range(ir.u16(d.blob, record + 4)):
            at = record + 7 + 8 * t
            to = int.from_bytes(d.blob[at + 3:at + 5], "little", signed=True)
            transitions.setdefault(v, []).append((to, d.blob[at + 7], ir.u16(d.blob, at + 5)))

    def sends(o, x, seen):
        # what reaching a value sends, through action lists and through the
        # writes to other variables it makes (a TV input request, 5af)
        if o == 0x7D:
            return [(x >> 8, x & 0xFF)]
        if (o, x) in seen or len(seen) > 64:
            return []
        seen.add((o, x))
        if o == 0x7F:
            return [s for oo, xx in d.ins(x) for s in sends(oo, xx, seen)]
        if 0x80 <= o < 0x100 and o != d.cas_op and o - 0x80 not in power:
            return [s for to, oo, xx in transitions.get(o - 0x80, []) if to == x
                    for s in sends(oo, xx, seen)]
        return []

    out = []
    for v in sorted(transitions):
        if v in power or 0x80 + v == d.cas_op:
            continue
        sent: dict[int, list] = {}
        for to, o, x in transitions[v]:
            got = sends(o, x, set())
            if got:
                sent.setdefault(to, []).extend(got)
        if not sent:
            continue
        groups = {g for s in sent.values() for g, _ in s}
        values = []
        for value in sorted(sent):
            names = list(dict.fromkeys(labels.get(s, (None, f"command {s[1]}"))[1] for s in sent[value]))
            values.append({"value": value, "label": " + ".join(names)})
        device = by_group.get(next(iter(groups)), "?") if len(groups) == 1 else \
            " / ".join(sorted(by_group.get(g, "?") for g in groups))
        out.append({"variable": v, "device": device, "values": values})
    return out


def describe_activity(raw: bytes, name: str) -> list:
    """What an activity's start does, as steps a person can read: devices
    switched on and off, commands sent, with their names. Follows the state
    variables it writes through their transitions to the commands they send,
    which is how a TV gets to "Input Hdmi1" (FORMAT 5af)."""
    d = _Doc(raw)
    act = [a for a in activities(raw) if a.name == name]
    if len(act) != 1:
        raise ComposeError(f"no single activity named {name!r}")
    act = act[0]
    power = {v: n for n, v in power_variables(raw).items()}
    labels = device_commands(raw)
    by_group = {x.group: x.name for x in devices(raw)}
    transitions: dict = {}
    s13 = d.S[13]
    for v in range(ir.u16(d.blob, s13)):
        record = ir.u24(d.blob, s13 + 8 + 3 * v) - BASE
        for t in range(ir.u16(d.blob, record + 4)):
            at = record + 7 + 8 * t
            frm = int.from_bytes(d.blob[at + 1:at + 3], "little", signed=True)
            to = int.from_bytes(d.blob[at + 3:at + 5], "little", signed=True)
            transitions.setdefault(v, []).append((frm, to, d.blob[at + 7], ir.u16(d.blob, at + 5)))
    steps: list = []

    def send(operand):
        g, cmd = operand >> 8, operand & 0xFF
        dev, label = labels.get((g, cmd), (by_group.get(g, f"group {g}"), f"command {cmd}"))
        steps.append({"kind": "send", "device": dev, "label": label, "group": g, "command": cmd})

    def run(op, operand, depth=0, seen=None):
        seen = seen if seen is not None else set()
        if depth > 8:
            return
        if op == 0x7D:
            send(operand)
        elif op == 0x7F and operand not in seen:
            seen.add(operand)
            ins = d.ins(operand)
            if any(o in (0x3F, 0x71) for o, _ in ins):
                return            # the chain to the activity's screen
            if ins and all(o == 0x7C for o, _ in ins) and len({x >> 8 for _, x in ins}) == 1:
                g = ins[0][1] >> 8
                steps.append({"kind": "wait", "device": by_group.get(g, f"group {g}"), "group": g,
                              "seconds": sum(x & 0xFF for _, x in ins) / 10})
                return
            for o, x in ins:
                run(o, x, depth + 1, seen)
        elif 0x80 <= op < 0x100 and op != d.cas_op:
            v = op - 0x80
            if v in power:
                steps.append({"kind": "power", "device": power[v], "on": bool(operand)})
                return
            matched = [(o, x) for frm, to, o, x in transitions.get(v, []) if to == operand]
            if not matched:
                steps.append({"kind": "set", "variable": v, "value": operand})
            for o, x in matched:
                run(o, x, depth + 1, seen)
    for op, operand in d.ins(act.start):
        run(op, operand)
    return steps


@_needs_counter_font
def add_activity(raw: bytes, name: str, like: str, on: Sequence[str],
                 writes: dict | None = None, steps: Sequence = (),
                 show: int | None = None, volume: int | None = None,
                 keys: dict | None = None, _replacing: str | None = None) -> bytes:
    """Add an activity built like an existing one.

    It copies `like`'s key set (so its keys do what `like`'s do), its Devices
    screen, its start and status screens with the name changed, and its start
    chain, but switches on the devices in `on` and every other device with a
    power variable off, then makes the state writes in `writes` (variable ->
    value, e.g. a TV input request); None takes `like`'s own, so the input
    stays what it was. `steps` are (group, command) to send, in order, or
    ("wait", group, tenths) to hold that device's next command back. `show`, a device's mode, makes the activity end on that
    device's own screen, its keys then the device's (as KontroLIR does), instead
    of a copy of `like`'s screen. `volume`, a device's mode, sends Vol +, Vol -
    and Mute to that device while the activity's screen is up; with `show` the
    activity then ends on its own copy of the device's screen, so the device
    itself is left as it was. `keys`, key name -> a device's mode, does the
    same for any key: channels to one device, the cursor to another. The menu gets it in the first free corner,
    a new page when the menu is full. Value: the variable's highest plus one,
    idle unchanged (@dannybloe, section 273; FORMAT 5af)."""
    d = _Doc(raw)
    blob, S = d.blob, d.S
    acts = activities(raw)
    src = [x for x in acts if x.name == like]
    if len(src) != 1:
        raise ComposeError(f"no single activity named {like!r}: {[x.name for x in acts]}")
    src = src[0]
    if any(x.name == name and x.name != _replacing for x in acts):
        raise ComposeError(f"there is already an activity named {name!r}")
    power = power_variables(raw)
    unknown = [n for n in on if n not in power]
    if unknown:
        raise ComposeError(f"no power variable for {unknown}; devices with one: {sorted(power)}")
    if writes is None:
        # the source's own settings, an input most of all: every state write its
        # start list makes that is not a power switch or the activity number
        writes = {o - 0x80: x for o, x in d.ins(src.start)
                  if 0x80 <= o < 0x100 and o != d.cas_op and o - 0x80 not in power.values()}
    state_writes = [(power[n], 1 if n in on else 0) for n in sorted(power, key=lambda n: power[n])]
    state_writes += sorted(writes.items())
    for var, value in state_writes:
        if not 0 <= var < 0x80 - 0x10 or value > 0xFFFF:
            raise ComposeError(f"state write {var} := {value}")
    cas_var = d.cas_var
    s13 = S[13]
    cas_record = ir.u24(blob, s13 + 8 + 3 * cas_var) - BASE
    idle, top = ir.u16(blob, cas_record), ir.u16(blob, cas_record + 2)
    if sorted(x.value for x in acts) + [idle] != list(range(top + 1)) and len(acts) != top:
        raise ComposeError("the activity values are not 0..max with one idle value")
    value = top + 1
    menu = _menu_mode(d)
    n_sets = blob[S[9]]
    new_set = n_sets
    if new_set > 0xFF:
        raise ComposeError("key sets are numbered in one byte")

    # ---- new modes: start screen, work screen, and the status screens that return to it ----
    next_mode = d.n_modes
    mode_map = {}                       # old mode -> new mode, for the ones copied
    new_mode_regions, mode_copies, mode_lists_sec8 = [], [], []

    def returns_to(mode_index, target):
        f = d.mode(mode_index)
        return any(op == 0x7E and v == target for _, op, v in cmp.tagged(blob, f["start"]))

    title = d.text(name, limit=96)
    # a Starting screen's lines below the name are Stopping Activity's below
    # its own first line, in whatever words the configuration has them
    stop_lines = _screen_lines(d, _stop_mode(d))
    fixed = {"".join(d.alphabet.get(ch, "?") for ch in codes)
             for y, codes in stop_lines.items() if y != min(stop_lines)}

    def name_replace(kind):
        state = {"done": False}
        def replace(x, y, text):
            if kind == "work":
                return ((96 - d.width(title)) // 2, y, title) if y == 0 else None
            if kind == "start":
                if text in fixed:
                    return None
                if y == 0:
                    return None
                if not state["done"] or state["y"] == y:
                    state.update(done=True, y=y)
                    return ((96 - d.width(title)) // 2, y, title)
                return False
            return None
        return replace

    # a device's own modes (issue #46): a copy of its screen does not take the
    # keys into them, since they lead back to the device and not here
    companions = {c for x in devices(raw) for c in d.companion_modes(x.mode)}

    def copy_mode(old, kind):
        if old in mode_map:
            return mode_map[old]
        nonlocal next_mode
        new = next_mode
        next_mode += 1
        mode_map[old] = new
        f = d.mode(old)
        lst = d.region(f["start"], "tagged_list")
        entries = copy.deepcopy(lst["entries"])
        for e in entries:
            if e["opcode"] == 0x7E and e["operand"] in companions:
                e["opcode"], e["operand"] = 0, 0
        ml = fresh_region("tagged_list", f"act{value}_m{new}_list", wide=lst.get("wide", True),
                          entries=entries, **({"role": lst["role"]} if "role" in lst else {}))
        pages, programs = [], []
        for k, p in enumerate(f["pages"]):
            pl_src = d.region(p["list"], "tagged_list")
            pl = fresh_region("tagged_list", f"act{value}_m{new}_p{k}_list", section=8,
                              wide=pl_src.get("wide", False), entries=copy.deepcopy(pl_src["entries"]))
            prog = _clone_program(d, p["program"], f"act{value}_m{new}_p{k}", name_replace(kind))
            record = fresh_region("mode_page", f"act{value}_m{new}_p{k}",
                                  targets=[{"to": pl["id"]}, {"to": prog[0]["id"]}])
            programs += [*prog, record]
            pages.append(record)
            mode_lists_sec8.append(pl)
            mode_copies.append(fresh_region("tagged_list", f"act{value}_m{new}_p{k}_copy",
                                            wide=pl_src.get("wide", False), role="page_binding_copy",
                                            entries=copy.deepcopy(pl_src["entries"])))
        entry = fresh_region("record_header", f"act{value}_m{new}_entry",
                             back_reference={"to": ml["id"]}, targets=[{"to": r["id"]} for r in pages])
        new_mode_regions.extend([ml, *programs, entry])
        return new

    device_modes = {x.mode for x in devices(raw)}
    # keys of the activity's screen that send to another device: key -> mode;
    # `volume` is Vol +, Vol - and Mute at once
    routed: dict[int, int] = {}
    if volume is not None:
        routed.update({KEYS[k]: volume for k in ("VolumeUp", "VolumeDown", "Mute")})
    for k, m in (keys or {}).items():
        tag = KEYS[k] if isinstance(k, str) else int(k)
        if tag not in KEYS.values():
            raise ComposeError(f"{k} is not a key of the 525")
        routed[tag] = int(m)
    for m in {show, *routed.values()} - {None}:
        if m not in device_modes:
            raise ComposeError(f"mode {m} is not a device on the Devices screens")
    routed = {t: m for t, m in routed.items() if m != show}   # the device's own already
    if show is None:
        work = copy_mode(src.work_mode, "work")
    elif not routed:
        # every way back to the source's screen now leads to the device's
        mode_map[src.work_mode] = show
        work = show
    else:
        work = copy_mode(show, "device")
        mode_map[src.work_mode] = work
    if routed:
        # the activity's screen sends those keys where the activity says
        ml = next(r for r in new_mode_regions if r["id"] == f"act{value}_m{work}_list")
        mine = {e["tag"]: e for e in ml["entries"]}
        names = {v: k for k, v in KEYS.items()}
        for tag, m in sorted(routed.items()):
            theirs = {e["tag"]: e for e in d.region(d.mode(m)["start"], "tagged_list")["entries"]}
            if tag not in theirs:
                raise ComposeError(f"{names[tag]} does nothing on mode {m}'s screen")
            e = theirs[tag]
            if e["opcode"] == 0x7E and e["operand"] in companions:
                raise ComposeError(f"{names[tag]} on mode {m}'s screen enters a screen of that device's own")
            if tag in mine:
                mine[tag].update(operand=e["operand"], opcode=e["opcode"])
            else:
                ml["entries"].append({**({"flags": 0} if ml.get("wide") else {}),
                                      "tag": tag, "operand": e["operand"], "opcode": e["opcode"]})
    start_mode = copy_mode(src.start_mode, "start")
    # status screens a chain shows that come back to the source's screen
    def chain_modes(index, seen=None):
        seen = seen or set()
        out = set()
        for o, v in d.ins(index):
            if o == 0x7E:
                out.add(v)
            elif o == 0x7F and v not in seen:
                seen.add(v)
                out |= chain_modes(v, seen)
        return out
    status = {m for m in chain_modes(src.start) | (chain_modes(src.repick) if src.repick is not None else set())
              if m not in (src.work_mode, src.start_mode) and returns_to(m, src.work_mode)}
    for m in sorted(status):
        copy_mode(m, "status")
    # copied modes' keys that went back to the source's screen go to the new one
    for region in new_mode_regions:
        if region["kind"] == "tagged_list":
            for e in region["entries"]:
                if e["opcode"] == 0x7E and e["operand"] == src.work_mode:
                    e["operand"] = work

    # ---- action lists ----
    next_action = d.n_actions
    new_actions = []
    copied = {}

    def needs_copy(index, seen=None):
        seen = seen or set()
        for o, v in d.ins(index):
            if o == 0x7E and v in mode_map:
                return True
            if o == 0x7F and v not in seen:
                seen.add(v)
                if needs_copy(v, seen):
                    return True
        return False

    def copy_list(index):
        if index in copied:
            return copied[index]
        nonlocal next_action
        new = next_action
        next_action += 1
        copied[index] = new
        ins = []
        for o, v in d.ins(index):
            if o == 0x7E and v in mode_map:
                v = mode_map[v]
            elif o == 0x7F and needs_copy(v):
                v = copy_list(v)
            ins.append((o, v))
        new_actions.append((new, ins))
        return new

    def make_list(ins):
        nonlocal next_action
        new = next_action
        next_action += 1
        new_actions.append((new, ins))
        return new

    src_start = d.ins(src.start)
    tail = src_start[-1]
    if tail[0] != 0x7F:
        raise ComposeError("the source's start list does not end in its chain")
    # commands sent after the switching, in order: each its own list, a send
    # paired with that device's usual 0x7C as every Logitech send is (Danny,
    # section 278), called from the start the way Watch DVD calls its XBOX one
    n_groups = len(ir.ir_groups(blob, S))
    queue_delay = {}
    for i in range(d.n_actions):
        for o, x in d.ins(i):
            if o == 0x7C:
                queue_delay.setdefault(x >> 8, []).append(x & 0xFF)
    step_calls = []
    for step in steps:
        if step[0] == "wait":
            # a wait holds back that device's next commands, not the others'
            # (@dannybloe, sections 70 and 235): tenths of a second, 100 at
            # most per instruction, so longer is 100s and the rest
            g, tenths = int(step[1]), int(step[2])
            if not 0 <= g < n_groups or not 0 < tenths <= 450:
                raise ComposeError(f"a wait of {tenths / 10} s for group {g}")
            parts = [100] * ((tenths - 1) // 100) + [tenths - 100 * ((tenths - 1) // 100)]
            step_calls.append((0x7F, make_list([(0x7C, g << 8 | t) for t in parts])))
            continue
        g, cmd = int(step[0]), int(step[1])
        if not 0 <= g < n_groups or not 0 <= cmd < len(ir.ir_groups(blob, S)[g]):
            raise ComposeError(f"no command {cmd} in group {g}")
        delays = queue_delay.get(g, [1])
        qd = max(set(delays), key=delays.count)
        step_calls.append((0x7F, make_list([(0x7D, g << 8 | cmd), (0x7C, g << 8 | qd)])))
    start_ins = [(0x7E, start_mode)] + [(0x80 + var, val) for var, val in state_writes] + \
        step_calls + [(d.cas_op, value), (0x7F, copy_list(tail[1]) if needs_copy(tail[1]) else tail[1])]
    start_list = make_list(start_ins)
    repick_list = None
    if src.repick is not None:
        src_re = d.ins(src.repick)
        if src_re[-1][0] != 0x7F:
            raise ComposeError("the source's re-pick list does not end in its chain")
        re_ins = [(0x80 + var, val) for var, val in sorted(writes.items())]
        re_ins.append((0x7F, copy_list(src_re[-1][1]) if needs_copy(src_re[-1][1]) else src_re[-1][1]))
        repick_list = make_list(re_ins)
    menu_list_index = make_list([(0x1F, 0xFF00 | new_set), (d.device_flag_op, 0)])

    def action_region(index, ins):
        return fresh_region("action_list", f"act{value}_action_{index}",
                            instructions=[{"operand": v, "opcode": f"0x{o:02X}"} for o, v in ins])
    # ---- Help: its own tree, with this activity's devices and inputs ----
    help_entry, regs, ls, cs = _help_tree(
        d, f"act{value}_help", work, _help_questions(d, raw, on, steps, writes), next_mode, make_list)
    next_mode += sum(1 for r in regs if r["kind"] == "record_header")
    new_mode_regions.extend(regs)
    mode_lists_sec8.extend(ls)
    mode_copies.extend(cs)

    # copy_list numbers a list before its children and finishes it after them
    new_actions.sort()
    if [i for i, _ in new_actions] != list(range(d.n_actions, next_action)):
        raise AssertionError("new action lists are not numbered contiguously")
    action_regions = [action_region(i, ins) for i, ins in new_actions]

    # ---- section 9: the key set ----
    src_set_at = ir.u24(blob, S[9] + 1 + 3 * src.key_set) - BASE
    src_set = d.region(src_set_at, "tagged_list")
    set_entries = copy.deepcopy(src_set["entries"])
    for e in set_entries:
        if e["tag"] == 1:
            e["operand"] = start_list
        elif e["tag"] == 5 and repick_list is not None:
            e["operand"] = repick_list
        elif e["tag"] == 0xAE:
            e["operand"], e["opcode"] = help_entry, 0x7E
    set_region = fresh_region("tagged_list", f"act{value}_key_set", wide=src_set.get("wide", False),
                              role="section_9_binding", entries=set_entries)

    # ---- section 14: every record that knows the source's value gets the new one ----
    s11_table = d.region(S[11], "pointer_table")
    new_streams = []
    def stream(op, operand, tag):
        a = fresh_region("screen_instruction", f"act{value}_s11_{tag}", opcode="0x11",
                         operands=(operand.to_bytes(2, "little") + bytes((op,))).hex())
        z = fresh_region("screen_instruction", f"act{value}_s11_{tag}_end", opcode="0x00", operands="")
        new_streams.extend([a, z])
        s11_table["targets"].append({"to": a["id"]})
        return a["id"]
    made = {}
    s14 = S[14]
    for k in range(blob[s14]):
        record = ir.u24(blob, s14 + 1 + 3 * k) - BASE
        n = blob[record + 1]
        keys = {ir.u16(blob, record + 2 + 5 * j): ir.u24(blob, record + 4 + 5 * j) - BASE for j in range(n)}
        if value in keys:
            continue
        if src.value in keys:
            target = keys[src.value]
        else:
            # an item keyed below covers the source's value too (the idle
            # value first, 0, and the source 1): when what it runs is the
            # source's own, the new value needs an item of its own, or it
            # runs the source's (found 2026-10-20)
            below = [x for x in keys if x < src.value]
            if not below:
                continue
            target = keys[max(below)]
            if not (blob[target] == 0x11 and (
                    (blob[target + 3] == 0x1F and ir.u16(blob, target + 1) == 0xFF00 | src.key_set)
                    or (blob[target + 3] == 0x7E and ir.u16(blob, target + 1) in mode_map))):
                continue
        op, operand = blob[target + 3], ir.u16(blob, target + 1)
        if blob[target] != 0x11:
            raise ComposeError(f"section 14 record {k} points at something that is not a stream")
        if op == 0x7E and operand in mode_map:
            sid = made.setdefault(("7e", mode_map[operand]), stream(0x7E, mode_map[operand], f"m{mode_map[operand]}"))
        elif op == 0x1F and operand == 0xFF00 | src.key_set:
            sid = made.setdefault(("1f", new_set), stream(0x1F, 0xFF00 | new_set, f"set{new_set}"))
        elif op == 0x7F and needs_copy(operand):
            raise ComposeError(f"section 14 record {k} runs a list that would need copying")
        else:
            sid = next(r for r in d.at[target] if r["kind"] == "screen_instruction")["id"]
        head = next(r for r in d.regions if "data" in r and r["offset"] <= record + 1 < r["offset"] + r["length"])
        data = bytearray(bytes.fromhex("".join(head["data"])))
        data[record + 1 - head["offset"]] = n + 1
        head["data"] = hconfig._hex_lines(bytes(data))
        last_ptr = record + 4 + 5 * (n - 1)
        i = d.index_of(next(r for r in d.at[last_ptr] if r["kind"] == "raw_pointer"))
        d.regions[i + 1:i + 1] = [opaque(f"act{value}_s14_{k}_key", value.to_bytes(2, "little")),
                                  fresh_region("raw_pointer", f"act{value}_s14_{k}_ptr", targets=[{"to": sid}])]
    if new_streams:
        last = max(i for i, r in enumerate(d.regions) if r["kind"] == "screen_instruction"
                   and r.get("opcode") in ("0x11", "0x00") and S[11] > r["offset"] and any(
                       t["to"] == r["id"] for t in s11_table["targets"][:ir.u16(blob, S[11])]))
        d.regions[last + 2:last + 2] = new_streams

    # ---- CurrentActivityState's highest value ----
    _patch_bytes(d, cas_record + 2, value.to_bytes(2, "little"))
    _name_value_count(d, cas_var, value)

    # ---- the menu: first free corner, or a new page ----
    menu_pages = d.mode(menu)["pages"]
    corner, page = None, None
    for k, p in enumerate(menu_pages):
        used = {t for t, _, _ in cmp.tagged(blob, p["list"])}
        free = [i for i, tag in enumerate(SOFT) if tag not in used]
        if free:
            corner, page = free[0], k
            break
    entry_select = {"tag": SOFT[corner if corner is not None else 0], "operand": menu_list_index, "opcode": 0x7F}
    menu_regions = []
    if corner is not None:
        p = menu_pages[page]
        twin = d.pool_copy(p["list"])
        d.region(p["list"], "tagged_list")["entries"].append(dict(entry_select))
        twin["entries"].append(dict(entry_select))
        ends = d.band_ends(p["program"])
        fonts_at = _fonts_at_band_ends(d, p["program"])
        draws = _corner_draws(d, corner, name, f"act{value}_menu")
        for band in sorted(draws, reverse=True):
            d.insert_before(ends[band], _in_font_0(draws[band], fonts_at[band], f"act{value}_b{band}"))
    else:
        corner, page = 0, len(menu_pages)
        if page >= 6:
            raise ComposeError("the activity menu would get a seventh page")
        program = _blank_devices_page(d, menu_pages[0]["program"], f"act{value}_menu_screen")
        fonts_at = _program_fonts(program)
        _insert_in_program(program, {band: _in_font_0(regs, fonts_at.get(band, 0), f"act{value}_new_b{band}")
                                     for band, regs in _corner_draws(d, 0, name, f"act{value}_menu").items()})
        _insert_in_program(program, {7: [
            opaque(f"act{value}_menu_cf", b"\x10\x04"),
            _draw(35, 56, bytes((_digit(d, page + 1),)), f"act{value}_menu_cc"),
            _draw(47, 56, _of(d), f"act{value}_menu_co"),
            _draw(65, 56, bytes((_digit(d, page + 1),)), f"act{value}_menu_ct")]})
        mlist = fresh_region("tagged_list", f"act{value}_menu_list", section=8, wide=False,
                             entries=[entry_select])
        record = fresh_region("mode_page", f"act{value}_menu_page",
                              targets=[{"to": mlist["id"]}, {"to": program[0]["id"]}])
        menu_regions = [*program, record]
        mode_lists_sec8.append(mlist)
        d.region(d.mode(menu)["entry"], "record_header")["targets"].append({"to": record["id"]})
        after = d.pool_copy(menu_pages[-1]["list"])
        d.regions.insert(d.index_of(after) + 1,
                         fresh_region("tagged_list", f"act{value}_menu_copy", wide=False,
                                      role="page_binding_copy", entries=[dict(entry_select)]))
        _set_counters(d, {p["program"]: (k + 1, page + 1) for k, p in enumerate(menu_pages)},
                      f"act{value}_menu_counter")

    # ---- tables and placement ----
    d.region(S[sem.MODE_SLOT], "pointer_table")["targets"].extend(
        {"to": r["id"]} for r in new_mode_regions if r["kind"] == "record_header")
    d.region(S[sem.ACTION_SLOT], "pointer_table")["targets"].extend({"to": r["id"]} for r in action_regions)
    d.region(S[9], "pointer_table")["targets"].append({"to": set_region["id"]})
    last_set = ir.u24(blob, S[9] + 1 + 3 * (n_sets - 1)) - BASE
    d.regions.insert(d.index_of(d.region(last_set, "tagged_list")) + 1, set_region)
    first_set = ir.u24(blob, S[9] + 1 + 3 * 4) - BASE
    i4 = d.index_of(d.region(first_set, "tagged_list"))
    d.regions[i4:i4] = mode_copies
    s9 = next(i for i, r in enumerate(d.regions) if r.get("section") == 9)
    d.regions[s9:s9] = mode_lists_sec8
    s0 = next(i for i, r in enumerate(d.regions) if r.get("section") == 0)
    d.regions[s0:s0] = [*action_regions, *new_mode_regions, *menu_regions]
    out = d.compile()

    # ---- checks ----
    after_doc = _Doc(out)
    a = after_doc.blob
    if not all(d.ins(i) == after_doc.ins(i) for i in range(d.n_actions)):
        raise AssertionError("an existing action list changed")
    for i, ins in new_actions:
        if after_doc.ins(i) != ins:
            raise AssertionError(f"new action list {i}")
    # found by its value: a long name is drawn cut short ("A very lon..")
    got = [x for x in activities(out) if x.value == value]
    if len(got) != 1:
        raise AssertionError(f"the menu does not list {name!r}: {[x.name for x in activities(out)]}")
    got = got[0]
    if (got.value, got.key_set, got.start, got.start_mode, got.work_mode) != \
            (value, new_set, start_list, start_mode, work):
        raise AssertionError(f"activity reads back as {got}")
    for k in range(a[after_doc.S[14]]):
        record = ir.u24(a, after_doc.S[14] + 1 + 3 * k) - BASE
        keys = {ir.u16(a, record + 2 + 5 * j) for j in range(a[record + 1])}
        # the new value may have an item where the source is covered by one
        # keyed below it (that item runs the source's own)
        if src.value in keys and value not in keys:
            raise AssertionError(f"section 14 record {k} knows one value and not the other")
    old = {x.value: x for x in acts}
    for x in activities(out):
        if x.value != value and x != old[x.value]:
            raise AssertionError(f"activity {x.name} changed")
    r0, r1 = render_map(blob), render_map(a)
    changed = [k for k in r0 if k[0] != menu and r0[k][0] != r1[k][0]]
    if changed:
        raise AssertionError(f"screens changed that should not have: {changed}")
    _check_container(out)
    return out


def _drop_counters(d: _Doc, roots, gone: set) -> None:
    for root in roots:
        for item in sem.screen_program_path(d.blob, root):
            if item["opcode"] == 5 and item["operands"][1] == 56 or (
                    item["opcode"] == 0x10 and bytes(item["operands"]) == b"\x04"):
                gone.update(r["id"] for r in d.at[item["offset"]])
                gone.update(r["id"] for r in d.at.get(item["offset"] + 3, []) if r["kind"] == "glyph_string")


def _drop_program(d: _Doc, root: int, gone: set) -> None:
    for item in sem.screen_program_path(d.blob, root):
        gone.update(r["id"] for r in d.at[item["offset"]] if r["kind"] != "section")
        if item["opcode"] == 5:
            gone.update(r["id"] for r in d.at.get(item["offset"] + 3, []) if r["kind"] == "glyph_string")


def _drop_corner(d: _Doc, page: dict, corner: int, gone: set) -> None:
    """Take a corner's entry and drawn name off a two-line corner page."""
    tag = SOFT[corner]
    plist = d.region(page["list"], "tagged_list")
    twin = d.pool_copy(page["list"])
    plist["entries"] = [e for e in plist["entries"] if e["tag"] != tag]
    twin["entries"] = [e for e in twin["entries"] if e["tag"] != tag]
    if not plist["entries"]:
        # an empty list carries the wide header, as the empty 555's menu does
        plist["wide"] = twin["wide"] = True
    rows = TOP_ROWS if corner < 2 else BOTTOM_ROWS
    right = corner in (1, 3)
    for item in sem.screen_program_path(d.blob, page["program"]):
        if item["opcode"] in (4, 5) and item["operands"][1] in rows and (item["operands"][0] >= 48) == right:
            gone.update(r["id"] for r in d.at[item["offset"]])
            if item["opcode"] == 5:
                gone.update(r["id"] for r in d.at.get(item["offset"] + 3, []) if r["kind"] == "glyph_string")


@_needs_counter_font
def delete_activity(raw: bytes, name: str, value: int | None = None) -> bytes:
    """Delete an activity: its menu entry and name, key set, start, re-pick and
    menu lists and whatever only they reach, the screens only it shows, and its
    CurrentActivityState value, renumbering later action lists, modes, key
    sets and values. A last menu page it leaves empty goes too. `value` picks
    one of two activities of the same name (edit_activity's moment)."""
    d = _Doc(raw)
    blob, S = d.blob, d.S
    acts = activities(raw)
    hit = [a for a in acts if a.name == name and (value is None or a.value == value)]
    if len(hit) != 1:
        raise ComposeError(f"no single activity named {name!r}: {[a.name for a in acts]}")
    act = hit[0]
    menu = _menu_mode(d)
    cas_var = d.cas_var
    cas_record = ir.u24(blob, S[13] + 8 + 3 * cas_var) - BASE
    idle, top = ir.u16(blob, cas_record), ir.u16(blob, cas_record + 2)
    v, k = act.value, act.key_set
    values = {x: x - 1 for x in range(v + 1, top + 1)}
    values[v] = None
    n_sets = blob[S[9]]
    sets = {k: None, **{j: j - 1 for j in range(k + 1, n_sets)}}

    # ---- the lists only this activity reaches ----
    menu_pages = d.mode(menu)["pages"]
    page = menu_pages[act.page]
    menu_select = {t: x for t, o, x in cmp.tagged(blob, page["list"])}[SOFT[act.corner]]
    set_at = ir.u24(blob, S[9] + 1 + 3 * k) - BASE
    set_entries = {t: (o, x) for t, o, x in cmp.tagged(blob, set_at)}
    # Logitech's pool copy of a menu page has lists of its own, twins of the page's
    twin_select = {e["tag"]: e for e in d.pool_copy(page["list"])["entries"]}.get(SOFT[act.corner])
    seeds = {menu_select, act.start} | ({act.repick} if act.repick is not None else set())
    if twin_select is not None and twin_select["opcode"] == 0x7F:
        seeds.add(twin_select["operand"])
    # its Devices screen, which a Logitech activity has of its own (5ae) and a
    # composed one shares with the activity it was built like: what 0xAF enters
    # in its key set goes when nothing else enters it, or the screen is left
    # behind, still sharing the later Devices pages, and the next device added
    # to one of them changes it (found 2026-10-20: an edited Watch TV +, then
    # deleted, then a device added)
    devices_entry = set_entries.get(0xAF)
    if devices_entry is not None and devices_entry[0] == 0x7F:
        seeds.add(devices_entry[1])
    # its Help: the screens its key set enters directly (0xAE), and every
    # screen and list those reach, up to a device's, an activity's or the
    # system's own (FORMAT 5am); what something else also reaches stays
    stop = set(d.devices_modes()) | {x.mode for x in devices(raw)} | \
        {a.work_mode for a in acts} | {a.start_mode for a in acts} | {menu}
    help_modes, help_lists = set(), set()
    frontier = [x for o, x in set_entries.values() if o == 0x7E]

    def walk(i):
        if i in help_lists:
            return
        help_lists.add(i)
        for o, x in d.ins(i):
            if o == 0x7E:
                frontier.append(x)
            elif o == 0x7F:
                walk(x)
    while frontier:
        m = frontier.pop()
        if m in help_modes or m in stop or m <= 40:
            continue
        help_modes.add(m)
        f = d.mode(m)
        # the pool copy of a page can hold twin lists of its own (5ai), so it
        # is walked as well as the page list
        for entries in [d.region(f["start"], "tagged_list")["entries"]] + [
                r["entries"] for p in f["pages"]
                for r in (d.region(p["list"], "tagged_list"), d.pool_copy(p["list"]))]:
            for e in entries:
                if e["opcode"] == 0x7E:
                    frontier.append(e["operand"])
                elif e["opcode"] == 0x7F:
                    walk(e["operand"])
    help_own = set()
    for m in help_modes:
        f = d.mode(m)
        help_own.add(f["start"])
        for p in f["pages"]:
            help_own |= {p["list"], d.pool_copy(p["list"])["offset"]}
    seeds |= help_lists
    # a list that selects its key set and that nothing names: the twins of
    # the menu page's pool copy, orphaned by a menu rewrite before
    # _apply_corner_layout kept them (2026-10-20, the owner's own config)
    named = {x for j in range(d.n_actions) for o, x in d.ins(j) if o == 0x7F}
    for r in d.regions:
        if r["kind"] == "tagged_list":
            named |= {e["operand"] for e in r["entries"] if e["opcode"] == 0x7F}
        elif r["kind"] == "action_list" and r.get("section") == 8:
            named |= {x["operand"] for x in r["instructions"] if int(x["opcode"], 16) == 0x7F}
    named |= {x for at, o, x in _transition_sites(blob, S) if o == 0x7F}
    for j in range(n_sets):
        named |= {x for t, o, x in cmp.tagged(blob, ir.u24(blob, S[9] + 1 + 3 * j) - BASE) if o == 0x7F}
    named |= {a.start for a in acts} | {a.repick for a in acts if a.repick is not None}
    seeds |= {j for j in range(d.n_actions) if j not in named and any(
        o == 0x1F and x >> 8 in (0xFF, 0xE8) and x & 0xFF == k for o, x in d.ins(j))}
    doomed = set(seeds)
    grow = True
    while grow:
        grow = False
        for i in list(doomed):
            for o, x in d.ins(i):
                if o == 0x7F and x not in doomed:
                    doomed.add(x)
                    grow = True
    menu_twin = d.pool_copy(page["list"])["offset"]
    # who names each list, outside what goes
    def outside_refs(i):
        for j in range(d.n_actions):
            if j not in doomed and (0x7F, i) in d.ins(j):
                return True
        for r in d.regions:
            if r["kind"] == "tagged_list" and r["offset"] not in {set_at, page["list"], menu_twin} | help_own:
                if any(e["opcode"] == 0x7F and e["operand"] == i for e in r["entries"]):
                    return True
            if r["kind"] == "action_list" and r.get("section") == 8:
                if any(int(x["opcode"], 16) == 0x7F and x["operand"] == i for x in r["instructions"]):
                    return True
        for at, o, x in _transition_sites(blob, S):
            if o == 0x7F and x == i:
                return True
        return False
    # the menu page's own entry for this activity goes; other entries of it stay
    shrink = True
    while shrink:
        shrink = False
        for i in list(doomed):
            if outside_refs(i):
                doomed.discard(i)
                shrink = True
    if menu_select not in doomed or act.start not in doomed:
        raise ComposeError("the activity's menu or start list is used elsewhere")

    # ---- the screens only it shows ----
    candidate_modes = {x for i in doomed for o, x in d.ins(i) if o == 0x7E} | help_modes
    s14 = S[14]
    items = []                                     # (record, key, item offset, stream offset)
    for r_i in range(blob[s14]):
        rec = ir.u24(blob, s14 + 1 + 3 * r_i) - BASE
        for j in range(blob[rec + 1]):
            items.append((r_i, ir.u16(blob, rec + 2 + 5 * j), rec + 2 + 5 * j,
                          ir.u24(blob, rec + 4 + 5 * j) - BASE))
    # An item covers its key up to the next one: 0x72 tries the exact key, then
    # the ranges (4o). A record whose first key is v: alone, it covers every
    # value and stays; followed by v + 1, that item's stream moves onto the
    # first and the second goes; followed by more, it still covers v + 1 on.
    # And a record looked up by the activity state that holds the last
    # activity's value alone is emptied: the 555's, with no activity, has one
    # such record with no item at all (FORMAT 5al).
    shifted, kept_first, emptied, replaced = {}, set(), set(), set()
    by_state = _records_keyed_by(d, cas_var)
    def own_set(stream):
        """The stream picks this activity's key set."""
        return blob[stream + 3] == 0x1F and blob[stream + 2] in (0xFF, 0xE8) and blob[stream + 1] == k
    for r_i in sorted({r for r, *_ in items}):
        mine = [(key, at, stream) for rr, key, at, stream in items if rr == r_i]
        below = [j for j, x in enumerate(mine) if x[0] < v]
        cover = below[-1] if below and not any(x[0] == v for x in mine) else None
        if len(mine) == 1 and len(acts) == 1 and r_i in by_state and own_set(mine[0][2]):
            # the last activity, whose value is above an idle one the item is
            # keyed by (the owner's config, idle 0 and the activity 1)
            emptied.add(r_i)
        elif cover is not None and own_set(mine[cover][2]) and cover + 1 < len(mine):
            # no item of its own: one keyed below covers it (the idle value
            # first, 0, and this activity 1) and picks its key set; the next
            # item's stream moves onto that one, as below
            shifted[r_i] = (mine[cover][1], mine[cover + 1])
            replaced.add((r_i, mine[cover][0]))
        elif mine[0][0] == v:
            # followed by more, but its stream picks this activity's key set:
            # the values up to the next key are the idle one, not another
            # activity (an idle value between two activities, the owner's
            # config after Watch TV + was deleted and added again), so the
            # next item's stream moves onto the first as well
            if len(mine) > 1 and (mine[1][0] == v + 1 or own_set(mine[0][2])):
                shifted[r_i] = (mine[0][1], mine[1])
            elif len(mine) == 1 and len(acts) == 1 and r_i in by_state:
                emptied.add(r_i)
            else:
                kept_first.add(r_i)
    gone_streams = set()

    def item_goes(rr, kk):
        return (kk == v or rr in emptied or (rr, kk) in replaced) and rr not in kept_first
    for r_i, key, at, stream in items:
        if item_goes(r_i, key) and not any(
                s == stream and not item_goes(rr, kk) for rr, kk, _, s in items):
            gone_streams.add(stream)
    for stream in gone_streams:
        if blob[stream + 3] == 0x7E:
            candidate_modes.add(ir.u16(blob, stream + 1))
    def mode_named_elsewhere(m):
        for j in range(d.n_actions):
            if j not in doomed and (0x7E, m) in d.ins(j):
                return True
        own = {d.mode(x)["start"] for x in candidate_modes} | {
            p["list"] for x in candidate_modes for p in d.mode(x)["pages"]} | {
            d.pool_copy(p["list"])["offset"] for x in candidate_modes for p in d.mode(x)["pages"]}
        for r in d.regions:
            if r["kind"] == "tagged_list" and r["offset"] not in own | {set_at}:
                if any(e["opcode"] == 0x7E and e["operand"] == m for e in r["entries"]):
                    return True
            if r["kind"] == "screen_instruction" and r.get("opcode") == "0x11" and r["offset"] not in gone_streams:
                raw3 = bytes.fromhex(r["operands"])
                if len(raw3) == 3 and raw3[2] == 0x7E and raw3[0] | raw3[1] << 8 == m:
                    return True
        return False
    doomed_modes = set(candidate_modes)
    shrink = True
    while shrink:
        shrink = False
        for m in sorted(doomed_modes):
            if mode_named_elsewhere(m):
                doomed_modes.discard(m)
                shrink = True
    if any(m <= 40 for m in doomed_modes):
        raise ComposeError("would delete a firmware status screen")

    # ---- maps ----
    keep_actions = [i for i in range(d.n_actions) if i not in doomed]
    actions = {i: None for i in doomed}
    actions.update({old: new for new, old in enumerate(keep_actions) if old != new})
    keep_modes = [m for m in range(d.n_modes) if m not in doomed_modes]
    modes = {m: None for m in doomed_modes}
    modes.update({old: new for new, old in enumerate(keep_modes) if old != new})

    # ---- regions that go ----
    gone = set()
    # a page another screen that stays also shows (the later Devices pages are
    # one record for every Devices screen) stays with it
    kept_pages = {p["address"] for m in range(d.n_modes) if m not in doomed_modes for p in d.mode(m)["pages"]}
    for m in doomed_modes:
        f = d.mode(m)
        gone.add(d.region(f["entry"], "record_header")["id"])
        gone.add(d.region(f["start"], "tagged_list")["id"])
        for p in f["pages"]:
            if p["address"] in kept_pages:
                continue
            gone.add(d.region(p["address"], "mode_page")["id"])
            gone.add(d.region(p["list"], "tagged_list")["id"])
            gone.add(d.pool_copy(p["list"])["id"])
            _drop_program(d, p["program"], gone)
    # The pool holds one copy per page in the order the mode table first shows
    # each page (hconfig.arch9_record_body_regions). A shared page first shown
    # by a screen that goes is first shown later now, so its copy moves to
    # follow the copy of the page that comes before it in the new order.
    def first_shown(modes_):
        out = []
        for m in modes_:
            for p in d.mode(m)["pages"]:
                if p["address"] not in out:
                    out.append(p["address"])
        return out
    old_order = first_shown(range(d.n_modes))
    new_order = first_shown(keep_modes)
    lists_of = {p["address"]: p["list"] for m in range(d.n_modes) for p in d.mode(m)["pages"]}
    # Compared with where the copies are now, not with the old order: a run of
    # shared pages (Devices pages 2 and 3) moves as a whole, and the second
    # follows the first to its new place (found 2026-10-20, three pages)
    def copy_before(region):
        i = d.index_of(region)
        return next((r for r in reversed(d.regions[:i])
                     if r.get("role") == "page_binding_copy" and r["id"] not in gone), None)
    for at_k, page_at in enumerate(new_order):
        if at_k == 0 or old_order.index(page_at) == 0:
            continue
        copy_region = d.pool_copy(lists_of[page_at])
        after = d.pool_copy(lists_of[new_order[at_k - 1]])
        if copy_before(copy_region) is after:
            continue
        d.regions.remove(copy_region)
        d.regions.insert(d.index_of(after) + 1, copy_region)
    for r in d.regions:
        if r["kind"] == "action_list" and r.get("section") != 8:
            idx = next((i for i, a in enumerate(d.actions) if a == r["offset"]), None)
            if idx in doomed:
                gone.add(r["id"])
    gone.add(d.region(set_at, "tagged_list")["id"])
    s11 = d.region(S[11], "pointer_table")
    for stream in gone_streams:
        reg = next(r for r in d.at[stream] if r["kind"] == "screen_instruction")
        end = next(r for r in d.at[stream + 4] if r["kind"] == "screen_instruction")
        gone.update((reg["id"], end["id"]))
        s11["targets"] = [t for t in s11["targets"] if t["to"] != reg["id"]]
    # section 14: drop key v, lower the keys above it
    for r_i in sorted({r for r, *_ in items}):
        rec = ir.u24(blob, s14 + 1 + 3 * r_i) - BASE
        mine = [(key, at) for rr, key, at, _ in items if rr == r_i]
        # the keys above v one lower first: taking an item out shortens the
        # regions that hold the ones after it
        for key, at in mine:
            if key > v and key in values:
                _patch_bytes(d, at, values[key].to_bytes(2, "little"))
        # an item is five bytes, a u16 key and a u24 pointer, however the
        # decompiler divided them into regions
        if r_i in emptied:
            _patch_bytes(d, rec + 1, bytes((0,)))
            _drop_span(d, rec + 2, 5, gone)
        elif r_i in shifted:
            at0, (_, at1, _) = shifted[r_i]
            first = next(r for r in d.at[at0 + 2] if r["kind"] == "raw_pointer")
            first["targets"] = copy.deepcopy(next(r for r in d.at[at1 + 2] if r["kind"] == "raw_pointer")["targets"])
            _patch_bytes(d, rec + 1, bytes((blob[rec + 1] - 1,)))
            _drop_span(d, at1, 5, gone)
        elif r_i not in kept_first and any(key == v for key, _ in mine):
            at = next(at for key, at in mine if key == v)
            _patch_bytes(d, rec + 1, bytes((blob[rec + 1] - 1,)))
            _drop_span(d, at, 5, gone)
    # CurrentActivityState: max one lower, idle renumbered with the rest
    new_idle = values.get(idle, idle) if idle != v else None
    if new_idle is None:
        raise ComposeError("the activity holds the idle value")
    _patch_bytes(d, cas_record, new_idle.to_bytes(2, "little"))
    _patch_bytes(d, cas_record + 2, (top - 1).to_bytes(2, "little"))
    _name_value_count(d, cas_var, top - 1)

    # ---- the menu ----
    _drop_corner(d, page, act.corner, gone)
    plist = d.region(page["list"], "tagged_list")
    n_pages = len(menu_pages)
    if not plist["entries"] and act.page == n_pages - 1 and n_pages > 1:
        twin = d.pool_copy(page["list"])
        gone.update((plist["id"], twin["id"], d.region(page["address"], "mode_page")["id"]))
        _drop_program(d, page["program"], gone)
        entry = d.region(d.mode(menu)["entry"], "record_header")
        rid = d.region(page["address"], "mode_page")["id"]
        entry["targets"] = [t for t in entry["targets"] if t["to"] != rid]
        n_pages -= 1
        if n_pages == 1:
            _drop_counters(d, [menu_pages[0]["program"]], gone)
        else:
            _set_counters(d, {p["program"]: (i + 1, n_pages) for i, p in enumerate(menu_pages[:n_pages])},
                          f"delact{v}_counter")

    # ---- tables, maps, removal ----
    at_ = d.region(S[sem.ACTION_SLOT], "pointer_table")
    at_["targets"] = [t for i, t in enumerate(at_["targets"]) if i not in doomed]
    mt = d.region(S[sem.MODE_SLOT], "pointer_table")
    mt["targets"] = [t for i, t in enumerate(mt["targets"]) if i not in doomed_modes]
    st = d.region(S[9], "pointer_table")
    del st["targets"][k]
    _apply_maps(d, actions, modes, {}, keep=lambda r: r["id"] not in gone, sets=sets, values=values)
    _keep_shared_strings(d, gone)
    d.regions[:] = [r for r in d.regions if r["id"] not in gone]
    dangling = [(r["id"], t["to"]) for r in d.regions for t in (r.get("targets") or []) if t and t["to"] in gone]
    if dangling:
        raise ComposeError(f"still referenced after deletion: {dangling[:3]}")
    out = d.compile()

    # ---- checks ----
    after = _Doc(out)
    a = after.blob
    for new, old in enumerate(keep_actions):
        want = [(o, _remap(o, x, actions, modes, {}, sets, values, d.cas_op)) for o, x in d.ins(old)]
        if after.ins(new) != want:
            raise AssertionError(f"action list {old} -> {new}")
    want_acts = sorted((x.name, values.get(x.value, x.value), sets.get(x.key_set, x.key_set))
                       for x in acts if x.value != act.value)
    got_acts = sorted((x.name, x.value, x.key_set) for x in activities(out))
    if got_acts != want_acts:
        raise AssertionError(f"activities {got_acts} != {want_acts}")
    if [(x.name, x.page, x.corner) for x in devices(out)] != [(x.name, x.page, x.corner) for x in devices(raw)]:
        raise AssertionError("the device list changed")
    r0, r1 = render_map(blob), render_map(a)
    for (m, p), value in r0.items():
        if m in doomed_modes or m == menu:
            continue
        if r1.get((modes.get(m, m), p), (None,))[0] != value[0]:
            raise AssertionError(f"screen of mode {m} page {p} changed")
    _check_container(out)
    return out


def activity_settings(raw: bytes, name: str) -> dict:
    """An activity as add_activity's arguments, so it can be edited:
    {on, writes, steps, show, volume, unread, page, corner}. `steps` are the
    commands its start sends itself (add_activity's own step lists, or any list
    that only sends); `writes` its other state writes, a TV input request say;
    `show` the device whose screen it ends on (its own, or a copy with the
    volume keys rebound, and then `volume` names the device they send to), None
    for a screen of its own. `unread` counts calls in its start this cannot say
    as steps; an edit would drop them."""
    d = _Doc(raw)
    acts = [a for a in activities(raw) if a.name == name]
    if len(acts) != 1:
        raise ComposeError(f"no single activity named {name!r}")
    act = acts[0]
    power = power_variables(raw)
    start = d.ins(act.start)
    on = sorted(n for n, v in power.items() if (0x80 + v, 1) in start)
    writes = {o - 0x80: x for o, x in start
              if 0x80 <= o < 0x100 and o != d.cas_op and o - 0x80 not in power.values()}
    steps, unread = [], 0
    calls = [x for o, x in start if o == 0x7F]
    for x in calls[:-1]:                # the last one is the chain to its screen
        ins = d.ins(x)
        if ins and all(o == 0x7C for o, _ in ins) and len({v >> 8 for _, v in ins}) == 1:
            steps.append(("wait", ins[0][1] >> 8, sum(v & 0xFF for _, v in ins)))
        elif ins and all(o in (0x7D, 0x7C) for o, _ in ins):
            steps += [(v >> 8, v & 0xFF) for o, v in ins if o == 0x7D]
        elif ins and all(o - 0x80 in power.values() for o, _ in ins):
            on = sorted(set(on) | {n for n, v in power.items() if (0x80 + v, 1) in ins})
        else:
            unread += 1
    show = volume = None
    routed: dict[int, int] = {}
    devs = sorted({x.mode for x in devices(raw)})
    binds = {m: {t: (o, v) for t, o, v in cmp.tagged(d.blob, d.mode(m)["start"])}
             for m in [*devs, act.work_mode]}
    if act.work_mode in devs:
        show = act.work_mode
    else:
        # a copy of a device's screen whose every other key sends what some
        # other device's same key sends: that device, and where those keys go
        work = binds[act.work_mode]
        pages = len(d.mode(act.work_mode)["pages"])
        fits = []
        for m in devs:
            if len(d.mode(m)["pages"]) != pages:
                continue
            differ = {t for t in set(work) | set(binds[m]) if work.get(t) != binds[m].get(t)}
            where = {t: next((x for x in devs if x != m and t in work and binds[x].get(t) == work[t]), None)
                     for t in differ}
            if None not in where.values():
                fits.append((len(differ), m, where))
        fits.sort(key=lambda f: f[0])
        if fits and (len(fits) == 1 or fits[0][0] < fits[1][0]):
            _, show, routed = fits[0]
    vol_tags = {KEYS["VolumeUp"], KEYS["VolumeDown"], KEYS["Mute"]}
    targets = {routed.get(t) for t in vol_tags}
    if len(targets) == 1 and None not in targets:
        volume = targets.pop()
        routed = {t: m for t, m in routed.items() if t not in vol_tags}
    names = {v: k for k, v in KEYS.items()}
    return {"on": on, "writes": writes, "steps": steps, "show": show, "volume": volume,
            "keys": {names[t]: m for t, m in sorted(routed.items()) if t in names},
            "unread": unread, "page": act.page, "corner": act.corner}


@_needs_counter_font
def edit_activity(raw: bytes, name: str, new_name: str, like: str, on: Sequence[str],
                  writes: dict | None = None, steps: Sequence = (),
                  show: int | None = None, volume: int | None = None,
                  keys: dict | None = None) -> bytes:
    """Change an activity: it is built again with these settings beside the
    old one, the old one deleted, and the new one put in the old one's corner
    of the menu. `like` may be the activity itself. writes None keeps the
    activity's own state writes (its inputs), not `like`'s."""
    acts = activities(raw)
    old = [a for a in acts if a.name == name]
    if len(old) != 1:
        raise ComposeError(f"no single activity named {name!r}")
    old = old[0]
    if writes is None:
        writes = activity_settings(raw, name)["writes"]
    out = add_activity(raw, new_name, like, on, writes=writes, steps=steps,
                       show=show, volume=volume, keys=keys, _replacing=name)
    value = max(a.value for a in activities(out))
    out = delete_activity(out, name, value=old.value)
    mine = [a for a in activities(out) if a.value == value - 1]
    if len(mine) != 1:
        raise AssertionError("the rebuilt activity cannot be found")
    if (mine[0].page, mine[0].corner) != (old.page, old.corner):
        out = place_activity(out, mine[0].name, old.page, old.corner)
    # by place, not name: a long name is drawn cut short
    got = sorted((a.page, a.corner) for a in activities(out))
    if got != sorted((a.page, a.corner) for a in acts):
        raise AssertionError(f"the menu has {got} after the edit")
    others = sorted((a.name, a.page, a.corner) for a in activities(out) if (a.page, a.corner) != (old.page, old.corner))
    if others != sorted((a.name, a.page, a.corner) for a in acts if a.value != old.value):
        raise AssertionError("another activity moved or changed name")
    return out


# --------------------------------------------------------- moving and swapping ---
# A Devices screen and the activity menu are pages of four corners, each corner a
# soft key bound to a list that opens a device or starts an activity, and a name
# drawn beside it. A layout is that as data: pages of four, each None or
# (list, name). Moving an item rewrites the pages it touches from the layout.

def _apply_corner_layout(d: _Doc, modes: list, layout: list, prefix: str) -> set:
    """Make every page of `modes` follow `layout`. A page record shared by the
    modes is rewritten once; each mode's own first page (the Devices screen of
    each activity) gets the first page of the layout, so they all agree. Pages
    past the layout go, missing ones are added and shared. Returns `gone`."""
    gone: set = set()
    n = len(layout)
    if not 1 <= n <= 6:
        raise ComposeError("one to six pages")
    first_mode = d.mode(modes[0])
    template = first_mode["pages"][0]["program"]
    records: dict = {}
    for m in modes:
        for k, p in enumerate(d.mode(m)["pages"]):
            records.setdefault(p["address"], (k, p))

    def program_for(k, rid):
        program = _blank_devices_page(d, template, rid)
        fonts_at = _program_fonts(program)
        names: dict = {}
        for c, item in enumerate(layout[k]):
            if item is not None:
                for band, regs in _corner_draws(d, c, item[1], f"{rid}_c{c}").items():
                    names.setdefault(band, []).extend(regs)
        draws = {band: _in_font_0(regs, fonts_at.get(band, 0), f"{rid}_b{band}")
                 for band, regs in names.items()}
        if n > 1:
            draws.setdefault(7, []).extend([
                opaque(f"{rid}_cf", b"\x10\x04"),
                _draw(38 if k == 0 else 35, 56, bytes((_digit(d, k + 1),)), f"{rid}_cc"),
                _draw(47, 56, _of(d), f"{rid}_co"),
                _draw(65, 56, bytes((_digit(d, n),)), f"{rid}_ct")])
        _insert_in_program(program, draws)
        return program

    def entries_for(k):
        return [{"tag": SOFT[c], "operand": item[0], "opcode": 0x7F}
                for c, item in enumerate(layout[k]) if item is not None]

    # Logitech's pool copy of a menu page names twin lists of its own (5ai):
    # an entry that moves keeps its twin, or the twin is orphaned and the
    # activity can no longer be deleted (2026-10-20)
    twin_of: dict = {}
    for address, (k, p) in records.items():
        mine = {e["tag"]: e for e in d.region(p["list"], "tagged_list")["entries"]}
        for e in d.pool_copy(p["list"])["entries"]:
            if e["tag"] in mine and mine[e["tag"]]["opcode"] == e["opcode"] == 0x7F:
                twin_of.setdefault(mine[e["tag"]]["operand"], e["operand"])

    def twin_entries_for(k):
        return [dict(e, operand=twin_of.get(e["operand"], e["operand"])) for e in entries_for(k)]

    for address, (k, p) in records.items():
        record = d.region(address, "mode_page")
        plist = d.region(p["list"], "tagged_list")
        twin = d.pool_copy(p["list"])
        if k >= n:
            gone.update((record["id"], plist["id"], twin["id"]))
            _drop_program(d, p["program"], gone)
            for m in modes:
                e = d.region(d.mode(m)["entry"], "record_header")
                e["targets"] = [t for t in e["targets"] if t["to"] != record["id"]]
            continue
        entries = entries_for(k)
        if not entries and k > 0:
            raise ComposeError(f"page {k + 1} would be empty but is not the last")
        plist["entries"] = entries
        plist["wide"] = not entries
        twin["entries"] = twin_entries_for(k)
        twin["wide"] = not entries
        program = program_for(k, f"{prefix}_p{k}_{address:x}")
        first_item = sem.screen_program_path(d.blob, p["program"])[0]
        anchor = next(r for r in d.at[first_item["offset"]] if r["kind"] != "section")
        i = d.index_of(anchor)
        d.regions[i:i] = program
        _drop_program(d, p["program"], gone)
        record["targets"][1] = {"to": program[0]["id"]}

    have = len(first_mode["pages"])
    last_list = first_mode["pages"][-1]["list"]
    after = d.pool_copy(last_list)
    insert_copy_at = d.index_of(after) + 1
    new_regions = []
    for k in range(have, n):
        entries = entries_for(k)
        program = program_for(k, f"{prefix}_new{k}")
        plist = fresh_region("tagged_list", f"{prefix}_new{k}_list", section=8, wide=False, entries=entries)
        record = fresh_region("mode_page", f"{prefix}_new{k}",
                              targets=[{"to": plist["id"]}, {"to": program[0]["id"]}])
        d.regions.insert(insert_copy_at, fresh_region(
            "tagged_list", f"{prefix}_new{k}_copy", wide=False, role="page_binding_copy",
            entries=twin_entries_for(k)))
        insert_copy_at += 1
        s9 = next(i for i, r in enumerate(d.regions) if r.get("section") == 9)
        d.regions.insert(s9, plist)
        new_regions += [*program, record]
        for m in modes:
            d.region(d.mode(m)["entry"], "record_header")["targets"].append({"to": record["id"]})
    if new_regions:
        s0 = next(i for i, r in enumerate(d.regions) if r.get("section") == 0)
        d.regions[s0:s0] = new_regions

    _keep_shared_strings(d, gone)
    d.regions[:] = [r for r in d.regions if r["id"] not in gone]
    dangling = [(r["id"], t["to"]) for r in d.regions for t in (r.get("targets") or []) if t and t["to"] in gone]
    if dangling:
        raise ComposeError(f"still referenced after the move: {dangling[:3]}")
    return gone


def _devices_layout(d: _Doc, raw: bytes) -> list:
    idle = d.idle_devices_mode()
    pages = d.mode(idle)["pages"]
    layout = [[None] * 4 for _ in pages]
    by_mode = {x.mode: x for x in devices(raw)}
    for k, p in enumerate(pages):
        for tag, op, v in cmp.tagged(d.blob, p["list"]):
            if op == 0x7F and tag in SOFT:
                target = d.selection_target(v)
                if target in by_mode:
                    layout[k][SOFT.index(tag)] = (v, by_mode[target].name)
    return layout


def _activities_layout(d: _Doc, raw: bytes) -> list:
    menu = _menu_mode(d)
    pages = d.mode(menu)["pages"]
    layout = [[None] * 4 for _ in pages]
    for a in activities(raw):
        p = pages[a.page]
        sel = {t: v for t, o, v in cmp.tagged(d.blob, p["list"])}[SOFT[a.corner]]
        layout[a.page][a.corner] = (sel, a.name)
    return layout


def _move(layout: list, find, page: int, corner: int) -> list:
    layout = [list(p) for p in layout]
    here = [(k, c) for k, p in enumerate(layout) for c, item in enumerate(p) if item and find(item)]
    if len(here) != 1:
        raise ComposeError("no single item to move")
    (pk, pc), = here
    if not (0 <= corner < 4) or not (0 <= page <= len(layout)):
        raise ComposeError(f"page {page + 1}, corner {corner} does not exist")
    if page == len(layout):
        layout.append([None] * 4)
    layout[pk][pc], layout[page][corner] = layout[page][corner], layout[pk][pc]
    while len(layout) > 1 and not any(layout[-1]):
        layout.pop()
    if any(not any(p) for p in layout[1:]):
        raise ComposeError("that would leave an empty page in the middle")
    return layout


# ------------------------------------------------------------ power state ---

STATE_BAND_SUBS = {0xF2, 0xF1, 0xF0, 0xEF, 0xEE, 0xED}   # @dannybloe, actions.ts


def _state_index(op: int, operand: int) -> int | None:
    """The state variable an instruction names, or None (@dannybloe, section 277:
    the write band, 0x70 to 0x72's low byte, six subs of the 0x1F band)."""
    if op >= 0x80:
        return op - 0x80
    if op in (0x70, 0x71, 0x72):
        return operand & 0xFF
    if op == 0x1F and (operand >> 8) & 0xFF in STATE_BAND_SUBS:
        return operand & 0xFF
    return None


def _state_references(d: _Doc) -> set[int]:
    """Every state variable any instruction in the config names: action lists,
    mode, page and section 9 binding lists, section 11 streams, and section 13's
    own transitions."""
    b, out = d.blob, set()
    add = lambda o, x: out.add(_state_index(o, x)) if _state_index(o, x) is not None else None
    for i in range(d.n_actions):
        for o, x in d.ins(i):
            add(o, x)
    lists = [ir.u24(b, d.S[9] + 1 + 3 * k) - BASE for k in range(b[d.S[9]])]
    for m in range(d.n_modes):
        f = d.mode(m)
        lists += [f["start"]] + [p["list"] for p in f["pages"]]
    for at in lists:
        for _, o, x in cmp.tagged(b, at):
            add(o, x)
    s11 = d.S[11]
    for k in range(ir.u16(b, s11)):
        at = ir.u24(b, s11 + 2 + 3 * k) - BASE
        if b[at] == 0x11:
            add(b[at + 3], ir.u16(b, at + 1))
    s13 = d.S[13]
    for v in range(ir.u16(b, s13)):
        rec = ir.u24(b, s13 + 8 + 3 * v) - BASE
        for t in range(ir.u16(b, rec + 4)):
            at = rec + 7 + 8 * t
            add(b[at + 7], ir.u16(b, at + 5))
    return out


def _all_off_list(d: _Doc) -> int:
    """The action list Off runs: zero writes into every power variable and
    nothing else (@dannybloe, section 280), the longest such list."""
    s13 = d.S[13]
    def has_off(v):
        rec = ir.u24(d.blob, s13 + 8 + 3 * v) - BASE
        return any(int.from_bytes(d.blob[rec + 8 + 8 * t:rec + 10 + 8 * t], "little", signed=True) == 1 and
                   int.from_bytes(d.blob[rec + 10 + 8 * t:rec + 12 + 8 * t], "little", signed=True) == 0
                   for t in range(ir.u16(d.blob, rec + 4)))
    n_vars = ir.u16(d.blob, s13)
    best = None
    for i in range(d.n_actions):
        ins = d.ins(i)
        vs = [o - 0x80 for o, x in ins if 0x80 <= o < 0x100 and x == 0]
        if len(ins) >= 2 and len(vs) == len(ins) and all(v < n_vars and has_off(v) for v in vs):
            if best is None or len(ins) > len(d.ins(best)):
                best = i
    if best is None:
        raise ComposeError("no all-off list: a configuration with one device has none (section 280)")
    return best


def add_power(raw: bytes, mode: int, on: int, off: int | None = None,
              delay_tenths: int = 10) -> bytes:
    """Give a device a power state the remote keeps, as Logitech's compiler
    does for the TV, the amplifier and the XBOX: a state variable whose 0 -> 1
    sends command `on` and waits `delay_tenths` for the device to wake, and
    whose 1 -> 0 sends `off` (the same command for a toggle); Off switches it
    off, and every activity switches it off unless it is one of theirs.

    @dannybloe's composer, sections 277, 278 and 280: the variable goes in at
    the state table's `narrow` count, a send is always paired with a 0x7C, and
    the variable joins the all-off list and every activity's start list right
    before the activity counter's write. Refuses when a variable at or above
    `narrow` is named anywhere, since moving one would need renumbering."""
    d = _Doc(raw)
    blob, S = d.blob, d.S
    dev = [x for x in devices(raw) if x.mode == mode]
    if len(dev) != 1 or dev[0].group is None:
        raise ComposeError(f"mode {mode} is not a device that sends infrared")
    dev = dev[0]
    g = dev.group
    if dev.name in power_variables(raw):
        raise ComposeError(f"{dev.name} already has a power state")
    n_cmds = len(ir.ir_groups(blob, S)[g])
    off = on if off is None else off
    for cmd in (on, off):
        if not 0 <= cmd < n_cmds:
            raise ComposeError(f"{dev.name} has no command {cmd}")
    if not 0 < delay_tenths <= 0xFF:
        raise ComposeError("the power on delay is 0.1 to 25.5 seconds")

    s13 = S[13]
    count, narrow = ir.u16(blob, s13), ir.u16(blob, s13 + 2)
    wide, narrow_again = ir.u16(blob, s13 + 4), ir.u16(blob, s13 + 6)
    if count != narrow + wide or narrow_again != narrow:
        raise ComposeError(f"state table header {count}, {narrow}, {wide}, {narrow_again} is not the known shape")
    named = {v for v in _state_references(d) if v >= narrow}
    if named:
        raise ComposeError(f"variables {sorted(named)} at or above narrow are named; renumbering is not done here")
    var = narrow
    if 0x80 + var > 0xFF - 1 or var + 1 > 0x40:
        raise ComposeError("no room for another one byte state variable")

    # ---- action lists: each send with its device's usual 0x7C, the power on
    # list waiting for the device after it, as the amplifier's and the TV's do
    delays = [x & 0xFF for i in range(d.n_actions) for o, x in d.ins(i) if o == 0x7C and x >> 8 == g]
    qd = max(set(delays), key=delays.count) if delays else 1
    first = d.n_actions
    lists = [[(0x7D, g << 8 | on), (0x7C, g << 8 | qd)]]
    if off != on:
        lists.append([(0x7D, g << 8 | off), (0x7C, g << 8 | qd)])
    send_on, send_off = first, first + len(lists) - 1
    lists.append([(0x7F, send_on), (0x7C, g << 8 | delay_tenths)])
    power_on = first + len(lists) - 1
    action_regions = [fresh_region("action_list", f"power{var}_action_{first + k}",
                                   instructions=[{"operand": x, "opcode": f"0x{o:02X}"} for o, x in ins])
                      for k, ins in enumerate(lists)]

    # ---- the state variable: first 0, second 1, two transitions, as var 16's
    record = (0).to_bytes(2, "little") + (1).to_bytes(2, "little") + (2).to_bytes(2, "little") + b"\x00"
    for frm, to, target in ((0, 1, power_on), (1, 0, send_off)):
        record += b"\x00" + frm.to_bytes(2, "little", signed=True) + to.to_bytes(2, "little", signed=True) + \
            target.to_bytes(2, "little") + b"\x7f"
    rec_region = opaque(f"power{var}_record", record)
    table = d.region(s13, "pointer_table")
    header = bytearray(bytes.fromhex(table["header"]))
    # count's high byte, narrow, wide, narrow again (@dannybloe, section 276)
    if len(header) != 7 or int.from_bytes(header[1:3], "little") != narrow:
        raise ComposeError(f"section 13 header {table['header']} is not the known shape")
    header[1:3] = (narrow + 1).to_bytes(2, "little")
    header[5:7] = (narrow + 1).to_bytes(2, "little")
    table["header"] = header.hex()
    table["targets"].insert(var, {"to": rec_region["id"]})
    last = ir.u24(blob, s13 + 8 + 3 * (narrow - 1)) - BASE
    # right after the last one byte variable's record: 7 bytes and 8 per
    # transition. The bytes holding it may run on into what follows (the
    # start of section 14 on a 515, 2026-10-20), so the new record goes in
    # exactly there, not after the whole region
    end = last + 7 + 8 * ir.u16(blob, last + 4)
    holder = _split_at(d, end)
    d.regions.insert(d.index_of(holder) + 1, rec_region)

    # ---- its name, as Logitech's compiler names a power variable: the remote
    # never reads slot 0, readers do (@dannybloe, sections 77 and 126)
    names = d.region(S[0], "name_table")["records"]
    label = "_".join(re.findall(r"[A-Za-z0-9]+", dev.name.replace("..", ""))) or f"Device{g}"
    after = max((k for k, r in enumerate(names) if r["parent"] == 1 and r["name"].endswith("_Power_2")),
                default=max(k for k, r in enumerate(names) if r["parent"] == 1))
    names.insert(after + 1, {"parent": 1, "index": var, "name": f"{label}_Power_2"})

    # ---- off: the all-off list, and every activity's start before its counter
    def action_region(i):
        return d.region(d.actions[i], "action_list")
    write_off = {"operand": 0, "opcode": f"0x{0x80 + var:02X}"}
    all_off = _all_off_list(d)
    action_region(all_off)["instructions"].append(dict(write_off))
    starts = sorted({a.start for a in activities(raw)})
    for s in starts:
        r = action_region(s)
        at = next(k for k, i in enumerate(r["instructions"]) if int(i["opcode"], 16) == d.cas_op)
        r["instructions"].insert(at, dict(write_off))

    d.region(S[sem.ACTION_SLOT], "pointer_table")["targets"].extend({"to": r["id"]} for r in action_regions)
    s0 = next(i for i, r in enumerate(d.regions) if r.get("section") == 0)
    d.regions[s0:s0] = action_regions
    out = d.compile()

    # ---- checks ----
    a = _Doc(out)
    if ir.u16(a.blob, a.S[13]) != count + 1 or ir.u16(a.blob, a.S[13] + 2) != narrow + 1:
        raise AssertionError("state table header")
    if power_variables(out).get(dev.name) != var:
        raise AssertionError(f"{dev.name} does not read back with power variable {var}: {power_variables(out)}")
    for k, ins in enumerate(lists):
        if a.ins(first + k) != ins:
            raise AssertionError(f"new action list {first + k}")
    for i in range(d.n_actions):
        want = d.ins(i)
        if i == all_off:
            want = want + [(0x80 + var, 0)]
        elif i in starts:
            at = next(k for k, (o, _) in enumerate(want) if o == d.cas_op)
            want = want[:at] + [(0x80 + var, 0)] + want[at:]
        if a.ins(i) != want:
            raise AssertionError(f"action list {i} changed beyond the power write")
    for act in activities(out):
        steps = describe_activity(out, act.name)
        if {"kind": "power", "device": dev.name, "on": False} not in steps:
            raise AssertionError(f"{act.name} does not switch {dev.name} off")
    if render_map(blob) != render_map(a.blob):
        raise AssertionError("a screen changed")
    _check_container(out)
    return out


@_needs_counter_font
def place_device(raw: bytes, mode: int, page: int, corner: int) -> bytes:
    """Put a device on Devices page `page` (0 based) in `corner` (index into
    SOFT). A device already there takes the moved one's place; `page` one past
    the last opens a new page. Every Devices screen, each activity's included,
    gets the same layout, the one the remote shows with nothing running."""
    d = _Doc(raw)
    before = devices(raw)
    target = [x for x in before if x.mode == mode]
    if len(target) != 1:
        raise ComposeError(f"mode {mode} is not on the Devices screens")
    layout = _move(_devices_layout(d, raw), lambda item: d.selection_target(item[0]) == mode, page, corner)
    _apply_corner_layout(d, d.devices_modes(), layout, f"place{mode}")
    out = d.compile()
    after = _Doc(out)
    want = sorted((item[1], k, c) for k, p in enumerate(layout) for c, item in enumerate(p) if item)
    got = sorted((x.name, x.page, x.corner) for x in devices(out))
    if got != want:
        raise AssertionError(f"layout {got} != {want}")
    for m in after.devices_modes():
        pages = after.mode(m)["pages"]
        for k, p in enumerate(pages):
            tags = {t: after.selection_target(v) for t, o, v in cmp.tagged(after.blob, p["list"]) if o == 0x7F}
            exp = {SOFT[c]: d.selection_target(item[0]) for c, item in enumerate(layout[k]) if item}
            if tags != exp:
                raise AssertionError(f"Devices mode {m} page {k + 1} binds {tags}, not {exp}")
    r0, r1 = render_map(d.blob), render_map(after.blob)
    changed = [k for k in r0 if k[0] not in set(d.devices_modes()) and r0[k][0] != r1.get(k, (None,))[0]]
    if changed:
        raise AssertionError(f"screens changed that should not have: {changed}")
    if [a.name for a in activities(out)] != [a.name for a in activities(raw)]:
        raise AssertionError("the activities changed")
    _check_container(out)
    return out


@_needs_counter_font
def place_activity(raw: bytes, name: str, page: int, corner: int) -> bytes:
    """Put an activity on menu page `page` in `corner`; one already there takes
    its place, `page` one past the last opens a new page."""
    d = _Doc(raw)
    acts = activities(raw)
    if sum(a.name == name for a in acts) != 1:
        raise ComposeError(f"no single activity named {name!r}")
    layout = _move(_activities_layout(d, raw), lambda item: item[1] == name, page, corner)
    menu = _menu_mode(d)
    _apply_corner_layout(d, [menu], layout, f"placeact{len(name)}")
    out = d.compile()
    want = sorted((item[1], k, c) for k, p in enumerate(layout) for c, item in enumerate(p) if item)
    got = sorted((a.name, a.page, a.corner) for a in activities(out))
    if got != want:
        raise AssertionError(f"menu {got} != {want}")
    old = {a.name: (a.value, a.key_set) for a in acts}
    if {a.name: (a.value, a.key_set) for a in activities(out)} != old:
        raise AssertionError("an activity changed")
    after = _Doc(out)
    r0, r1 = render_map(d.blob), render_map(after.blob)
    changed = [k for k in r0 if k[0] != menu and r0[k][0] != r1.get(k, (None,))[0]]
    if changed:
        raise AssertionError(f"screens changed that should not have: {changed}")
    if [(x.name, x.page, x.corner) for x in devices(out)] != [(x.name, x.page, x.corner) for x in devices(raw)]:
        raise AssertionError("the device list changed")
    _check_container(out)
    return out


@_needs_counter_font
def rename_device(raw: bytes, mode: int, name: str) -> bytes:
    """Give a device a new name: in its corner of every Devices screen, as
    the title of each of its own pages and in every Help question that names
    it. Screens copied from it for an activity keep the name they were drawn
    with."""
    d = _Doc(raw)
    before = devices(raw)
    target = [x for x in before if x.mode == mode]
    if len(target) != 1:
        raise ComposeError(f"mode {mode} is not on the Devices screens")
    if not name.strip():
        raise ComposeError("a device needs a name")
    title = d.text(name, limit=96)
    old_title = device_title(d, target[0])
    # as the corner draws it, cut short when it is long
    drawn = " ".join("".join(d.alphabet.get(ch, "?") for ch in line) for line in _name_lines(d, name))
    layout = [[None if item is None else
               (item[0], name if d.selection_target(item[0]) == mode else item[1]) for item in p]
              for p in _devices_layout(d, raw)]
    _apply_corner_layout(d, d.devices_modes(), layout, f"rename{mode}")
    gone: set = set()
    # the device's own pages: the line drawn at the top is its title
    for k, p in enumerate(d.mode(mode)["pages"]):
        record = d.region(p["address"], "mode_page")
        program = _clone_program(d, p["program"], f"rename{mode}_p{k}",
                                 lambda x, y, text: ((96 - d.width(title)) // 2, y, title) if y == 0 else None)
        first_item = sem.screen_program_path(d.blob, p["program"])[0]
        anchor = next(r for r in d.at[first_item["offset"]] if r["kind"] != "section")
        i = d.index_of(anchor)
        d.regions[i:i] = program
        _drop_program(d, p["program"], gone)
        record["targets"][1] = {"to": program[0]["id"]}
    # Help's questions name it by its whole title ("Is the TV Panasonic on?"),
    # Logitech's own and composed ones alike: the new name, wrapped again
    said = re.compile(rf"(?<!\w){re.escape(old_title)}(?!\w)")
    most_ys = None
    asked = {}
    for m in range(d.n_modes):
        question = _help_text(d, m)
        if question is None or not said.search(question[2]):
            continue
        ys, font, text, _, row = question
        most_ys = most_ys or _question_template(d)[1]
        # a longer name may need the lines the richest Yes/No screen has
        if len(most_ys) > len(ys) and ys == most_ys[:len(ys)]:
            ys = most_ys
        lines = _wrap(d, said.sub(name.strip(), text), font, len(ys))
        by_y = dict(zip(ys, lines))
        asked[m] = by_y
        p = d.mode(m)["pages"][0]
        record = d.region(p["address"], "mode_page")
        centred = lambda y, by_y=by_y, font=font: ((96 - d.width(by_y[y], font)) // 2, y, by_y[y], font)

        def replace(x, y, t, by_y=by_y, centred=centred, row=row):
            if y == row:
                return None
            return centred(y) if y in by_y else False
        program = _clone_program(d, p["program"], f"rename{mode}_help{m}", replace)
        # lines the screen did not have, in every band their glyphs reach
        fonts_at = _fonts_at_band_ends(d, p["program"])
        per_band: dict[int, list[dict]] = {}
        for y in set(by_y) - set(question[0]):
            x, _, codes, _ = centred(y)
            for band in _bands(y):
                rid = f"rename{mode}_help{m}_y{y}_b{band}"
                per_band.setdefault(band, []).extend(
                    [opaque(f"{rid}_f", bytes((0x10, font))), _draw(x, y, codes, rid),
                     opaque(f"{rid}_fb", bytes((0x10, fonts_at[band])))])
        _insert_in_program(program, per_band)
        first_item = sem.screen_program_path(d.blob, p["program"])[0]
        anchor = next(r for r in d.at[first_item["offset"]] if r["kind"] != "section")
        i = d.index_of(anchor)
        d.regions[i:i] = program
        _drop_program(d, p["program"], gone)
        record["targets"][1] = {"to": program[0]["id"]}
    _keep_shared_strings(d, gone)
    d.regions[:] = [r for r in d.regions if r["id"] not in gone]
    dangling = [(r["id"], t["to"]) for r in d.regions for t in (r.get("targets") or []) if t and t["to"] in gone]
    if dangling:
        raise ComposeError(f"still referenced after the rename: {dangling[:3]}")
    out = d.compile()

    after = _Doc(out)
    want = sorted((x.mode, x.group, x.page, x.corner, drawn if x.mode == mode else x.name) for x in before)
    got = sorted((x.mode, x.group, x.page, x.corner, x.name) for x in devices(out))
    if got != want:
        raise AssertionError(f"the Devices screens read {got}, not {want}")
    for k, p in enumerate(after.mode(mode)["pages"]):
        tops = {renderer_string(after, it) for it in sem.screen_program_path(after.blob, p["program"])
                if it["opcode"] in (4, 5) and it["operands"][1] == 0}
        if tops and tops != {title}:
            raise AssertionError(f"page {k + 1} of the device is titled {tops}")
    if [a.name for a in activities(out)] != [a.name for a in activities(raw)]:
        raise AssertionError("the activities changed")
    if any(d.ins(i) != after.ins(i) for i in range(d.n_actions)) or after.n_actions != d.n_actions:
        raise AssertionError("an action list changed")
    for m, by_y in asked.items():
        question = _help_text(after, m)
        if question is None or question[3] != {y: bytes(t) for y, t in by_y.items()}:
            raise AssertionError(f"Help screen {m} reads {question and question[2]}")
    r0, r1 = render_map(d.blob), render_map(after.blob)
    moved = set(d.devices_modes()) | {mode} | set(asked)
    changed = [k for k in r0 if k[0] not in moved and r0[k][0] != r1.get(k, (None,))[0]]
    if changed:
        raise AssertionError(f"screens changed that should not have: {changed}")
    _check_container(out)
    return out


# ------------------------------------------------------------ texts on screens ---
# Every text a screen draws that no other form edits: messages (batteries,
# Stopping Activity, the firmware's own screens), Starting screens, Help's
# questions and its Yes and No, and the titles of the Devices screens and the
# activity menu. Device and activity names, soft key labels and page counters
# have forms of their own and are left alone. A text is edited once for every
# screen that draws it the same way.

def _screen_parts(d: _Doc, m: int, k: int, titled: set) -> dict[str, list[tuple]]:
    """The editable texts of page k of mode m, by part: "body" (a message, or a
    Help question), "soft" (Help's two labels, left to right) or "title" (of a
    Devices screen or the menu). Each a list of (y, x, font, codes), one per
    draw, band copies once. Pages with soft key corners give their title at
    most, and only when they are in `titled`."""
    page = d.mode(m)["pages"][k]
    tags = {t for t, _, _ in cmp.tagged(d.blob, page["list"])}
    is_help = tags == {HELP_YES, HELP_NO}
    corners = bool(tags & set(SOFT)) and not is_help
    font, items = 0, {}
    for it in sem.screen_program_path(d.blob, page["program"]):
        if it["opcode"] == 0x10:
            font = it["operands"][0]
        elif it["opcode"] in (4, 5) and font != COUNTER_FONT:
            x, y = it["operands"][0], it["operands"][1]
            codes = bytes(renderer_string(d, it))
            if codes and y < 64:
                items.setdefault((y, x), (y, x, font, codes))
    lines = sorted(items.values())
    if corners:
        top = [line for line in lines if line[0] == 0]
        return {"title": top} if m in titled and len(top) == 1 else {}
    if not lines:
        return {}
    if is_help:
        row = max(line[0] for line in lines)
        soft = [line for line in lines if line[0] == row]
        if row >= SOFT_ROW and len(soft) == 2:
            return {"body": [line for line in lines if line[0] != row],
                    "soft": sorted(soft, key=lambda line: line[1])}
    return {"body": lines}


def _titled(d: _Doc) -> set:
    titled = set(d.devices_modes())
    try:
        titled.add(_menu_mode(d))
    except ComposeError:
        pass
    return titled


def _is_help(d: _Doc, m: int) -> bool:
    return len(d.mode(m)["pages"]) == 1 and _help_text(d, m) is not None


def _reference_texts(reference: bytes | None) -> dict:
    """What Logitech's English says, read off `reference` (the public 525
    sample): its firmware screens by mode, Stopping Activity, Yes and No, the
    Devices and menu titles."""
    if reference is None:
        return {}
    d = _Doc(reference)
    spell = lambda codes: "".join(d.alphabet.get(ch, "?") for ch in codes)
    out = {"modes": {}}
    for m in range(min(d.n_modes, FIRMWARE_MODES)):
        body = _screen_parts(d, m, 0, set()).get("body")
        if body:
            out["modes"][m] = [(y, spell(c)) for y, _, _, c in body]
    out["stop"] = [spell(c) for _, _, _, c in _screen_parts(d, _stop_mode(d), 0, set())["body"]]
    for m in range(d.n_modes):
        if _is_help(d, m):
            out["soft"] = [spell(c) for _, _, _, c in _screen_parts(d, m, 0, set())["soft"]]
            break
    titled = _titled(d)
    out["devices"] = spell(_screen_parts(d, d.idle_devices_mode(), 0, titled)["title"][0][3])
    out["menu"] = spell(_screen_parts(d, _menu_mode(d), 0, titled)["title"][0][3])
    return out


# the screens a configuration starts with that are the firmware's own
# vocabulary (FORMAT 5x): the same mode numbers in every arch 9 config read
FIRMWARE_MODES = 42


def _default_text(d: _Doc, part: str, text: str, modes: list, ref: dict, starts: dict) -> str | None:
    """Logitech's English for a text, or None when it is not known."""
    if part == "soft":
        return "\n".join(ref["soft"]) if "soft" in ref else None
    if part == "title":
        if d.idle_devices_mode() in modes or set(modes) & set(d.devices_modes()):
            return ref.get("devices")
        return ref.get("menu")
    if any(_is_help(d, m) for m in modes):
        # a question in one of the sentences in force, said as Logitech would
        for key in ("fix", "is_on", "now_on", "is_off", "now_off", "is_input", "now_input"):
            pattern = re.escape(_phrases[key]).replace(r"\{device\}", "(?P<device>.+)") \
                                                 .replace(r"\{input\}", "(?P<input>.+)")
            hit = re.fullmatch(pattern, text)
            if hit:
                return PHRASES[key].format(**hit.groupdict())
        return None
    lines = text.split("\n")
    try:
        stop = _stop_mode(d)
    except ComposeError:
        stop = None
    if stop in modes and "stop" in ref:
        return "\n".join(ref["stop"])
    start = next((m for m in modes if m in starts), None)
    if start is not None and "stop" in ref:
        # "Starting", the name as drawn, then what Stopping Activity says below its own first line
        rest = ref["stop"][1:]
        if len(lines) == 2 + len(rest):
            return "\n".join([PHRASES["starting"], lines[1], *rest])
        return None
    for m in modes:
        theirs = ref.get("modes", {}).get(m)
        # the same screen if its lines sit where these do
        if theirs and [y for y, _ in theirs] == [y for y, _, _, _ in _screen_parts(d, m, 0, set()).get("body", [])]:
            return "\n".join(t for _, t in theirs)
    return None


def screen_texts(raw: bytes, reference: bytes | None = None) -> list[dict]:
    """The editable texts, each once however many screens draw it the same:
    [{part, text, screens: [[mode, page]], group: "activity" | "device" |
    "common", owner: its name, kind, default: Logitech's English or None}].
    A Help question is one text, wrapped on its screen; anything else is its
    lines, one per line of the screen."""
    d = _Doc(raw)
    spell = lambda codes: "".join(d.alphabet.get(ch, "?") for ch in codes)
    titled = _titled(d)
    starts = {a.start_mode: a.name for a in activities(raw)}
    devs = devices(raw)
    names = sorted({device_title(d, x): device_title(d, x) for x in devs}.items(), key=lambda kv: -len(kv[0]))
    ref = _reference_texts(reference)
    found: dict = {}
    for m in range(d.n_modes):
        help_screen = _is_help(d, m)
        for k in range(len(d.mode(m)["pages"])):
            for part, lines in _screen_parts(d, m, k, titled).items():
                if part == "body" and help_screen:
                    text = " ".join(spell(c) for _, _, _, c in lines)
                else:
                    text = "\n".join(spell(c) for _, _, _, c in lines)
                entry = found.setdefault((part, help_screen, text), {"part": part, "text": text, "screens": []})
                if [m, k] not in entry["screens"]:
                    entry["screens"].append([m, k])
    out = []
    for (part, help_screen, text), entry in found.items():
        modes = [m for m, _ in entry["screens"]]
        start = next((starts[m] for m in modes if m in starts), None)
        said = next((name for title, name in names
                     if re.search(rf"(?<!\w){re.escape(title)}(?!\w)", text)), None) if part == "body" else None
        if start is not None:
            group, owner, kind = "activity", start, "Starting screen"
        elif said is not None:
            group, owner, kind = "device", said, "Help question"
        else:
            group, owner = "common", ""
            kind = {"soft": "Help's Yes and No", "title": "title"}.get(part) or \
                ("Help question" if help_screen else "message")
        entry.update(group=group, owner=owner, kind=kind,
                     default=_default_text(d, part, text, modes, ref, starts))
        out.append(entry)
    order = {"activity": 0, "device": 1, "common": 2}
    out.sort(key=lambda e: (order[e["group"]], e["owner"], min(e["screens"])))
    return out


def _placed(d: _Doc, codes: bytes, font: int, old: tuple) -> int:
    """Where a line goes that replaces `old` (y, x, font, codes): as the old
    one was aligned, at the left, the right, the middle of the screen, or
    around the same centre."""
    _, x, f, c = old
    w_old, w = d.width(c, f), d.width(codes, font)
    if x <= 1:
        return x
    if x + w_old >= 95:
        return max(0, 96 - w)
    if abs(x + w_old / 2 - 48) <= 2:
        return max(0, (96 - w) // 2)
    return min(max(0, round(x + w_old / 2 - w / 2)), max(0, 96 - w))


@_needs_counter_font
def set_screen_text(raw: bytes, screens: Sequence, part: str, text: str) -> bytes:
    """Give every screen in `screens` ([mode, page]) the text `text` in its
    `part` (screen_texts): a Help question wrapped to its screen's lines (a
    longer one may take the lines the richest Yes/No screen has), Yes and No
    as two lines, a title as one, a message line by line. Each line keeps its
    font and its alignment; letters a font lacks are added to it."""
    d = _Doc(raw)
    titled = _titled(d)
    screens = [tuple(int(v) for v in s) for s in screens]
    if not screens:
        raise ComposeError("no screen to change")
    if part not in ("body", "soft", "title"):
        raise ComposeError(f"no part {part!r}")
    roots: dict[int, list] = {}
    for m, k in screens:
        if not 0 <= m < d.n_modes or not 0 <= k < len(d.mode(m)["pages"]):
            raise ComposeError(f"no page {k + 1} of screen {m}")
        if part not in _screen_parts(d, m, k, titled):
            raise ComposeError(f"screen {m} has no {part} to edit")
        roots.setdefault(d.mode(m)["pages"][k]["program"], []).append((m, k))
    # every page that draws one of these programs changes with it
    affected = {(m, k) for m in range(d.n_modes) for k, p in enumerate(d.mode(m)["pages"]) if p["program"] in roots}

    gone: set = set()
    expected: dict = {}
    for root, pages in roots.items():
        m, k = pages[0]
        old = _screen_parts(d, m, k, titled)[part]
        help_screen = part == "body" and _is_help(d, m)
        new: dict = {}       # (y, x) -> (x, y, codes, font) or False
        extra: list = []     # lines the screen did not have: (x, y, codes, font)
        if help_screen:
            words = " ".join(text.split())
            if not words:
                raise ComposeError("a Help question cannot be empty")
            font = old[0][2]
            ys = [line[0] for line in old]
            most = _question_template(d)[1]
            needed = len(_wrap(d, words, font, 99))
            # a longer question may take the lines the richest Yes/No screen has
            if needed > len(ys) and len(most) > len(ys) and ys == most[:len(ys)]:
                ys = most
            if needed > len(ys):
                raise ComposeError(f"{words!r} needs {needed} lines; this screen has {len(ys)}")
            wrapped = _wrap(d, words, font, len(ys))
            if any(d.width(d.text(w, font, limit=None), font) > 94 for w in words.split()):
                raise ComposeError(f"a word of {words!r} is wider than the screen")
            for i, y in enumerate(ys):
                codes = wrapped[i] if i < len(wrapped) else None
                at = next((line for line in old if line[0] == y), None)
                if codes is None:
                    if at is not None:
                        new[(y, at[1])] = False
                    continue
                place = ((96 - d.width(codes, font)) // 2, y, codes, font)
                if at is None:
                    extra.append(place)
                else:
                    new[(y, at[1])] = place
            expected[root] = [w for w in wrapped]
        else:
            lines = text.split("\n")
            if part == "soft" and len(lines) != 2:
                raise ComposeError("Yes and No are two lines, left then right")
            if part == "title" and len(lines) != 1:
                raise ComposeError("a title is one line")
            if len(lines) > len(old):
                raise ComposeError(f"this screen has {len(old)} line{'s' if len(old) > 1 else ''} of text, "
                                   f"not {len(lines)}")
            want = []
            for i, line in enumerate(old):
                value = lines[i].strip() if i < len(lines) else ""
                if not value:
                    if part != "body":
                        raise ComposeError("Yes, No and a title cannot be empty")
                    new[(line[0], line[1])] = False
                    continue
                font = line[2]
                try:
                    codes = d.text(value, font, limit=None)
                    width = d.width(codes, font)
                except ComposeError:
                    # a font whose typeface is not known cannot be given the
                    # letters (the 555's menu title is in a font of ten);
                    # font 0 is the one letters are added to
                    font = 0
                    codes = d.text(value, font, limit=None)
                    width = d.width(codes, font)
                if width > 96:
                    raise ComposeError(f"line {i + 1}, {value!r}, is {width} pixels; the screen is 96")
                new[(line[0], line[1])] = (_placed(d, codes, font, line), line[0], codes, font)
                want.append(codes)
            expected[root] = want

        def replace(x, y, t, new=new):
            return new.get((y, x))
        rid = f"text_m{m}_p{k}_{part}"
        program = _clone_program(d, root, rid, replace)
        if extra:
            fonts_at = _fonts_at_band_ends(d, root)
            per_band: dict = {}
            for x, y, codes, font in extra:
                for band in _bands(y, d.fonts[font].height):
                    b = f"{rid}_y{y}_b{band}"
                    per_band.setdefault(band, []).extend(
                        [opaque(f"{b}_f", bytes((0x10, font))), _draw(x, y, codes, b),
                         opaque(f"{b}_fb", bytes((0x10, fonts_at[band])))])
            _insert_in_program(program, per_band)
        first_item = sem.screen_program_path(d.blob, root)[0]
        anchor = next(r for r in d.at[first_item["offset"]] if r["kind"] != "section")
        i = d.index_of(anchor)
        d.regions[i:i] = program
        _drop_program(d, root, gone)
        for mm, kk in affected:
            p = d.mode(mm)["pages"][kk]
            if p["program"] == root:
                d.region(p["address"], "mode_page")["targets"][1] = {"to": program[0]["id"]}
    _keep_shared_strings(d, gone)
    d.regions[:] = [r for r in d.regions if r["id"] not in gone]
    dangling = [(r["id"], t["to"]) for r in d.regions for t in (r.get("targets") or []) if t and t["to"] in gone]
    if dangling:
        raise ComposeError(f"still referenced after the change: {dangling[:3]}")
    out = d.compile()

    # ---- checks ----
    after = _Doc(out)
    for m, k in affected:
        root = next(r for r, pages in roots.items() if d.mode(m)["pages"][k]["program"] == r)
        got = [c for _, _, _, c in _screen_parts(after, m, k, _titled(after)).get(part, [])]
        if got != [bytes(c) for c in expected[root]]:
            raise AssertionError(f"screen {m} page {k + 1} reads {got}")
    if any(d.ins(i) != after.ins(i) for i in range(d.n_actions)) or after.n_actions != d.n_actions:
        raise AssertionError("an action list changed")
    if [(a.name, a.page, a.corner) for a in activities(out)] != [(a.name, a.page, a.corner) for a in activities(raw)] \
            or [(x.name, x.page, x.corner) for x in devices(out)] != [(x.name, x.page, x.corner) for x in devices(raw)]:
        raise AssertionError("a device or an activity changed")
    r0, r1 = render_map(d.blob), render_map(after.blob)
    changed = [key for key in r0 if key not in affected and r0[key][0] != r1.get(key, (None,))[0]]
    if changed:
        raise AssertionError(f"screens changed that should not have: {changed}")
    _check_container(out)
    return out


# ----------------------------------------------------- an activity from nothing ---
# A configuration with no activity (an empty 555, or a 525 whose activities were
# all deleted) has nothing to build one like. What a first one needs is read off
# what every configuration keeps (FORMAT 5al): the idle key set, which Off
# selects and whose event 1 is the stop sequence; the "Stopping Activity" screen
# that sequence shows, whose frame a "Starting" screen shares; a "Replace the
# batteries" screen; and the section 14 records looked up by the activity state.

def _copy_mode(d: _Doc, old: int, prefix: str, replace=None, entries=None, page_entries=None):
    """A new mode copying `old`: its key list (or `entries`), its pages, each
    page's list in section 8 (or `page_entries[k]`) and its pool copy.
    replace(x, y, text) as for _clone_program. Returns (mode regions,
    section 8 lists, pool copies)."""
    f = d.mode(old)
    lst = d.region(f["start"], "tagged_list")
    ml = fresh_region("tagged_list", f"{prefix}_list", wide=lst.get("wide", True),
                      entries=copy.deepcopy(lst["entries"] if entries is None else entries),
                      **({"role": lst["role"]} if "role" in lst else {}))
    regions, lists, copies, pages = [ml], [], [], []
    for k, p in enumerate(f["pages"]):
        src = d.region(p["list"], "tagged_list")
        if page_entries is not None:
            src = dict(src, entries=page_entries[k])
        pl = fresh_region("tagged_list", f"{prefix}_p{k}_list", section=8, wide=src.get("wide", False),
                          entries=copy.deepcopy(src["entries"]))
        prog = _clone_program(d, p["program"], f"{prefix}_p{k}", replace or (lambda x, y, t: None))
        record = fresh_region("mode_page", f"{prefix}_p{k}", targets=[{"to": pl["id"]}, {"to": prog[0]["id"]}])
        regions += [*prog, record]
        pages.append(record)
        lists.append(pl)
        copies.append(fresh_region("tagged_list", f"{prefix}_p{k}_copy", wide=src.get("wide", False),
                                   role="page_binding_copy", entries=copy.deepcopy(src["entries"])))
    regions.append(fresh_region("record_header", f"{prefix}_entry", back_reference={"to": ml["id"]},
                                targets=[{"to": r["id"]} for r in pages]))
    return regions, lists, copies


# ------------------------------------------------------------------- Help ---
# Help, while an activity runs, is a tree of Yes/No screens Logitech wrote per
# activity (FORMAT 5am): on entry it sends every device's power and input
# again and asks "Did that fix the problem?"; No goes through the devices one
# at a time ("Is the TV on?", then "Now is the TV on?" while sending its power
# again); Yes anywhere goes back to the activity's screen. A composed activity
# gets its own, with its own devices' names.

HELP_YES, HELP_NO = 0x9F, 0x9E          # the bottom soft keys under "Yes" and "No"
HELP_ENTRY = 0x06                       # the event a screen gets when it is shown

# The sentences a composed screen is written in, as Logitech's English has
# them. set_phrases() replaces them (the page keeps a person's own wording), so
# a new activity speaks the language its configuration was given; {device} is
# a device's whole title, {input} the input command's label.
PHRASES = {
    "starting": "Starting",
    "fix": "Did that fix the problem?",
    "is_on": "Is the {device} on?",
    "now_on": "Now is the {device} on?",
    "is_off": "Is the {device} off?",
    "now_off": "Now is the {device} off?",
    "is_input": "Is the {device} on {input}?",
    "now_input": "Now is the {device} on {input}?",
}
_phrases = dict(PHRASES)


def set_phrases(phrases: dict | None) -> dict:
    """Use `phrases` (key -> sentence) for screens composed from now on; keys
    left out or empty keep Logitech's. Returns the sentences in force."""
    new = dict(PHRASES)
    for key, text in (phrases or {}).items():
        if key not in PHRASES:
            raise ComposeError(f"no sentence called {key!r}")
        text = str(text).strip()
        if text:
            try:
                text.format(device="x", input="y")
            except (KeyError, IndexError, ValueError):
                raise ComposeError(f"{key}: only {{device}} and {{input}} can be filled in") from None
            new[key] = text
    # all or nothing: a refused one leaves the sentences as they were
    _phrases.clear()
    _phrases.update(new)
    return dict(_phrases)


def phrase(key: str, **fill) -> str:
    return _phrases[key].format(**fill)


def _question_template(d: _Doc):
    """A Yes/No screen to draw questions on: the one with most lines of text.
    Returns (mode, [line ys], font of the lines)."""
    best = None
    for m in range(d.n_modes):
        question = _help_text(d, m)
        if question and (best is None or len(question[0]) > len(best[1])):
            best = (m, question[0], question[1])
    if best is None:
        raise ComposeError("no Yes/No screen to draw Help questions on")
    return best


def _screen_lines(d: _Doc, mode: int) -> dict[int, bytes]:
    """A one page screen's text as {y: codes}, the first string drawn at each y."""
    lines: dict[int, bytes] = {}
    for it in sem.screen_program_path(d.blob, d.mode(mode)["pages"][0]["program"]):
        if it["opcode"] in (4, 5):
            lines.setdefault(it["operands"][1], bytes(renderer_string(d, it)))
    return lines


SOFT_ROW = 45   # the soft key labels: y 49 on the 525 and 555, 50 on the 515


def _help_text(d: _Doc, m: int):
    """Mode m as a Help Yes/No screen: (line ys, their font, the question as
    one text, {y: codes}, the y of the Yes and No labels), or None when it is
    not one. Known by its shape, not its words, so it survives a translation:
    one page whose soft keys are Yes and No and nothing else, their two labels
    in the soft key row, the question above them in one font."""
    f = d.mode(m)
    if len(f["pages"]) != 1:
        return None
    tags = {t for t, _, _ in cmp.tagged(d.blob, f["pages"][0]["list"])}
    if tags != {HELP_YES, HELP_NO}:
        return None
    font, items = 0, []
    for it in sem.screen_program_path(d.blob, f["pages"][0]["program"]):
        if it["opcode"] == 0x10:
            font = it["operands"][0]
        elif it["opcode"] in (4, 5):
            items.append((it["operands"][1], it["operands"][0], font, bytes(renderer_string(d, it))))
    if not items:
        return None
    row = max(y for y, _, _, _ in items)
    if row < SOFT_ROW or len({x for y, x, _, _ in items if y == row}) != 2:
        return None
    lines, fonts = {}, {}
    for y, _, fnt, codes in items:
        if y != row:
            lines.setdefault(y, codes)
            fonts.setdefault(y, fnt)
    if not lines or len(set(fonts.values())) != 1:
        return None
    ys = sorted(lines)
    text = " ".join("".join(d.alphabet.get(ch, "?") for ch in lines[y]) for y in ys)
    return ys, fonts[ys[0]], text, lines, row


def _wrap(d: _Doc, text: str, font: int, lines: int) -> list[bytes]:
    """Words onto at most `lines` lines of the screen's width, the last cut
    short with '..' when they do not fit."""
    out, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if cur and d.width(d.text(trial, font, limit=None), font) > 94:
            out.append(cur)
            cur = word
        else:
            cur = trial
    out.append(cur)
    if len(out) > lines:
        out = out[:lines - 1] + [" ".join(out[lines - 1:])]
    return [d.text(line, font, limit=94) for line in out]


def _help_tree(d: _Doc, prefix: str, work: int, questions: list, first_mode: int, make_list):
    """The Help screens of an activity whose screen is `work`. `questions`:
    [(is_text, now_text, [instruction, ...] that sends it again)]. Returns
    (entry mode, mode regions, section 8 lists, pool copies)."""
    template, ys, font = _question_template(d)
    entries_of = lambda m: d.region(d.mode(m)["start"], "tagged_list")
    wide = entries_of(template).get("wide", False)
    entry = lambda tag, op, x: {**({"flags": 0} if wide else {}), "tag": tag, "operand": x, "opcode": op}
    page_wide = d.region(d.mode(template)["pages"][0]["list"], "tagged_list").get("wide", False)
    soft = lambda tag, op, x: {**({"flags": 0} if page_wide else {}), "tag": tag, "operand": x, "opcode": op}
    n = len(questions)
    fix, q, now, fixed = first_mode, [], [], []
    for i in range(n):
        q.append(first_mode + 1 + 3 * i)
        now.append(first_mode + 2 + 3 * i)
        fixed.append(first_mode + 3 + 3 * i)
    after = lambda i: q[i + 1] if i + 1 < n else work
    everything = [ins for _, _, sends in questions for ins in sends]
    regions, lists, copies = [], [], []

    def screen(mode, text, yes, no, on_entry=None):
        lines = _wrap(d, text, font, len(ys))
        by_y = dict(zip(ys, lines))

        def replace(x, y, t):
            if y >= SOFT_ROW:
                return None
            if y not in by_y:
                return False
            return ((96 - d.width(by_y[y], font)) // 2, y, by_y[y], font)
        keys = [entry(0xAE, 0x7E, work)]
        if on_entry:
            keys.append(entry(HELP_ENTRY, 0x7F, make_list(on_entry)))
        regs, ls, cs = _copy_mode(d, template, f"{prefix}_m{mode}", replace, entries=keys,
                                  page_entries=[[soft(HELP_NO, *no), soft(HELP_YES, *yes)]])
        regions.extend(regs)
        lists.extend(ls)
        copies.extend(cs)

    screen(fix, phrase("fix"), (0x7E, work), (0x7E, q[0] if n else work), everything or None)
    for i, (is_text, now_text, sends) in enumerate(questions):
        again = lambda nxt, sends=sends: (0x7F, make_list([*sends, (0x7E, nxt)]))
        screen(q[i], is_text, (0x7E, after(i)), again(now[i]), sends)
        screen(now[i], now_text, (0x7E, fixed[i]), again(now[i]))
        screen(fixed[i], phrase("fix"), (0x7E, work), (0x7E, after(i)))
    return fix, regions, lists, copies


def device_title(d: _Doc, dev) -> str:
    """A device's whole name: the title its own screen draws at the top
    ("TV Panasonic"), where the Devices corner may cut it ("TV Panason..")."""
    for it in sem.screen_program_path(d.blob, d.mode(dev.mode)["pages"][0]["program"]):
        if it["opcode"] in (4, 5) and it["operands"][1] == 0:
            text = "".join(d.alphabet.get(ch, "?") for ch in renderer_string(d, it))
            if text.strip() and "?" not in text:
                return text
    return dev.name.replace("..", "")


def _help_questions(d: _Doc, raw: bytes, on: Sequence[str], steps: Sequence,
                    writes: dict | None = None) -> list:
    """Help's questions for an activity: each device it switches on (its power
    state's 0 -> 1 sends), each input it sets through a state variable (what
    the transition to that value sends, a TV's "Input Hdmi1"), then each
    command it sends (the step itself)."""
    power = power_variables(raw)
    labels = device_commands(raw)
    devs = devices(raw)
    full = {x.name: device_title(d, x) for x in devs}
    by_group = {x.group: full[x.name] for x in devs}
    out = []
    s13 = d.S[13]
    for name in on:
        record = ir.u24(d.blob, s13 + 8 + 3 * power[name]) - BASE
        for t in range(ir.u16(d.blob, record + 4)):
            at = record + 7 + 8 * t
            frm = int.from_bytes(d.blob[at + 1:at + 3], "little", signed=True)
            to = int.from_bytes(d.blob[at + 3:at + 5], "little", signed=True)
            if (frm, to) == (0, 1):
                send = (d.blob[at + 7], ir.u16(d.blob, at + 5))
                shown = full[name]
                out.append((phrase("is_on", device=shown), phrase("now_on", device=shown), [send]))
    power_vars = set(power.values())
    for var, value in sorted((writes or {}).items()):
        if var in power_vars:
            continue
        record = ir.u24(d.blob, s13 + 8 + 3 * var) - BASE
        for t in range(ir.u16(d.blob, record + 4)):
            at = record + 7 + 8 * t
            if int.from_bytes(d.blob[at + 3:at + 5], "little", signed=True) != value:
                continue
            send = (d.blob[at + 7], ir.u16(d.blob, at + 5))
            stack, first, seen = [send], None, set()
            while stack and first is None:
                o, x = stack.pop(0)
                if o == 0x7D:
                    first = x
                elif o == 0x7F and x not in seen:
                    seen.add(x)
                    stack = d.ins(x) + stack
                elif 0x80 <= o < 0x100 and (o, x) not in seen:
                    # an input set through another variable (input type)
                    seen.add((o, x))
                    rec2 = ir.u24(d.blob, s13 + 8 + 3 * (o - 0x80)) - BASE
                    for t2 in range(ir.u16(d.blob, rec2 + 4)):
                        at2 = rec2 + 7 + 8 * t2
                        if int.from_bytes(d.blob[at2 + 3:at2 + 5], "little", signed=True) == x:
                            stack.append((d.blob[at2 + 7], ir.u16(d.blob, at2 + 5)))
            if first is None:
                continue
            label = labels.get((first >> 8, first & 0xFF), ("", f"command {first & 0xFF}"))[1]
            dev = by_group.get(first >> 8, f"group {first >> 8}")
            # Help sends the command itself: writing the variable again would
            # change nothing and send nothing
            g = first >> 8
            delays = [x & 0xFF for i in range(d.n_actions) for o, x in d.ins(i) if o == 0x7C and x >> 8 == g] or [1]
            again = [(0x7D, first), (0x7C, g << 8 | max(set(delays), key=delays.count))]
            out.append((phrase("is_input", device=dev, input=label), phrase("now_input", device=dev, input=label), again))
            break
    for step in steps:
        if step[0] == "wait":
            continue
        g, cmd = int(step[0]), int(step[1])
        label = labels.get((g, cmd), ("", f"command {cmd}"))[1]
        dev = by_group.get(g, f"group {g}")
        delays = [x & 0xFF for i in range(d.n_actions) for o, x in d.ins(i) if o == 0x7C and x >> 8 == g] or [1]
        send = [(0x7D, g << 8 | cmd), (0x7C, g << 8 | max(set(delays), key=delays.count))]
        out.append((phrase("is_input", device=dev, input=label), phrase("now_input", device=dev, input=label), send))
    return out


def _idle_set(d: _Doc) -> dict:
    """The idle key set, tag -> (opcode, operand): the one Off (0xB6 in key
    set 1) selects."""
    blob, S = d.blob, d.S
    set_at = lambda k: ir.u24(blob, S[9] + 1 + 3 * k) - BASE
    off = {t: (o, v) for t, o, v in cmp.tagged(blob, set_at(1))}.get(0xB6)
    if off is None or off[0] != 0x1F or off[1] >> 8 != 0xFF:
        raise ComposeError("Off does not select a key set here")
    idle_set = {t: (o, v) for t, o, v in cmp.tagged(blob, set_at(off[1] & 0xFF))}
    if not {1, 0xAF, 0xAE} <= set(idle_set):
        raise ComposeError("the idle key set has no stop sequence, Devices or Help")
    return idle_set


def _stop_mode(d: _Doc) -> int:
    """The "Stopping Activity" screen: the one the idle key set's event 1, the
    stop sequence, shows."""
    stop = _idle_set(d)[1]
    stop_mode = next((v for o, v in d.ins(stop[1]) if o == 0x7E), None) if stop[0] == 0x7F else None
    if stop_mode is None:
        raise ComposeError("the stop sequence shows no screen")
    return stop_mode


@_needs_counter_font
def add_first_activity(raw: bytes, name: str, on: Sequence[str], show: int,
                       writes: dict | None = None, steps: Sequence = (),
                       volume: int | None = None, keys: dict | None = None) -> bytes:
    """The first activity of a configuration that has none, nothing copied
    from another activity. It switches on the devices in `on` and the others
    with a power state off, makes the state writes in `writes`, sends `steps`
    (as add_activity takes them), and ends on device mode `show`'s screen, or on
    a copy of it whose `volume` and `keys` go to other devices."""
    d = _Doc(raw)
    blob, S = d.blob, d.S
    if activities(raw):
        raise ComposeError("there are activities already; build a new one like one of them")
    devs = {x.mode for x in devices(raw)}
    if show not in devs:
        raise ComposeError(f"mode {show} is not a device on the Devices screens")
    power = power_variables(raw)
    unknown = [n for n in on if n not in power]
    if unknown:
        raise ComposeError(f"no power variable for {unknown}; devices with one: {sorted(power)}")
    writes = dict(writes or {})
    state_writes = [(power[n], 1 if n in on else 0) for n in sorted(power, key=lambda n: power[n])]
    state_writes += sorted(writes.items())
    cas_var = d.cas_var
    cas_record = ir.u24(blob, S[13] + 8 + 3 * cas_var) - BASE
    idle, top = ir.u16(blob, cas_record), ir.u16(blob, cas_record + 2)
    if (idle, top) != (0, 0):
        raise ComposeError(f"the activity state reads {idle} of {top}, not the empty 0 of 0")
    value = 1
    menu = _menu_mode(d)
    n_sets = blob[S[9]]
    new_set = n_sets
    flag = d.device_flag_op

    # ---- the idle key set: what Off selects (0xB6 in key set 1) ----
    set_at = lambda k: ir.u24(blob, S[9] + 1 + 3 * k) - BASE
    off = {t: (o, v) for t, o, v in cmp.tagged(blob, set_at(1))}[0xB6]
    idle_set = _idle_set(d)
    stop_mode = _stop_mode(d)
    battery = next((ins[1][1] for ins in map(d.ins, range(d.n_actions))
                    if len(ins) >= 2 and ins[0] == (0x71, 0x8008) and ins[1][0] == 0x7E), None)
    if battery is None:
        raise ComposeError("no 'Replace the batteries' screen to copy")

    # ---- modes: the work screen, the start screen, the battery screen ----
    next_mode = d.n_modes
    new_regions, sec8, copies = [], [], []
    vol_tags = (KEYS["VolumeUp"], KEYS["VolumeDown"], KEYS["Mute"])
    routed = {t: volume for t in vol_tags} if volume is not None else {}
    for k, m in (keys or {}).items():
        routed[KEYS[k] if isinstance(k, str) else int(k)] = int(m)
    routed = {t: m for t, m in routed.items() if m != show}
    if routed:
        work = next_mode
        next_mode += 1
        regs, ls, cs = _copy_mode(d, show, f"first_m{work}")
        names = {v: k for k, v in KEYS.items()}
        mine = {e["tag"]: e for e in regs[0]["entries"]}
        for tag, m in sorted(routed.items()):
            if m not in devs:
                raise ComposeError(f"mode {m} is not a device on the Devices screens")
            theirs = {e["tag"]: e for e in d.region(d.mode(m)["start"], "tagged_list")["entries"]}
            if tag not in theirs:
                raise ComposeError(f"{names.get(tag, hex(tag))} does nothing on mode {m}'s screen")
            if tag in mine:
                mine[tag].update(operand=theirs[tag]["operand"], opcode=theirs[tag]["opcode"])
            else:
                regs[0]["entries"].append({**({"flags": 0} if regs[0].get("wide") else {}), "tag": tag,
                                           "operand": theirs[tag]["operand"], "opcode": theirs[tag]["opcode"]})
        new_regions += regs
        sec8 += ls
        copies += cs
    else:
        work = show

    start_mode = next_mode
    next_mode += 1
    title = d.text(name, limit=96)
    starting = d.text(phrase("starting"), limit=96)

    # the name goes where "Stopping Activity" is, the screen's first line
    header_y = min(_screen_lines(d, stop_mode))

    def starting_screen(x, y, text):
        if y == 0:
            return False
        if y == header_y:
            return ((96 - d.width(title)) // 2, y, title)
        return None
    regs, ls, cs = _copy_mode(d, stop_mode, f"first_m{start_mode}", starting_screen)
    # "Starting" over the name, as Logitech draws it: again in every band its
    # glyphs reach (0 and 1), in font 0 and back to the font in force there
    body = regs[1:-1]
    prog = [r for r in body if r["kind"] != "mode_page"]
    page = next(r for r in body if r["kind"] == "mode_page")
    prog_with = list(prog)
    fonts_at = _fonts_at_band_ends(d, d.mode(stop_mode)["pages"][0]["program"])
    x0 = (96 - d.width(starting)) // 2
    _insert_in_program(prog_with, {
        band: _in_font_0([_draw(x0, 0, starting, f"first_m{start_mode}_st{band}")], fonts_at[band],
                         f"first_m{start_mode}_st{band}")
        for band in _bands(0, d.fonts[0].height - 1)})
    page["targets"][1] = {"to": prog_with[0]["id"]}
    regs = [regs[0], *prog_with, page, regs[-1]]
    new_regions += regs
    sec8 += ls
    copies += cs

    battery_mode = next_mode
    next_mode += 1
    bat_entries = [dict(e, operand=work, opcode=0x7E) if e["opcode"] in (0x72, 0x7E) else dict(e)
                   for e in d.region(d.mode(battery)["start"], "tagged_list")["entries"]]
    regs, ls, cs = _copy_mode(d, battery, f"first_m{battery_mode}", entries=bat_entries)
    new_regions += regs
    sec8 += ls
    copies += cs

    # ---- action lists ----
    next_action = d.n_actions
    new_actions = []

    def make_list(ins):
        nonlocal next_action
        new_actions.append((next_action, ins))
        next_action += 1
        return next_action - 1
    n_groups = len(ir.ir_groups(blob, S))
    queue_delay = {}
    for i in range(d.n_actions):
        for o, x in d.ins(i):
            if o == 0x7C:
                queue_delay.setdefault(x >> 8, []).append(x & 0xFF)
    end = make_list([(0x71, 0x8008), (0x7E, battery_mode), (0x7E, work)])
    event = make_list([(0x1F, 0xFB00), (0x7F, end)])
    wait = make_list([(0x3F, 0xD000), (0x7F, event)])
    step_calls = []
    for step in steps:
        if step[0] == "wait":
            g, tenths = int(step[1]), int(step[2])
            if not 0 <= g < n_groups or not 0 < tenths <= 450:
                raise ComposeError(f"a wait of {tenths / 10} s for group {g}")
            parts = [100] * ((tenths - 1) // 100) + [tenths - 100 * ((tenths - 1) // 100)]
            step_calls.append((0x7F, make_list([(0x7C, g << 8 | t) for t in parts])))
            continue
        g, cmd = int(step[0]), int(step[1])
        if not 0 <= g < n_groups or not 0 <= cmd < len(ir.ir_groups(blob, S)[g]):
            raise ComposeError(f"no command {cmd} in group {g}")
        delays = queue_delay.get(g, [1])
        step_calls.append((0x7F, make_list([(0x7D, g << 8 | cmd), (0x7C, g << 8 | max(set(delays), key=delays.count))])))
    start = make_list([(0x7E, start_mode), *[(0x80 + v, x) for v, x in state_writes], *step_calls,
                       (d.cas_op, value), (0x7F, wait)])
    repick = make_list([*[(0x80 + v, x) for v, x in sorted(writes.items())], (0x7F, event)])
    select = make_list([(0x1F, 0xFF00 | new_set), (flag, 0)])

    # ---- Help: its own tree, with this activity's devices ----
    help_entry, regs, ls, cs = _help_tree(d, "first_help", work, _help_questions(d, raw, on, steps),
                                          next_mode, make_list)
    next_mode += sum(1 for r in regs if r["kind"] == "record_header")
    new_regions += regs
    sec8 += ls
    copies += cs
    action_regions = [fresh_region("action_list", f"first_action_{i}",
                                   instructions=[{"operand": x, "opcode": f"0x{o:02X}"} for o, x in ins])
                      for i, ins in new_actions]

    # ---- the key set: start and re-pick, Devices as when idle, its Help ----
    idle_region = d.region(set_at(off[1] & 0xFF), "tagged_list")
    wide = idle_region.get("wide", False)
    entry = lambda tag, op, x: {**({"flags": 0} if wide else {}), "tag": tag, "operand": x, "opcode": op}
    set_region = fresh_region("tagged_list", "first_key_set", wide=wide, role="section_9_binding", entries=[
        entry(1, 0x7F, start), entry(0xAF, *idle_set[0xAF]), entry(0xAE, 0x7E, help_entry),
        entry(5, 0x7F, repick)])

    # ---- section 14: the activity's screen and its key set, by its value ----
    by_state = _records_keyed_by(d, cas_var)
    s11_table = d.region(S[11], "pointer_table")
    new_streams = []

    def stream(op, operand, tag):
        a = fresh_region("screen_instruction", f"first_s11_{tag}", opcode="0x11",
                         operands=(operand.to_bytes(2, "little") + bytes((op,))).hex())
        z = fresh_region("screen_instruction", f"first_s11_{tag}_end", opcode="0x00", operands="")
        new_streams.extend([a, z])
        s11_table["targets"].append({"to": a["id"]})
        return a["id"]
    placed = []
    for k in sorted(by_state):
        record = ir.u24(blob, S[14] + 1 + 3 * k) - BASE
        n = blob[record + 1]
        streams = [ir.u24(blob, record + 4 + 5 * j) - BASE for j in range(n)]
        if n == 0:
            target = stream(0x1F, 0xFF00 | new_set, "set")          # the key set record
        elif n == 1 and (blob[streams[0] + 3], ir.u16(blob, streams[0] + 1)) == (0x72, 13):
            target = stream(0x7E, work, "screen")                     # the activity's screen
        else:
            continue
        head = next(r for r in d.regions if "data" in r and r["offset"] <= record + 1 < r["offset"] + r["length"])
        data = bytearray(bytes.fromhex("".join(head["data"])))
        data[record + 1 - head["offset"]] = n + 1
        pointer = fresh_region("raw_pointer", f"first_s14_{k}_ptr", targets=[{"to": target}])
        if n == 0:
            # the key and its pointer go right after the count; what follows
            # the count in the same region (the next record's header, read as
            # one with it) moves into a region of its own after the pointer
            cut = record + 2 - head["offset"]
            rest = bytes(data[cut:])
            data = data[:cut] + value.to_bytes(2, "little")
            head["length"] = len(data)
            i = d.index_of(head)
            if rest:
                tail = opaque(f"first_s14_{k}_rest", rest)
                d.regions[i + 1:i + 1] = [pointer, tail]
                # whatever pointed past the cut (the next record) points into the tail now
                for r in d.regions:
                    for t in r.get("targets") or []:
                        if t and t.get("to") == head["id"] and t.get("delta", 0) >= cut:
                            t["to"], t["delta"] = tail["id"], t.get("delta", 0) - cut
                    br = r.get("back_reference")
                    if br and br.get("to") == head["id"] and br.get("delta", 0) >= cut:
                        br["to"], br["delta"] = tail["id"], br.get("delta", 0) - cut
            else:
                d.regions.insert(i + 1, pointer)
        else:
            last = next(r for r in d.at[record + 4 + 5 * (n - 1)] if r["kind"] == "raw_pointer")
            i = d.index_of(last)
            d.regions[i + 1:i + 1] = [opaque(f"first_s14_{k}_key", value.to_bytes(2, "little")), pointer]
        head["data"] = hconfig._hex_lines(bytes(data))
        placed.append(k)
    if len(placed) != 2:
        raise ComposeError(f"found {len(placed)} of the two section 14 records an activity needs: {placed}")
    last = max(i for i, r in enumerate(d.regions) if r["kind"] == "screen_instruction"
               and r.get("opcode") in ("0x11", "0x00") and S[11] > r["offset"] and any(
                   t["to"] == r["id"] for t in s11_table["targets"][:ir.u16(blob, S[11])]))
    d.regions[last + 2:last + 2] = new_streams

    # ---- the activity state counts one value more ----
    _patch_bytes(d, cas_record + 2, value.to_bytes(2, "little"))
    _name_value_count(d, cas_var, value)

    # ---- the menu: top left of its first page ----
    mpage = d.mode(menu)["pages"][0]
    plist = d.region(mpage["list"], "tagged_list")
    twin = d.pool_copy(mpage["list"])
    if plist["entries"]:
        raise ComposeError("the activity menu is not empty")
    selector = {"tag": SOFT[0], "operand": select, "opcode": 0x7F}
    plist["wide"] = twin["wide"] = False
    plist["entries"].append(dict(selector))
    twin["entries"].append(dict(selector))
    ends = d.band_ends(mpage["program"])
    fonts_at = _fonts_at_band_ends(d, mpage["program"])
    draws = _corner_draws(d, 0, name, "first_menu")
    for band in sorted(draws, reverse=True):
        d.insert_before(ends[band], _in_font_0(draws[band], fonts_at[band], f"first_menu_b{band}"))

    # ---- tables and placement, as add_activity places them ----
    d.region(S[sem.MODE_SLOT], "pointer_table")["targets"].extend(
        {"to": r["id"]} for r in new_regions if r["kind"] == "record_header")
    d.region(S[sem.ACTION_SLOT], "pointer_table")["targets"].extend({"to": r["id"]} for r in action_regions)
    d.region(S[9], "pointer_table")["targets"].append({"to": set_region["id"]})
    d.regions.insert(d.index_of(d.region(set_at(n_sets - 1), "tagged_list")) + 1, set_region)
    i4 = d.index_of(d.region(set_at(4), "tagged_list"))
    d.regions[i4:i4] = copies
    s9 = next(i for i, r in enumerate(d.regions) if r.get("section") == 9)
    d.regions[s9:s9] = sec8
    s0 = next(i for i, r in enumerate(d.regions) if r.get("section") == 0)
    d.regions[s0:s0] = [*action_regions, *new_regions]
    out = d.compile()

    # ---- checks ----
    after = _Doc(out)
    if not all(d.ins(i) == after.ins(i) for i in range(d.n_actions)):
        raise AssertionError("an existing action list changed")
    for i, ins in new_actions:
        if after.ins(i) != ins:
            raise AssertionError(f"new action list {i}")

    def records(doc):
        b, found = doc.blob, []
        for k in range(b[doc.S[14]]):
            rec = ir.u24(b, doc.S[14] + 1 + 3 * k) - BASE
            items = []
            for j in range(b[rec + 1]):
                at = ir.u24(b, rec + 4 + 5 * j) - BASE
                # a stream (0x11), or on the 555 a screen program that starts
                # with another instruction: its first bytes, which hold no address
                items.append((ir.u16(b, rec + 2 + 5 * j), b[at + 3], ir.u16(b, at + 1))
                             if b[at] == 0x11 else (ir.u16(b, rec + 2 + 5 * j), bytes(b[at:at + 2])))
            found.append(items)
        return found
    for k, (was, now) in enumerate(zip(records(d), records(after))):
        if k in placed:
            if now[:-1] != was or now[-1][0] != value:
                raise AssertionError(f"section 14 record {k}: {was} -> {now}")
        elif now != was:
            raise AssertionError(f"section 14 record {k} changed: {was} -> {now}")
    got = activities(out)
    if len(got) != 1 or (got[0].value, got[0].key_set, got[0].start, got[0].start_mode, got[0].work_mode) != \
            (value, new_set, start, start_mode, work):
        raise AssertionError(f"the menu reads {got}")
    r0, r1 = render_map(blob), render_map(after.blob)
    changed = [k for k in r0 if k[0] != menu and r0[k][0] != r1[k][0]]
    if changed:
        raise AssertionError(f"screens changed that should not have: {changed}")
    _check_container(out)
    return out
