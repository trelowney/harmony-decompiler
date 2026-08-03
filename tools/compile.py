"""Compile JSON back into a config.

    python compile.py config.json out.EZHex
    python compile.py config.json out.EZHex --against original.EZHex

Section addresses and the end-of-config address are recomputed from where the
regions actually land, and the container's BINARYDATASIZE and CHECKSUM are
updated to match the rebuilt blob. So a change that alters a section's length
still produces a self-consistent file.

What it cannot fix is a pointer sitting inside a region that is still opaque -
those are hex we copy verbatim, and nothing here knows they are addresses. Until
every section carrying pointers is decoded, keep edits length-neutral. Changing
a key table's targets is safe; adding an entry to one is not.
"""
import sys
from pathlib import Path

import hconfig


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    if not args:
        print(__doc__.strip())
        return 2

    doc = hconfig.load(args[0])
    out = hconfig.compile_config(doc)

    ref = None
    if "--against" in argv:
        ref = Path(argv[argv.index("--against") + 1]).read_bytes()
    elif doc.get("source", {}).get("sha256"):
        import hashlib
        same = hashlib.sha256(out).hexdigest() == doc["source"]["sha256"]
        print(f"against the sha256 recorded at decompile time: "
              f"{'identical' if same else 'DIFFERENT'}")
        if not same:
            print("  (expected, if you edited the JSON on purpose)")

    if ref is not None:
        if out == ref:
            print(f"byte-identical to {Path(argv[argv.index('--against') + 1]).name}")
        else:
            off = hconfig.first_difference(ref, out)
            print(f"DIFFERS: sizes {len(ref)} vs {len(out)}, "
                  f"first difference at 0x{off:06X}")
            print(f"  original: {ref[off:off + 16].hex(' ').upper()}")
            print(f"  rebuilt : {out[off:off + 16].hex(' ').upper()}")

    if len(args) > 1:
        Path(args[1]).write_bytes(out)
        print(f"written to {args[1]} ({len(out)} B)")
    else:
        print(f"rebuilt {len(out)} B; pass an output path to save it")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
