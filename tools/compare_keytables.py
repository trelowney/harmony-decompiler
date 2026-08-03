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
if ot and at:
    a, b = set(ot[0][2]), set(at[0][2])
    print(f"\n{'='*72}\nARCH 9 vs ARCH 8 - main tables\n{'='*72}")
    print(f"  arch 9 : {len(a)} codes, 0x{min(a):02X}-0x{max(a):02X}")
    print(f"  arch 8 : {len(b)} codes, 0x{min(b):02X}-0x{max(b):02X}")
    print(f"  shared      : {len(a & b)}")
    print(f"  arch 9 only : {sorted(hex(c) for c in a - b)}")
    print(f"  arch 8 only : {sorted(hex(c) for c in b - a)}")
