"""Decompile a config and compile it straight back, then compare bytes.

This is the project's correctness test. A config that survives unchanged proves
the model in hconfig.py is complete for that file. A config that does not points
at exactly which offset the model gets wrong.

Usage:
    python roundtrip.py                 # the bundled 525 sample
    python roundtrip.py file.EZHex ...  # any number of configs
    python roundtrip.py --all           # every sample in the repo
    python roundtrip.py --resize        # lengthen a name, check it relinks
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


def check_resize(path: Path) -> bool:
    """Lengthen something and check the whole file relinks around it.

    The round trip proves the model can rebuild what it read. This proves the
    model can rebuild something it did not read: a name in the name table gets
    longer, every section after it shifts, and every pointer has to be
    recomputed. If the config comes back with the same region structure and the
    same pointers - the same *symbolic* pointers, meaning each still refers to
    the thing it referred to before - the relinking worked.

    What it does not prove is that a remote would accept the result. Pointers
    hidden inside regions that are still opaque are invisible to this code and
    would have been silently left behind. That is the risk, and it is the reason
    the remaining sections are worth decoding.
    """
    raw = path.read_bytes()
    try:
        doc = hconfig.symbolise(hconfig.decompile(raw, path.name))
    except hconfig.ConfigError:
        return None

    nt = next((r for r in doc["blob"]["regions"]
               if r["kind"] == "name_table" and r["records"]), None)
    if nt is None:
        print(f"  {path.name:<28} SKIP  no name table to lengthen")
        return None

    grew = "_resize_probe"
    nt["records"][0]["name"] += grew
    out = hconfig.compile_config(doc)

    before = hconfig.split_container(raw)[2]
    after = hconfig.split_container(out)[2]
    delta = len(after) - len(before)

    problems = []
    if delta != len(grew):
        problems.append(f"blob grew by {delta}, expected {len(grew)}")

    again = hconfig.symbolise(hconfig.decompile(out))
    shape = lambda d: [(r["kind"], r.get("section"), len(r.get("targets", [])))
                       for r in d["blob"]["regions"]]
    if shape(doc) != shape(again):
        problems.append("region structure changed")

    ptrs = lambda d: [t for r in d["blob"]["regions"] for t in r.get("targets", [])]
    if ptrs(doc) != ptrs(again):
        problems.append("pointers do not refer to the same regions afterwards")

    tag = hconfig._tag(hconfig.split_container(out)[0], "BINARYDATASIZE")
    if int(tag) != len(after):
        problems.append(f"BINARYDATASIZE says {tag}, blob is {len(after)}")
    ck = hconfig._tag(hconfig.split_container(out)[0], "CHECKSUM")
    if int(ck) != hconfig.blob_checksum(after):
        problems.append("checksum does not match the rebuilt blob")

    if problems:
        print(f"  {path.name:<28} FAIL  " + "; ".join(problems))
        return False

    n = sum(len(r.get("targets", [])) for r in doc["blob"]["regions"])
    print(f"  {path.name:<28} OK    +{delta} B absorbed, {n} pointers relinked, "
          f"structure unchanged")
    return True


def main(argv):
    if "--resize" in argv:
        paths = [Path(a) for a in argv if not a.startswith("--")] or [SAMPLE_EZHEX]
        print(f"=== RESIZE: {len(paths)} file(s) ===")
        results = [check_resize(p) for p in paths]
        done = [r for r in results if r is not None]
        return 0 if done and all(done) else 1

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
