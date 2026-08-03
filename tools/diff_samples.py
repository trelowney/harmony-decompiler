"""Diff sample configs against each other.

Three of the arch 8 samples were created ten minutes apart, so they should differ
in almost nothing. They differ in 73-84% of their bytes, with the first
difference at offset 0x000004 - inside the u32 end-of-config address.

That is the single most important negative result in this project: the config is
a compiled image with absolute pointers, so any change in length shifts
everything after it. Differential analysis is dead, and patching is only possible
without changing any length. See FORMAT.md §5.

Needs the arch 8 samples; see tools/_paths.py for where to get them.

Usage:
    python diff_samples.py
"""
import sys

from _paths import SAMPLE_BLOB, arch8_samples, get_blob

files = arch8_samples()
if not files:
    print("\nNothing to compare. This script needs at least two configs.")
    sys.exit(1)

blobs = {p.name: get_blob(p) for p in files}

print("=== SAMPLES ===")
for n, b in blobs.items():
    print(f"  {n:<24} {len(b):7d} B  magic={b[:4]!r} end={b[-4:]!r}")

ours = get_blob(SAMPLE_BLOB)
print(f"  {'Harmony 525 (arch 9)':<24} {len(ours):7d} B  "
      f"magic={ours[:4]!r} end={ours[-4:]!r}")

# --- header of the first sample, for comparison against ours ---
first = sorted(blobs)[0]
b1 = blobs[first]
print(f"\n=== HEADER OF {first} (first 64 B) ===")
for base in range(0, 64, 16):
    c = b1[base:base + 16]
    print(f"{base:06X}  {' '.join(f'{x:02X}' for x in c):<47}  "
          f"|{''.join(chr(x) if 32 <= x < 127 else '.' for x in c)}|")

# --- pairwise diffs ---
names = sorted(blobs)
print(f"\n=== DIFFERENCES BETWEEN SAMPLES ===")
for i in range(len(names) - 1):
    a, b = blobs[names[i]], blobs[names[i + 1]]
    n = min(len(a), len(b))
    firstd = next((j for j in range(n) if a[j] != b[j]), None)
    lastd = next((j for j in range(n - 1, -1, -1) if a[j] != b[j]), None)
    diff = sum(1 for j in range(n) if a[j] != b[j])
    print(f"\n  {names[i]}  vs  {names[i+1]}")
    print(f"    lengths    : {len(a)} vs {len(b)}  (delta {len(b)-len(a):+d} B)")
    print(f"    differing  : {diff} of {n} shared ({diff/n*100:.1f} %)")
    if firstd is None:
        print("    identical over the shared prefix")
        continue
    print(f"    first change: 0x{firstd:06X}")
    print(f"    last change : 0x{lastd:06X}")
    print(f"    identical header for the first {firstd} B")
