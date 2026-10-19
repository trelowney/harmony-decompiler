"""Build tools/arch9_glyphs.json: a glyph's pixels -> the character it draws.

A glyph code is assigned per configuration, so a code means nothing on its
own; the typeface of a skin is the same in every configuration of it, so a
code is read by its pixels (@dannybloe's method, his section 112). One
configuration per typeface was rendered and read by eye, its codes in order:

* skin 22, Harmony 525: the public sample, samples/harmony525/config.EZHex,
  whose alphabet is h525_lcd_author.H525_OWNER_ALPHABET.
* skin 67, Harmony 515: @kkong42's sample from issue #35 (H515-Config.EZHex),
  read on 2026-10-19. The first 49 codes are the 525's; from 50 the generator
  met other characters.

The Harmony 555 (skin 48) draws with the 525's typeface: 50 of its 53 codes
match it. The other three are characters the 525 never draws, read from
@kkong42's 555 sample from issue #42 (H555-Config.EZHez): a comma ("still on,
press"), a K ("OK") and a double quote ('"Help" now').

    python build_arch9_glyphs.py <H515-Config.EZHex> <H555-Config.EZHez>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import _paths  # noqa: F401
import compose525
import hconfig
import render_525_screens as renderer
import verify_525_semantics as sem
from h525_lcd_author import H525_OWNER_ALPHABET

# skin 67, code 1 first; code 13 is the space, which draws nothing
H515_ALPHABET = "HyOFaTermint EUSBCNDpgducs0:o2flL.wGWbIzkRPhAMq" + "\\" + 'v,x?Y()"/16V345+-'

# skin 48: only the codes the 525's shapes do not explain
H555_CODES = {50: ",", 51: "K", 52: '"'}

OUT = Path(__file__).resolve().parent / "arch9_glyphs.json"


def shapes_of(raw: bytes, alphabet) -> dict[str, set]:
    blob = hconfig.split_container(raw)[2]
    found = compose525._glyph_shapes(blob, renderer.fonts(blob, sem.section_offsets(blob)))
    out: dict[str, set] = {}
    for code, shapes in found.items():
        if isinstance(alphabet, dict):
            char = alphabet.get(code)
        else:
            char = alphabet[code - 1] if 1 <= code <= len(alphabet) else None
        if char is not None:
            for shape in shapes:
                out.setdefault(compose525.shape_key(shape), set()).add(char)
    return out


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    assert len(H515_ALPHABET) == 65 and len(set(H515_ALPHABET)) == 65, len(H515_ALPHABET)
    root = Path(__file__).resolve().parents[1]
    table: dict[str, set] = {}
    for raw, alphabet in (((root / "samples/harmony525/config.EZHex").read_bytes(), H525_OWNER_ALPHABET),
                          (Path(sys.argv[1]).read_bytes(), H515_ALPHABET),
                          (Path(sys.argv[2]).read_bytes(), H555_CODES)):
        for key, chars in shapes_of(raw, alphabet).items():
            table.setdefault(key, set()).update(chars)
    out = {key: "".join(sorted(chars)) for key, chars in sorted(table.items())}
    OUT.write_text(json.dumps(out, indent=0, ensure_ascii=False) + "\n", encoding="utf-8")
    shared = {k: v for k, v in out.items() if len(v) > 1}
    print(f"{len(out)} shapes, {len(shared)} drawn alike by two characters: {sorted(shared.values())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
