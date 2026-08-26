"""Reusable, offline-only Harmony 525 LCD authoring primitives.

The architecture-9 config uses a container-wide generated character alphabet.
A glyph code can be NULL in one font set and live in another; that NULL is not
permission to give the code a different meaning.  This module keeps that rule
at the authoring boundary and allocates a new code only after the whole existing
alphabet.

It never opens USB hardware and does not write a config by itself.
"""
from __future__ import annotations

from collections.abc import Mapping, MutableMapping, Sequence

import hconfig
import render_525_screens as renderer


# Public H525 owner-config alphabet, one character per code starting at 1.
# Source and derivation: harmony-explorations packages/codec/bin/alphabets.ts,
# independently checked against the local rendered labels used by this project.
H525_OWNER_ALPHABET = (
    "HyOFaTermint EUSBCNDpgducs0:o2flL.wGWbIzkRPhAMq\\vV-X361?Y9x+/4#5Z~"
)


def alphabet_map(characters: str = H525_OWNER_ALPHABET, *, first: int = 1) -> dict[int, str]:
    """Turn a generated alphabet string into its code-to-character mapping."""
    if len(set(characters)) != len(characters):
        raise ValueError("generated alphabet assigns one character more than once")
    return {first + index: char for index, char in enumerate(characters)}


def encode_text(text: str, alphabet: Mapping[int, str]) -> bytes:
    """Encode text through one explicit container alphabet, never through ASCII."""
    reverse = {character: code for code, character in alphabet.items()}
    if len(reverse) != len(alphabet):
        raise ValueError("alphabet is not one-to-one")
    try:
        return bytes(reverse[character] for character in text)
    except KeyError as exc:
        raise ValueError(f"character {exc.args[0]!r} is not in this container alphabet") from None


def glyph_shape_key(rows: Sequence[str]) -> str:
    """Return the public codec's stable identity for a two-bit pixel glyph."""
    body = ";".join(
        ",".join(str(renderer.INK if char == "#" else renderer.PAPER) for char in row)
        for row in rows
    )

    def fnv1a(seed: int) -> int:
        value = seed
        for character in body:
            value ^= ord(character)
            value = (value * 0x01000193) & 0xFFFFFFFF
        return value

    return f"{len(rows)}:{fnv1a(0x811C9DC5):08x}{fnv1a(0x01000193):08x}"


def encode_literal_glyph(rows: Sequence[str]) -> bytes:
    """Encode a glyph using only the decoder's two-bit literal row opcode."""
    if not rows:
        raise ValueError("glyph has no rows")
    width = len(rows[0])
    if width == 0 or width > 16 or any(len(row) != width for row in rows):
        raise ValueError("invalid glyph dimensions")
    if any(character not in ".#" for row in rows for character in row):
        raise ValueError("glyph rows may contain only '.' and '#'")

    out = bytearray((width,))
    for row in rows:
        packed = bytearray((2 * width + 7) // 8)
        for index, character in enumerate(row):
            value = renderer.INK if character == "#" else renderer.PAPER
            bit = 2 * index
            packed[bit >> 3] |= value << (6 - (bit & 7))
        row_program = bytes((0x50 | (width - 1),)) + bytes(packed)
        out += bytes((0x20 | len(row_program),)) + row_program
    out += b"\x00"
    return bytes(out)


def _font_region(doc: dict, address: int) -> dict:
    matches = [
        region for region in doc["blob"]["regions"]
        if region["kind"] == "font_set" and region["offset"] == address
    ]
    if len(matches) != 1:
        raise ValueError(f"expected one font set at 0x{address:X}, found {len(matches)}")
    return matches[0]


def install_character_glyph(
    blob: bytes,
    doc: dict,
    sections: list[int | None],
    *,
    font_index: int,
    character: str,
    rows: Sequence[str],
    alphabet: MutableMapping[int, str],
    region_id: str,
) -> tuple[int, dict]:
    """Install one bitmap without ever reassigning an existing character code.

    If ``character`` already belongs to the container alphabet, a NULL slot at
    that exact code may be filled.  A genuinely new character is appended after
    the maximum code used by any font/alphabet.  A live target is never replaced.
    The returned region must be inserted into the document by the caller.
    """
    if len(character) != 1:
        raise ValueError("one glyph must represent exactly one character")
    if len(set(alphabet.values())) != len(alphabet):
        raise ValueError("alphabet is not one-to-one")

    fonts = renderer.fonts(blob, sections)
    if not 0 <= font_index < len(fonts):
        raise ValueError(f"font index {font_index} is out of range")
    decoded = fonts[font_index]
    if decoded.height != len(rows):
        raise ValueError(f"glyph height {len(rows)} does not match font height {decoded.height}")

    table = sections[7]
    if table is None:
        raise ValueError("config has no font table")
    address = int.from_bytes(blob[table + 2 + 3 * font_index:table + 5 + 3 * font_index], "little")
    font = _font_region(doc, address - hconfig.CONFIG_BASE)

    existing = [code for code, value in alphabet.items() if value == character]
    if existing:
        code = existing[0]
    else:
        maximum = max(
            max(alphabet, default=0),
            *(item.first + item.count - 1 for item in fonts),
        )
        code = maximum + 1
        alphabet[code] = character

    if code < font["first"]:
        raise ValueError(f"code {code} precedes font's first code {font['first']}")
    index = code - font["first"]
    if index < len(font["targets"]) and font["targets"][index] is not None:
        raise ValueError(f"font {font_index} code {code} already has a live glyph")
    while len(font["targets"]) <= index:
        font["targets"].append(None)

    region = {
        "kind": "opaque",
        "id": region_id,
        "offset": -1,
        "length": 0,
        "data": hconfig._hex_lines(encode_literal_glyph(rows)),
    }
    font["targets"][index] = {"to": region_id}
    return code, region
