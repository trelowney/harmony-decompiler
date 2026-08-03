"""Compare every 0x7F-terminated table - arch 9 against arch 8.

Shows two things: that the smaller tables in our config are near-subsets of the
main one (per-activity overlays), and that the main tables of the two
architectures share most of their codes in the same order.

Needs the arch 8 samples to do the cross-architecture half; see tools/_paths.py.

Usage:
    python compare_keytables.py [config.bin|config.EZHex]
"""
import sys

from _paths import SAMPLE_BLOB, arch8_samples, get_blob
from find_keytables import find_runs

TERM = 0x7F


def tables(data, min_run=15):
    out = []
    for off, cnt in find_runs(data, TERM, min_run=min_run):
        codes = [data[off + k * 4] for k in range(cnt)]
        if len(set(codes)) / cnt < 0.9:
            continue
        targets = [int.from_bytes(data[off + k * 4 + 1:off + k * 4 + 3], "little")
                   for k in range(cnt)]
        out.append((off, cnt, codes, targets))
    return out


def show(name, data):
    ts = tables(data)
    print(f"\n{'='*72}\n{name}  -  {len(ts)} tables with terminator 0x7F\n{'='*72}")
    for off, cnt, codes, targets in ts:
        print(f"\n  offset 0x{off:06X}   {cnt} entries   "
              f"codes 0x{min(codes):02X}-0x{max(codes):02X}")
        print(f"    codes  : {' '.join(f'{c:02X}' for c in codes)}")
        print(f"    targets: {targets}")
    return ts


ours = get_blob(sys.argv[1] if len(sys.argv) > 1 else SAMPLE_BLOB)
ot = show("Harmony 525 (arch 9)", ours)

samples = arch8_samples()
at = show(f"{samples[0].name} (arch 8)", get_blob(samples[0])) if samples else []

# --- how our own tables relate to each other ---
if len(ot) > 1:
    print(f"\n{'='*72}\nRELATIONSHIPS BETWEEN OUR TABLES\n{'='*72}")
    main = set(ot[0][2])
    for off, cnt, codes, _ in ot[1:]:
        cs = set(codes)
        print(f"  0x{off:06X} ({cnt}): subset of main? "
              f"{cs <= main}   shared {len(cs & main)}/{len(cs)}   "
              f"extra {sorted(hex(c) for c in cs - main)}")

# --- arch 9 versus arch 8 ---
#
# Compare against every arch 8 table, not just the first one found. Arch 8 has
# two: a canonical table at 0x000A22 that is byte-identical across all samples,
# and a per-configuration one at 0x0001EF. They give different overlap figures
# (41 shared versus 34), and an earlier version of this script quietly picked
# whichever came first, which is how the two ended up conflated in the docs.
if ot and at:
    a = set(ot[0][2])
    print(f"\n{'='*72}\nARCH 9 main table vs EVERY ARCH 8 TABLE\n{'='*72}")
    print(f"  arch 9 main @0x{ot[0][0]:06X}: {len(a)} codes, "
          f"0x{min(a):02X}-0x{max(a):02X}")
    for off, cnt, codes, targets in at:
        b = set(codes)
        contiguous = sorted(targets) == list(range(min(targets), max(targets) + 1))
        print(f"\n  arch 8 @0x{off:06X}: {len(b)} codes, "
              f"0x{min(b):02X}-0x{max(b):02X}, targets {min(targets)}-{max(targets)}"
              f"{' (contiguous)' if contiguous else ''}")
        print(f"    shared      : {len(a & b)}")
        print(f"    arch 9 only : {sorted(hex(c) for c in a - b)}")
        print(f"    arch 8 only : {sorted(hex(c) for c in b - a)}")

# --- which arch 8 tables are the same in every sample? ---
if len(samples) > 1:
    print(f"\n{'='*72}\nWHICH ARCH 8 TABLES ARE SAMPLE-INDEPENDENT\n{'='*72}")
    print("  Caveat before reading anything into this: the bundled arch 8")
    print("  samples all carry the same board and flash IDs and came from one")
    print("  person, so they are probably four configs for a single remote.")
    print("  'Identical across samples' therefore shows a table does not change")
    print("  with configuration - NOT that it is the same on every model.\n")
    per_file = {p.name: {off: codes for off, _, codes, _ in tables(get_blob(p))}
                for p in samples}
    all_offs = sorted({o for t in per_file.values() for o in t})
    for off in all_offs:
        present = [n for n, t in per_file.items() if off in t]
        variants = {tuple(t[off]) for n, t in per_file.items() if off in t}
        if len(present) == 1:
            verdict = "only in one sample - nothing to compare"
        elif len(variants) == 1:
            verdict = "IDENTICAL wherever present"
        else:
            verdict = f"varies ({len(variants)} different versions)"
        print(f"  0x{off:06X}: in {len(present)}/{len(per_file)} samples, {verdict}")
