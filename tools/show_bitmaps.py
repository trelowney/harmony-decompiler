"""Render the LCD bitmaps in a config as text.

    python show_bitmaps.py                  # the bundled 525 sample
    python show_bitmaps.py config.EZHex
    python show_bitmaps.py --all            # include rows that are entirely blank

The 525 carries four 96x64 monochrome images in section 17, and every one of the
1072 block headers in the config points at one of them. Three are referenced; the
fourth is filled with 0xFF, which is what erased flash looks like.

This exists so the claim in FORMAT.md can be checked by looking rather than
believed. A wrong pixel format would produce noise; straight lines mean the
reading is right.
"""
import sys
from pathlib import Path

import hconfig
from _paths import SAMPLE_EZHEX


def render(bm, show_blank=False):
    w, h = bm["width"], bm["height"]
    px = hconfig._unhex(bm["pixels"])
    print(f"=== 0x{bm['offset']:06X}  {w} x {h}  ({len(px)} B) ===")

    ink = sum(bin(b).count("1") for b in px)
    print(f"    {ink} of {w * h} pixels set ({ink / (w * h) * 100:.1f}%)")

    shown = 0
    for y in range(h):
        row = "".join(
            "#" if (px[(y * w + x) // 8] >> (7 - ((y * w + x) % 8))) & 1 else "."
            for x in range(w))
        if show_blank or row.strip("."):
            print(f"  {y:2d} {row}")
            shown += 1
    if not shown:
        print("    (entirely blank)")
    print()


def main(argv):
    flags = {a for a in argv if a.startswith("--")}
    args = [a for a in argv if not a.startswith("--")]
    src = Path(args[0]) if args else SAMPLE_EZHEX

    doc = hconfig.decompile(src.read_bytes(), src.name)
    bitmaps = [r for r in doc["blob"]["regions"] if r["kind"] == "bitmap"]
    if not bitmaps:
        print("no bitmaps found")
        return 1

    targets = {t for r in doc["blob"]["regions"] if r["kind"] == "block_header"
               for t in r["targets"]}
    print(f"{len(bitmaps)} bitmaps, {len(targets)} of them referenced by block "
          f"headers\n")
    for bm in bitmaps:
        render(bm, "--all" in flags)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
