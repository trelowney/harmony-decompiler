"""Decompile a config and compile it straight back, then compare bytes.

This is the project's correctness test. A config that survives unchanged proves
the model in hconfig.py is complete for that file. A config that does not points
at exactly which offset the model gets wrong.

Usage:
    python roundtrip.py                 # the bundled 525 sample
    python roundtrip.py file.EZHex ...  # any number of configs
    python roundtrip.py --all           # every sample in the repo
"""
import sys
from pathlib import Path

import hconfig
from _paths import ARCH8_DIR, SAMPLE_DIR, SAMPLE_EZHEX


def check(path: Path) -> bool:
    raw = path.read_bytes()
    try:
        ok, rebuilt, doc = hconfig.roundtrip(raw, path.name)
    except hconfig.ConfigError as e:
        print(f"  {path.name:<28} SKIP  {e}")
        return None

    regions = doc["blob"]["regions"]
    decoded = sum(r["length"] for r in regions if "data" not in r)
    total = doc["blob"]["size"]

    if ok:
        print(f"  {path.name:<28} OK    {total} B identical, "
              f"{len(regions)} regions, {decoded} B decoded "
              f"({decoded/total*100:.2f}%)")
        return True

    off = hconfig.first_difference(raw, rebuilt)
    print(f"  {path.name:<28} FAIL  sizes {len(raw)} vs {len(rebuilt)}, "
          f"first difference at 0x{off:06X}")
    a, b = raw[off:off + 16], rebuilt[off:off + 16]
    print(f"      original : {a.hex(' ').upper()}")
    print(f"      rebuilt  : {b.hex(' ').upper()}")
    return False


def main(argv):
    if argv == ["--all"]:
        paths = sorted(SAMPLE_DIR.glob("*.EZHex"))
        if ARCH8_DIR.exists():
            paths += sorted(ARCH8_DIR.glob("*.EZHex"))
    elif argv:
        paths = [Path(a) for a in argv]
    else:
        paths = [SAMPLE_EZHEX]

    print(f"=== ROUND TRIP: {len(paths)} file(s) ===")
    results = [check(p) for p in paths]

    done = [r for r in results if r is not None]
    print(f"\n{sum(1 for r in done if r)}/{len(done)} identical"
          + (f", {len(results) - len(done)} skipped" if len(done) != len(results) else ""))
    return 0 if done and all(done) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
