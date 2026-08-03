"""Generic key-table detector.

Our config holds tables shaped <u8 code> <u16 target> <0x7F>, repeated. The
detector does not assume arch 8 uses the same terminator - it tries all 256
values and reports the longest contiguous runs.

Candidates are ranked by the **ratio of unique codes**, not by length. That
matters: the longest runs are always filler bytes, with 2-3 distinct codes out of
hundreds. A real key table has every code distinct, which is the signature worth
looking for.

The run against our own config first is a self-test: the known-correct answer is
51 entries at 0x0000FB, so if that is not what comes out on top, the detector is
broken rather than the data being interesting.

Usage:
    python find_keytables.py [config.bin|config.EZHex]
"""
import sys
from pathlib import Path

from _paths import SAMPLE_BLOB, arch8_samples, get_blob

MIN_RUN = 12


def find_runs(data, term, min_run=MIN_RUN):
    """Find runs of 4-byte groups whose 4th byte == term."""
    runs, i, n = [], 0, len(data)
    while i + 4 <= n:
        if data[i + 3] == term:
            start, cnt = i, 0
            while i + 4 <= n and data[i + 3] == term:
                cnt += 1
                i += 4
            if cnt >= min_run:
                runs.append((start, cnt))
        else:
            i += 1
    return runs


def analyse(name, data):
    print(f"\n{'='*70}\n{name}  ({len(data)} B, magic {data[:4]!r})\n{'='*70}")

    cands = []
    for term in range(256):
        for off, cnt in find_runs(data, term, min_run=20):
            if cnt > 300:                     # a key table will not be enormous
                continue
            codes = [data[off + k * 4] for k in range(cnt)]
            uniq = len(set(codes)) / cnt
            if uniq < 0.8:                    # discard filler
                continue
            cands.append((uniq, cnt, term, off, codes))
    cands.sort(key=lambda c: (-c[0] * c[1], -c[1]))

    if not cands:
        print("  no candidate with a high ratio of unique codes")
        return

    print(f"  {'uniq':>6} {'length':>6} {'term':>5} {'offset':>9}  code range")
    for uniq, cnt, term, off, codes in cands[:10]:
        print(f"  {uniq*100:5.0f}% {cnt:6d}  0x{term:02X}  0x{off:06X}"
              f"  0x{min(codes):02X}-0x{max(codes):02X}")

    uniq, cnt, term, off, codes = cands[0]
    targets = [int.from_bytes(data[off + k * 4 + 1: off + k * 4 + 3], "little")
               for k in range(cnt)]
    print(f"\n  --- best: {cnt} entries, term 0x{term:02X}, "
          f"offset 0x{off:06X}, {uniq*100:.0f}% unique ---")
    print(f"  targets: {min(targets)}-{max(targets)}, "
          f"{len(set(targets))} unique")
    print(f"  codes  : {' '.join(f'{c:02X}' for c in codes[:24])}"
          f"{' ...' if cnt > 24 else ''}")
    print(f"  targets: {targets[:24]}{' ...' if cnt > 24 else ''}")


def main():
    if len(sys.argv) > 1:
        p = Path(sys.argv[1])
        analyse(p.name, get_blob(p))
        return 0

    analyse("Harmony 525 (arch 9) - DETECTOR SELF-TEST", get_blob(SAMPLE_BLOB))
    for p in arch8_samples():
        analyse(f"{p.name} (arch 8)", get_blob(p))
    return 0


if __name__ == "__main__":
    sys.exit(main())
