"""Render every Harmony 525 menu page to dependency-free BMP files.

The renderer targets the public arch-9 sample and the screen opcodes that sample
actually uses. It writes ordinary 24-bit BMPs plus a JSON manifest; Pillow or
other third-party packages are not required.

Usage:
    python tools/render_525_screens.py --out path/to/output
"""
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

import _paths
import verify_525_semantics as semantics

BASE = semantics.CONFIG_BASE
PAPER = 2
INK = 1


def u16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 2], "little")


def u24(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 3], "little")


def blank(width: int, height: int, value: int = PAPER) -> list[list[int]]:
    return [[value] * width for _ in range(height)]


def put(canvas: list[list[int]], x: int, y: int, value: int) -> None:
    if 0 <= y < len(canvas) and 0 <= x < len(canvas[y]):
        canvas[y][x] = value


def write_bmp(path: Path, pixels: list[list[int]]) -> None:
    """Write 2-bit logical pixels as a broadly viewable 24-bit BMP."""
    height = len(pixels)
    width = len(pixels[0]) if pixels else 0
    stride = (width * 3 + 3) & ~3
    body = bytearray()
    shades = {0: 128, INK: 0, PAPER: 255, 3: 192}
    for row in reversed(pixels):
        for value in row:
            shade = shades.get(value, 128)
            body.extend((shade, shade, shade))
        body.extend(b"\0" * (stride - width * 3))
    header_size = 14 + 40
    header = struct.pack("<2sIHHI", b"BM", header_size + len(body), 0, 0, header_size)
    dib = struct.pack("<IIIHHIIIIII", 40, width, height, 1, 24, 0, len(body),
                      2835, 2835, 0, 0)
    path.write_bytes(header + dib + body)


class Font:
    def __init__(self, blob: bytes, address: int):
        self.blob = blob
        self.offset = address - BASE
        self.height = blob[self.offset]
        second, third = blob[self.offset + 1:self.offset + 3]
        self.first = 1 if third == 0 else second
        self.count = second if third == 0 else third
        self.glyph_addresses = [u24(blob, self.offset + 3 + 3 * k)
                                for k in range(self.count)]
        self._cache: dict[int, list[list[int]]] = {}

    def glyph(self, code: int) -> list[list[int]]:
        if code in self._cache:
            return self._cache[code]
        index = code - self.first
        if not 0 <= index < self.count:
            raise ValueError(f"glyph code {code} outside font range")
        address = self.glyph_addresses[index]
        if address == 0:
            raise ValueError(f"glyph code {code} is NULL")
        at = address - BASE
        width = self.blob[at]
        at += 1
        rows = []
        while self.blob[at] != 0:
            leader = self.blob[at]
            at += 1
            if leader & 0xF0 != 0x20:
                raise ValueError(f"bad glyph row leader 0x{leader:02X}")
            stop = at + (leader & 0x0F)
            row = []
            while at < stop:
                operation = self.blob[at]
                at += 1
                kind, count = operation >> 4, (operation & 0x0F) + 1
                if kind == 0x6:
                    row.extend([PAPER] * count)
                elif kind == 0xA:
                    row.extend([INK] * count)
                elif kind == 0x5:
                    size = (2 * count + 7) // 8
                    encoded = self.blob[at:at + size]
                    at += size
                    for k in range(count):
                        bit = 2 * k
                        row.append((encoded[bit >> 3] >> (6 - (bit & 7))) & 3)
                else:
                    raise ValueError(f"unknown glyph operation kind {kind}")
            if at != stop or len(row) != width:
                raise ValueError("glyph row length does not close")
            rows.append(row)
        if len(rows) != self.height:
            raise ValueError(f"glyph height {len(rows)} != font height {self.height}")
        self._cache[code] = rows
        return rows


def fonts(blob: bytes, sections: list[int | None]) -> list[Font]:
    table = sections[7]
    assert table is not None
    return [Font(blob, u24(blob, table + 2 + 3 * k))
            for k in range(u16(blob, table))]


def picture(blob: bytes, address: int) -> list[list[int]]:
    at = address - BASE
    kind = blob[at]
    width, height = u16(blob, at + 1), u16(blob, at + 3)
    if kind != 2:
        raise ValueError(f"expected arch-9 monochrome picture kind 2, got {kind}")
    stride = (width + 7) // 8
    data = blob[at + 5:at + 5 + stride * height]
    return [[INK if data[y * stride + (x >> 3)] & (0x80 >> (x & 7)) else PAPER
             for x in range(width)] for y in range(height)]


def draw_picture(canvas, source, source_x, source_y, dest_x, dest_y, width, height):
    for y in range(height):
        for x in range(width):
            sy, sx = source_y + y, source_x + x
            if 0 <= sy < len(source) and 0 <= sx < len(source[sy]):
                put(canvas, dest_x + x, dest_y + y, source[sy][sx])


def draw_string(canvas, font: Font, codes: bytes, x: int, y: int) -> int:
    start = x
    for code in codes:
        glyph = font.glyph(code)
        for gy, row in enumerate(glyph):
            for gx, value in enumerate(row):
                put(canvas, x + gx, y + gy, value)
        x += len(glyph[0])
    return x - start


def page_roots(blob: bytes, sections: list[int | None]):
    table = sections[6]
    assert table is not None
    result = []
    for mode_index in range(u24(blob, table)):
        entry = u24(blob, table + 3 + 3 * mode_index) - BASE
        for page_index in range(u16(blob, entry + 4)):
            page = u24(blob, entry + 6 + 3 * page_index) - BASE
            result.append({"mode": mode_index, "page": page_index,
                           "root": u24(blob, page + 3) - BASE})
    return result


def render_page(blob: bytes, font_sets: list[Font], root: int):
    canvas = blank(96, 64)
    current_font = 0
    strings = []
    image_cache = {}
    for item in semantics.screen_program_path(blob, root):
        opcode, operands = item["opcode"], item["operands"]
        if opcode == 3:
            # Destination first, then source. The 1,080 background strips have
            # dx == sx and dy == sy, so they cannot tell the two orders apart;
            # the 34 draws that copy from the all-ink bitmap can. Every one of
            # them is (0, 12, 0, 0, 96, 1) and sits in the program between the
            # row-1 select and the text at y=13, so the 12 is where the line
            # lands and the 0 is the row it is copied from. Reading it the other
            # way puts a full-width rule at y=0, in a band that has already been
            # transferred. See docs/FORMAT.md section 4f.
            dx, dy, sx, sy, width, height = operands[:6]
            address = u24(operands, 6)
            source = image_cache.setdefault(address, picture(blob, address))
            draw_picture(canvas, source, sx, sy, dx, dy, width, height)
        elif opcode == 16:
            current_font = operands[0]
        elif opcode in (4, 5):
            x, y = operands[:2]
            if opcode == 4:
                at = u24(operands, 2) - BASE
                end = blob.index(0, at)
                codes = blob[at:end]
                storage = "external"
            else:
                codes = operands[2:-1]
                storage = "inline"
            draw_string(canvas, font_sets[current_font], codes, x, y)
            strings.append({"opcode": opcode, "storage": storage, "font": current_font,
                            "x": x, "y": y, "codes": list(codes)})
    return canvas, strings


DIGITS = {
    "0": ("111", "101", "101", "101", "111"),
    "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"),
    "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"),
    "7": ("111", "001", "010", "010", "010"),
    "8": ("111", "101", "111", "101", "111"),
    "9": ("111", "101", "111", "001", "111"),
}


def draw_number(canvas, number: int, x: int, y: int) -> None:
    for char in str(number):
        for py, row in enumerate(DIGITS[char]):
            for px, bit in enumerate(row):
                if bit == "1":
                    put(canvas, x + px, y + py, INK)
        x += 4


def contact_sheet(screens: list[list[list[int]]], columns: int = 8):
    cell_width, cell_height = 104, 74
    rows = (len(screens) + columns - 1) // columns
    sheet = blank(columns * cell_width, rows * cell_height)
    for index, screen in enumerate(screens):
        ox = (index % columns) * cell_width + 4
        oy = (index // columns) * cell_height
        draw_number(sheet, index, ox, oy + 1)
        for y, row in enumerate(screen):
            for x, value in enumerate(row):
                put(sheet, ox + x, oy + 8 + y, value)
    return sheet


def font_sheet(font: Font):
    columns, cell_width, cell_height = 11, 24, font.height + 9
    rows = (font.count + columns - 1) // columns
    sheet = blank(columns * cell_width, rows * cell_height)
    for index in range(font.count):
        code = font.first + index
        address = font.glyph_addresses[index]
        if address == 0:
            continue
        glyph = font.glyph(code)
        ox, oy = (index % columns) * cell_width + 2, (index // columns) * cell_height
        draw_number(sheet, code, ox, oy)
        for y, row in enumerate(glyph):
            for x, value in enumerate(row):
                put(sheet, ox + x, oy + 7 + y, value)
    return sheet


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", nargs="?", type=Path, default=_paths.SAMPLE_BLOB)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    blob = _paths.get_blob(args.config)
    sections = semantics.section_offsets(blob)
    font_sets = fonts(blob, sections)
    roots = page_roots(blob, sections)
    screens, manifest = [], []
    for index, entry in enumerate(roots):
        screen, strings = render_page(blob, font_sets, entry["root"])
        screens.append(screen)
        filename = f"screen-{index:03d}-mode-{entry['mode']:03d}-page-{entry['page']:02d}.bmp"
        write_bmp(args.out / filename, screen)
        manifest.append({"index": index, **entry, "file": filename, "strings": strings})

    write_bmp(args.out / "contact-sheet.bmp", contact_sheet(screens))
    for index, font in enumerate(font_sets):
        write_bmp(args.out / f"font-{index}.bmp", font_sheet(font))
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"rendered {len(screens)} screens, {len(font_sets)} font sheets")
    print(args.out.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
