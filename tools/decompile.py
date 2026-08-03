"""Decompile a config into JSON.

    python decompile.py                          # the bundled 525 sample
    python decompile.py config.EZHex out.json
    python decompile.py config.EZHex --summary   # print the layout, write nothing
    python decompile.py config.EZHex --absolute  # raw offsets, not region+delta

Regions with a `data` field are still opaque and pass through as hex; the rest
are decoded into fields and rebuilt from them by compile.py. Check the result
with roundtrip.py before trusting anything you edit.
"""
import sys
from pathlib import Path

import hconfig
from _paths import SAMPLE_EZHEX

KIND_LABEL = {
    "blob_header": "header",
    "blob_footer": "footer",
    "name_table": "name table",
    "key_table": "key table",
    "pointer_table": "pointers",
    "opaque": "-",
    "section": "-",
}


def summarise(doc):
    regions = doc["blob"]["regions"]
    total = doc["blob"]["size"]

    print(f"{'offset':>9} {'length':>7}  {'sec':>4}  {'kind':<12} detail")
    print("-" * 74)
    for r in regions:
        sec = f"{r['section']:>4}" if "section" in r else "    "
        detail = ""
        if r["kind"] == "pointer_table":
            detail = (f"{len(r['targets'])} addresses, "
                      f"u{r['count_width'] * 8} count")
        elif r["kind"] == "key_table":
            detail = f"{len(r['entries'])} keys"
        elif r["kind"] == "name_table":
            detail = f"{len(r['records'])} names"
        elif "data" in r:
            detail = "opaque"
        print(f"0x{r['offset']:07X} {r['length']:7d}  {sec}  "
              f"{KIND_LABEL.get(r['kind'], r['kind']):<12} {detail}")

    decoded = sum(r["length"] for r in regions if "data" not in r)
    kinds = {}
    for r in regions:
        if "data" not in r:
            kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1

    print("-" * 74)
    print(f"{len(regions)} regions, {decoded} of {total} B decoded "
          f"({decoded / total * 100:.2f}%)")
    print("  " + ", ".join(f"{n}x {KIND_LABEL.get(k, k)}"
                           for k, n in sorted(kinds.items())))

    sections = sorted({r["section"] for r in regions if "section" in r})
    with_ptrs = sorted({r["section"] for r in regions
                        if r["kind"] == "pointer_table" and "section" in r})
    print(f"  sections carrying pointers: {with_ptrs}")
    print(f"  sections still entirely opaque: "
          f"{[s for s in sections if s not in with_ptrs and not any(r.get('section') == s and 'data' not in r for r in regions)]}")


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    flags = {a for a in argv if a.startswith("--")}

    src = Path(args[0]) if args else SAMPLE_EZHEX
    doc = hconfig.decompile(src.read_bytes(), src.name)
    if "--absolute" not in flags:
        # pointers as `region + delta` rather than raw offsets, so the compiler
        # can relink them if anything before them changes length
        doc = hconfig.symbolise(doc)

    summarise(doc)

    if "--summary" in flags:
        return 0

    dst = Path(args[1]) if len(args) > 1 else src.with_suffix(".json")
    hconfig.dump(doc, dst)
    print(f"\nwritten to {dst} ({dst.stat().st_size} B)")

    rebuilt = hconfig.compile_config(doc)
    ok = rebuilt == src.read_bytes()
    print(f"round trip: {'byte-identical' if ok else 'DIFFERS - do not trust this'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
