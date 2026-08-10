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

    On arch 9 it additionally renders every mode page before and after. This
    caught pointer families that a graph-only comparison could not see while
    they were still opaque. It still does not prove hardware acceptance; an
    unread opaque pointer can remain outside every exercised root.
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

    render_note = ""
    if before[:4] == b"AHCM":
        import analyze_525_ir as ir
        import render_525_screens as renderer
        import verify_525_semantics as semantics

        def rendered(blob):
            sections = semantics.section_offsets(blob)
            fonts = renderer.fonts(blob, sections)
            return {
                (entry["mode"], entry["page"]): renderer.render_page(
                    blob, fonts, entry["root"])[0]
                for entry in renderer.page_roots(blob, sections)
            }

        before_screens = rendered(before)
        after_screens = rendered(after)
        if before_screens != after_screens:
            problems.append("rendered arch-9 screens changed after a relocation-only resize")
        else:
            render_note = f", {len(after_screens)} screens pixel-identical"

        def expanded_ir(blob):
            sections = semantics.section_offsets(blob)
            groups = ir.ir_groups(blob, sections)
            result = []
            for group in groups:
                records = []
                for address in group:
                    start = address - ir.BASE - 7
                    pointer_groups = blob[start + 11]
                    pointers = [ir.u24(blob, start + 12 + 3 * slot)
                                for slot in range(3 * pointer_groups)]
                    streams = tuple(
                        None if pointer == 0 else tuple(ir.body(blob, pointer)["words"])
                        for pointer in pointers
                    )
                    records.append((ir.u24(blob, start + 1), streams))
                result.append(records)
            return result

        before_ir = expanded_ir(before)
        after_ir = expanded_ir(after)
        if before_ir != after_ir:
            problems.append("expanded arch-9 class-5 IR changed after relocation-only resize")
        else:
            render_note += f", {sum(map(len, after_ir))} IR records exact"

    # A bare blob has no XML header, so there is nothing to check the size and
    # checksum against. That is not a failure, it just means this file cannot
    # exercise the container half of the test.
    xml = hconfig.split_container(out)[0]
    note = ""
    if xml is None:
        note = ", container not checked (bare blob, no XML header)"
    else:
        tag = hconfig._tag(xml, "BINARYDATASIZE")
        if int(tag) != len(after):
            problems.append(f"BINARYDATASIZE says {tag}, blob is {len(after)}")
        ck = hconfig._tag(xml, "CHECKSUM")
        if int(ck) != hconfig.blob_checksum(after):
            problems.append("checksum does not match the rebuilt blob")

    if problems:
        print(f"  {path.name:<28} FAIL  " + "; ".join(problems))
        return False

    n = sum(len(r.get("targets", [])) for r in doc["blob"]["regions"])
    print(f"  {path.name:<28} OK    +{delta} B absorbed, {n} pointers relinked, "
          f"structure unchanged{render_note}{note}")
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
